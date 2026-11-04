"""v1.2 M2：无设定卡门槛的结构起步提示与结构草稿检查。"""

from __future__ import annotations

from hastings.workspace.structure_startup import (
    STARTUP_PROMPT_VERSION,
    STARTUP_QUESTION_LIMIT,
    STRUCTURE_PROMPT_VERSION,
    check_first_structure,
    guided_reply_report,
    startup_conversation_prompt,
    structure_draft_prompt,
)


def test_guided_reply_report_counts_questions() -> None:
    one = guided_reply_report("故事发生在哪里？")
    assert one["questions"] == 1 and one["single_question"] and one["within_limit"]
    many = guided_reply_report("在哪里？谁死了？几点？")
    assert many["questions"] == 3
    assert not many["single_question"] and many["note"]
    within = guided_reply_report("？" * STARTUP_QUESTION_LIMIT)
    assert within["within_limit"]
    over = guided_reply_report("？" * (STARTUP_QUESTION_LIMIT + 1))
    assert not over["within_limit"]


def test_startup_conversation_prompt_modes_differ() -> None:
    assert STARTUP_PROMPT_VERSION == 2
    guided_sys, guided_open = startup_conversation_prompt("guided")
    free_sys, free_open = startup_conversation_prompt("freeform")
    assert "一次只问一个" in guided_open
    assert str(STARTUP_QUESTION_LIMIT) in guided_open
    assert "跳过" in guided_open
    assert "STRUCTURE.md" in guided_sys and "STRUCTURE.md" in free_sys
    assert "设定卡" not in guided_sys and "设定卡" not in free_sys
    assert "直接描述想法" in free_open
    assert "一次只问一个" not in free_open


def test_structure_draft_prompt_uses_author_request_directly() -> None:
    request = "写一部暴雪旅馆故事，先保留凶手未知。"
    system, instructions = structure_draft_prompt(request)
    assert STRUCTURE_PROMPT_VERSION == 2
    assert "STRUCTURE.md" in system
    assert request in instructions
    assert "## 铁律" in instructions
    assert "write_structure" not in instructions
    assert "设定卡" not in system
    assert "不在本步骤自动创建" in instructions


def test_check_first_structure_accepts_wellformed() -> None:
    text = (
        "## 铁律\n第三人称限制视角。\n凶手不在未出场人物中。\n\n"
        "## 路线\n暴雪之夜逐步收束。\n\n## 骨架\n1. 抵达旅馆。\n"
    )
    report = check_first_structure(text)
    assert report.ok
    assert report.iron_rules.rules == ("第三人称限制视角。", "凶手不在未出场人物中。")


def test_check_first_structure_flags_missing_marker() -> None:
    report = check_first_structure("# 我的作品\n## 铁律\n规则\n")
    assert not report.iron_rules.ok
    assert not report.ok


def test_check_first_structure_flags_over_budget() -> None:
    text = "## 铁律\n规则\n" + "x" * 7000
    report = check_first_structure(text)
    assert report.iron_rules.ok
    assert not report.budget.ok
    assert not report.ok
