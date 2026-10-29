"""构思结构的作用品接口（1.0.1 P5）。

界面需要的读取与确定性写入都在这里：方案列表、图数据、节点详情、变更提交、备选创建、
差异查看、采用、改动回看与撤销。这一层不调用模型——「点比较」不会偷偷生成，「建备选」只
复制确定的基线；采用只做 ``exact``（按选中范围复制结构），需要模型改写时明确拒绝。

写入请求都带 ``expected_revision`` 与 ``request_key``：修订不符返回 409，同键同负载返回
同一收据。响应里的 ID 与修订直接来自状态文件，不由界面拼。
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.state import RecentWorkspaces, conversation_root
from hastings.workspace.story_adoption import adoption_plan
from hastings.workspace.story_models import StoryChangeSet, StoryNode, StoryVariant
from hastings.workspace.story_store import StoryStore

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


class StoryChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant_id: str = Field(min_length=1, max_length=64)
    expected_revision: str = Field(min_length=1, max_length=64)
    request_key: str = Field(min_length=1, max_length=120)
    operations: list[dict[str, Any]] = Field(min_length=1)
    note: str = Field(default="", max_length=500)


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
    """采用结果：先给计划（复制了什么、哪些冲突），再给提交收据。"""

    plan: AdoptionPlanView
    receipt: StoryReceiptView | None = None


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


def _plan_view(plan: Any) -> AdoptionPlanView:
    summary = plan.summary()
    return AdoptionPlanView(
        **summary,
        note="只复制结构；正文关联仍指向原方案的正文副本，需要单独绑定。",
    )


def _changed_ids(store: StoryStore, change_id: str) -> list[str]:
    snapshot = store.read()
    if snapshot.document is None or not change_id:
        return []
    record = snapshot.document.changes.get(change_id)
    return sorted(record.touched) if record is not None else []


def create_story_router(workspaces: RecentWorkspaces) -> APIRouter:
    router = APIRouter(prefix="/api/file-workspaces", tags=["story"])

    def store_for(workspace_id: str) -> StoryStore:
        workspaces.get(workspace_id)
        return StoryStore(conversation_root(workspaces.root, workspace_id))

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
        variant = require_variant(store_for(workspace_id), variant_id)
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
            id=f"ui_{body.request_key[:24]}",
            base_revision=body.expected_revision,
            operations=body.operations,  # type: ignore[arg-type]
            requested_scope=body.note,
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
    def adopt(workspace_id: str, body: AdoptionRequest) -> AdoptionResultView:
        """采用备选：整案或选定片段；只复制结构，冲突时不静默覆盖。

        没有可复制的对象时不写任何变更，返回空收据；冲突返回 409 与逐项原因。
        ``assist`` 需要模型按依赖改写，这一版没有接通，明确拒绝而不是假装完成。
        """
        store = store_for(workspace_id)
        source = require_variant(store, body.source_variant_id)
        target = require_variant(store, body.target_variant_id)
        # 源方案必须仍是作者看到的那个；目标修订交给提交时的查重与修订检查：
        # 同键重试会先命中收据，不会因为上一轮已经改过目标而报冲突。
        if source.revision != body.source_revision:
            raise WorkspaceError(
                code="STORY_REVISION_CONFLICT",
                where="story.adoption",
                why="采用时方案已经变化",
                fix="刷新构思后重新确认要采用的内容",
                details={"source_revision": source.revision},
            )
        if body.mode == "assist":
            raise WorkspaceError(
                code="STORY_ADOPTION_UNAVAILABLE",
                where="story.adoption",
                why="需要模型按依赖改写正文的采用还没有接通",
                fix="先用局部采用复制确定的结构；要改写正文请在对话里说明要求",
            )
        plan = adoption_plan(
            source, target, node_ids=tuple(body.node_ids), replace=body.replace
        )
        view = _plan_view(plan)
        if plan.conflicts:
            raise WorkspaceError(
                code="STORY_ADOPTION_CONFLICT",
                where="story.adoption",
                why=f"目标方案里已有 {len(plan.conflicts)} 个同 ID 但内容不同的对象",
                fix="确认要覆盖时勾选覆盖重试，或先改名再采用",
                details=view.model_dump(mode="json"),
            )
        if not plan.operations:
            # 同键重试：这一轮之前已经应用过，返回当时的收据而不是「什么都没做」。
            document = store.read().document
            prior = document.requests.get(body.request_key) if document else None
            if prior is not None and prior.variant_id == body.target_variant_id:
                return AdoptionResultView(
                    plan=view,
                    receipt=_receipt_view(prior, _changed_ids(store, prior.change_id)),
                )
            return AdoptionResultView(plan=view)
        change = StoryChangeSet(
            id=f"adopt_{body.request_key[:24]}",
            base_revision=body.target_revision,
            operations=list(plan.operations),  # type: ignore[arg-type]
            requested_scope=f"采用 {source.name}"[:500],
        )
        receipt = store.commit_change(
            variant_id=target.id, change=change, request_key=body.request_key
        )
        return AdoptionResultView(
            plan=view, receipt=_receipt_view(receipt, _changed_ids(store, receipt.change_id))
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
            changed=sorted(record.touched),
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
