"""图谱只读投影（V03-6.1 / M12）：把某 Commit 的 StoryCatalog 投影为节点与边。

图谱不是存储，也不建立第二份可写事实（I08/I09）：每个节点与边都必须能追回
账本 Artifact（``kind``/``id``/``revision``）；候选留在辅助表、不在正式快照里，
因此天然不进入本投影。缺失的正式连接只表示「未记录」，不在此推断（M12）。

四语义层（M12 合同 ``semanticLayer``）：

- ``truth``：故事世界中为真的设定与事实（人物、地点、关系、线索、真相、事件）；
- ``knowledge``：角色认知与证词（``story.knowledge``，含误信/怀疑，不升级为客观事实）；
- ``disclosure``：读者披露（``story.disclosure``）；
- ``plan``：尚未发生的叙事结构（``narrative.*``）。

边的来源分两类：条目自身引用字段（如线索「指向」事实），以及显式关系
（``snapshot.relations`` 中 add 的依赖边）。前者 ``declared_by`` 指向声明条目，
后者 ``declared_by`` 为空、来源是写入该边的提交。通用引用字段口径以
``canon/entries.py`` 的 ``LEDGER_REF_FIELDS`` 为准，两处不各维护一份字段清单。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from hastings.domain.repository.models import RELATION_ACTION_ADD, RelationRevision
from hastings.domain.repository.serialization import content_hash
from hastings.domain.story import error_codes as ec
from hastings.domain.story import kinds
from hastings.domain.story.canon.models import Clue
from hastings.domain.story.catalog import StoryCatalog
from hastings.domain.story.errors import violation
from hastings.domain.story.narrative.state import story_state_at_beat, story_state_final
from hastings.domain.story.views import position_map, reader_reveals

__all__ = [
    "CLUE_STATUS_IMPLEMENTED",
    "CLUE_STATUS_MISSING",
    "CLUE_STATUS_PLANNED",
    "EVIDENCE_NEEDS_CHECK",
    "EVIDENCE_NOT_RECORDED",
    "EVIDENCE_VALID",
    "GRAPH_NODE_KINDS",
    "LAYER_DISCLOSURE",
    "LAYER_KNOWLEDGE",
    "LAYER_PLAN",
    "LAYER_TRUTH",
    "PERSPECTIVE_AUTHOR",
    "PERSPECTIVE_CHARACTER",
    "SEMANTIC_LAYERS",
    "CharacterEdge",
    "CharacterGraph",
    "ClueGraph",
    "ClueState",
    "GraphEdge",
    "GraphNode",
    "GraphProjection",
    "GraphSourceRef",
    "character_projection",
    "clue_projection",
    "graph_projection",
    "semantic_layer",
]

# --- 语义层 ----------------------------------------------------------------
LAYER_TRUTH = "truth"
LAYER_KNOWLEDGE = "knowledge"
LAYER_DISCLOSURE = "disclosure"
LAYER_PLAN = "plan"
SEMANTIC_LAYERS: tuple[str, ...] = (LAYER_TRUTH, LAYER_KNOWLEDGE, LAYER_DISCLOSURE, LAYER_PLAN)

# --- 证据状态（与 M05 EvidenceLink 同一口径） -------------------------------
EVIDENCE_VALID = "valid"
EVIDENCE_NEEDS_CHECK = "needs_check"
EVIDENCE_NOT_RECORDED = "not_recorded"

#: 图谱节点覆盖的正式种类；作品约定（``story.agreement``）不是故事对象，不入图。
GRAPH_NODE_KINDS: frozenset[str] = frozenset(
    (*kinds.CANON_KINDS, *kinds.NARRATIVE_NODE_KINDS, kinds.KIND_BEAT)
)

_PLAN_KINDS: frozenset[str] = frozenset((*kinds.NARRATIVE_NODE_KINDS, kinds.KIND_BEAT))
_LAYER_BY_KIND: dict[str, str] = {
    kinds.KIND_KNOWLEDGE: LAYER_KNOWLEDGE,
    kinds.KIND_DISCLOSURE: LAYER_DISCLOSURE,
    **{kind: LAYER_PLAN for kind in _PLAN_KINDS},
}

#: 认知模式的中文标签；误信/怀疑保留原语义，不作为客观事实（M12 头部图）。
_KNOWLEDGE_LABELS: dict[str, str] = {
    "knows": "知道",
    "suspects": "怀疑",
    "misbelieves": "误信",
    "unaware": "不知道",
}

#: 单端引用字段：声明的条目自身 → 被引对象（kind → ((字段, 标签), ...)）。
_ENTRY_REF_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    kinds.KIND_CLUE: (("points_to", "指向"),),
    kinds.KIND_TRUTH_FACT: (("event_id", "对应事件"),),
    kinds.KIND_MYSTERY_TRUTH: (
        ("culprit_id", "凶手"),
        ("accomplice_ids", "协助者"),
        ("true_timeline", "真实时间线"),
    ),
    kinds.KIND_TIMELINE_EVENT: (("place_id", "地点"), ("participants", "参与")),
}

#: 两端字段：声明的条目连接两个被引对象（字段顺序：起点、终点、默认标签）。
_TWO_ENDED_FIELDS: dict[str, tuple[str, str, str]] = {
    kinds.KIND_RELATIONSHIP: ("source_id", "target_id", "关系"),
    kinds.KIND_KNOWLEDGE: ("character_id", "fact_id", "认知"),
}


def semantic_layer(kind: str) -> str:
    """节点/声明条目的语义层；未特别归类的正式设定按 ``truth``。"""
    return _LAYER_BY_KIND.get(kind, LAYER_TRUTH)


@dataclass(frozen=True, slots=True)
class GraphSourceRef:
    """节点或边的正式来源引用（可追回账本 Artifact 的版本）。"""

    kind: str
    id: str
    revision: str

    def to_payload(self) -> dict[str, str]:
        return {"kind": self.kind, "id": self.id, "revision": self.revision}


@dataclass(frozen=True, slots=True)
class GraphNode:
    """图谱节点：正式账本对象（含叙事结构），带语义层与证据状态。"""

    id: str
    kind: str
    revision: str
    semantic_layer: str
    evidence_status: str


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """图谱边：由条目引用字段或显式关系导出，永远指向快照内的节点。"""

    id: str
    source: str
    target: str
    label: str
    relation_type: str
    semantic_layer: str
    evidence_status: str
    declared_by: str | None
    source_refs: tuple[GraphSourceRef, ...]


@dataclass(frozen=True, slots=True)
class GraphProjection:
    """某 Commit 的统一图谱投影（只读，节点与边互不建立新事实）。"""

    commit_id: str | None
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]


def _as_ids(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item)]
    return []


def _edge_id(
    declared_by: str | None, relation_type: str, source: str, target: str, salt: str
) -> str:
    return "edge_" + content_hash([declared_by or "", relation_type, source, target, salt])[:16]


def _evidence_for(declared_by: str | None, target: str, evidence_status: Mapping[str, str]) -> str:
    """边的证据状态：优先取声明条目的核对结果，其次取目标节点，最后未记录。"""
    if declared_by is not None and declared_by in evidence_status:
        return evidence_status[declared_by]
    return evidence_status.get(target, EVIDENCE_NOT_RECORDED)


def _two_ended_label(kind: str, content: Mapping[str, object], default_label: str) -> str:
    if kind == kinds.KIND_RELATIONSHIP:
        relation_type = content.get("relation_type")
        return str(relation_type) if relation_type else default_label
    if kind == kinds.KIND_KNOWLEDGE:
        mode = str(content.get("mode", ""))
        return _KNOWLEDGE_LABELS.get(mode, mode or default_label)
    return default_label


def graph_projection(
    catalog: StoryCatalog, *, evidence_status: Mapping[str, str] | None = None
) -> GraphProjection:
    """把目录投影为节点与边；``evidence_status`` 为账本 Artifact ID → 证据状态。"""
    status = evidence_status or {}
    nodes: list[GraphNode] = []
    node_ids: set[str] = set()
    for entry in catalog.entries:
        if entry.kind not in GRAPH_NODE_KINDS:
            continue
        node_ids.add(entry.artifact_id)
        nodes.append(
            GraphNode(
                id=entry.artifact_id,
                kind=entry.kind,
                revision=entry.revision_id,
                semantic_layer=semantic_layer(entry.kind),
                evidence_status=status.get(entry.artifact_id, EVIDENCE_NOT_RECORDED),
            )
        )

    edges: list[GraphEdge] = []
    seen: set[str] = set()

    def add_edge(
        *,
        declared_by: str | None,
        source: str,
        target: str,
        label: str,
        relation_type: str,
        layer: str,
        refs: tuple[GraphSourceRef, ...],
        salt: str = "",
    ) -> None:
        if source not in node_ids or target not in node_ids:
            return  # 端点必须存在（不变量 11）；悬空引用不入图
        edge_id = _edge_id(declared_by, relation_type, source, target, salt)
        if edge_id in seen:
            return
        seen.add(edge_id)
        edges.append(
            GraphEdge(
                id=edge_id,
                source=source,
                target=target,
                label=label,
                relation_type=relation_type,
                semantic_layer=layer,
                evidence_status=_evidence_for(declared_by, target, status),
                declared_by=declared_by,
                source_refs=refs,
            )
        )

    for entry in catalog.entries:
        if entry.kind not in GRAPH_NODE_KINDS:
            continue
        layer = semantic_layer(entry.kind)
        ref = GraphSourceRef(kind=entry.kind, id=entry.artifact_id, revision=entry.revision_id)
        content = entry.content

        two = _TWO_ENDED_FIELDS.get(entry.kind)
        if two is not None:
            source_field, target_field, default_label = two
            source = str(content.get(source_field) or "")
            target = str(content.get(target_field) or "")
            add_edge(
                declared_by=entry.artifact_id,
                source=source,
                target=target,
                label=_two_ended_label(entry.kind, content, default_label),
                relation_type=entry.kind.removeprefix("story."),
                layer=layer,
                refs=(ref,),
            )

        for field, label in _ENTRY_REF_FIELDS.get(entry.kind, ()):
            for target in _as_ids(content.get(field)):
                add_edge(
                    declared_by=entry.artifact_id,
                    source=entry.artifact_id,
                    target=target,
                    label=label,
                    relation_type=field,
                    layer=layer,
                    refs=(ref,),
                )

    for relation in catalog.relations:
        if relation.action != RELATION_ACTION_ADD:
            continue
        add_edge(
            declared_by=None,
            source=relation.source.value,
            target=relation.target.value,
            label=relation.relation_type,
            relation_type=relation.relation_type,
            layer=_relation_layer(catalog, relation),
            refs=(),
            salt=relation.id,
        )

    nodes.sort(key=lambda node: node.id)
    edges.sort(key=lambda edge: edge.id)
    return GraphProjection(commit_id=catalog.commit_id, nodes=tuple(nodes), edges=tuple(edges))


def _relation_layer(catalog: StoryCatalog, relation: RelationRevision) -> str:
    source = catalog.get(relation.source.value)
    return semantic_layer(source.kind) if source is not None else LAYER_TRUTH


# --- 人物图（V03-6.2）：在统一投影上按视角与叙述位置筛选 ---------------------
#: 视角两种模式；``author`` 为作者全知，其余值按角色 ArtifactId 解析。
PERSPECTIVE_AUTHOR = "author"
PERSPECTIVE_CHARACTER = "character"

#: 人物图只保留这两类边；其余（线索「指向」等）留给线索图/手法图。
_RELATIONSHIP_REL_TYPE = kinds.KIND_RELATIONSHIP.removeprefix(kinds.CANON_PREFIX)
_KNOWLEDGE_REL_TYPE = kinds.KIND_KNOWLEDGE.removeprefix(kinds.CANON_PREFIX)


@dataclass(frozen=True, slots=True)
class CharacterEdge:
    """人物图边：统一投影边 + 关系状态；角色视角隐藏真实状态。"""

    edge: GraphEdge
    public_state: str = ""
    true_state: str = ""
    true_state_hidden: bool = False


@dataclass(frozen=True, slots=True)
class CharacterGraph:
    """人物图视图：统一投影的子集 + 视角/叙述位置声明（只读，不新建事实）。"""

    commit_id: str | None
    perspective_mode: str
    perspective_id: str | None
    at_beat_id: str | None
    nodes: tuple[GraphNode, ...]
    edges: tuple[CharacterEdge, ...]


def _character_edge_kind(edge: GraphEdge) -> str | None:
    """人物图关心的边种类；其余边不进入人物图。"""
    if edge.relation_type == _RELATIONSHIP_REL_TYPE:
        return kinds.KIND_RELATIONSHIP
    if edge.relation_type == _KNOWLEDGE_REL_TYPE:
        return kinds.KIND_KNOWLEDGE
    return None


def character_projection(
    catalog: StoryCatalog,
    *,
    perspective: str | None = None,
    at_beat_id: str | None = None,
    evidence_status: Mapping[str, str] | None = None,
) -> CharacterGraph:
    """人物图：在统一投影上筛选，不另建失效/位置/事实逻辑。

    - **视角**：``author`` 露全部关系边与认知边；角色视角只露该角色参与的
      关系边与其本人认知边，且关系边只露 ``public_state``（``true_state`` 置空
      并标记隐藏）——真关系与角色理解分开显示，不把认知当客观事实。
    - **叙述位置**：``at_beat_id`` 交给 ``story_state_at_beat`` 折叠，认知按获知
      事件生效、后序覆盖前序；关系是基础事实，任何位置都在（状态随位置变化）。
      「截至某章」由调用方先解析为该章末节拍，再走同一路径。
    - **未记录不变成不知道**：只在存在 ``story.knowledge`` 记录时才有认知边，
      缺失即缺口，绝不合成 ``unaware`` 边。
    """
    projection = graph_projection(catalog, evidence_status=evidence_status)

    character_ids = {entry.artifact_id for entry in catalog.by_kind(kinds.KIND_CHARACTER)}
    mode = PERSPECTIVE_AUTHOR
    perspective_id: str | None = None
    if perspective and perspective != PERSPECTIVE_AUTHOR:
        if perspective not in character_ids:
            raise violation(
                ec.STORY_UNKNOWN_ARTIFACT,
                "graphs.characters.perspective",
                f"视角角色 {perspective!r} 不在当前快照中",
                "改用快照内存在的人物 ID，或用 author 表示作者全知",
                character_id=perspective,
            )
        mode = PERSPECTIVE_CHARACTER
        perspective_id = perspective

    state = (
        story_state_at_beat(catalog, at_beat_id, after=True)
        if at_beat_id is not None
        else story_state_final(catalog)
    )
    relationships = {(r.source_id, r.target_id): r for r in state.relationships}
    knowledge = {(k.character_id, k.fact_id) for k in state.knowledge}

    edges: list[CharacterEdge] = []
    visible: set[str] = {perspective_id} if perspective_id is not None else set()
    for edge in projection.edges:
        kind = _character_edge_kind(edge)
        if kind is None:
            continue
        if kind == kinds.KIND_RELATIONSHIP:
            relation = relationships.get((edge.source, edge.target))
            if relation is None:
                continue
            if perspective_id is not None and perspective_id not in (edge.source, edge.target):
                continue
            hidden = perspective_id is not None
            edges.append(
                CharacterEdge(
                    edge=edge,
                    public_state=relation.public_state,
                    true_state="" if hidden else relation.true_state,
                    true_state_hidden=hidden,
                )
            )
        else:
            if (edge.source, edge.target) not in knowledge:
                continue
            if perspective_id is not None and edge.source != perspective_id:
                continue
            edges.append(CharacterEdge(edge=edge))
        visible.add(edge.source)
        visible.add(edge.target)

    nodes = (
        tuple(node for node in projection.nodes if node.id in visible)
        if mode == PERSPECTIVE_CHARACTER
        else projection.nodes
    )
    return CharacterGraph(
        commit_id=projection.commit_id,
        perspective_mode=mode,
        perspective_id=perspective_id,
        at_beat_id=at_beat_id,
        nodes=nodes,
        edges=tuple(edges),
    )


# --- 线索图（V03-6.3）：在统一投影上筛出线索子图 + 计划/实现分离 ---------------
CLUE_STATUS_IMPLEMENTED = "implemented"  # 指向的事实已向读者披露（已在正文出现）
CLUE_STATUS_PLANNED = "planned"  # 有计划的出现位置，尚未向读者披露
CLUE_STATUS_MISSING = "missing"  # 无出现记录：合法缺口（可以是故意悬念），不判错


@dataclass(frozen=True, slots=True)
class ClueState:
    """一条线索在当前视图下的出现情况；计划与实现分开报出。"""

    clue_id: str
    statement: str
    necessity: bool
    points_to: tuple[str, ...]
    planned_beats: tuple[str, ...]
    disclosed_beats: tuple[str, ...]
    status: str
    first_position: int | None
    within_position: bool | None


@dataclass(frozen=True, slots=True)
class ClueGraph:
    """线索图视图：统一投影中与线索相连的子图 + 每条线索的出现状态。"""

    commit_id: str | None
    at_beat_id: str | None
    position_index: int | None
    clues: tuple[ClueState, ...]
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]


def clue_projection(
    catalog: StoryCatalog,
    *,
    at_beat_id: str | None = None,
    focus: str | None = None,
    evidence_status: Mapping[str, str] | None = None,
) -> ClueGraph:
    """线索图：以线索为中心的投影子图，附计划/实现与叙述位置筛选。

    - **边**：只保留统一投影中与线索相连的边（线索自身的「指向」，以及与线索
      相连的显式关系）。没有正式记录的连接不出现——误导解释、反驳等没有账本
      依据时不凭措辞连线，缺口如实留白。
    - **计划/实现分离**：计划出现取 ``Clue.disclosure_plan`` 落在结构内的节拍，
      已在正文出现取 ``points_to`` 所指事实的读者披露位置（``reader_reveals``）；
      二者不合并成一个「已实现」状态，也不因原文变化另建失效逻辑（证据状态仍
      由 M05 决定）。
    - **叙述位置**：``at_beat_id`` 给出截至位置，线索最早出现位置晚于它即标记
      ``within_position=False``，后文线索不提前进入视图；无位置记录则留 ``None``。
    """
    projection = graph_projection(catalog, evidence_status=evidence_status)
    positions = position_map(catalog)
    if at_beat_id is not None and at_beat_id not in positions:
        raise violation(
            ec.STORY_UNKNOWN_ARTIFACT,
            "graphs.clues.position",
            f"节拍 {at_beat_id!r} 不在结构顺序中",
            "改用结构中存在的 Beat ID（先用结构树查询可用节拍）",
            beat_id=at_beat_id,
        )

    clue_entries = catalog.by_kind(kinds.KIND_CLUE)
    clue_ids = {entry.artifact_id for entry in clue_entries}
    reveals = reader_reveals(catalog)

    node_ids: set[str] = set(clue_ids)
    edges: list[GraphEdge] = []
    for edge in projection.edges:
        if edge.source in clue_ids or edge.target in clue_ids:
            edges.append(edge)
            node_ids.add(edge.source)
            node_ids.add(edge.target)

    cutoff = positions.get(at_beat_id) if at_beat_id is not None else None
    states: list[ClueState] = []
    for entry in sorted(clue_entries, key=lambda item: item.artifact_id):
        clue = Clue.from_content(entry.content)
        planned = tuple(
            sorted(
                (beat for beat in clue.disclosure_plan if beat in positions),
                key=positions.__getitem__,
            )
        )
        disclosed = tuple(
            sorted(
                {
                    beat
                    for fact_id in clue.points_to
                    for beat in reveals.get(fact_id, ())
                    if beat in positions
                },
                key=positions.__getitem__,
            )
        )
        if disclosed:
            status = CLUE_STATUS_IMPLEMENTED
        elif planned:
            status = CLUE_STATUS_PLANNED
        else:
            status = CLUE_STATUS_MISSING
        appearance = tuple(sorted(set(disclosed) | set(planned), key=positions.__getitem__))
        first_position = positions[appearance[0]] if appearance else None
        if cutoff is None or first_position is None:
            within_position: bool | None = None
        else:
            within_position = first_position <= cutoff
        node_ids.update(appearance)
        states.append(
            ClueState(
                clue_id=entry.artifact_id,
                statement=clue.statement,
                necessity=clue.necessity,
                points_to=tuple(clue.points_to),
                planned_beats=planned,
                disclosed_beats=disclosed,
                status=status,
                first_position=first_position,
                within_position=within_position,
            )
        )

    if focus is not None:
        if focus not in {node.id for node in projection.nodes}:
            raise violation(
                ec.STORY_UNKNOWN_ARTIFACT,
                "graphs.clues.focus",
                f"聚焦对象 {focus!r} 不在当前快照中",
                "改用快照内存在的对象 ID",
                node_id=focus,
            )
        kept = [edge for edge in edges if edge.source == focus or edge.target == focus]
        focus_nodes = {focus}
        for edge in kept:
            focus_nodes.add(edge.source)
            focus_nodes.add(edge.target)
        edges = kept
        node_ids = focus_nodes
        states = [state for state in states if state.clue_id in node_ids]

    nodes = tuple(node for node in projection.nodes if node.id in node_ids)
    return ClueGraph(
        commit_id=projection.commit_id,
        at_beat_id=at_beat_id,
        position_index=cutoff,
        clues=tuple(states),
        nodes=nodes,
        edges=tuple(edges),
    )
