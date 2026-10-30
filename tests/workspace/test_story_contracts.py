"""1.0.1 P0：共享样例与结构契约一致。

样例是前后端对齐的唯一样本来源：格式改动时先让这里失败，再改实现。这些用例不判断
故事好不好，只判断「写进去的东西能被原样读出来，且不该写的写不进去」。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from hastings.workspace.errors import WorkspaceError, status_for_code
from hastings.workspace.story_changes import StoryInvalid, apply_change_set, diff_variant
from hastings.workspace.story_models import (
    AddStage,
    ProseBinding,
    RemoveStage,
    StoryChangeSet,
    StoryStage,
    StoryVariant,
    UpdateNode,
    validate_story_variant,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "story"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def variant(name: str) -> StoryVariant:
    item = StoryVariant.model_validate(load(name))
    validate_story_variant(item)
    return item


def test_blank_mystery_has_no_prose_and_allows_undecided_truth() -> None:
    item = variant("blank-mystery.json")
    assert not item.bindings
    assert item.clue_plans["c1"].author_truth is None
    assert item.clue_plans["c1"].resolution_intent == "undecided"
    assert item.clue_plans["c2"].resolution_intent == "leave_open"
    assert item.nodes["n1"].clue_uses[0].mode == "narration"


def test_stage_order_and_membership_are_explicit() -> None:
    item = variant("blank-mystery.json")
    assert item.stages["s1"].order == 0
    assert {node.stage_id for node in item.nodes.values()} == {"s1"}

    raw = load("blank-mystery.json")
    raw["stages"]["s2"] = {"id": "s2", "title": "第二幕", "order": 0}
    with pytest.raises(ValueError, match="阶段顺序不能重复"):
        StoryVariant.model_validate(raw)

    raw = load("blank-mystery.json")
    raw["nodes"]["n1"]["stage_id"] = "missing"
    with pytest.raises(ValueError, match="节点引用了不存在的阶段"):
        StoryVariant.model_validate(raw)


def test_next_edge_cannot_return_to_an_earlier_stage() -> None:
    raw = load("blank-mystery.json")
    raw["stages"]["s2"] = {"id": "s2", "title": "第二幕", "order": 1}
    raw["nodes"]["n2"]["stage_id"] = "s2"
    raw["edges"]["e3"] = {
        "id": "e3", "from_id": "n2", "to_id": "n1", "kind": "next", "reason": "回退"
    }
    item = StoryVariant.model_validate(raw)
    with pytest.raises(ValueError, match="较后阶段"):
        validate_story_variant(item)


def test_removing_a_stage_requires_reassigning_its_nodes_first() -> None:
    base = variant("blank-mystery.json")
    change = StoryChangeSet.model_validate(
        {
            "id": "change_remove_stage",
            "base_revision": base.revision,
            "operations": [{"op": "remove_stage", "stage_id": "s1"}],
        }
    )
    with pytest.raises(StoryInvalid, match="删除阶段前"):
        apply_change_set(base, change)

    updates = [
        UpdateNode(node_id=node.id, node=node.model_copy(update={"stage_id": None}))
        for node in base.nodes.values()
    ]
    change = StoryChangeSet(
        id="change_remove_stage_after_move",
        base_revision=base.revision,
        operations=[*updates, RemoveStage(stage_id="s1")],
    )
    updated = apply_change_set(base, change)
    assert not updated.stages
    assert set(updated.nodes) == set(base.nodes)


def test_stage_roundtrip_through_diff() -> None:
    base = variant("blank-mystery.json")
    change = StoryChangeSet(
        id="change_add_stage",
        base_revision=base.revision,
        operations=[
            AddStage(stage=StoryStage(id="s2", title="第二幕", order=1)),
            UpdateNode(
                node_id="n3",
                node=base.nodes["n3"].model_copy(update={"stage_id": "s2"}),
            ),
        ],
    )
    updated = apply_change_set(base, change)
    assert updated.nodes["n3"].stage_id == "s2"
    inverse = diff_variant(updated, base)
    restored = apply_change_set(
        updated,
        StoryChangeSet(id="change_undo_stage", base_revision=updated.revision, operations=inverse),
    )
    assert set(restored.stages) == set(base.stages)
    assert restored.nodes["n3"].stage_id == "s1"


def test_long_distance_clue_is_planted_first_and_resolved_last() -> None:
    item = variant("long-distance.json")
    assert len(item.nodes) == 24
    planted = [node for node in item.nodes.values() if node.clue_uses]
    assert {node.id for node in planted} == {"n01", "n24"}
    resolve_nodes = [
        node.id
        for node in item.nodes.values()
        if any(use.clue_id == "c1" and "resolve" in use.roles for use in node.clue_uses)
    ]
    assert resolve_nodes == ["n24"]
    assert item.clue_plans["c1"].resolution_intent == "explain"
    assert item.members("t1")[0] == "n01"
    assert item.members("t2")[-1] == "n24"
    assert "n13" in item.members("t1") and "n13" in item.members("t2")


def test_flashback_keeps_disclosure_order_and_event_time_separate() -> None:
    item = variant("flashback.json")
    reframe = item.edges["e2"]
    assert reframe.kind == "reframe"
    assert reframe.from_id == "f2" and reframe.to_id == "f1"
    # 揭示顺序 f1 → f2，实际时间 f2 在前；两者不冲突。
    assert item.edges["e1"].kind == "next"
    assert item.event_times[0].before_node_id == "f2"


def test_testimony_keeps_truth_and_belief_apart() -> None:
    item = variant("testimony.json")
    use = item.nodes["t2"].clue_uses[0]
    assert use.mode == "testimony"
    assert use.speaker_id == "管家"
    change = item.nodes["t2"].knowledge_changes[0]
    assert change.stance == "believes"
    assert item.clue_plans["c1"].author_truth == "信是管家自己藏起来的"
    assert change.claim != item.clue_plans["c1"].author_truth


def test_alternative_records_its_baseline() -> None:
    item = variant("alternative.json")
    assert item.base_variant_id == "main"
    assert item.base_revision == "rev_blank_1"
    main = variant("blank-mystery.json")
    assert item.nodes["n1"].title == main.nodes["n1"].title
    assert item.clue_plans["c2"].author_truth != main.clue_plans["c2"].author_truth


def test_emoji_and_crlf_positions_use_code_points() -> None:
    payload = load("emoji-positions.json")
    content = payload["content"]
    normalized = content.replace("\r\n", "\n")
    binding = ProseBinding.model_validate(payload["binding"])
    assert hashlib.sha256(content.encode("utf-8")).hexdigest() == binding.digest
    assert normalized[binding.start : binding.end] == binding.quote
    assert binding.end - binding.start == len(binding.quote)
    # UTF-16 下标会把 emoji 算成两个，直接用它会切错；这里固定这条差异。
    assert len(content.encode("utf-16-le")) // 2 > len(normalized)


def test_change_set_applies_to_the_blank_fixture() -> None:
    base = variant("blank-mystery.json")
    change = StoryChangeSet.model_validate(load("change-set.json"))
    assert change.base_revision == base.revision
    updated = apply_change_set(base, change)
    assert "n4" in updated.nodes
    assert updated.clue_plans["c1"].resolution_intent == "explain"
    assert any(
        use.clue_id == "c1" and "resolve" in use.roles
        for use in updated.nodes["n4"].clue_uses
    )
    assert updated.constraints["k2"].strength == "preference"
    assert updated.edges["e3"].kind == "next"
    # 原样例不被修改：应用是纯函数。
    assert "n4" not in base.nodes
    inverse = diff_variant(updated, base)
    restored = apply_change_set(updated, StoryChangeSet(
        id="change_undo", base_revision=updated.revision, operations=inverse
    ))
    assert set(restored.nodes) == set(base.nodes)
    assert restored.clue_plans["c1"].resolution_intent == "undecided"


def test_error_sample_matches_the_shared_message_format() -> None:
    payload = load("error.json")
    error = WorkspaceError(
        code=payload["code"],
        where="story.change",
        why="推进顺序存在循环",
        fix="按提示补齐目标或关系后重新提交",
        details=payload["details"],
    )
    assert error.message == payload["message"]
    assert status_for_code(payload["code"]) == payload["status"]


def test_unknown_fields_are_rejected() -> None:
    raw = load("blank-mystery.json")
    raw["nodes"]["n1"]["confidence"] = 0.8
    with pytest.raises(ValueError):
        StoryVariant.model_validate(raw)


def test_dangling_reference_is_rejected() -> None:
    raw = load("blank-mystery.json")
    raw["nodes"]["n1"]["thread_ids"] = ["missing"]
    with pytest.raises(ValueError, match="节点引用了不存在的分支"):
        StoryVariant.model_validate(raw)


def test_self_reference_is_rejected() -> None:
    raw = load("flashback.json")
    raw["edges"]["e1"]["to_id"] = "f1"
    with pytest.raises(ValueError, match="节点不能指向自己"):
        StoryVariant.model_validate(raw)


def test_resolution_intent_and_roles_must_agree() -> None:
    raw = load("blank-mystery.json")
    raw["clue_plans"]["c1"]["resolution_intent"] = "explain"
    with pytest.raises(ValueError, match="明确解释的线索需要至少一个安排为回收的节点"):
        StoryVariant.model_validate(raw)

    raw = load("blank-mystery.json")
    raw["clue_plans"]["c2"]["resolution_intent"] = "leave_open"
    raw["nodes"]["n2"]["clue_uses"][0]["roles"] = ["resolve"]
    with pytest.raises(ValueError, match="未计划解释或仍待定的线索不能安排回收作用"):
        StoryVariant.model_validate(raw)


def test_change_set_must_change_something() -> None:
    with pytest.raises(ValueError, match="变更没有任何操作"):
        StoryChangeSet(id="c1", base_revision="rev_1", operations=[])
    prose_only = StoryChangeSet(
        id="c2",
        base_revision="rev_1",
        prose_patches=[
            {
                "path": "正文/0001.md",
                "base_digest": "d" * 64,
                "start": 0,
                "end": 0,
                "replacement": "新的一段",
                "expected": "",
            }
        ],
    )
    assert prose_only.operations == []


def test_removing_a_thread_only_clears_membership() -> None:
    from hastings.workspace.story_models import RemoveThread

    base = variant("blank-mystery.json")
    change = StoryChangeSet(
        id="remove-thread",
        base_revision=base.revision,
        operations=[RemoveThread(thread_id="t1")],
    )
    updated = apply_change_set(base, change)
    assert not updated.threads
    assert set(updated.nodes) == set(base.nodes)
    assert all(node.thread_ids == [] for node in updated.nodes.values())
    inverse = diff_variant(updated, base)
    restored = apply_change_set(
        updated,
        StoryChangeSet(id="restore-thread", base_revision=updated.revision, operations=inverse),
    )
    assert "t1" in restored.threads
    assert restored.nodes["n1"].thread_ids == ["t1"]
