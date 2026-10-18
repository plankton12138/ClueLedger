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
from hastings.domain.story import kinds
from hastings.domain.story.catalog import StoryCatalog

__all__ = [
    "EVIDENCE_NEEDS_CHECK",
    "EVIDENCE_NOT_RECORDED",
    "EVIDENCE_VALID",
    "GRAPH_NODE_KINDS",
    "LAYER_DISCLOSURE",
    "LAYER_KNOWLEDGE",
    "LAYER_PLAN",
    "LAYER_TRUTH",
    "SEMANTIC_LAYERS",
    "GraphEdge",
    "GraphNode",
    "GraphProjection",
    "GraphSourceRef",
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
