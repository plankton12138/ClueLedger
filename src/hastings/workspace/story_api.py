"""构思结构的作用品接口（1.0.1 P5）。

仅保留当前构思、节点详情、结构变更、改动回看与撤销；备选、比较、采用和候选正文浏览接口已移除。

写入请求都带 ``expected_revision`` 与 ``request_key``：修订不符返回 409，同键同负载返回
同一收据。响应里的 ID 与修订直接来自状态文件，不由界面拼。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from hastings.workspace.atomic_io import atomic_write_json, locked
from hastings.workspace.chapter_writing import save_chapter_prose
from hastings.workspace.creative_candidates import CreativeCandidateStore
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.index_confirmation import confirm_index_candidate
from hastings.workspace.revision_analysis import RevisionAnalysisService, RevisionAnalysisStore
from hastings.workspace.revision_apply import (
    apply_module_decisions,
    apply_structure_decisions,
)
from hastings.workspace.revision_diff import active_hunk_ids
from hastings.workspace.revision_store import RevisionStore, sync_chapter_revisions
from hastings.workspace.service_hub import WorkspaceServices
from hastings.workspace.state import ConversationStore, RecentWorkspaces, conversation_root
from hastings.workspace.story_clue_commit import prepare_clue_undo
from hastings.workspace.story_index_jobs import IndexAnalysisService, IndexJobStore
from hastings.workspace.story_models import FORMAT_VERSION
from hastings.workspace.story_store import MAIN_VARIANT_ID, StoryStore
from hastings.workspace.structure_document import BUDGET_LIMIT, check_budget
from hastings.workspace.structure_io import read_structure_state
from hastings.workspace.style_io import read_style_state, write_style_document
from hastings.workspace.writing_actions import (
    rename_chapter,
    rename_unplanned_prose,
)
from hastings.workspace.writing_api_models import (
    ChapterRenameRequest,
    IndexConfirmRequest,
    ProseRenameRequest,
    ProseSaveRequest,
)
from hastings.workspace.writing_view import (
    ModelSupportView,
    WritingWorkbenchView,
    build_chapter_index,
    build_workbench,
)

__all__ = ["create_story_router"]


class StoryReceiptView(BaseModel):
    variant_id: str
    base_revision: str
    revision: str
    ledger_revision: str = ""
    change_id: str = ""
    request_key: str
    id_map: dict[str, str] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    changed: list[str] = Field(default_factory=list)


class StoryChangeRecordView(BaseModel):
    change_id: str
    variant_id: str
    base_revision: str
    revision: str
    created_at: str
    operations: list[dict[str, Any]]
    changed: list[str]
    undone_by: str | None = None
    sync: dict[str, Any] | None = None


class StyleSaveRequest(BaseModel):
    model_config = {"extra": "forbid"}

    text: str
    expected_digest: str | None = Field(default=None, max_length=128)
    request_key: str = Field(default="", max_length=200)


class AnalysisStartRequest(BaseModel):
    model_config = {"extra": "forbid"}

    hunk_ids: list[str] = Field(default_factory=list, max_length=50)
    conversation_id: str = Field(default="", max_length=64)
    operation_id: str = Field(default="", max_length=64)
    request_key: str = Field(min_length=1, max_length=200)


class AnalysisApplyRequest(BaseModel):
    model_config = {"extra": "forbid"}

    text: str
    expected_digest: str | None = Field(default=None, max_length=128)
    decision_indexes: list[int] = Field(default_factory=list, max_length=50)
    request_key: str = Field(default="", max_length=200)


class SuggestionApplyRequest(BaseModel):
    model_config = {"extra": "forbid"}

    decision_indexes: list[int] = Field(default_factory=list, max_length=50)
    expected_digest: str | None = Field(default=None, max_length=128)
    request_key: str = Field(default="", max_length=200)


def _receipt_view(receipt: Any, changed: list[str]) -> StoryReceiptView:
    return StoryReceiptView(
        variant_id=receipt.variant_id,
        base_revision=receipt.base_revision,
        revision=receipt.revision,
        ledger_revision=receipt.ledger_revision,
        change_id=receipt.change_id,
        request_key=receipt.request_key,
        id_map=dict(receipt.id_map),
        result=dict(receipt.result),
        changed=changed,
    )


def _changed_ids(store: StoryStore, change_id: str) -> list[str]:
    snapshot = store.read()
    if snapshot.document is None or not change_id:
        return []
    record = snapshot.document.changes.get(change_id)
    if record is None:
        return []
    return sorted([*record.touched, *(f"prose:{p.path}" for p in record.inverse_prose_patches)])


def create_story_router(
    workspaces: RecentWorkspaces, hub: WorkspaceServices | None = None
) -> APIRouter:
    router = APIRouter(prefix="/api/file-workspaces", tags=["story"])

    def store_for(workspace_id: str, *, format_version: int = 2) -> StoryStore:
        workspaces.get(workspace_id)

        def conversation_seq(conversation_id: str) -> str | None:
            """会话依赖按受理时的事件序号核对：有更新事件即视为变化。"""
            store = ConversationStore(conversation_root(workspaces.root, workspace_id))
            events = store.events(conversation_id)
            return str(events[-1]["seq"]) if events else "0"

        return StoryStore(
            conversation_root(workspaces.root, workspace_id),
            workspaces.workspace(workspace_id),
            library=hub.library if hub is not None else None,
            conversation_seq=conversation_seq,
            format_version=format_version,
        )

    @router.get("/{workspace_id}/story/changes/{change_id}", response_model=StoryChangeRecordView)
    def read_change(workspace_id: str, change_id: str) -> StoryChangeRecordView:
        snapshot = store_for(workspace_id).read()
        document = snapshot.document
        record = document.changes.get(change_id) if document is not None else None
        if record is None:
            raise WorkspaceError(
                code="STORY_CHANGE_NOT_FOUND",
                where="story.api",
                why=f"没有这次改动：{change_id}",
                fix="刷新改动列表后重试",
                details={"change_id": change_id},
            )
        return StoryChangeRecordView(
            change_id=record.change_id,
            variant_id=record.variant_id,
            base_revision=record.base_revision,
            revision=record.revision,
            created_at=record.created_at,
            operations=[item.model_dump(mode="json") for item in record.operations],
            changed=sorted(
                [*record.touched, *(f"prose:{p.path}" for p in record.inverse_prose_patches)]
            ),
            undone_by=record.undone_by,
            sync=record.sync.model_dump(mode="json") if record.sync else None,
        )

    @router.post(
        "/{workspace_id}/story/changes/{change_id}/undo",
        response_model=StoryReceiptView,
        status_code=201,
    )
    def undo_change(
        workspace_id: str, change_id: str, request_key: str = Query(min_length=1)
    ) -> StoryReceiptView:
        """撤销一次改动：只恢复当时涉及的对象，后续无关编辑保留。"""
        store = store_for(workspace_id)
        snapshot = store.read()
        record = snapshot.document.changes.get(change_id) if snapshot.document is not None else None
        if record is None:
            raise WorkspaceError(
                code="STORY_CHANGE_NOT_FOUND",
                where="story.api",
                why=f"没有这次改动：{change_id}",
                fix="刷新改动列表后重试",
                details={"change_id": change_id},
            )
        receipt = store.undo(
            change_id=change_id,
            request_key=request_key,
            prepare_joint=prepare_clue_undo(
                conversation_root(workspaces.root, workspace_id),
                variant_id=record.variant_id,
                inverse_ledger=list(record.inverse_ledger),
            ),
        )
        return _receipt_view(receipt, _changed_ids(store, receipt.change_id))

    def writing_store_for(workspace_id: str) -> StoryStore:
        """当前写作格式的存储入口；新作品第一次提交会写入格式 5。"""
        return store_for(workspace_id, format_version=FORMAT_VERSION)

    @router.get("/{workspace_id}/story/workbench", response_model=WritingWorkbenchView)
    async def read_workbench(
        workspace_id: str,
        chapter_id: str | None = Query(default=None, max_length=64),
        prose_path: str | None = Query(default=None, max_length=512),
    ) -> WritingWorkbenchView:
        store = writing_store_for(workspace_id)
        workspace = workspaces.workspace(workspace_id)
        variant = store.read().variant(MAIN_VARIANT_ID)
        jobs = IndexJobStore(conversation_root(workspaces.root, workspace_id))
        support = ModelSupportView(
            available=False, disabled_reason="当前没有可用模型", supported_assists=[]
        )
        if hub is not None:
            try:
                snapshot = hub.provider_factory()
            except Exception:
                snapshot = None
            else:
                assert snapshot is not None
                await snapshot.aclose()
                support = ModelSupportView(
                    available=True,
                    disabled_reason=None,
                    supported_assists=[
                        "chapter_direction",
                        "title",
                        "prose_draft",
                        "prose_continue",
                        "alignment_resolution",
                        "idea",
                        "module_content",
                        "next_action",
                    ],
                )
        return build_workbench(
            workspace,
            variant,
            workspace_id=workspace_id,
            chapter_id=chapter_id,
            prose_path=prose_path,
            model_support=support,
            index_jobs=jobs,
        )

    @router.get("/{workspace_id}/story/structure")
    def get_structure(workspace_id: str) -> dict[str, Any]:
        """结构文档现状（v1.1 M2 只读页）：全文、体量与预算占用；起步前缺失。"""
        workspace = workspaces.workspace(workspace_id)
        state = read_structure_state(workspace)
        budget = check_budget(state.text)
        return {
            "exists": state.exists,
            "text": state.text,
            "digest": state.digest,
            "size": state.size,
            "within_budget": budget.ok,
            "budget_limit": BUDGET_LIMIT,
        }

    @router.put("/{workspace_id}/story/chapters/{chapter_id}")
    def put_chapter(
        workspace_id: str, chapter_id: str, body: ChapterRenameRequest
    ) -> dict[str, Any]:
        store = writing_store_for(workspace_id)
        workspace = workspaces.workspace(workspace_id)
        result = rename_chapter(
            store,
            workspace,
            chapter_id=chapter_id,
            title=body.title,
            expected_plan_revision=body.expected_plan_revision,
            request_key=body.request_key,
        )
        return {
            "chapter_id": result.chapter_id,
            "title": result.title,
            "plan_revision": result.plan_revision,
            "change_id": result.receipt.change_id,
        }

    @router.put("/{workspace_id}/story/prose-title")
    def put_prose_title(workspace_id: str, body: ProseRenameRequest) -> dict[str, Any]:
        store = writing_store_for(workspace_id)
        variant = store.read().variant(MAIN_VARIANT_ID)
        if variant is not None and variant.chapter_for_path(body.path) is not None:
            raise WorkspaceError(
                code="CHAPTER_RENAME_REQUIRED",
                where=body.path,
                why="这一章已经登记在作品计划中",
                fix="使用章节改名入口，让标题与正文在同一提交里更新",
                details={"path": body.path},
            )
        result = rename_unplanned_prose(
            workspaces.workspace(workspace_id),
            path=body.path,
            title=body.title,
            expected_digest=body.expected_digest,
        )
        return {"path": result.path, "title": result.title, "digest": result.digest}

    @router.put("/{workspace_id}/story/chapters/{chapter_id}/prose")
    async def put_chapter_prose(
        workspace_id: str, chapter_id: str, body: ProseSaveRequest
    ) -> dict[str, Any]:
        store = writing_store_for(workspace_id)
        workspace = workspaces.workspace(workspace_id)
        if body.candidate_id is not None:
            candidate = CreativeCandidateStore(
                conversation_root(workspaces.root, workspace_id)
            ).read(body.candidate_id)
            if candidate.status != "ready":
                raise WorkspaceError(
                    code="ASSIST_TARGET_STALE",
                    where=f"章节/{chapter_id}",
                    why="这条候选已过期或未完成",
                    fix="比较候选与当前草稿，或重新生成",
                    details={"candidate_id": body.candidate_id},
                )
            if (
                candidate.target.kind != "prose"
                or candidate.target.chapter_id != chapter_id
                or (
                    candidate.base.prose_digest is not None
                    and candidate.base.prose_digest != body.expected_digest
                )
            ):
                raise WorkspaceError(
                    code="ASSIST_TARGET_STALE",
                    where=f"章节/{chapter_id}",
                    why="候选依据的章节或正文已经变化",
                    fix="重新读取当前正文后再决定是否采用",
                    details={"candidate_id": body.candidate_id},
                )
        result = await asyncio.to_thread(
            lambda: save_chapter_prose(
                store,
                workspace,
                chapter_id=chapter_id,
                text=body.text,
                expected_digest=body.expected_digest,
                request_key=body.request_key,
                draft_revision=body.draft_revision,
                source="candidate" if body.candidate_id is not None else "author",
                candidate_ref=body.candidate_id or "",
            )
        )
        job_id: str | None = None
        if result.changed_text and hub is not None:
            variant = store.require_variant(MAIN_VARIANT_ID)
            plan = variant.chapter(chapter_id)
            if plan is not None:
                service = IndexAnalysisService(
                    state_root=conversation_root(workspaces.root, workspace_id),
                    workspace=workspace,
                    provider_factory=hub.provider_factory,
                )
                job_id = await service.enqueue(
                    variant=variant,
                    plan=plan,
                    prose_digest=result.prose_digest,
                    context_revision=result.context_revision,
                    changed=result.changed_text,
                )
        index = result.index.model_dump(mode="json")
        if job_id is not None:
            index["analysis"] = {
                "state": "queued",
                "based_on_digest": result.prose_digest,
                "operation_id": job_id,
            }
        return {
            "path": result.path,
            "prose_digest": result.prose_digest,
            "plan_revision": result.plan_revision,
            "context_revision": result.context_revision,
            "index_revision": result.index_revision,
            "index": index,
            "analysis_job_id": job_id,
            "committed_change_id": result.committed_change_id,
            "revision_id": result.revision_id or None,
            "clue_findings": [
                {"code": finding.code, "message": finding.message}
                for finding in result.clue_findings
            ],
        }

    @router.post("/{workspace_id}/story/chapters/{chapter_id}/index-review")
    async def post_index_review(workspace_id: str, chapter_id: str) -> dict[str, Any]:
        """空章反馈里的「让模型看看本章」：重新提交整章做一次增量语义分析。"""
        store = writing_store_for(workspace_id)
        workspace = workspaces.workspace(workspace_id)
        variant = store.require_variant(MAIN_VARIANT_ID)
        plan = variant.chapter(chapter_id)
        if plan is None or plan.prose_path is None:
            raise WorkspaceError(
                code="CHAPTER_NOT_FOUND",
                where=f"章节/{chapter_id}",
                why="章节或正文不存在",
                fix="刷新工作台后重新选择章节",
                details={"chapter_id": chapter_id},
            )
        if hub is None:
            raise WorkspaceError(
                code="MODEL_UNAVAILABLE",
                where=f"章节/{chapter_id}",
                why="没有可用模型",
                fix="先在设置中完成模型连接与选择",
            )
        view = workspace.read(plan.prose_path)
        index = build_chapter_index(
            variant, plan, path=plan.prose_path, text=view.text, digest=view.digest
        )
        service = IndexAnalysisService(
            state_root=conversation_root(workspaces.root, workspace_id),
            workspace=workspace,
            provider_factory=hub.provider_factory,
        )
        job_id = await service.enqueue(
            variant=variant,
            plan=plan,
            prose_digest=view.digest,
            context_revision=index.context_revision,
            changed=view.text,
        )
        return {"analysis_job_id": job_id}

    @router.post("/{workspace_id}/story/index-candidates/{candidate_id}/confirm")
    def post_index_confirm(
        workspace_id: str, candidate_id: str, body: IndexConfirmRequest
    ) -> dict[str, Any]:
        """确认一条索引候选入账；联合提交创建信息线、信息变化与绑定。"""
        store = writing_store_for(workspace_id)
        workspace = workspaces.workspace(workspace_id)
        jobs = IndexJobStore(conversation_root(workspaces.root, workspace_id))
        result = confirm_index_candidate(
            store,
            workspace,
            jobs,
            chapter_id=body.chapter_id,
            candidate_id=candidate_id,
            expected_prose_digest=body.expected_prose_digest,
            request_key=body.request_key,
        )
        return {
            "chapter_id": result.chapter_id,
            "thread": result.thread.model_dump(mode="json"),
            "move": result.move.model_dump(mode="json"),
            "binding": result.binding.model_dump(mode="json"),
            "plan_revision": result.plan_revision,
            "change_id": result.receipt.change_id,
        }

    # -- 修订与写作约定（1.2 修订学习） -------------------------------------

    @router.get("/{workspace_id}/story/chapters/{chapter_id}/revisions")
    def list_chapter_revisions(workspace_id: str, chapter_id: str) -> dict[str, Any]:
        """按章列出修订；已确认入稿的正文改动才在列表里。"""
        store = writing_store_for(workspace_id)
        workspace = workspaces.workspace(workspace_id)
        variant = store.require_variant(MAIN_VARIANT_ID)
        plan = variant.chapter(chapter_id)
        if plan is None or plan.prose_path is None:
            raise WorkspaceError(
                code="CHAPTER_NOT_FOUND",
                where=f"章节/{chapter_id}",
                why="章节或正文不存在",
                fix="刷新工作台后重新选择章节",
                details={"chapter_id": chapter_id},
            )
        revisions = RevisionStore(store.root)
        records = sync_chapter_revisions(
            store,
            workspace,
            chapter_id=chapter_id,
            path=plan.prose_path,
            revisions=revisions,
        )
        try:
            current = workspace.read(plan.prose_path).text
        except WorkspaceError:
            current = ""
        items: list[dict[str, Any]] = []
        for record in records:
            live = active_hunk_ids(record.hunks, current) if current else set()
            items.append(
                {
                    **record.summary(),
                    "active_hunk_count": len(live),
                    "analyzable": bool(live),
                }
            )
        return {"chapter_id": chapter_id, "path": plan.prose_path, "items": items}

    @router.get("/{workspace_id}/story/revisions/{revision_id}")
    def read_revision(
        workspace_id: str, revision_id: str, conversation_id: str | None = None
    ) -> dict[str, Any]:
        """一次修订的完整差异；每个片段标明现在是否仍能在稿内确认。"""
        store = writing_store_for(workspace_id)
        workspace = workspaces.workspace(workspace_id)
        revisions = RevisionStore(store.root)
        record = revisions.get(revision_id)
        if record is None:
            raise WorkspaceError(
                code="REVISION_NOT_FOUND",
                where=f"修订/{revision_id}",
                why="没有这次修订记录",
                fix="刷新改动列表后重新选择",
                details={"revision_id": revision_id},
            )
        try:
            current_text = workspace.read(record.path).text
        except WorkspaceError:
            current_text = ""
        detail = revisions.detail(revision_id, current_text)
        assert detail is not None
        hunks = [
            {**hunk.to_dict(), "active": hunk.id in detail.active_hunk_ids} for hunk in detail.hunks
        ]
        analyses = RevisionAnalysisStore(store.root).list_for_revision(
            revision_id, limit=1, conversation_id=conversation_id
        )
        return {
            "revision": record.summary(),
            "hunks": hunks,
            "active_hunk_ids": sorted(detail.active_hunk_ids),
            "analyzable": detail.analyzable,
            "current_text_available": bool(current_text),
            "latest_analysis": analyses[0].to_dict() if analyses else None,
        }

    @router.get("/{workspace_id}/story/style")
    def read_style(workspace_id: str) -> dict[str, Any]:
        workspace = workspaces.workspace(workspace_id)
        state = read_style_state(workspace)
        return {
            "path": "STYLE.md",
            "exists": state.exists,
            "text": state.text,
            "digest": state.digest,
            "size": state.size,
        }

    @router.put("/{workspace_id}/story/style")
    def put_style(workspace_id: str, body: StyleSaveRequest) -> dict[str, Any]:
        """作者直接保存整份写作约定；同键同内容返回原回执。"""
        workspace = workspaces.workspace(workspace_id)
        state_root = conversation_root(workspaces.root, workspace_id)
        request_log = state_root / "story" / "style-requests.json"
        text_digest = hashlib.sha256(body.text.encode("utf-8")).hexdigest()
        if body.request_key:
            with locked(request_log.with_suffix(".lock")):
                prior = _read_request_log(request_log).get(body.request_key)
                if isinstance(prior, dict):
                    if prior.get("text_digest") == text_digest:
                        return {
                            "path": "STYLE.md",
                            "text": body.text,
                            "digest": prior.get("digest", ""),
                            "replayed": True,
                        }
                    raise WorkspaceError(
                        code="STYLE_REQUEST_CONFLICT",
                        where="STYLE.md",
                        why="同一个请求键已经保存过另一份内容",
                        fix="换一个请求键重新保存，避免把两次编辑合成一次",
                        details={"request_key": body.request_key},
                    )
        view = write_style_document(workspace, text=body.text, expected_digest=body.expected_digest)
        if body.request_key:
            with locked(request_log.with_suffix(".lock")):
                log = _read_request_log(request_log)
                log[body.request_key] = {
                    "text_digest": text_digest,
                    "digest": view.digest,
                }
                atomic_write_json(request_log, log, prefix=".style-requests-")
        return {
            "path": view.path,
            "text": view.text,
            "digest": view.digest,
            "replayed": False,
        }

    @router.post("/{workspace_id}/story/revisions/{revision_id}/analyses")
    async def start_revision_analysis(
        workspace_id: str, revision_id: str, body: AnalysisStartRequest
    ) -> dict[str, Any]:
        """从对话发起一次影响分析；同一请求键返回同一条分析。"""
        if hub is None:
            raise WorkspaceError(
                code="MODEL_UNAVAILABLE",
                where="修订分析",
                why="没有可用模型",
                fix="先在设置中完成模型连接与选择",
            )
        workspace = workspaces.workspace(workspace_id)
        service = RevisionAnalysisService(
            state_root=conversation_root(workspaces.root, workspace_id),
            workspace=workspace,
            provider_factory=hub.provider_factory,
            library=hub.library,
        )
        analysis = await service.start(
            revision_id=revision_id,
            conversation_id=body.conversation_id,
            operation_id=body.operation_id,
            hunk_ids=body.hunk_ids,
            request_key=body.request_key,
        )
        return analysis.to_dict()

    @router.get("/{workspace_id}/story/revision-analyses/{analysis_id}")
    def read_revision_analysis(workspace_id: str, analysis_id: str) -> dict[str, Any]:
        """分析状态与判断；作者刷新后从原回执继续查看。"""
        workspace = workspaces.workspace(workspace_id)
        service = RevisionAnalysisService(
            state_root=conversation_root(workspaces.root, workspace_id),
            workspace=workspace,
            library=hub.library if hub is not None else None,
        )
        return service.read(analysis_id).to_dict()

    @router.post("/{workspace_id}/story/revision-analyses/{analysis_id}/applications")
    def apply_revision_analysis(
        workspace_id: str, analysis_id: str, body: AnalysisApplyRequest
    ) -> dict[str, Any]:
        """把作者确认的写作约定建议写回 `STYLE.md`。"""
        workspace = workspaces.workspace(workspace_id)
        state_root = conversation_root(workspaces.root, workspace_id)
        service = RevisionAnalysisService(
            state_root=state_root,
            workspace=workspace,
            library=hub.library if hub is not None else None,
        )
        analysis = service.read(analysis_id)
        if analysis.status != "ready":
            raise WorkspaceError(
                code="REVISION_ANALYSIS_NOT_READY",
                where=f"修订分析/{analysis_id}",
                why="这次分析还没有完成",
                fix="等待分析结束，或从原对话重新发起",
                details={"status": analysis.status},
            )
        chosen: set[int] = set()
        for index in body.decision_indexes:
            if 0 <= index < len(analysis.decisions):
                chosen.add(index)
        style_indexes = [index for index in chosen if analysis.decisions[index].target == "style"]
        receipts: list[dict[str, Any]] = []
        if not style_indexes:
            return {
                "analysis_id": analysis_id,
                "style": None,
                "decision_indexes": [],
                "message": "没有选择写入写作约定的建议",
            }
        previous = read_style_state(workspace)
        expected = body.expected_digest if body.expected_digest is not None else previous.digest
        view = write_style_document(workspace, text=body.text, expected_digest=expected)
        updated = replace(
            analysis,
            decisions=[
                replace(decision, applied=True) if index in style_indexes else decision
                for index, decision in enumerate(analysis.decisions)
            ],
        )
        RevisionAnalysisStore(state_root).save(updated)
        receipts.append(
            {
                "target": "style",
                "path": view.path,
                "digest_before": previous.digest,
                "digest_after": view.digest,
                "decision_indexes": style_indexes,
            }
        )
        return {
            "analysis_id": analysis_id,
            "style": {"path": view.path, "digest": view.digest, "text": view.text},
            "receipts": receipts,
            "decision_indexes": style_indexes,
        }

    @router.post("/{workspace_id}/story/revision-analyses/{analysis_id}/structure-applications")
    def apply_structure_analysis(
        workspace_id: str, analysis_id: str, body: SuggestionApplyRequest
    ) -> dict[str, Any]:
        """把作者勾选的结构建议写入 `STRUCTURE.md`；应用前再核对一次结构指纹。"""
        workspace = workspaces.workspace(workspace_id)
        state_root = conversation_root(workspaces.root, workspace_id)
        result = apply_structure_decisions(
            RevisionAnalysisStore(state_root),
            workspace,
            analysis_id=analysis_id,
            decision_indexes=body.decision_indexes,
            expected_digest=body.expected_digest,
        )
        return {
            "analysis": result.analysis.to_dict(),
            "structure": {
                "path": result.view.path,
                "digest": result.view.digest,
                "text": result.view.text,
            },
            "decision_indexes": result.applied_indexes,
        }

    @router.post("/{workspace_id}/story/revision-analyses/{analysis_id}/module-applications")
    def apply_module_analysis(
        workspace_id: str, analysis_id: str, body: SuggestionApplyRequest
    ) -> dict[str, Any]:
        """把作者勾选的模块条目建议写成作品专用版本；不改模块库头版本。"""
        if hub is None or hub.library is None:
            raise WorkspaceError(
                code="MODULE_CHANGE_TARGET_INVALID",
                where="修订分析",
                why="当前会话没有可用的模块库",
                fix="在设置中检查模块库位置后重试",
            )
        workspace = workspaces.workspace(workspace_id)
        state_root = conversation_root(workspaces.root, workspace_id)
        result = apply_module_decisions(
            RevisionAnalysisStore(state_root),
            workspace,
            hub.library,
            analysis_id=analysis_id,
            decision_indexes=body.decision_indexes,
            expected_digest=body.expected_digest,
        )
        return {
            "analysis": result.analysis.to_dict(),
            "change_set_id": result.change_set_id,
            "assembly": result.assembly,
            "decision_indexes": result.applied_indexes,
        }

    return router


def _read_request_log(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}
