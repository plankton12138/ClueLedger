"""写后自查三层与强制对账流程测试（v1.1 M5，Q3-D）。

契约（q0-contracts 第 3、7 节；开发计划 M5）：起草候选必带三层自查，
缺失按 ``SELF_CHECK_MISSING_CONFLICT`` 失败归因；起草提示词第一步核对
目标章与台账、缺章改为补缺；自查如实标注「模型自检」。
"""

from __future__ import annotations

import json

from hastings.prompts.self_check import (
    SELF_CHECK_MARKER,
    SELF_CHECK_TEMPLATE_VERSION,
    prose_draft_system_prompt,
    prose_draft_user_prompt,
    self_check_error,
    split_self_check,
)

_FULL = (
    "# 第三章\n\n林砚推开门，铜哨还挂在他脖子上。\n\n"
    f"{SELF_CHECK_MARKER}\n"
    "1. 必发生核对：铜哨出现——「铜哨还挂在他脖子上」。\n"
    "2. 铁律自查：不写超自然力量——本章无违例。\n"
    "3. 读者认知：误读（管家是凶手）立住；建议：把铜哨的特写提前一段。"
)


def test_template_version_is_registered() -> None:
    assert SELF_CHECK_TEMPLATE_VERSION == 1
    system = prose_draft_system_prompt()
    assert "核对目标章与正文台账" in system
    assert "### 自查" in system


def test_prose_prompt_injects_layers_and_reconcile() -> None:
    user = prose_draft_user_prompt(
        structure_text="### 铁律\n不写超自然力量",
        ledger_text="### 正文台账（系统事实，只读）",
        moves="- 铜哨交给林砚",
        prose="# 第三章\n",
        quote="",
        note="写快一点",
        continue_mode=False,
        target_chapter=3,
    )
    assert "写作目标：第 3 章" in user
    assert "不写超自然力量" in user  # 铁律注入
    assert "正文台账" in user
    assert "### 自查" in user
    assert "作者补充：写快一点" in user


def test_redirect_rewrites_target_in_prompt() -> None:
    user = prose_draft_user_prompt(
        structure_text="",
        ledger_text="改派第 1 章",
        moves="",
        prose="",
        quote="",
        note="",
        continue_mode=False,
        redirected_to=1,
        target_chapter=3,
    )
    assert "改为补写第 1 章" in user
    assert "不写原目标章" in user


def test_split_self_check_extracts_three_layers() -> None:
    block = split_self_check(_FULL)
    assert block.present is True
    assert block.prose.startswith("# 第三章")
    assert block.checklist_items and "铜哨出现" in block.checklist_items[0]
    assert block.iron_rule_items and "超自然" in block.iron_rule_items[0]
    assert "误读" in block.reader_note and "建议" in block.reader_note
    rows = block.to_rows()
    assert rows["present"] is True


def test_missing_marker_is_self_check_missing() -> None:
    block = split_self_check("# 第三章\n\n只有正文，没有自查。")
    assert block.present is False
    assert "铜哨" not in block.prose or "只有正文" in block.prose


def test_partial_self_check_is_missing() -> None:
    """三层缺任何一层都按缺失归因；不静默降级。"""
    partial = f"正文。\n\n{SELF_CHECK_MARKER}\n1. 必发生核对：铜哨出现。\n2. 铁律自查：无违例。\n"
    assert split_self_check(partial).present is False


def test_self_check_error_carries_contract_code() -> None:
    error = self_check_error("creative-assist")
    assert error.code == "SELF_CHECK_MISSING_CONFLICT"


def test_block_rows_are_json_serializable() -> None:
    payload = json.dumps(split_self_check(_FULL).to_rows(), ensure_ascii=False)
    assert "present" in payload
