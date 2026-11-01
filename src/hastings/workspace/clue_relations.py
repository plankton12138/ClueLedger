"""线索关系图：受控投影，不推断、不自动采纳。

默认只给选中线索的一跳关系；没有选中线索时给全图，但受节点与边预算约束。预算外的东西
继续通过线索列表可达，图里缺少连线不能解释为「已证实两条线索无关」。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, Field

from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.story_clue_catalog import StoryClueCatalog
from hastings.workspace.story_models import ClueKind, ClueRelation, StoryVariant

__all__ = ["ClueRelationsView", "RelationEdge", "RelationNode", "build_relations"]

#: 单张图最多展示的节点与连线；超过时用一跳子图与分页列表。
MAX_NODES = 40
MAX_EDGES = 80


class RelationNode(BaseModel):
    clue_id: str
    title: str
    kind: ClueKind | None = None
    relations: int = 0


class RelationEdge(BaseModel):
    relation_id: str
    kind: str
    from_clue_id: str
    to_clue_id: str
    reason: str
    basis_source_ids: list[str] = Field(default_factory=list)


class ClueRelationsView(BaseModel):
    variant_id: str
    variant_revision: str
    view_revision: str
    focus_clue_id: str | None = None
    nodes: list[RelationNode] = Field(default_factory=list)
    edges: list[RelationEdge] = Field(default_factory=list)
    #: 因预算没有展示的相连线索数量；不是「未发现关系」的数量。
    remaining_count: int = 0


def _view_revision(
    variant: StoryVariant, nodes: list[RelationNode], edges: list[RelationEdge]
) -> str:
    payload = {
        "variant": variant.revision,
        "nodes": [[row.clue_id, row.kind] for row in nodes],
        "edges": [[row.relation_id, row.kind, row.from_clue_id, row.to_clue_id] for row in edges],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def _focused(
    relations: list[ClueRelation], focus: str, max_neighbors: int
) -> tuple[list[ClueRelation], int]:
    incident = [row for row in relations if focus in (row.from_clue_id, row.to_clue_id)]
    neighbors = sorted(
        {row.to_clue_id if row.from_clue_id == focus else row.from_clue_id for row in incident}
    )
    shown = set(neighbors[:max_neighbors])
    edges = [
        row
        for row in incident
        if (row.to_clue_id if row.from_clue_id == focus else row.from_clue_id) in shown
    ]
    return edges, len(neighbors) - len(shown)


def build_relations(
    root: Path,
    variant: StoryVariant,
    *,
    focus_clue_id: str | None = None,
    max_neighbors: int = 20,
    max_nodes: int = MAX_NODES,
    max_edges: int = MAX_EDGES,
) -> ClueRelationsView:
    if max_neighbors < 1 or max_nodes < 2 or max_edges < 1:
        raise ValueError("关系图预算必须为正数")
    ledger = ClueLedgerStore(root).read()
    catalog = StoryClueCatalog.from_variant(variant, ledger)
    titles = {fact.clue_id: fact.title for fact in catalog}
    kinds = {
        clue_id: (plan.kind if plan is not None else None)
        for clue_id, plan in variant.clue_plans.items()
    }

    relations = sorted(variant.clue_relations.values(), key=lambda row: (row.kind, row.id))
    if focus_clue_id is not None:
        if focus_clue_id not in titles:
            raise KeyError(focus_clue_id)
        edges, remaining = _focused(relations, focus_clue_id, max_neighbors)
        node_ids = sorted({value for row in edges for value in (row.from_clue_id, row.to_clue_id)})
        # 选中线索放首位，图与列表用同一顺序。
        node_ids = [focus_clue_id, *[row for row in node_ids if row != focus_clue_id]]
    else:
        involved = sorted(
            {value for row in relations for value in (row.from_clue_id, row.to_clue_id)}
        )
        shown = set(involved[:max_nodes])
        edges = [row for row in relations if row.from_clue_id in shown and row.to_clue_id in shown][
            :max_edges
        ]
        node_ids = sorted({value for row in edges for value in (row.from_clue_id, row.to_clue_id)})
        remaining = len(involved) - len(shown)

    degree: dict[str, int] = {}
    for row in edges:
        for clue_id in (row.from_clue_id, row.to_clue_id):
            degree[clue_id] = degree.get(clue_id, 0) + 1
    nodes = [
        RelationNode(
            clue_id=clue_id,
            title=titles.get(clue_id, clue_id),
            kind=kinds.get(clue_id),
            relations=degree.get(clue_id, 0),
        )
        for clue_id in node_ids
    ]
    projected = [
        RelationEdge(
            relation_id=row.id,
            kind=row.kind,
            from_clue_id=row.from_clue_id,
            to_clue_id=row.to_clue_id,
            reason=row.reason,
            basis_source_ids=list(row.basis_source_ids),
        )
        for row in edges
    ]
    return ClueRelationsView(
        variant_id=variant.id,
        variant_revision=variant.revision,
        view_revision=_view_revision(variant, nodes, projected),
        focus_clue_id=focus_clue_id,
        nodes=nodes,
        edges=projected,
        remaining_count=max(0, remaining),
    )
