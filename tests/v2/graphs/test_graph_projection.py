"""统一图谱投影（V03-6.1 / M12）领域用例：节点/边/语义层/来源与证据状态。

这些是纯领域用例（无数据库/Web/模型）：直接由 (id, kind, content) 构造目录，
断言投影只从正式账本导出、端点必在快照内、悬空引用不入图。
"""

from __future__ import annotations

from datetime import UTC, datetime

from hastings.domain.repository.ids import (
    Actor,
    ArtifactId,
    CommitId,
    ContentHash,
    ProjectId,
    RevisionId,
)
from hastings.domain.repository.models import (
    Artifact,
    ArtifactView,
    RelationRevision,
    Revision,
    Snapshot,
)
from hastings.domain.repository.serialization import content_hash
from hastings.domain.story.catalog import StoryCatalog
from hastings.domain.story.graph import (
    EVIDENCE_NOT_RECORDED,
    LAYER_DISCLOSURE,
    LAYER_KNOWLEDGE,
    LAYER_PLAN,
    LAYER_TRUTH,
    graph_projection,
)

_TS = datetime(2026, 1, 1, tzinfo=UTC)
_PROJECT = ProjectId("prj_test")


def _catalog(
    entries: list[tuple[str, str, dict]],
    *,
    relations: tuple[RelationRevision, ...] = (),
    commit_id: str = "cmt_1",
) -> StoryCatalog:
    artifacts: dict[ArtifactId, ArtifactView] = {}
    for artifact_id, kind, content in entries:
        aid = ArtifactId(artifact_id)
        revision = Revision(
            id=RevisionId(f"rev_{artifact_id}"),
            artifact_id=aid,
            parent_id=None,
            content=content,
            content_hash=ContentHash(content_hash(content)),
            author=Actor("system", "sys"),
            created_at=_TS,
        )
        artifacts[aid] = ArtifactView(
            artifact=Artifact(id=aid, project_id=_PROJECT, kind=kind, created_at=_TS),
            revision=revision,
        )
    return StoryCatalog.from_snapshot(
        Snapshot(
            project_id=_PROJECT,
            commit_id=CommitId(commit_id),
            artifacts=artifacts,
            relations=relations,
        )
    )


def test_nodes_carry_semantic_layer_and_exclude_project_conventions() -> None:
    catalog = _catalog(
        [
            ("art_c", "story.character", {"name": "姐姐"}),
            ("art_fact", "story.truth_fact", {"statement": "九点到达"}),
            ("art_k", "story.knowledge", {"character_id": "art_c", "fact_id": "art_fact"}),
            ("art_d", "story.disclosure", {"audience": "reader"}),
            ("art_ch", "narrative.chapter", {"title": "第一章"}),
            ("art_agr", "story.agreement", {"statement": "不写血腥"}),
        ]
    )
    projection = graph_projection(catalog)
    layers = {node.id: node.semantic_layer for node in projection.nodes}
    assert layers == {
        "art_c": LAYER_TRUTH,
        "art_fact": LAYER_TRUTH,
        "art_k": LAYER_KNOWLEDGE,
        "art_d": LAYER_DISCLOSURE,
        "art_ch": LAYER_PLAN,
    }
    # 作品约定不是故事对象，不入图。
    assert "art_agr" not in layers
    assert {node.evidence_status for node in projection.nodes} == {EVIDENCE_NOT_RECORDED}


