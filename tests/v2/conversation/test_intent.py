"""V03-7.1 结构化意图三分（讨论/候选/执行）领域规则（计划 M10 第 1–4 条）。

覆盖：关键词分类的优先级、写范围只有执行意图才非空、范围未知时最多 1–2 个
关键选择、以及「讨论/候选不得携带写范围」的硬断言。
"""

from __future__ import annotations

import pytest

from hastings.application.services.intent_service import guard_intent_write_scope
from hastings.domain.conversation.intent import (
    IntentClassification,
    classify_author_intent,
    classify_intent,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("假如姐姐是凶手会怎样", "discussion"),
        ("如果改成姐姐是凶手会怎样", "discussion"),  # 假设线索优先，不误判执行
        ("我想知道这条线索够不够", "discussion"),
        ("先看看方案", "candidate"),
        ("给我一个替代写法", "candidate"),
        ("试着写她们抵达旅馆", "candidate"),
        ("按这个改", "execution"),
        ("按这个整体修改", "execution"),
        ("", "discussion"),  # 空文本降级为最安全
        ("这段对话读起来还行", "discussion"),  # 无法判断 → discussion
    ],
)
def test_classify_intent_three_way(text: str, expected: str) -> None:
    assert classify_intent(text) == expected


def test_discussion_has_no_write_scope() -> None:
    classified = classify_author_intent("假如姐姐是凶手会怎样", target_ids=("ch1",))
    assert classified.intent == "discussion"
    assert classified.function is None
    assert classified.write_scope is None  # 讨论无写权限
    assert classified.analysis_scope == ("ch1",)  # 只读分析仍可引用
    assert classified.requires_choice is False
    assert classified.choices == ()


def test_candidate_is_readonly_proposal() -> None:
    classified = classify_author_intent("先看看方案", target_ids=("ch1", "ch2"))
    assert classified.intent == "candidate"
    assert classified.function == "draft"
    assert classified.write_scope is None  # 候选不隐式发布
    assert classified.requires_choice is False


def test_execution_scope_known_no_repeat_approval() -> None:
    classified = classify_author_intent("按这个改", target_ids=("ch1",))
    assert classified.intent == "execution"
    assert classified.function == "revision"
    assert classified.write_scope == ("ch1",)
    # 范围明确 → 不追加关键选择（同范围不重复审批）
    assert classified.requires_choice is False
    assert classified.choices == ()


def test_execution_overall_maps_holistic_revision() -> None:
    classified = classify_author_intent("按这个整体修改", target_ids=("ch1",))
    assert classified.intent == "execution"
    assert classified.function == "holistic_revision"
    assert classified.write_scope == ("ch1",)


def test_execution_scope_unknown_asks_one_choice() -> None:
    classified = classify_author_intent("按这个改")
    assert classified.intent == "execution"
    assert classified.write_scope == ()
    assert classified.requires_choice is True
    assert len(classified.choices) == 1
    assert classified.choices[0].where == "intent.scope"
    assert "覆盖" in classified.choices[0].question


def test_execution_overall_scope_unknown_asks_at_most_two() -> None:
    classified = classify_author_intent("整体修改")
    assert classified.intent == "execution"
    assert len(classified.choices) == 2  # 上限 1–2
    assert {c.where for c in classified.choices} == {"intent.scope", "intent.preserve"}


def test_classify_author_intent_dedupes_targets() -> None:
    classified = classify_author_intent("按这个改", target_ids=("ch1", "ch1", "ch2"))
    assert classified.write_scope == ("ch1", "ch2")


def test_guard_allows_execution_write_scope() -> None:
    guard_intent_write_scope(classify_author_intent("按这个改", target_ids=("ch1",)))


def test_guard_rejects_candidate_write_scope() -> None:
    """硬断言：候选/讨论绝不携带写范围（即便人为构造）。"""
    forged = IntentClassification(
        intent="candidate",
        function="draft",
        analysis_scope=("ch1",),
        write_scope=("ch1",),
        requires_choice=False,
        choices=(),
        reason="forged",
    )
    with pytest.raises(Exception) as excinfo:
        guard_intent_write_scope(forged)
    assert excinfo.value.code == "INTENT_WRITE_SCOPE_INVALID"
