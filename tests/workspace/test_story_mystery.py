"""1.0.4 悬疑投影：实际时间、线索生命周期、知情与诊断。"""

from __future__ import annotations

from typing import Any

from hastings.workspace.story_clue_catalog import StoryClueCatalog, StoryClueFact
from hastings.workspace.story_models import (
    ClueUse,
    EventTimeRelation,
    KnowledgeChange,
    StoryCluePlan,
    StoryEdge,
    StoryNode,
    StoryVariant,
)
from hastings.workspace.story_mystery import build_mystery_view
from hastings.workspace.story_structure import structure_diagnostics


def node(node_id: str, **kwargs: Any) -> StoryNode:
    return StoryNode(id=node_id, title=node_id, action="局面变化", **kwargs)


def use(
    clue_id: str,
    roles: list[str] | None = None,
    expected: str = "",
    presentation: str = "线索出现",
) -> ClueUse:
    return ClueUse(
        clue_id=clue_id,
        roles=roles or [],
        presentation=presentation,
        expected_interpretation=expected,
    )


def edge(edge_id: str, source: str, target: str) -> StoryEdge:
    return StoryEdge(id=edge_id, from_id=source, to_id=target, kind="next")


def catalog(variant: StoryVariant, titles: dict[str, str]) -> StoryClueCatalog:
    return StoryClueCatalog(
        facts={
            clue_id: StoryClueFact(
                clue_id=clue_id,
                title=title,
                plan=variant.clue_plans.get(clue_id),
                has_identity=True,
            )
            for clue_id, title in titles.items()
        }
    )


def test_actual_order_uses_event_times_then_fallback() -> None:
    variant = StoryVariant(
        id="main",
        name="顺序",
        revision="r1",
        nodes={
            "n1": node("n1"),
            "n2": node("n2"),
            "n3": node("n3"),
            "n4": node("n4", reveal_order=0),
        },
        event_times=[
            EventTimeRelation(before_node_id="n2", after_node_id="n1"),
            EventTimeRelation(before_node_id="n1", after_node_id="n3"),
        ],
    )

    view = build_mystery_view(variant, StoryClueCatalog(facts={}))

    assert view["order"] == ["n2", "n1", "n3", "n4"]
    assert view["time_status"] == {
        "state": "partial",
        "registered": 3,
        "unregistered": 1,
        "total": 4,
    }
    by_id = {item["id"]: item for item in view["nodes"]}
    assert by_id["n1"]["before_ids"] == ["n2"]
    assert by_id["n1"]["after_ids"] == ["n3"]
    assert by_id["n4"]["time_status"] == "unregistered"


def test_unregistered_nodes_follow_stage_then_reveal_order() -> None:
    variant = StoryVariant(
        id="main",
        name="阶段回退",
        revision="r1",
        stages={
            "late": {"id": "late", "title": "后段", "order": 1, "summary": None},
            "early": {"id": "early", "title": "前段", "order": 0, "summary": None},
        },
        nodes={
            "n-late": node("n-late", stage_id="late", reveal_order=0),
            "n-early": node("n-early", stage_id="early", reveal_order=0),
            "n-late-2": node("n-late-2", stage_id="late", reveal_order=1),
            "n-early-2": node("n-early-2", stage_id="early", reveal_order=1),
        },
    )

    view = build_mystery_view(variant, StoryClueCatalog(facts={}))

    assert view["order"] == ["n-early", "n-early-2", "n-late", "n-late-2"]


def test_cycle_falls_back_and_reports_diagnostic() -> None:
    variant = StoryVariant(
        id="main",
        name="循环",
        revision="r1",
        nodes={"n1": node("n1"), "n2": node("n2")},
        event_times=[
            EventTimeRelation(before_node_id="n1", after_node_id="n2"),
            EventTimeRelation(before_node_id="n2", after_node_id="n1"),
        ],
    )

    view = build_mystery_view(variant, StoryClueCatalog(facts={}))

    assert set(view["order"]) == {"n1", "n2"}
    assert view["time_status"]["registered"] == 2
    assert any(item["code"] == "STORY_TIMELINE_CYCLE" for item in view["diagnostics"])