def test_edges_are_exported_from_declared_reference_fields() -> None:
    catalog = _catalog(
        [
            ("art_a", "story.character", {"name": "姐姐"}),
            ("art_b", "story.character", {"name": "妹妹"}),
            (
                "art_rel",
                "story.relationship",
                {"source_id": "art_a", "target_id": "art_b", "relation_type": "姐妹"},
            ),
            ("art_fact", "story.truth_fact", {"statement": "九点到达"}),
            ("art_ev", "story.timeline_event", {"time": "九点"}),
            ("art_clue", "story.clue", {"statement": "怀表停在九点", "points_to": "art_fact"}),
            (
                "art_k",
                "story.knowledge",
                {"character_id": "art_a", "fact_id": "art_fact", "mode": "suspects"},
            ),
        ]
    )
    projection = graph_projection(catalog)
    node_ids = {node.id for node in projection.nodes}
    # 不变量：边的端点必是快照内的正式节点（无第二份事实源）。
    assert all(edge.source in node_ids and edge.target in node_ids for edge in projection.edges)

    by_key = {(edge.source, edge.target): edge for edge in projection.edges}
    rel = by_key[("art_a", "art_b")]
    assert rel.label == "姐妹"
    assert rel.relation_type == "relationship"
    assert rel.semantic_layer == LAYER_TRUTH
    assert rel.declared_by == "art_rel"
    assert rel.source_refs[0].id == "art_rel"

    clue = by_key[("art_clue", "art_fact")]
    assert (clue.label, clue.relation_type, clue.declared_by) == ("指向", "points_to", "art_clue")

    knowledge = by_key[("art_a", "art_fact")]
    assert knowledge.semantic_layer == LAYER_KNOWLEDGE
    assert knowledge.label == "怀疑"
    assert knowledge.declared_by == "art_k"


def test_fact_event_and_timeline_references_become_edges() -> None:
    catalog = _catalog(
        [
            ("art_ev", "story.timeline_event", {"time": "九点", "place_id": "art_place"}),
            ("art_place", "story.place", {"name": "大厅"}),
            ("art_fact", "story.truth_fact", {"statement": "九点到达", "event_id": "art_ev"}),
        ]
    )
    projection = graph_projection(catalog)
    edges = {(edge.source, edge.target): edge for edge in projection.edges}
    assert edges[("art_fact", "art_ev")].relation_type == "event_id"
    assert edges[("art_ev", "art_place")].relation_type == "place_id"
    assert edges[("art_ev", "art_place")].label == "地点"


def test_dangling_reference_does_not_create_edge() -> None:
    catalog = _catalog(
        [("art_clue", "story.clue", {"statement": "怀表", "points_to": "art_missing"})]
    )
    projection = graph_projection(catalog)
    assert projection.edges == ()
    assert [node.id for node in projection.nodes] == ["art_clue"]


def test_explicit_relation_is_projected_without_inventing_a_declarer() -> None:
    catalog = _catalog(
        [
            ("art_new", "story.truth_fact", {"statement": "新事实"}),
            ("art_old", "story.truth_fact", {"statement": "旧事实"}),
        ],
        relations=(
            RelationRevision(
                id="rel_1",
                commit_id=CommitId("cmt_1"),
                action="add",
                source=ArtifactId("art_new"),
                relation_type="supersedes",
                target=ArtifactId("art_old"),
            ),
        ),
    )
    projection = graph_projection(catalog)
    assert len(projection.edges) == 1
    edge = projection.edges[0]
    assert (edge.source, edge.target, edge.relation_type) == ("art_new", "art_old", "supersedes")
    assert edge.declared_by is None
    assert edge.source_refs == ()


def test_evidence_status_is_carried_on_nodes_and_edges() -> None:
    catalog = _catalog(
        [
            ("art_a", "story.character", {"name": "姐姐"}),
            ("art_b", "story.character", {"name": "妹妹"}),
            (
                "art_rel",
                "story.relationship",
                {"source_id": "art_a", "target_id": "art_b", "relation_type": "姐妹"},
            ),
        ]
    )
    projection = graph_projection(
        catalog, evidence_status={"art_a": "needs_check", "art_b": "valid"}
    )
    nodes = {node.id: node for node in projection.nodes}
    assert nodes["art_a"].evidence_status == "needs_check"
    assert nodes["art_b"].evidence_status == "valid"
    # 声明条目（art_rel）没有证据时，边沿目标节点的证据状态归因。
    assert projection.edges[0].evidence_status == "valid"

    # 声明条目有证据时优先取声明条目，而不是目标节点。
    declared = graph_projection(
        catalog, evidence_status={"art_rel": "needs_check", "art_b": "valid"}
    )
    assert declared.edges[0].evidence_status == "needs_check"
