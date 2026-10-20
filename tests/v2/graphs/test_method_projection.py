"""手法图领域投影（V03-7.5）：只投影已有文字与关系，缺口如实显示。

验收口径（计划 M12 手法结构行 / 阶段指南 §11.4）：不新增步骤/条件/证据对象，
不按模型措辞补边；资料不完整时仍能看出「缺什么、去哪里补」，完整度不冒充逻辑证明。
"""

from __future__ import annotations

from hastings.domain.story import kinds
from hastings.domain.story.catalog import StoryCatalog, StoryEntry
from hastings.domain.story.errors import StoryViolation
from hastings.domain.story.graph import method_projection


def _entry(kind: str, artifact_id: str, content: dict) -> StoryEntry:
    return StoryEntry(artifact_id=artifact_id, kind=kind, revision_id="rev_1", content=content)


def _catalog(*entries: StoryEntry) -> StoryCatalog:
    return StoryCatalog(commit_id="c1", entries=tuple(entries))


def test_without_truth_reports_gap_not_an_empty_success() -> None:
    graph = method_projection(_catalog(_entry(kinds.KIND_CHARACTER, "ch1", {"name": "姐姐"})))
    assert graph.truths == ()
    assert graph.nodes == ()
    assert [gap.code for gap in graph.gaps] == ["METHOD_TRUTH_NOT_RECORDED"]
    assert "账本" in graph.gaps[0].fix
    assert graph.completeness.total == 0


def test_partial_truth_reports_each_missing_text_and_no_clue() -> None:
    graph = method_projection(
        _catalog(
            _entry(kinds.KIND_CHARACTER, "ch1", {"name": "姐姐"}),
            _entry(kinds.KIND_MYSTERY_TRUTH, "t1", {"culprit_id": "ch1", "method": ""}),
        )
    )
    truth = graph.truths[0]
    assert truth.missing_fields == ("method", "motive", "cover_up")
    codes = [gap.code for gap in graph.gaps]
    assert codes.count("METHOD_TEXT_NOT_RECORDED") == 3
    assert "METHOD_CLUES_NOT_RECORDED" in codes
    assert graph.completeness.recorded == 0
    assert graph.completeness.total == 3
    assert "不代表推理链成立" in graph.completeness.note
    # 凶手边来自账本已有的 culprit_id 引用，不新增结构。
    assert {node.id for node in graph.nodes} == {"ch1", "t1"}
    assert [(edge.source, edge.target, edge.label) for edge in graph.edges] == [
        ("t1", "ch1", "凶手")
    ]


def test_dangling_timeline_reference_is_a_gap_not_a_node() -> None:
    graph = method_projection(
        _catalog(
            _entry(kinds.KIND_CHARACTER, "ch1", {"name": "姐姐"}),
            _entry(
                kinds.KIND_MYSTERY_TRUTH,
                "t1",
                {
                    "culprit_id": "ch1",
                    "method": "换药",
                    "motive": "遗产",
                    "cover_up": "伪装成意外",
                    "true_timeline": ["ev_missing"],
                },
            ),
        )
    )
    codes = [gap.code for gap in graph.gaps]
    assert "METHOD_TIMELINE_UNRESOLVED" in codes
    assert "METHOD_CLUES_NOT_RECORDED" in codes
    assert "ev_missing" not in {node.id for node in graph.nodes}


def test_full_chain_projects_existing_relations_only() -> None:
    graph = method_projection(
        _catalog(
            _entry(kinds.KIND_CHARACTER, "ch1", {"name": "姐姐"}),
            _entry(kinds.KIND_PLACE, "p1", {"name": "书房"}),
            _entry(
                kinds.KIND_TIMELINE_EVENT,
                "e1",
                {"time": "晚上九点", "place_id": "p1", "participants": ["ch1"]},
            ),
            _entry(
                kinds.KIND_MYSTERY_TRUTH,
                "t1",
                {
                    "culprit_id": "ch1",
                    "method": "换药",
                    "motive": "遗产",
                    "cover_up": "伪装成意外",
                    "true_timeline": ["e1"],
                },
            ),
            _entry(kinds.KIND_TRUTH_FACT, "f1", {"statement": "药被换过", "event_id": "e1"}),
            _entry(kinds.KIND_CLUE, "cl1", {"statement": "空药瓶", "points_to": ["f1"]}),
            _entry(
                kinds.KIND_KNOWLEDGE,
                "k1",
                {"character_id": "ch1", "fact_id": "f1", "mode": "knows"},
            ),
        )
    )
    assert graph.gaps == ()
    assert graph.completeness.recorded == 3
    assert {node.id for node in graph.nodes} == {"ch1", "p1", "e1", "t1", "f1", "cl1"}
    pairs = {(edge.source, edge.target, edge.label) for edge in graph.edges}
    assert ("t1", "ch1", "凶手") in pairs
    assert ("t1", "e1", "真实时间线") in pairs
    assert ("e1", "p1", "地点") in pairs
    assert ("e1", "ch1", "参与") in pairs
    assert ("cl1", "f1", "指向") in pairs
    # 认知边（knowledge 层）不进入手法结构。
    assert all(edge.semantic_layer == "truth" for edge in graph.edges)


def test_focus_keeps_only_neighbors_and_unknown_focus_is_rejected() -> None:
    catalog = _catalog(
        _entry(kinds.KIND_CHARACTER, "ch1", {"name": "姐姐"}),
        _entry(kinds.KIND_PLACE, "p1", {"name": "书房"}),
        _entry(
            kinds.KIND_TIMELINE_EVENT,
            "e1",
            {"time": "九点", "place_id": "p1", "participants": ["ch1"]},
        ),
        _entry(
            kinds.KIND_MYSTERY_TRUTH,
            "t1",
            {
                "culprit_id": "ch1",
                "method": "换药",
                "motive": "遗产",
                "cover_up": "意外",
                "true_timeline": ["e1"],
            },
        ),
    )
    focused = method_projection(catalog, focus="e1")
    assert {node.id for node in focused.nodes} == {"e1", "p1", "ch1", "t1"}
    assert all("e1" in (edge.source, edge.target) for edge in focused.edges)
    try:
        method_projection(catalog, focus="missing")
    except StoryViolation as exc:
        assert exc.code == "STORY_UNKNOWN_ARTIFACT"
    else:  # pragma: no cover - 明确失败信息
        raise AssertionError("未知聚焦对象应被拒绝")