def test_clue_projection_aggregates_plan_and_uses() -> None:
    variant = StoryVariant(
        id="main",
        name="线索",
        revision="r1",
        nodes={
            "n1": node("n1", clue_uses=[use("c1", ["plant"])]),
            "n2": node(
                "n2",
                clue_uses=[
                    use("c1", ["resolve"], "读者以为手套是父亲的"),
                    use("c4", ["misdirect"], "读者怀疑管家"),
                ],
            ),
            "n3": node("n3", clue_uses=[use("c2", ["develop"])]),
        },
        edges={"e1": edge("e1", "n1", "n2"), "e2": edge("e2", "n2", "n3")},
        event_times=[
            EventTimeRelation(before_node_id="n1", after_node_id="n2"),
            EventTimeRelation(before_node_id="n2", after_node_id="n3"),
        ],
        clue_plans={
            "c1": StoryCluePlan(
                clue_id="c1", author_truth="管家搬过尸体", resolution_intent="explain"
            ),
            "c2": StoryCluePlan(
                clue_id="c2", intention="让读者以为药被调包", resolution_intent="leave_open"
            ),
            "c3": StoryCluePlan(clue_id="c3", resolution_intent="undecided"),
        },
    )
    facts = catalog(variant, {"c1": "湿手套", "c2": "药盒"})

    view = build_mystery_view(variant, facts)
    clues = {item["clue_id"]: item for item in view["clues"]}

    assert [item["clue_id"] for item in view["clues"]] == ["c1", "c2", "c3", "c4"]
    assert clues["c1"]["title"] == "湿手套"
    assert clues["c1"]["roles"] == ["plant", "resolve"]
    assert clues["c1"]["author_truth"] == "管家搬过尸体"
    assert clues["c1"]["has_identity"] is True
    assert [item["node_id"] for item in clues["c1"]["uses"]] == ["n1", "n2"]
    assert clues["c2"]["title"] == "药盒"
    assert clues["c2"]["uses"][0]["expected_interpretation"] == ""
    assert clues["c3"]["uses"] == []
    assert clues["c4"]["title"] == "c4"
    assert clues["c4"]["has_identity"] is False
    assert clues["c4"]["roles"] == ["misdirect"]
    assert clues["c4"]["uses"][0]["node_title"] == "n2"


def test_knowledge_subjects_are_grouped() -> None:
    variant = StoryVariant(
        id="main",
        name="知情",
        revision="r1",
        nodes={
            "n1": node(
                "n1",
                knowledge_changes=[
                    KnowledgeChange(
                        subject_id="detective",
                        claim="有人进过房间",
                        stance="believes",
                        source_kind="observation",
                        effective_event_node_id="n1",
                    )
                ],
            ),
            "n2": node(
                "n2",
                knowledge_changes=[
                    KnowledgeChange(
                        subject_id="detective",
                        claim="管家搬过尸体",
                        stance="knows",
                        source_kind="observation",
                        effective_event_node_id="n2",
                    ),
                    KnowledgeChange(
                        subject_id="assistant",
                        claim="手套是干的",
                        stance="believes",
                        source_kind="observation",
                        effective_event_node_id="n2",
                    ),
                ],
            ),
        },
        edges={"e1": edge("e1", "n1", "n2")},
        event_times=[EventTimeRelation(before_node_id="n1", after_node_id="n2")],
    )

    view = build_mystery_view(variant, StoryClueCatalog(facts={}))

    subjects = {item["subject_id"]: item for item in view["subjects"]}
    assert subjects["detective"]["claims"] == 2
    assert subjects["detective"]["stances"] == ["believes", "knows"]
    assert subjects["detective"]["node_ids"] == ["n1", "n2"]
    assert subjects["assistant"]["claims"] == 1


