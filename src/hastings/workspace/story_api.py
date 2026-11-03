"""构思结构的作用品接口（1.0.1 P5）。

仅保留当前构思、节点详情、结构变更、改动回看与撤销；备选、比较、采用和候选正文浏览接口已移除。

写入请求都带 ``expected_revision`` 与 ``request_key``：修订不符返回 409，同键同负载返回
同一收据。响应里的 ID 与修订直接来自状态文件，不由界面拼。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from hastings.workspace.book_startup import confirm_startup_book_intent
from hastings.workspace.chapter_writing import save_chapter_prose
from hastings.workspace.creative_candidates import CreativeCandidateStore
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.index_confirmation import confirm_index_candidate
from hastings.workspace.service_hub import WorkspaceServices
from hastings.workspace.state import ConversationStore, RecentWorkspaces, conversation_root
from hastings.workspace.story_clue_commit import prepare_clue_undo
from hastings.workspace.story_index_jobs import IndexAnalysisService, IndexJobStore
from hastings.workspace.story_store import MAIN_VARIANT_ID, StoryStore
from hastings.workspace.structure_document import BUDGET_LIMIT, check_budget
from hastings.workspace.structure_io import read_structure_state
from hastings.workspace.writing_actions import create_chapter, save_book_intent
from hastings.workspace.writing_api_models import (
    BookIntentRequest,
    ChapterCreateRequest,
    IndexConfirmRequest,
    ProseSaveRequest,
    StartupConfirmRequest,
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
        """当前写作格式的存储入口；新作品第一次提交会写入格式 4。"""
        return store_for(workspace_id, format_version=4)

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
                await snapshot.aclose()
                support = ModelSupportView(
                    available=True,
                    disabled_reason=None,
                    supported_assists=[
                        "book_intent",
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

    @router.put("/{workspace_id}/story/book-intent")
    def put_book_intent(workspace_id: str, body: BookIntentRequest) -> dict[str, Any]:
        store = writing_store_for(workspace_id)
        receipt = save_book_intent(
            store,
            text=body.text,
            expected_plan_revision=body.expected_plan_revision,
            request_key=body.request_key,
        )
        variant = store.require_variant(MAIN_VARIANT_ID)
        return {
            "plan_revision": variant.revision,
            "change_id": receipt.change_id,
        }

    @router.post("/{workspace_id}/story/startup/confirm-intent", status_code=201)
    def post_startup_confirm(workspace_id: str, body: StartupConfirmRequest) -> dict[str, Any]:
        """起步设定卡确认（v1.1 M1）：一次性门槛，重复确认拒绝；可整体撤销。"""
        store = writing_store_for(workspace_id)
        result = confirm_startup_book_intent(
            store, text=body.text, request_key=body.request_key
        )
        return {
            "change_id": result.change_id,
            "plan_revision": result.plan_revision,
        }

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

    @router.post("/{workspace_id}/story/chapters", status_code=201)
    def post_chapter(workspace_id: str, body: ChapterCreateRequest) -> dict[str, Any]:
        store = writing_store_for(workspace_id)
        workspace = workspaces.workspace(workspace_id)
        result = create_chapter(
            store,
            workspace,
            title=body.title,
            after_chapter_id=body.after_chapter_id,
            expected_plan_revision=body.expected_plan_revision,
            request_key=body.request_key,
        )
        return {
            "chapter_id": result.chapter_id,
            "order": result.order,
            "path": result.path,
            "prose_digest": result.prose_digest,
            "plan_revision": result.plan_revision,
            "change_id": result.receipt.change_id,
        }

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

    return router
