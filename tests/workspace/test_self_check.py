"""写后自查四层与强制对账流程测试（v1.1 M5，Q3-D；v1.3 P2 扩展第四层）。

契约（q0-contracts 第 3、7 节；开发计划 M5；v1.3 提案 §5.2）：起草候选必带
四层自查，缺失按 ``SELF_CHECK_MISSING_CONFLICT`` 失败归因，只缺线索核对层时按
``SELF_CHECK_CLUE_MISSING``；起草提示词第一步核对目标章与台账、缺章改为补缺；
自查如实标注「模型自检」。
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
    "3. 读者认知：误读（管家是凶手）立住；建议：把铜哨的特写提前一段。\n"
    "4. 线索核对：CL-01 箱扣缺漆按计划推进，回收动作与登记一致。"
)


def test_template_version_is_registered() -> None:
    assert SELF_CHECK_TEMPLATE_VERSION == 4
    system = prose_draft_system_prompt()
    assert "核对目标章与正文台账" in system
    assert "### 自查" in system


def test_checklist_layer_does_not_invite_missing_module_report() -> None:
    """空必发生清单不许引导模型报「模块缺失、建议补骨架」（真模型探针实测）。"""
    user = prose_draft_user_prompt(
        structure_text="",
        ledger_text="",
        moves="",
        prose="",
        quote="",
        note="",
        continue_mode=False,
    )

    assert "本章无必发生项" in user
    assert "不要建议补骨架" in user
    assert "建议补骨架或模块" not in user
    # 计划外线索登记（P5）：固定小节与三段字段。
    assert "### 线索登记" in user
    assert "类型｜名称与为什么埋在这里｜「正文里的短引」" in user


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
    assert "线索核对" in user  # 第四层要求随提示下发
    assert "作者补充：写快一点" in user


def test_clue_context_is_injected_before_moves() -> None:
    user = prose_draft_user_prompt(
        structure_text="",
        ledger_text="",
        moves="- 铜哨交给林砚",
        prose="",
        quote="",
        note="",
        continue_mode=False,
        clue_context="### 线索清单（系统事实，只读）\n【未回收线索】\n- CL-01 箱扣缺漆",
        target_chapter=3,
    )
    assert "### 线索清单" in user
    assert user.index("### 线索清单") < user.index("本章已入账信息")


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


def test_split_self_check_extracts_four_layers() -> None:
    block = split_self_check(_FULL)
    assert block.present is True
    assert block.prose.startswith("# 第三章")
    assert block.checklist_items and "铜哨出现" in block.checklist_items[0]
    assert block.iron_rule_items and "超自然" in block.iron_rule_items[0]
    assert "误读" in block.reader_note and "建议" in block.reader_note
    assert block.clue_items and "CL-01" in block.clue_items[0]
    rows = block.to_rows()
    assert rows["present"] is True
    assert rows["missing_layers"] == []


def test_missing_marker_is_self_check_missing() -> None:
    block = split_self_check("# 第三章\n\n只有正文，没有自查。")
    assert block.present is False
    assert block.missing_layers == ("必发生", "铁律", "读者认知", "线索核对")
    assert "铜哨" not in block.prose or "只有正文" in block.prose


def test_partial_self_check_is_missing() -> None:
    """缺任何一层都按缺失归因；不静默降级。"""
    partial = f"正文。\n\n{SELF_CHECK_MARKER}\n1. 必发生核对：铜哨出现。\n2. 铁律自查：无违例。\n"
    block = split_self_check(partial)
    assert block.present is False
    assert block.missing_layers == ("读者认知", "线索核对")


def test_only_clue_layer_missing_gets_dedicated_code() -> None:
    partial = (
        "正文。\n\n"
        f"{SELF_CHECK_MARKER}\n"
        "1. 必发生核对：铜哨出现。\n"
        "2. 铁律自查：无违例。\n"
        "3. 读者认知：误读立住。\n"
    )
    block = split_self_check(partial)
    assert block.present is False
    assert block.missing_layers == ("线索核对",)
    error = self_check_error("creative-assist", block.missing_layers)
    assert error.code == "SELF_CHECK_CLUE_MISSING"
    assert "线索核对" in error.why


def test_self_check_error_carries_contract_code() -> None:
    error = self_check_error("creative-assist")
    assert error.code == "SELF_CHECK_MISSING_CONFLICT"


def test_block_rows_are_json_serializable() -> None:
    payload = json.dumps(split_self_check(_FULL).to_rows(), ensure_ascii=False)
    assert "present" in payload
    assert "clue_items" in payload


def test_bold_layer_headers_are_parsed() -> None:
    """真模型常用加粗层头：只认行首层名，不把 ``**`` 留成条目。"""
    text = (
        "正文。\n\n"
        f"{SELF_CHECK_MARKER}\n\n"
        "**必发生核对**\n- 铜哨出现。\n"
        "**铁律自查**\n- 无违例。\n"
        "**读者认知**\n- 误读立住。\n"
        "**线索核对**\n- CL-01 已回收。\n"
    )
    block = split_self_check(text)
    assert block.present is True
    assert block.missing_layers == ()
    assert block.checklist_items == ("- 铜哨出现。",)
    assert block.clue_items == ("- CL-01 已回收。",)


def test_layer_content_mentioning_another_layer_stays_put() -> None:
    """层内正文提到「读者」不等于新层头：条目不能被截断。"""
    text = (
        "正文。\n\n"
        f"{SELF_CHECK_MARKER}\n"
        "1. 必发生核对：手套出现。\n"
        "2. 铁律自查：无违例。\n"
        "3. 读者认知：误读立住。\n"
        "4. 线索核对：CL-02 已回收。\n"
        "- 严格说，读者的推理仍需 CL-03 补实。\n"
    )
    block = split_self_check(text)
    assert block.present is True
    assert len(block.clue_items) == 2
    assert "CL-02" in block.clue_items[0]
    assert "CL-03" in block.clue_items[1]