def test_long_main_chain_with_branch_is_diagnosed() -> None:
    nodes = {f"n{index}": node(f"n{index}") for index in range(1, 21)}
    edges = {f"e{index}": edge(f"e{index}", f"n{index}", f"n{index + 1}") for index in range(1, 20)}
    edges["branch"] = edge("branch", "n18", "n20")
    variant = StoryVariant(id="main", name="长链", revision="r1", nodes=nodes, edges=edges)

    issues = structure_diagnostics(variant)
    codes = {item["code"] for item in issues}

    assert "STORY_MAIN_CHAIN_LONG" in codes
    assert "STORY_TIMELINE_EMPTY" in codes
    assert "STORY_SINGLE_CHAIN" not in codes


def test_clue_diagnostics_cover_lifecycle_reading_truth_and_unused() -> None:
    variant = StoryVariant(
        id="main",
        name="线索诊断",
        revision="r1",
        nodes={
            "n1": node("n1", clue_uses=[use("plant-only", ["plant"])]),
            "n2": node(
                "n2",
                clue_uses=[
                    use("misdirect-no-reading", ["misdirect"]),
                    use("misdirect-with-reading", ["misdirect"], "读者怀疑管家"),
                ],
            ),
            "n3": node(
                "n3",
                clue_uses=[
                    use("reinterpreted", ["reinterpret"], "读者以为是旧案"),
                    use("truth-missing", ["resolve"]),
                ],
            ),
        },
        edges={
            "e1": edge("e1", "n1", "n2"),
            "e2": edge("e2", "n2", "n3"),
        },
        clue_plans={
            "plant-only": StoryCluePlan(clue_id="plant-only", resolution_intent="undecided"),
            "misdirect-no-reading": StoryCluePlan(
                clue_id="misdirect-no-reading", resolution_intent="leave_open"
            ),
            "misdirect-with-reading": StoryCluePlan(
                clue_id="misdirect-with-reading", resolution_intent="leave_open"
            ),
            "reinterpreted": StoryCluePlan(clue_id="reinterpreted", resolution_intent="leave_open"),
            "truth-missing": StoryCluePlan(clue_id="truth-missing", resolution_intent="explain"),
            "unused": StoryCluePlan(clue_id="unused", resolution_intent="undecided"),
        },
    )

    issues = structure_diagnostics(variant)
    by_code: dict[str, list[str]] = {}
    for item in issues:
        by_code.setdefault(item["code"], []).append(item["object_id"])

    assert "plant-only" in by_code["STORY_CLUE_LIFECYCLE_INCOMPLETE"]
    assert "misdirect-no-reading" in by_code["STORY_CLUE_LIFECYCLE_INCOMPLETE"]
    assert by_code["STORY_CLUE_READING_MISSING"] == ["misdirect-no-reading"]
    assert "truth-missing" in by_code["STORY_CLUE_TRUTH_MISSING"]
    assert by_code["STORY_CLUE_UNUSED"] == ["unused"]
    assert "related_ids" in issues[0]


def test_missing_knowledge_is_diagnosed_when_clues_exist() -> None:
    variant = StoryVariant(
        id="main",
        name="知情缺项",
        revision="r1",
        nodes={
            "n1": node("n1", clue_uses=[use("c1", ["plant"])]),
        },
        clue_plans={"c1": StoryCluePlan(clue_id="c1", resolution_intent="undecided")},
    )

    codes = {item["code"] for item in structure_diagnostics(variant)}

    assert "STORY_KNOWLEDGE_MISSING" in codes


def test_empty_and_single_node_have_no_timeline_noise() -> None:
    empty = build_mystery_view(
        StoryVariant(id="main", name="空", revision="r1"), StoryClueCatalog(facts={})
    )
    assert empty["order"] == []
    assert empty["time_status"]["state"] == "complete"
    assert empty["diagnostics"] == []

    single = build_mystery_view(
        StoryVariant(
            id="main",
            name="单节点",
            revision="r1",
            nodes={"n1": node("n1")},
        ),
        StoryClueCatalog(facts={}),
    )
    assert single["order"] == ["n1"]
    assert not any(item["code"].startswith("STORY_TIMELINE") for item in single["diagnostics"])
