"""构思结构的作用品接口（1.0.1 P5）。

仅保留当前构思、节点详情、结构变更、改动回看与撤销；备选、比较、采用和候选正文浏览接口已移除。

写入请求都带 ``expected_revision`` 与 ``request_key``：修订不符返回 409，同键同负载返回
同一收据。响应里的 ID 与修订直接来自状态文件，不由界面拼。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.module_assembly import AssemblyStore, ModuleRef
from hastings.workspace.service_hub import WorkspaceServices
from hastings.workspace.state import ConversationStore, RecentWorkspaces, conversation_root
from hastings.workspace.story_clue_catalog import StoryClueCatalog
from hastings.workspace.story_clue_commit import prepare_clue_joint, prepare_clue_undo
from hastings.workspace.story_constraint_sources import ConstraintSources, source_key
from hastings.workspace.story_drafts import StoryDraftStore, candidate_data
from hastings.workspace.story_models import (
    Constraint,
    ProsePatch,
    ReadDependency,
    StoryChangeSet,
    StoryNode,
    StoryVariant,
)
from hastings.workspace.story_store import StoryStore, new_id

__all__ = ["create_story_router"]


class ConfirmDraftConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_revision: str
    variant_id: str = "main"


class StoryCountsView(BaseModel):
    stages: int = 0
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
    change: str | None = None
    stage_id: str | None = None
    thread_ids: list[str]
    clue_ids: list[str]
    primary_thread_id: str | None = None
    is_convergence: bool = False
    reveal_order: int | None = None
    time_note: str


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
    stages: list[dict[str, Any]]
    nodes: list[StoryNodeView]
    edges: list[StoryEdgeView]
    threads: list[dict[str, Any]]
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


class StoryChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant_id: str = Field(min_length=1, max_length=64)
    expected_revision: str = Field(min_length=1, max_length=64)
    expected_ledger_revision: str = ""
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


def _counts(variant: StoryVariant) -> StoryCountsView:
    return StoryCountsView(
        stages=len(variant.stages),
        nodes=len(variant.nodes),
        clues=len(variant.clue_plans),
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
        change=node.change,
        stage_id=node.stage_id,
        primary_thread_id=node.primary_thread_id,
        is_convergence=node.is_convergence,
        reveal_order=node.reveal_order,
        thread_ids=[item for item in node.thread_ids if item in variant.threads],
        clue_ids=[use.clue_id for use in node.clue_uses],
        time_note=node.time_note,
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
        if variant is None or (
            snapshot.document is not None and variant_id != snapshot.document.primary_variant_id
        ):
            raise WorkspaceError(
                code="STORY_VARIANT_NOT_FOUND",
                where="story.api",
                why=f"没有这个方案：{variant_id}",
                fix="刷新构思后按当前方案重试",
                details={"variant_id": variant_id},
            )
        return variant

    def task_draft(workspace_id: str, operation_id: str, variant_id: str) -> dict[str, Any] | None:
        workspaces.get(workspace_id)
        if hub is None:
            return None
        operation = hub.service_for(workspace_id).operations.read(operation_id)
        if operation.workspace_id != workspace_id:
            raise WorkspaceError(
                code="STORY_DRAFT_NOT_FOUND",
                where="story.draft",
                why="本作品没有这份任务草稿",
                fix="重新打开当前作品的任务",
            )
        return StoryDraftStore(
            conversation_root(workspaces.root, workspace_id), operation_id, variant_id
        ).read()

    @router.get("/{workspace_id}/story/drafts/{operation_id}")
    def read_draft(
        workspace_id: str, operation_id: str, variant_id: str = "main"
    ) -> dict[str, Any]:
        draft = task_draft(workspace_id, operation_id, variant_id)
        if not draft or draft.get("committed"):
            return {"draft": None}
        snapshot = store_for(workspace_id).read()
        variant = (
            snapshot.variant(variant_id)
            if snapshot.document
            else StoryVariant(id=variant_id, name="构思", revision="empty")
        )
        assert variant is not None
        data = candidate_data(
            variant,
            StoryChangeSet(
                id=draft["change_id"],
                base_revision=draft["base_revision"],
                operations=draft["operations"],
            ),
        )
        constraints = [row for row in data["constraints"].values() if row["strength"] == "required"]
        return {
            "draft": {
                "revision": draft["digest"],
                "constraints": constraints,
                "operations": len(draft["operations"]),
            }
        }

    @router.post("/{workspace_id}/story/drafts/{operation_id}/confirm-constraints")
    def confirm_draft_constraints(
        workspace_id: str, operation_id: str, body: ConfirmDraftConstraints
    ) -> dict[str, Any]:
        draft = task_draft(workspace_id, operation_id, body.variant_id)
        if not draft or draft.get("committed") or draft["digest"] != body.draft_revision:
            raise WorkspaceError(
                code="STORY_DRAFT_CONFLICT",
                where="story.draft",
                why="待确认草稿已变化",
                fix="刷新列表后核对当前约定",
            )
        assert hub is not None
        operation = hub.service_for(workspace_id).operations.read(operation_id)
        if operation.status not in ("paused", "awaiting_answer", "needs_attention"):
            raise WorkspaceError(
                code="STORY_DRAFT_CONFLICT",
                where="story.draft",
                why="任务尚未停下供作者核对",
                fix="等待任务需要处理后再确认",
            )
        root = conversation_root(workspaces.root, workspace_id)
        store = store_for(workspace_id)
        snapshot = store.read()
        variant = snapshot.variant(body.variant_id) if snapshot.document else None
        revision = variant.revision if variant else "empty"
        if revision != draft["base_revision"]:
            raise WorkspaceError(
                code="STORY_DRAFT_CONFLICT",
                where="story.draft",
                why="正式结构已变化",
                fix="重新核对当前结构，不能确认过期草稿",
            )
        current = variant or StoryVariant(id=body.variant_id, name="构思", revision="empty")
        data = candidate_data(
            current,
            StoryChangeSet(
                id=draft["change_id"],
                base_revision=draft["base_revision"],
                operations=draft["operations"],
            ),
        )
        constraints = [
            Constraint.model_validate(row)
            for row in data["constraints"].values()
            if row["strength"] == "required"
        ]
        modules = tuple(
            binding.module
            for binding in AssemblyStore(workspaces.workspace(workspace_id).root).read().bindings
        )
        frozen_modules = tuple(ModuleRef(**row) for row in draft.get("module_refs", []))
        if any(m not in modules for m in frozen_modules):
            raise WorkspaceError(
                code="STORY_SOURCE_CONFLICT",
                where="story.draft",
                why="草稿采用的模块版本已不在当前装配中",
                fix="保留草稿，核对模块变化后重新发起任务",
            )
        # 模块来源仍校验原文；无来源条目由作者逐项审阅当前草稿后明确采用。
        ConstraintSources(root).confirm(
            [c for c in constraints if c.source_ref is not None], hub.library, frozen_modules
        )
        StoryDraftStore(root, operation_id, body.variant_id).confirm_constraints(
            draft["digest"], [source_key(c) for c in constraints]
        )
        return {"confirmed": len(constraints), "draft_revision": draft["digest"]}

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
                if variant.id == document.primary_variant_id
            ],
        )

    @router.get("/{workspace_id}/story/variants/{variant_id}", response_model=StoryGraphView)
    def read_variant(
        workspace_id: str,
        variant_id: str,
    ) -> StoryGraphView:
        """图数据：节点与连接；阶段折叠由前端完成，读接口返回完整结构。"""
        store = store_for(workspace_id)
        variant = require_variant(store, variant_id)
        document = store.read().document
        latest_change = (
            next(
                (
                    record.change_id
                    for record in document.changes.values()
                    if record.variant_id == variant.id
                    and record.revision == variant.revision
                    and record.undone_by is None
                ),
                None,
            )
            if document
            else None
        )
        nodes = list(variant.nodes.values())
        shown = {node.id for node in nodes}
        return StoryGraphView(
            latest_change_id=latest_change,
            variant_id=variant.id,
            name=variant.name,
            revision=variant.revision,
            stages=[
                {
                    "id": stage.id,
                    "title": stage.title,
                    "order": stage.order,
                    "summary": stage.summary,
                }
                for stage in sorted(variant.stages.values(), key=lambda item: (item.order, item.id))
            ],
            nodes=[_node_view(variant, node) for node in sorted(nodes, key=lambda item: item.id)],
            edges=[
                _edge_view(edge)
                for edge in variant.edges.values()
                if edge.from_id in shown and edge.to_id in shown
            ],
            threads=[
                {
                    "id": thread.id,
                    "title": thread.title,
                    "order": thread.order,
                    "goal": thread.goal,
                    "obstacle": thread.obstacle,
                    "intended_resolution": thread.intended_resolution,
                }
                for thread in sorted(
                    variant.threads.values(), key=lambda item: (item.order, item.id)
                )
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
            collapsed=[],
        )

    @router.get("/{workspace_id}/story/nodes/{node_id}", response_model=StoryNodeDetailView)
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
        catalog = StoryClueCatalog.from_root(
            variant, conversation_root(workspaces.root, workspace_id)
        )
        uses: list[StoryClueUseView] = []
        for use in node.clue_uses:
            fact = catalog.get(use.clue_id)
            plan = fact.plan if fact is not None else None
            uses.append(
                StoryClueUseView(
                    clue_id=use.clue_id,
                    title=fact.title if fact is not None else use.clue_id,
                    author_truth=plan.author_truth if plan is not None else None,
                    intention=plan.intention if plan is not None else "",
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
            incoming=[_edge_view(edge) for edge in variant.edges.values() if edge.to_id == node.id],
            outgoing=[
                _edge_view(edge) for edge in variant.edges.values() if edge.from_id == node.id
            ],
        )

    @router.post("/{workspace_id}/story/changes", response_model=StoryReceiptView, status_code=201)
    def submit_change(workspace_id: str, body: StoryChangeRequest) -> StoryReceiptView:
        """界面发起的确定性结构变更；不接受伪造的授权字段。"""
        store = store_for(workspace_id)
        if store.read().document is not None:
            require_variant(store, body.variant_id)
        change = StoryChangeSet(
            id=new_id("ui"),
            base_revision=body.expected_revision,
            operations=body.operations,  # type: ignore[arg-type]
            requested_scope=body.note,
            prose_patches=body.prose_patches,
            read_dependencies=body.read_dependencies,
        )
        receipt = store.commit_change(
            variant_id=body.variant_id,
            change=change,
            request_key=body.request_key,
            prepare_joint=prepare_clue_joint(
                conversation_root(workspaces.root, workspace_id),
                variant_id=body.variant_id,
                change=change,
                expected_ledger_revision=body.expected_ledger_revision,
            ),
        )
        return _receipt_view(receipt, _changed_ids(store, receipt.change_id))

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

    return router
