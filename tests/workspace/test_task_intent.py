"""构思任务目标：讨论、结构、模块与候选确认的确定性判断。"""

from __future__ import annotations

from typing import Any

from hastings.workspace.story_context import StoryContextSnapshot
from hastings.workspace.story_models import StoryChangeSet, StoryVariant
from hastings.workspace.story_store import MAIN_VARIANT_ID, StoryStore
from hastings.workspace.task_intent import (
    CandidateSource,
    assess_story_goal,
    classify_design_intent,
    last_candidate_source,
)


def _events(*rows: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"seq": index + 1, **row} for index, row in enumerate(rows)]


def _candidate(text: str, *, multiple: bool = False) -> CandidateSource:
    return CandidateSource(
        turn_id="t1", seq=4, digest="digest", text=text, multiple=multiple
    )


def test_discussion_and_explicit_stop_do_not_target_structure() -> None:
    for text in (
        "解释一下这条线索是什么意思",
        "先只讨论，不保存",
        "不要写入构思，先分析一下",
        "这条线索加在哪里？",
    ):
        intent = classify_design_intent(text)
        assert intent.kind == "discussion", text
        assert intent.requires_structure is False


def test_direct_structure_commands_need_no_fixed_phrase() -> None:
    for text in (
        "在构思里加一个节点：雪停",
        "梳理一下线索和调查推进",
        "把对质那场的真相改成客人所留",
        "构建一条从发现异样到核对路线的线索",
    ):
        intent = classify_design_intent(text)
        assert intent.kind == "structure", text
        assert intent.allows_structure_write is True


def test_module_request_stays_module_goal() -> None:
    for text in (
        "把这段讨论保存为资料模块",
        "把这些线索存成资料",
        "新建模块记录人物小传",
    ):
        intent = classify_design_intent(text)
        assert intent.kind == "module", text
        assert intent.allows_module_write is True
        assert intent.allows_structure_write is False


def test_generic_material_words_do_not_create_a_module_goal() -> None:
    intent = classify_design_intent("把资料里的线索整理进构思")
    assert intent.kind == "structure"
    assert intent.requires_module is False


def test_creation_action_keeps_named_modules_as_sources() -> None:
    intent = classify_design_intent(
        "创建构思：根据人物设定模块和线索档案，建立发现与对质两个节点。",
        explicit_creation=True,
    )
    assert intent.kind == "structure"
    assert intent.allows_structure_write
    assert not intent.allows_module_write


def test_creation_action_with_read_only_request_requires_clarification() -> None:
    intent = classify_design_intent("创建构思：只讨论，不保存。", explicit_creation=True)
    assert intent.kind == "clarify"
    assert not intent.allows_structure_write
    assert not intent.allows_module_write


def test_both_goals_are_recorded_together() -> None:
    candidate = _candidate("建议新增线索「木板」。")
    intent = classify_design_intent(
        "写入构思，再把补充资料保存为模块", candidate=candidate
    )
    assert intent.kind == "mixed"
    assert intent.requires_structure and intent.requires_module


def test_confirmation_uses_only_the_immediately_previous_candidate() -> None:
    candidate = _candidate("建议新增线索「没有响的木板」，再连接核对路线。")
    intent = classify_design_intent("就用这个，写入构思", candidate=candidate)
    assert intent.kind == "structure"
    assert intent.candidate is candidate


def test_confirmation_without_candidate_asks_instead_of_choosing() -> None:
    intent = classify_design_intent("就用这个")
    assert intent.kind == "clarify"
    assert intent.requires_structure
    assert intent.allows_structure_write is False


def test_confirmation_with_several_candidates_asks_instead_of_choosing() -> None:
    candidate = _candidate("1. 方案一：木板\n2. 方案二：脚步", multiple=True)
    intent = classify_design_intent("用这个", candidate=candidate)
    assert intent.kind == "clarify"
    assert intent.candidate is candidate


def test_last_candidate_skips_plain_replies() -> None:
    events = _events(
        {"type": "assistant_message", "turn_id": "t1", "text": "好的，我明白了。"},
        {"type": "assistant_message", "turn_id": "t2", "text": "建议：新增线索「木板」。"},
    )
    candidate = last_candidate_source(events)
    assert candidate is not None
    assert candidate.turn_id == "t2"
    assert candidate.seq == 2
    assert last_candidate_source(events[:1]) is None


def test_two_named_proposals_are_treated_as_several() -> None:
    events = _events(
        {
            "type": "assistant_message",
            "turn_id": "t1",
            "text": "方案一：木板线索。方案二：脚步线索。",
        }
    )
    candidate = last_candidate_source(events)
    assert candidate is not None
    assert candidate.multiple is True


