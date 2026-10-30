"""构思结构的作用品接口（1.0.1 P5）。

界面需要的读取与确定性写入都在这里：方案列表、图数据、节点详情、变更提交、备选创建、
差异查看、采用、改动回看与撤销。这一层不调用模型——「点比较」不会偷偷生成，「建备选」只
复制确定的基线；采用只做 ``exact``（按选中范围复制结构），需要模型改写时明确拒绝。

写入请求都带 ``expected_revision`` 与 ``request_key``：修订不符返回 409，同键同负载返回
同一收据。响应里的 ID 与修订直接来自状态文件，不由界面拼。
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Literal, cast

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from hastings.prompts.story import (
    ADOPTION_NOTHING_COPIED,
    adoption_instruction,
    adoption_pulled_text,
    adoption_scope,
)
from hastings.workspace.api import FileViewResponse
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.materials import ContextRequest
from hastings.workspace.service_hub import WorkspaceServices
from hastings.workspace.state import ConversationStore, RecentWorkspaces, conversation_root
from hastings.workspace.story_context import StoryRequest
from hastings.workspace.story_models import (
    ProsePatch,
    ReadDependency,
    StoryChangeSet,
    StoryNode,
    StoryVariant,
)
from hastings.workspace.story_store import StoryStore, new_id

if TYPE_CHECKING:
    from hastings.workspace.service import TurnService

__all__ = ["create_story_router"]


class StoryCountsView(BaseModel):
    nodes: int = 0
    clues: int = 0
    threads: int = 0
    constraints: int = 0
    edges: int = 0
    bindings: int = 0


class StoryVariantSummaryView(BaseModel):
    id: str
    name: str
    revision: str
    primary: bool
    base_variant_id: str | None = None
    base_revision: str | None = None
    counts: StoryCountsView


class StoryVariantListView(BaseModel):
    revision: str
    primary_variant_id: str
    variants: list[StoryVariantSummaryView]


class StoryNodeView(BaseModel):
    id: str
    title: str
    action: str
    threads: list[str]
    clues: list[str]
    time_note: str
    group_id: str | None = None


class StoryEdgeView(BaseModel):
    id: str
    from_id: str
    to_id: str
    kind: str
    reason: str


class StoryGraphView(BaseModel):
    latest_change_id: str | None = None
    variant_id: str
    name: str
    revision: str
    nodes: list[StoryNodeView]
    edges: list[StoryEdgeView]
    threads: list[dict[str, str]]
    event_times: list[dict[str, str]]
    constraints: list[dict[str, Any]]
    collapsed: list[str] = Field(default_factory=list)


class StoryClueUseView(BaseModel):
    clue_id: str
    title: str
    author_truth: str | None = None
    intention: str
    presentation: str
    mode: str
    speaker_id: str
    expected_interpretation: str


class StoryNodeDetailView(BaseModel):
    variant_id: str
    revision: str
    node: StoryNodeView
    clue_uses: list[StoryClueUseView]
    knowledge_changes: list[dict[str, Any]]
    bindings: list[dict[str, Any]]
    incoming: list[StoryEdgeView]
    outgoing: list[StoryEdgeView]


class StoryProseView(FileViewResponse):
    """备选方案的正文：与普通文件相同，另带方案标识与「是否沿用主方案」。"""

    variant_id: str
    inherited: bool


class StoryChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant_id: str = Field(min_length=1, max_length=64)
    expected_revision: str = Field(min_length=1, max_length=64)
    request_key: str = Field(min_length=1, max_length=120)
    operations: list[dict[str, Any]] = Field(default_factory=list)
    prose_patches: list[ProsePatch] = Field(default_factory=list)
    read_dependencies: list[ReadDependency] = Field(default_factory=list)
    note: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def require_changes(self) -> StoryChangeRequest:
        if not self.operations and not self.prose_patches:
            raise ValueError("变更没有任何操作")
        return self


class StoryReceiptView(BaseModel):
    variant_id: str
    base_revision: str
    revision: str
    change_id: str = ""
    request_key: str
    result: dict[str, Any] = Field(default_factory=dict)
    changed: list[str] = Field(default_factory=list)


class CreateVariantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    source_variant_id: str = Field(min_length=1, max_length=64)
    source_revision: str = Field(min_length=1, max_length=64)
    request_key: str = Field(min_length=1, max_length=120)


class StoryComparisonView(BaseModel):
    base: str
    base_revision: str
    candidate: str
    candidate_revision: str
    added: dict[str, list[str]]
    removed: dict[str, list[str]]
    updated: dict[str, list[str]]
    note: str


class AdoptionRequest(BaseModel):
    """采用备选：明确是整案还是选定片段，以及确定性应用还是交给模型调整。"""

    model_config = ConfigDict(extra="forbid")

    source_variant_id: str = Field(min_length=1, max_length=64)
    source_revision: str = Field(min_length=1, max_length=64)
    target_variant_id: str = Field(min_length=1, max_length=64)
    target_revision: str = Field(min_length=1, max_length=64)
    #: 空表示整案；给出节点时只采用这些节点及其必需前提。
    node_ids: list[str] = Field(default_factory=list, max_length=200)
    mode: Literal["exact", "assist"] = "exact"
    #: 目标里已有同名对象且内容不同时，是否覆盖。默认不覆盖。
    replace: bool = False
    request_key: str = Field(min_length=1, max_length=120)
    #: ``assist`` 时写入哪个会话；留空表示新建一个会话承载这次调整。
    conversation_id: str | None = Field(default=None, max_length=64)


class AdoptionPlanView(BaseModel):
    source: str
    target: str
    node_ids: list[str] = Field(default_factory=list)
    copied: dict[str, list[str]] = Field(default_factory=dict)
    unchanged: list[str] = Field(default_factory=list)
    pulled_in: list[str] = Field(default_factory=list)
    skipped_edges: list[str] = Field(default_factory=list)
    conflicts: list[dict[str, str]] = Field(default_factory=list)
    operations: int = 0
    note: str = ""


class AdoptionResultView(BaseModel):
    """采用结果：先给计划（复制了什么、哪些冲突），再给提交收据。

    ``assist`` 还会给出承接正文调整的操作标识；界面按这个标识跟随流式与暂停。
    """

    plan: AdoptionPlanView
    receipt: StoryReceiptView | None = None
    operation_id: str = ""


class StoryChangeRecordView(BaseModel):
    change_id: str
    variant_id: str
    base_revision: str
    revision: str
    created_at: str
    operations: list[dict[str, Any]]
    changed: list[str]
    undone_by: str | None = None


def _counts(variant: StoryVariant) -> StoryCountsView:
    return StoryCountsView(
        nodes=len(variant.nodes),
        clues=len(variant.clues),
        threads=len(variant.threads),
        constraints=len(variant.constraints),
        edges=len(variant.edges),
        bindings=len(variant.bindings),
    )


def _node_view(variant: StoryVariant, node: StoryNode) -> StoryNodeView:
    return StoryNodeView(
        id=node.id,
        title=node.title,
        action=node.action,
        threads=[item for item in node.thread_ids if item in variant.threads],
        clues=[use.clue_id for use in node.clue_uses],
        time_note=node.time_note,
        group_id=node.group_id,
    )


def _edge_view(edge: Any) -> StoryEdgeView:
    return StoryEdgeView(
        id=edge.id, from_id=edge.from_id, to_id=edge.to_id, kind=edge.kind, reason=edge.reason
    )


def _receipt_view(receipt: Any, changed: list[str]) -> StoryReceiptView:
    return StoryReceiptView(
        variant_id=receipt.variant_id,
        base_revision=receipt.base_revision,
        revision=receipt.revision,
        change_id=receipt.change_id,
        request_key=receipt.request_key,
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


def _ids(items: object, limit: int = 20) -> str:
    listed = [str(item) for item in items] if isinstance(items, list) else []
    shown = "、".join(listed[:limit])
    return f"{shown}（共 {len(listed)} 个）" if len(listed) > limit else shown


def _assist_instruction(
    *,
    source_name: str,
    target_name: str,
    node_ids: list[str],
    summary: dict[str, Any],
) -> str:
    """写给模型的采用说明：结构已提交，只差正文按依赖调整。"""
    scope = adoption_scope(len(node_ids))
    copied = summary.get("copied") or {}
    parts = []
    for name, label in (
        ("nodes", "节点"), ("clues", "线索"), ("threads", "分支"), ("constraints", "约定")
    ):
        if isinstance(copied, dict) and copied.get(name):
            parts.append(f"{label} {_ids(copied.get(name))}")
    copied_text = "；".join(parts) if parts else ADOPTION_NOTHING_COPIED
    pulled = summary.get("pulled_in") or []
    pulled_text = adoption_pulled_text(_ids(pulled)) if pulled else ""
    return adoption_instruction(source_name, target_name, scope, copied_text, pulled_text)


def _operation_id_for_turn(hub: WorkspaceServices, turn_id: str) -> str:
    """同键重试返回原轮次时也要给出操作标识，界面才能继续跟随同一个任务。"""
    for operation in hub.operations.list() if hub.operations is not None else ():
        if operation.turn_id == turn_id:
            return operation.id
    return ""


def create_story_router(
    workspaces: RecentWorkspaces, hub: WorkspaceServices | None = None
) -> APIRouter:
    router = APIRouter(prefix="/api/file-workspaces", tags=["story"])

    def store_for(workspace_id: str) -> StoryStore:
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
        )

    def require_variant(store: StoryStore, variant_id: str) -> StoryVariant:
        snapshot = store.read()
        variant = snapshot.variant(variant_id)
        if variant is None:
            raise WorkspaceError(
                code="STORY_VARIANT_NOT_FOUND",
                where="story.api",
                why=f"没有这个方案：{variant_id}",
                fix="刷新构思后按当前方案重试",
                details={"variant_id": variant_id},
            )
        return variant

    @router.get("/{workspace_id}/story/variants", response_model=StoryVariantListView)
    def list_variants(workspace_id: str) -> StoryVariantListView:
        snapshot = store_for(workspace_id).read()
        document = snapshot.document
        if document is None:
            return StoryVariantListView(revision="", primary_variant_id="", variants=[])
        return StoryVariantListView(
            revision=document.revision,
            primary_variant_id=document.primary_variant_id,
            variants=[
                StoryVariantSummaryView(
                    id=variant.id,
                    name=variant.name,
                    revision=variant.revision,
                    primary=variant.id == document.primary_variant_id,
                    base_variant_id=variant.base_variant_id,
                    base_revision=variant.base_revision,
                    counts=_counts(variant),
                )
                for variant in document.variants.values()
            ],
        )

    @router.get(
        "/{workspace_id}/story/variants/{variant_id}", response_model=StoryGraphView
    )
    def read_variant(
        workspace_id: str,
        variant_id: str,
        group_id: str | None = Query(default=None),
    ) -> StoryGraphView:
        """图数据：节点与连接；指定 ``group_id`` 时只返回该片段，其余折叠。"""
        store = store_for(workspace_id)
        variant = require_variant(store, variant_id)
        document = store.read().document
        latest_change = next((record.change_id for record in document.changes.values()
            if record.variant_id == variant.id and record.revision == variant.revision
            and record.undone_by is None), None) if document else None
        nodes = [
            node
            for node in variant.nodes.values()
            if group_id is None or node.group_id == group_id
        ]
        shown = {node.id for node in nodes}
        collapsed = sorted(
            node.id
            for node in variant.nodes.values()
            if group_id is not None and node.id not in shown
        )
        return StoryGraphView(
            latest_change_id=latest_change,
            variant_id=variant.id,
            name=variant.name,
            revision=variant.revision,
            nodes=[
                _node_view(variant, node)
                for node in sorted(nodes, key=lambda item: item.id)
            ],
            edges=[
                _edge_view(edge)
                for edge in variant.edges.values()
                if edge.from_id in shown and edge.to_id in shown
            ],
            threads=[
                {"id": thread.id, "title": thread.title} for thread in variant.threads.values()
            ],
            event_times=[
                {"before": row.before_node_id, "after": row.after_node_id}
                for row in variant.event_times
            ],
            constraints=[
                {
                    "id": constraint.id,
                    "text": constraint.text,
                    "strength": constraint.strength,
                    "scope": {"kind": constraint.scope.kind, "target": constraint.scope.target_id},
                }
                for constraint in variant.constraints.values()
            ],
            collapsed=collapsed,
        )

    @router.get(
        "/{workspace_id}/story/variants/{variant_id}/prose",
        response_model=StoryProseView,
    )
    def read_variant_prose(
        workspace_id: str,
        variant_id: str,
        path: str = Query(min_length=1),
    ) -> StoryProseView:
        """方案实际可见的正文：主方案读正式文件，备选读它的受管副本。"""
        store = store_for(workspace_id)
        require_variant(store, variant_id)
        digest, text, inherited = store.prose_state(path, variant_id)
        if digest == "absent":
            raise WorkspaceError(
                code="STORY_PROSE_NOT_FOUND",
                where="story.prose",
                why=f"这个方案里没有这份正文：{path}",
                fix="确认文件仍存在，或切换方案后重新定位",
                details={"variant_id": variant_id, "path": path},
            )
        entry = workspaces.workspace(workspace_id).describe(path)
        return StoryProseView(
            variant_id=variant_id,
            inherited=inherited,
            path=path,
            kind=entry.kind,
            title=entry.title,
            display_title=entry.display_title,
            text=text,
            display_text=text,
            digest=digest,
            size=len(text.encode("utf-8")),
            chapter_number=entry.chapter_number,
            chapter_type=entry.chapter_type,
            roles=list(entry.roles),
            metadata_warnings=list(entry.metadata_warnings),
        )

    @router.get(
        "/{workspace_id}/story/nodes/{node_id}", response_model=StoryNodeDetailView
    )
    def read_node(
        workspace_id: str,
        node_id: str,
        variant_id: str = Query(min_length=1),
    ) -> StoryNodeDetailView:
        """节点详情：安排、线索的真实作用、知情与正文入口；就近展开用。"""
        variant = require_variant(store_for(workspace_id), variant_id)
        node = variant.nodes.get(node_id)
        if node is None:
            raise WorkspaceError(
                code="STORY_CONTEXT_INCOMPLETE",
                where="story.api",
                why=f"没有这个节点：{node_id}",
                fix="刷新构思后重新选择节点",
                details={"node_id": node_id},
            )
        uses: list[StoryClueUseView] = []
        for use in node.clue_uses:
            clue = variant.clues.get(use.clue_id)
            uses.append(
                StoryClueUseView(
                    clue_id=use.clue_id,
                    title=clue.title if clue is not None else use.clue_id,
                    author_truth=clue.author_truth if clue is not None else None,
                    intention=clue.intention if clue is not None else "",
                    presentation=use.presentation,
                    mode=use.mode,
                    speaker_id=use.speaker_id,
                    expected_interpretation=use.expected_interpretation,
                )
            )
        return StoryNodeDetailView(
            variant_id=variant.id,
            revision=variant.revision,
            node=_node_view(variant, node),
            clue_uses=uses,
            knowledge_changes=[change.model_dump(mode="json") for change in node.knowledge_changes],
            bindings=[
                {
                    "id": binding.id,
                    "path": binding.path,
                    "start": binding.start,
                    "end": binding.end,
                    "quote": binding.quote,
                    "observation": binding.observation,
                }
                for binding in variant.bindings.values()
                if binding.node_id == node.id
            ],
            incoming=[
                _edge_view(edge) for edge in variant.edges.values() if edge.to_id == node.id
            ],
            outgoing=[
                _edge_view(edge) for edge in variant.edges.values() if edge.from_id == node.id
            ],
        )

    @router.post(
        "/{workspace_id}/story/changes", response_model=StoryReceiptView, status_code=201
    )
    def submit_change(workspace_id: str, body: StoryChangeRequest) -> StoryReceiptView:
        """界面发起的确定性结构变更；不接受伪造的授权字段。"""
        store = store_for(workspace_id)
        change = StoryChangeSet(
            id=new_id("ui"),
            base_revision=body.expected_revision,
            operations=body.operations,  # type: ignore[arg-type]
            requested_scope=body.note,
            prose_patches=body.prose_patches,
            read_dependencies=body.read_dependencies,
        )
        receipt = store.commit_change(
            variant_id=body.variant_id, change=change, request_key=body.request_key
        )
        return _receipt_view(receipt, _changed_ids(store, receipt.change_id))

    @router.post(
        "/{workspace_id}/story/variants", response_model=StoryReceiptView, status_code=201
    )
    def create_variant(workspace_id: str, body: CreateVariantRequest) -> StoryReceiptView:
        """从确定基线复制一个备选；只复制结构，不带正文关联与核对。"""
        store = store_for(workspace_id)
        receipt = store.create_variant(
            name=body.name,
            source_variant_id=body.source_variant_id,
            source_revision=body.source_revision,
            request_key=body.request_key,
        )
        return _receipt_view(receipt, [])

    @router.post("/{workspace_id}/story/adoptions", response_model=AdoptionResultView)
    async def adopt(workspace_id: str, body: AdoptionRequest) -> AdoptionResultView:
        """采用备选：整案或选定片段；冲突时不静默覆盖。

        ``exact`` 只复制结构并返回收据。``assist`` 先完成同样的结构采用，再受理一次
        可见操作，由模型按依赖调整受影响正文；没有产生结构变化时不请求模型。
        """
        store = store_for(workspace_id)
        service: TurnService | None = None
        if body.mode == "assist":
            if hub is None:
                raise WorkspaceError(
                    code="STORY_ADOPTION_UNAVAILABLE",
                    where="story.adoption",
                    why="这个服务实例没有接入写作操作",
                    fix="在完整应用里使用采用；需要改写正文请在对话里说明要求",
                )
            service = hub.service_for(workspace_id)
            if service.active_turn() is not None:
                raise WorkspaceError(
                    code="WORKSPACE_BUSY_CONFLICT",
                    where="story.adoption",
                    why="这部作品还有一轮正在运行",
                    fix="等当前这轮结束或暂停它，再采用备选",
                    details={"id": workspace_id},
                )
        receipt = store.adopt(
            source_variant_id=body.source_variant_id,
            source_revision=body.source_revision,
            target_variant_id=body.target_variant_id,
            target_revision=body.target_revision,
            node_ids=tuple(body.node_ids),
            replace=body.replace,
            request_key=body.request_key,
            mode=body.mode,
        )
        raw_summary = receipt.result.get("adoption_plan")
        if not isinstance(raw_summary, dict):
            raise WorkspaceError(
                code="STORY_STATE_FAILED",
                where="story.adoption",
                why="采用收据缺少计划",
                fix="检查构思状态并从有效快照恢复",
            )
        summary = cast(dict[str, Any], raw_summary)
        view = AdoptionPlanView.model_validate({
            **summary,
            "note": "只复制结构；正文关联仍指向原方案的正文副本，需要单独绑定。",
        })
        operation_id = ""
        if body.mode == "assist" and int(summary.get("operations") or 0) > 0:
            assert service is not None
            source = require_variant(store, body.source_variant_id)
            target = require_variant(store, body.target_variant_id)
            text = _assist_instruction(
                source_name=source.name,
                target_name=target.name,
                node_ids=body.node_ids,
                summary=summary,
            )
            info = await service.accept(
                text,
                body.request_key,
                conversation_id=body.conversation_id,
                context=ContextRequest(
                    story=StoryRequest(
                        variant_id=target.id,
                        revision=target.revision,
                        node_ids=tuple(summary.get("node_ids") or ()),
                        purpose="revise",
                        structure_readonly=True,
                    )
                ),
            )
            operation_id = info.operation_id or (
                _operation_id_for_turn(hub, info.turn_id) if hub is not None else ""
            )
        elif body.mode == "assist":
            view.note = "采用没有产生结构变化；没有启动正文调整。"
        return AdoptionResultView(
            plan=view,
            receipt=_receipt_view(receipt, _changed_ids(store, receipt.change_id)),
            operation_id=operation_id,
        )

    @router.get("/{workspace_id}/story/comparison", response_model=StoryComparisonView)
    def compare(
        workspace_id: str,
        base: str = Query(min_length=1),
        candidate: str = Query(min_length=1),
    ) -> StoryComparisonView:
        """两个方案的差异：只比较对象是否存在与内容是否一致，不宣称语义完整。"""
        store = store_for(workspace_id)
        source = require_variant(store, base)
        target = require_variant(store, candidate)
        added: dict[str, list[str]] = {}
        removed: dict[str, list[str]] = {}
        updated: dict[str, list[str]] = {}
        for name, left, right in (
            ("nodes", source.nodes, target.nodes),
            ("clues", source.clues, target.clues),
            ("threads", source.threads, target.threads),
            ("constraints", source.constraints, target.constraints),
            ("edges", source.edges, target.edges),
            ("bindings", source.bindings, target.bindings),
        ):
            added[name] = sorted(set(right) - set(left))
            removed[name] = sorted(set(left) - set(right))
            updated[name] = sorted(
                key for key in set(left) & set(right) if left[key] != right[key]
            )
        return StoryComparisonView(
            base=source.id,
            base_revision=source.revision,
            candidate=target.id,
            candidate_revision=target.revision,
            added=added,
            removed=removed,
            updated=updated,
            note="只比较对象与内容；跨边界依赖与语义是否成立需要单独核对。",
        )

    def candidate_view(workspace_id: str, candidate_id: str) -> dict[str, Any]:
        store = store_for(workspace_id)
        snapshot = store.read()
        if not re.fullmatch(r"candidate_[a-f0-9]{32}", candidate_id):
            raise WorkspaceError(code="STORY_CANDIDATE_NOT_FOUND", where="story.candidate",
                                 why="候选标识无效", fix="重新打开候选列表")
        path = store.dir / "candidates" / f"{candidate_id}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("id") != candidate_id:
                raise ValueError("候选身份不一致")
        except FileNotFoundError as exc:
            raise WorkspaceError(code="STORY_CANDIDATE_NOT_FOUND", where="story.candidate",
                                 why="找不到这份候选", fix="刷新候选列表") from exc
        except (OSError, ValueError) as exc:
            raise WorkspaceError(code="STORY_STATE_FAILED", where="story.candidate",
                                 why="候选文件无法读取", fix="检查作品状态目录后重试") from exc
        status = raw.get("status", "draft")
        if snapshot.document and f"candidate:{candidate_id}" in snapshot.document.requests:
            status = "committed"
        return {key: raw.get(key) for key in ("id", "documents", "report", "repaired",
                    "operations", "operation_id", "generation", "author_text")} | {
                    "status": status}

    @router.get("/{workspace_id}/story/candidates")
    def list_candidates(workspace_id: str) -> list[dict[str, Any]]:
        store = store_for(workspace_id)
        paths = sorted((store.dir / "candidates").glob("candidate_*.json"),
                       key=lambda p: p.stat().st_mtime_ns, reverse=True)[:20]
        return [candidate_view(workspace_id, path.stem) for path in paths]

    @router.get("/{workspace_id}/story/candidates/{candidate_id}")
    def get_candidate(workspace_id: str, candidate_id: str) -> dict[str, Any]:
        return candidate_view(workspace_id, candidate_id)

    @router.get(
        "/{workspace_id}/story/changes/{change_id}", response_model=StoryChangeRecordView
    )
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
            changed=sorted([*record.touched,
                            *(f"prose:{p.path}" for p in record.inverse_prose_patches)]),
            undone_by=record.undone_by,
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
        receipt = store.undo(change_id=change_id, request_key=request_key)
        return _receipt_view(receipt, _changed_ids(store, receipt.change_id))

    return router
