"""线索图（V03-6.3 / M12）领域用例：线索子图、计划/实现分离与叙述位置筛选。

纯领域用例（无数据库/Web/模型）：直接构造目录，断言线索图只在统一投影
（V03-6.1）上筛出「与线索相连」的边——只有账本有记录才算正式关系，没有记录
（如独立的误导解释）就是缺口，不凭措辞连线；计划出现、已在正文出现分别报出，
「截至某节拍」只放行此前已出现的线索，后文线索不提前进入视图。
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
from hastings.domain.repository.models import (
    Artifact,
    ArtifactView,
    RelationRevision,
    Revision,
    Snapshot,
)
from hastings.domain.repository.serialization import content_hash
from hastings.domain.story import error_codes as ec
from hastings.domain.story.catalog import StoryCatalog
from hastings.domain.story.errors import StoryViolation
from hastings.domain.story.graph import (
    CLUE_STATUS_IMPLEMENTED,
    CLUE_STATUS_MISSING,
    CLUE_STATUS_PLANNED,
    EVIDENCE_NEEDS_CHECK,
    clue_projection,
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


def _scene() -> StoryCatalog:
    """三拍结构：b1 计划出现、b2 已披露、b3 后文；三条线索 + 一个独立误导解释。"""
    return _catalog(
        [
            (
                "art_ch",
                "narrative.chapter",
                {"node_id": "art_ch", "order_key": "1", "title": "第一章"},
            ),
            ("art_sc", "narrative.scene", {"node_id": "art_sc", "parent_id": "art_ch"}),
            (
                "art_b1",
                "narrative.beat",
                {"beat_id": "art_b1", "parent_id": "art_sc", "order_key": "1"},
            ),
            (
                "art_b2",
                "narrative.beat",
                {"beat_id": "art_b2", "parent_id": "art_sc", "order_key": "2"},
            ),
            (
                "art_b3",
                "narrative.beat",
                {
                    "beat_id": "art_b3",
                    "parent_id": "art_sc",
                    "order_key": "3",
                    "reader_disclosures": [
                        {"fact_id": "art_f2", "audience": "reader", "interpretation": "true"}
                    ],
                },
            ),
            ("art_f1", "story.truth_fact", {"statement": "怀表在更早以前已经损坏"}),
            ("art_f2", "story.truth_fact", {"statement": "姐姐九点离开"}),
            (
                "art_clue_plan",
                "story.clue",
                {"statement": "停走的怀表", "points_to": ["art_f1"], "disclosure_plan": ["art_b1"]},
            ),
            ("art_clue_done", "story.clue", {"statement": "九点的钟声", "points_to": ["art_f2"]}),
            ("art_clue_gap", "story.clue", {"statement": "走廊的脚印"}),
            ("art_rh", "story.red_herring", {"false_explanation": "怀表是姐姐弄坏的"}),
        ]
    )


def test_clue_graph_keeps_only_edges_incident_to_clues() -> None:
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
            ("art_clue", "story.clue", {"statement": "怀表停在九点", "points_to": "art_fact"}),
        ]
    )
    graph = clue_projection(catalog)

    edges = {(edge.source, edge.target): edge for edge in graph.edges}
    # 只有与线索相连的边进入线索图；人物关系边留给人物图。
    assert set(edges) == {("art_clue", "art_fact")}
    assert (
        edges[("art_clue", "art_fact")].label,
        edges[("art_clue", "art_fact")].relation_type,
    ) == (
        "指向",
        "points_to",
    )
    # 节点是线索与相连端点；无关人物不入图。
    assert {node.id for node in graph.nodes} == {"art_clue", "art_fact"}


def test_plan_and_implementation_are_reported_separately() -> None:
    graph = clue_projection(_scene())
    states = {state.clue_id: state for state in graph.clues}

    planned = states["art_clue_plan"]
    assert planned.points_to == ("art_f1",)
    assert planned.planned_beats == ("art_b1",)
    assert planned.disclosed_beats == ()
    assert planned.status == CLUE_STATUS_PLANNED
    assert planned.first_position == 0

    # 指向的事实到 b3 才向读者披露 → 「已在正文出现」。
    done = states["art_clue_done"]
    assert done.disclosed_beats == ("art_b3",)
    assert done.status == CLUE_STATUS_IMPLEMENTED
    assert done.first_position == 2


def test_later_clues_do_not_appear_before_their_position() -> None:
    graph = clue_projection(_scene(), at_beat_id="art_b1")
    assert graph.position_index == 0
    states = {state.clue_id: state for state in graph.clues}
    # b1 位置的线索在「截至 b1」内；b3 才出现的线索不在当前位置前。
    assert states["art_clue_plan"].within_position is True
    assert states["art_clue_done"].within_position is False

    # 不指定位置时不做筛选判定（None 表示未按位置取视图）。
    unfiltered = {state.clue_id: state for state in clue_projection(_scene()).clues}
    assert unfiltered["art_clue_done"].within_position is None


def test_unrecorded_clue_is_a_gap_not_an_error() -> None:
    states = {state.clue_id: state for state in clue_projection(_scene()).clues}
    gap = states["art_clue_gap"]
    # 没有出现计划也没有正文披露是合法缺口（可以是故意悬念），不判错也不合成位置。
    assert gap.status == CLUE_STATUS_MISSING
    assert gap.first_position is None
    assert gap.within_position is None

    # 独立的误导解释没有正式连接记录，不凭语义猜测连线。
    assert "art_rh" not in {node.id for node in clue_projection(_scene()).nodes}


def test_source_change_marks_clue_as_needs_check() -> None:
    graph = clue_projection(_scene(), evidence_status={"art_clue_plan": EVIDENCE_NEEDS_CHECK})
    nodes = {node.id: node for node in graph.nodes}
    assert nodes["art_clue_plan"].evidence_status == EVIDENCE_NEEDS_CHECK


def test_explicit_relation_connects_clue_without_inventing_a_declarer() -> None:
    catalog = _catalog(
        [
            ("art_clue", "story.clue", {"statement": "怀表"}),
            ("art_rh", "story.red_herring", {"false_explanation": "怀表是姐姐弄坏的"}),
        ],
        relations=(
            RelationRevision(
                id="rel_1",
                commit_id=CommitId("cmt_1"),
                action="add",
                source=ArtifactId("art_clue"),
                relation_type="mentions",
                target=ArtifactId("art_rh"),
            ),
        ),
    )
    graph = clue_projection(catalog)
    edges = {(edge.source, edge.target) for edge in graph.edges}
    assert edges == {("art_clue", "art_rh")}
    assert {node.id for node in graph.nodes} == {"art_clue", "art_rh"}


def test_unknown_position_and_focus_are_rejected() -> None:
    catalog = _scene()
    with pytest.raises(StoryViolation) as unknown_beat:
        clue_projection(catalog, at_beat_id="art_missing")
    assert unknown_beat.value.code == ec.STORY_UNKNOWN_ARTIFACT

    with pytest.raises(StoryViolation) as unknown_focus:
        clue_projection(catalog, focus="art_missing")
    assert unknown_focus.value.code == ec.STORY_UNKNOWN_ARTIFACT


def test_focus_returns_local_neighbours_within_the_view() -> None:
    graph = clue_projection(_scene(), focus="art_f1")
    assert {node.id for node in graph.nodes} == {"art_f1", "art_clue_plan"}
    assert {(edge.source, edge.target) for edge in graph.edges} == {("art_clue_plan", "art_f1")}
    assert {state.clue_id for state in graph.clues} == {"art_clue_plan"}