def test_goal_requires_a_structure_receipt_not_only_modules() -> None:
    variant = StoryVariant(
        id=MAIN_VARIANT_ID,
        name="主方案",
        revision="rev_1",
        clue_plans={},
        nodes={},
    )
    intent = classify_design_intent("写入构思并构建线索")
    modules_only = [
        {
            "kind": "module",
            "event_seq": 1,
            "saved": [{"module_id": "m1", "revision_id": "r1"}],
        }
    ]
    assessment = assess_story_goal(intent, variant=variant, receipts=modules_only)
    assert assessment.complete is False
    assert "构思结构没有提交" in assessment.missing[0]

    committed = [
        {
            "kind": "story",
            "event_seq": 2,
            "variant_id": MAIN_VARIANT_ID,
            "revision": "rev_2",
            "changed": ["node:n1"],
            "labels": ["节点「抵达」"],
        }
    ]
    assert assess_story_goal(intent, variant=variant, receipts=committed).complete


def test_goal_reports_named_targets_missing_from_commit(tmp_path) -> None:
    store = StoryStore(tmp_path / "state")
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        request_key="seed",
        change=StoryChangeSet(
            id="seed",
            base_revision="empty",
            operations=[
                {"op": "add_node", "node": {"id": "n1", "title": "抵达", "action": "进门"}},
                {"op": "add_node", "node": {"id": "n2", "title": "对质", "action": "问话"}},
            ],
        ),
    )
    variant = store.require_variant(MAIN_VARIANT_ID)
    intent = classify_design_intent("把「对质」那一场改成他先沉默")
    receipts = [
        {
            "kind": "story",
            "event_seq": 3,
            "variant_id": MAIN_VARIANT_ID,
            "revision": "rev_2",
            "changed": ["node:n1"],
        }
    ]
    assessment = assess_story_goal(intent, variant=variant, receipts=receipts)
    assert assessment.complete is False
    assert "对质" in assessment.note()


def test_noop_receipt_covers_named_target(tmp_path) -> None:
    store = StoryStore(tmp_path / "state")
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        request_key="seed",
        change=StoryChangeSet(
            id="seed",
            base_revision="empty",
            operations=[
                {"op": "add_node", "node": {"id": "n1", "title": "抵达", "action": "进门"}},
            ],
        ),
    )
    variant = store.require_variant(MAIN_VARIANT_ID)
    intent = classify_design_intent("把「抵达」加一个细节")
    receipts = [
        {
            "kind": "story_noop",
            "event_seq": 4,
            "variant_id": MAIN_VARIANT_ID,
            "revision": variant.revision,
            "objects": [{"kind": "node", "id": "n1", "label": "节点「抵达」"}],
        }
    ]
    assert assess_story_goal(intent, variant=variant, receipts=receipts).complete


def test_module_partial_save_is_not_a_completed_module_goal() -> None:
    intent = classify_design_intent("把这段讨论保存为资料模块")
    receipts = [
        {
            "kind": "module_partial",
            "event_seq": 1,
            "saved": [{"module_id": "m1", "revision_id": "r1"}],
        }
    ]
    assessment = assess_story_goal(intent, variant=None, receipts=receipts)
    assert assessment.complete is False
    assert "资料模块没有保存" in assessment.note()


def test_mixed_goal_requires_both_receipts() -> None:
    variant = StoryVariant(id=MAIN_VARIANT_ID, name="主方案", revision="rev_1")
    intent = classify_design_intent(
        "写入构思，再把补充资料保存为模块", candidate=_candidate("建议新增线索「木板」。")
    )
    story = [
        {
            "kind": "story",
            "event_seq": 1,
            "variant_id": MAIN_VARIANT_ID,
            "revision": "rev_2",
            "changed": ["clue:board"],
        }
    ]
    assessment = assess_story_goal(intent, variant=variant, receipts=story)
    assert assessment.complete is False
    assert "资料模块没有保存" in assessment.note()

    complete = [
        *story,
        {
            "kind": "module",
            "event_seq": 2,
            "saved": [{"module_id": "m1", "revision_id": "r1"}],
        },
    ]
    assert assess_story_goal(intent, variant=variant, receipts=complete).complete


def test_discussion_has_no_write_goal() -> None:
    variant = StoryVariant(id=MAIN_VARIANT_ID, name="主方案", revision="rev_1")
    intent = classify_design_intent("解释一下这条线索")
    assert assess_story_goal(intent, variant=variant, receipts=[]).complete


def test_structure_readonly_does_not_require_commit() -> None:
    intent = classify_design_intent("修改线索「手套」的呈现")
    assert assess_story_goal(
        intent, variant=None, receipts=[], structure_readonly=True
    ).complete


def test_snapshot_identity_is_not_changed_by_intent() -> None:
    frozen = StoryContextSnapshot(
        variant_id=MAIN_VARIANT_ID, revision="empty", purpose="design"
    )
    assert frozen.revision == "empty"
