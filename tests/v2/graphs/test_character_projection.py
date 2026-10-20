"""人物图（V03-6.2 / M12）领域用例：真关系/认知分层、视角与叙述位置筛选。

纯领域用例（无数据库/Web/模型）：直接构造目录，断言人物图只在统一投影
（V03-6.1）上做筛选——真关系（truth 层）与角色认知（knowledge 层）分开，
角色视角隐藏真实状态，叙述位置按获知事件过滤，未记录不合成「不知道」边。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hastings.domain.repository.ids import (
    Actor,
    ArtifactId,
    CommitId,
    ContentHash,
    ProjectId,
    RevisionId,
)
from hastings.domain.repository.models import Artifact, ArtifactView, Revision, Snapshot
from hastings.domain.repository.serialization import content_hash
from hastings.domain.story import error_codes as ec
from hastings.domain.story.catalog import StoryCatalog
from hastings.domain.story.errors import StoryViolation
from hastings.domain.story.graph import (
    LAYER_KNOWLEDGE,
    LAYER_TRUTH,
    PERSPECTIVE_AUTHOR,
    PERSPECTIVE_CHARACTER,
    character_projection,
)
from hastings.domain.story.narrative.order import last_beat_of_node

_TS = datetime(2026, 1, 1, tzinfo=UTC)
_PROJECT = ProjectId("prj_test")


def _catalog(entries: list[tuple[str, str, dict]], *, commit_id: str = "cmt_1") -> StoryCatalog:
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
            relations=(),
        )
    )


def _family() -> StoryCatalog:
    """姐姐/妹妹/侦探 + 一条真关系 + 妹妹对九点到达的怀疑 + 一条无关真关系。"""
    return _catalog(
        [
            ("art_a", "story.character", {"name": "姐姐"}),
            ("art_b", "story.character", {"name": "妹妹"}),
            ("art_c", "story.character", {"name": "侦探"}),
            (
                "art_rel",
                "story.relationship",
                {
                    "source_id": "art_a",
                    "target_id": "art_b",
                    "relation_type": "姐妹",
                    "public_state": "表面和睦",
                    "true_state": "暗中猜忌",
                },
            ),
            (
                "art_rel2",
                "story.relationship",
                {"source_id": "art_a", "target_id": "art_c", "relation_type": "雇佣"},
            ),
            ("art_fact", "story.truth_fact", {"statement": "九点到达"}),
            (
                "art_k",
                "story.knowledge",
                {"character_id": "art_b", "fact_id": "art_fact", "mode": "suspects"},
            ),
        ]
    )


def test_author_view_separates_true_relations_from_character_knowledge() -> None:
    graph = character_projection(_family())
    assert graph.perspective_mode == PERSPECTIVE_AUTHOR
    assert graph.perspective_id is None
    assert graph.at_beat_id is None

    edges = {(edge.edge.source, edge.edge.target): edge for edge in graph.edges}
    # 只保留关系边与认知边；线索「指向」等跨层引用不进人物图。
    assert set(edges) == {("art_a", "art_b"), ("art_a", "art_c"), ("art_b", "art_fact")}

    relation = edges[("art_a", "art_b")]
    assert relation.edge.semantic_layer == LAYER_TRUTH
    assert relation.edge.label == "姐妹"
    assert relation.public_state == "表面和睦"
    assert relation.true_state == "暗中猜忌"
    assert relation.true_state_hidden is False

    knowledge = edges[("art_b", "art_fact")]
    assert knowledge.edge.semantic_layer == LAYER_KNOWLEDGE
    assert knowledge.edge.label == "怀疑"
    assert (knowledge.public_state, knowledge.true_state) == ("", "")

    # 作者全知看到投影内全部节点（人物、关系、事实、认知）。
    assert {node.id for node in graph.nodes} == {
        "art_a",
        "art_b",
        "art_c",
        "art_rel",
        "art_rel2",
        "art_fact",
        "art_k",
    }


def test_character_view_hides_true_state_and_keeps_only_its_own_edges() -> None:
    graph = character_projection(_family(), perspective="art_b")
    assert graph.perspective_mode == PERSPECTIVE_CHARACTER
    assert graph.perspective_id == "art_b"

    edges = {(edge.edge.source, edge.edge.target): edge for edge in graph.edges}
    # 只露该角色参与的关系边与本人认知边；姐姐与侦探的雇佣关系不出现。
    assert set(edges) == {("art_a", "art_b"), ("art_b", "art_fact")}

    relation = edges[("art_a", "art_b")]
    assert relation.public_state == "表面和睦"
    assert relation.true_state == ""  # 角色视角不见作者真相
    assert relation.true_state_hidden is True

    knowledge = edges[("art_b", "art_fact")]
    assert knowledge.true_state_hidden is False

    # 节点集收缩为视角角色与视图内边的端点；无关角色不入图。
    assert {node.id for node in graph.nodes} == {"art_a", "art_b", "art_fact"}


def test_position_filters_knowledge_by_gaining_event_but_keeps_true_relations() -> None:
    catalog = _catalog(
        [
            ("art_a", "story.character", {"name": "姐姐"}),
            ("art_c", "story.character", {"name": "侦探"}),
            (
                "art_rel",
                "story.relationship",
                {"source_id": "art_a", "target_id": "art_c", "relation_type": "雇佣"},
            ),
            ("art_fact1", "story.truth_fact", {"statement": "九点到达"}),
            ("art_fact2", "story.truth_fact", {"statement": "十点离开"}),
            ("art_b1", "narrative.beat", {"beat_id": "art_b1", "title": "第一拍"}),
            ("art_b2", "narrative.beat", {"beat_id": "art_b2", "title": "第二拍"}),
            (
                "art_k1",
                "story.knowledge",
                {
                    "character_id": "art_a",
                    "fact_id": "art_fact1",
                    "mode": "knows",
                    "at_beat_id": "art_b1",
                },
            ),
            (
                "art_k2",
                "story.knowledge",
                {
                    "character_id": "art_a",
                    "fact_id": "art_fact2",
                    "mode": "suspects",
                    "at_beat_id": "art_b2",
                },
            ),
        ]
    )

    early = character_projection(catalog, at_beat_id="art_b1")
    assert early.at_beat_id == "art_b1"
    # 第一拍时只应看到第一拍之前的获知；第二拍的认知尚未生效。
    assert {(edge.edge.source, edge.edge.target) for edge in early.edges} == {
        ("art_a", "art_fact1"),
        ("art_a", "art_c"),  # 真关系是基础事实，任何位置都在
    }

    final = character_projection(catalog)
    assert {(edge.edge.source, edge.edge.target) for edge in final.edges} == {
        ("art_a", "art_fact1"),
        ("art_a", "art_fact2"),
        ("art_a", "art_c"),
    }


def test_unrecorded_knowledge_never_becomes_an_unaware_edge() -> None:
    catalog = _catalog(
        [
            ("art_a", "story.character", {"name": "姐姐"}),
            ("art_fact", "story.truth_fact", {"statement": "九点到达"}),
        ]
    )
    # 没有 story.knowledge 记录时，视图里没有「姐姐—事实」的认知边，
    # 也不会合成一条 unaware（不知道）边冒充已记录。
    author = character_projection(catalog)
    assert author.edges == ()
    character = character_projection(catalog, perspective="art_a")
    assert character.edges == ()
    assert {node.id for node in character.nodes} == {"art_a"}


def test_unknown_perspective_or_beat_is_a_story_violation() -> None:
    catalog = _family()
    with pytest.raises(StoryViolation) as missing_character:
        character_projection(catalog, perspective="art_missing")
    assert missing_character.value.code == ec.STORY_UNKNOWN_ARTIFACT

    with pytest.raises(StoryViolation) as missing_beat:
        character_projection(catalog, at_beat_id="art_missing")
    assert missing_beat.value.code == ec.STORY_UNKNOWN_ARTIFACT


def test_last_beat_of_node_follows_structure_not_chapter_index() -> None:
    catalog = _catalog(
        [
            (
                "art_ch",
                "narrative.chapter",
                {"node_id": "art_ch", "kind": "narrative.chapter", "title": "第一章"},
            ),
            (
                "art_sc1",
                "narrative.scene",
                {
                    "node_id": "art_sc1",
                    "kind": "narrative.scene",
                    "parent_id": "art_ch",
                    "order_key": "a",
                },
            ),
            ("art_b1", "narrative.beat", {"beat_id": "art_b1", "parent_id": "art_sc1"}),
            (
                "art_sc2",
                "narrative.scene",
                {
                    "node_id": "art_sc2",
                    "kind": "narrative.scene",
                    "parent_id": "art_ch",
                    "order_key": "b",
                },
            ),
            ("art_b2", "narrative.beat", {"beat_id": "art_b2", "parent_id": "art_sc2"}),
        ]
    )
    assert last_beat_of_node(catalog, "art_ch", where="t") == "art_b2"
    assert last_beat_of_node(catalog, "art_sc1", where="t") == "art_b1"

    with pytest.raises(StoryViolation) as unknown:
        last_beat_of_node(catalog, "art_missing", where="t")
    assert unknown.value.code == ec.STORY_UNKNOWN_ARTIFACT

    empty = _catalog(
        [
            (
                "art_ch2",
                "narrative.chapter",
                {"node_id": "art_ch2", "kind": "narrative.chapter"},
            )
        ]
    )
    with pytest.raises(StoryViolation) as no_beat:
        last_beat_of_node(empty, "art_ch2", where="t")
    assert no_beat.value.code == ec.STORY_MISSING_PRECONDITION


def test_chapter_position_equals_its_last_beat() -> None:
    catalog = _catalog(
        [
            ("art_a", "story.character", {"name": "姐姐"}),
            ("art_fact1", "story.truth_fact", {"statement": "九点到达"}),
            ("art_fact2", "story.truth_fact", {"statement": "十点离开"}),
            (
                "art_ch",
                "narrative.chapter",
                {"node_id": "art_ch", "kind": "narrative.chapter", "title": "第一章"},
            ),
            ("art_b1", "narrative.beat", {"beat_id": "art_b1", "parent_id": "art_ch"}),
            ("art_b2", "narrative.beat", {"beat_id": "art_b2", "parent_id": "art_ch"}),
            (
                "art_k1",
                "story.knowledge",
                {
                    "character_id": "art_a",
                    "fact_id": "art_fact1",
                    "mode": "knows",
                    "at_beat_id": "art_b1",
                },
            ),
            (
                "art_k2",
                "story.knowledge",
                {
                    "character_id": "art_a",
                    "fact_id": "art_fact2",
                    "mode": "suspects",
                    "at_beat_id": "art_b2",
                },
            ),
        ]
    )
    beat_id = last_beat_of_node(catalog, "art_ch", where="t")
    by_chapter = character_projection(catalog, at_beat_id=beat_id)
    by_beat = character_projection(catalog, at_beat_id="art_b2")
    by_chapter_keys = {(e.edge.source, e.edge.target) for e in by_chapter.edges}
    by_beat_keys = {(e.edge.source, e.edge.target) for e in by_beat.edges}
    assert by_chapter_keys == by_beat_keys
    assert by_beat_keys == {("art_a", "art_fact1"), ("art_a", "art_fact2")}
