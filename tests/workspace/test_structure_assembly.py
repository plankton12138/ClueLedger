"""结构层注入装配的纯函数测试（v1.1 M3，Q2-A）。

契约依据：``dev-notes/v1.1/notes/q0-contracts.md`` 第 3、4 节——
铁律标记丢失降级全文注入并标注；台账与结构「已写」标记冲突以台账为准；
对账缺章默认改派、作者明确跳过才放行；宽松机检不判失败、只报相对偏差。
"""

from __future__ import annotations

from hastings.workspace.prose_ledger import ProseLedger, reconcile
from hastings.workspace.structure_assembly import (
    STYLE_PLACEHOLDER,
    LooseCheckRow,
    StructureLayers,
    StructureSnapshot,
    chapter_number_of,
    ledger_section,
    loose_keyword_check,
    reconcile_note,
    split_structure_layers,
    structure_section_text,
)

_STRUCTURE = """\
## 铁律
凶手必须在结局前出现三次
不写超自然力量

## 路线
三幕：雨夜→追查→对峙

## 骨架
- 第 1 章：雨夜发现尸体
- 第 2 章：警方介入
"""


def test_split_layers_extracts_iron_rules_and_body() -> None:
    layers = split_structure_layers(_STRUCTURE)

    assert layers.degraded is False
    assert layers.iron_rules == ("凶手必须在结局前出现三次", "不写超自然力量")
    assert "## 路线" in layers.body
    assert "铁律" not in layers.body


def test_missing_marker_degrades_to_full_text() -> None:
    text = "# 我的书\n\n三幕结构，主角是法医。"

    layers = split_structure_layers(text)

    assert layers.degraded is True
    assert layers.iron_rules == ()
    assert "三幕结构" in layers.body
    assert "# 我的书" in layers.body


def test_empty_text_is_an_empty_layer_not_degraded() -> None:
    layers = split_structure_layers("   \n ")

    assert layers == StructureLayers(iron_rules=(), body="", degraded=False)


def test_section_text_includes_all_three_layers() -> None:
    text = structure_section_text(split_structure_layers(_STRUCTURE))

    assert "### 铁律" in text
    assert "- 凶手必须在结局前出现三次" in text
    assert "### 全书结构" in text
    assert "雨夜发现尸体" in text
    assert "### 风格（STYLE.md）" in text
    assert STYLE_PLACEHOLDER in text


def test_degraded_section_states_the_loss_and_injects_full_text() -> None:
    text = structure_section_layers_text("# 我的书\n\n三幕结构。")

    assert "标记「## 铁律」丢失" in text
    assert "三幕结构" in text
    assert STYLE_PLACEHOLDER in text


def structure_section_layers_text(text: str) -> str:
    return structure_section_text(split_structure_layers(text))


def test_empty_structure_still_keeps_style_placeholder() -> None:
    text = structure_section_text(split_structure_layers(""))

    assert "### 铁律" not in text
    assert "### 全书结构" not in text
    assert STYLE_PLACEHOLDER in text


def test_ledger_section_empty_ledger_is_empty_text() -> None:
    assert ledger_section(ProseLedger(chapters={})) == ""


def test_ledger_section_lists_chapters_and_priority() -> None:
    ledger = ProseLedger(chapters={1: "rev-aaaaaaaa-1", 2: "rev-bbbbbbbb-2"})

    text = ledger_section(ledger)

    assert "第 1 章｜最近入档修订 rev-aaaa" in text
    assert "第 2 章｜最近入档修订 rev-bbbb" in text
    assert "以本台账为准" in text
    assert "模型只读" in text


def test_reconcile_note_redirect_explains_the_reassignment() -> None:
    decision = reconcile(ProseLedger(chapters={3: "rev-c"}), 4)

    assert decision.action == "redirect"
    text = reconcile_note(decision)
    assert "第 1 章" in text
    assert "不要写第 4 章" in text
    assert "按第 1 章推进" in text


def test_reconcile_note_plain_write_injects_nothing() -> None:
    decision = reconcile(ProseLedger(chapters={1: "r1", 2: "r2"}), 3)

    assert decision.action == "write"
    assert decision.skipped == ()
    assert reconcile_note(decision) == ""
    assert reconcile_note(None) == ""


def test_reconcile_note_records_author_confirmed_skip() -> None:
    decision = reconcile(ProseLedger(chapters={}), 3, allow_skip=True)

    assert decision.action == "write"
    assert decision.skipped == (1, 2)
    text = reconcile_note(decision)
    assert "作者明确跳过" in text
    assert "第 1、2 章" in text


def test_chapter_number_parses_prose_paths_only() -> None:
    assert chapter_number_of("正文/0003 雨夜.md") == 3
    assert chapter_number_of("正文/0012.md") == 12
    assert chapter_number_of("STRUCTURE.md") is None
    assert chapter_number_of("设定集/线索.md") is None
    assert chapter_number_of("正文/草稿.md") is None


def test_loose_keyword_check_reports_hits_and_missing_without_verdict() -> None:
    prose = "雨夜里，陈默推开了旅馆的门。铜哨还挂在他的脖子上。"
    rows = loose_keyword_check(
        [("铜哨出现", ["铜哨"]), ("旅馆场景", ["旅馆"]), ("无关项", ["雪崩"])],
        prose,
    )

    assert [row.hits for row in rows] == [("铜哨",), ("旅馆",), ()]
    assert rows[0].missing == ()
    assert rows[2].missing == ("雪崩",)
    # 不判失败：没有 pass/fail 字段，行对象只有命中与缺漏。
    assert isinstance(rows[0], LooseCheckRow)


def test_loose_keyword_check_is_whitespace_and_case_tolerant() -> None:
    rows = loose_keyword_check(
        [("铜哨", ["铜 哨"]), ("whisper", ["Whisper"])],
        "铜\n哨在 whispers 中响起",
    )

    assert all(row.hits == row.keywords for row in rows)


def test_loose_keyword_check_skips_blank_keywords() -> None:
    rows = loose_keyword_check([("铜哨出现", ["铜哨", "  "])], "铜哨")

    assert rows[0].keywords == ("铜哨",)
    assert rows[0].hits == ("铜哨",)


def test_snapshot_to_dict_shape() -> None:
    snapshot = StructureSnapshot(
        structure_injected=True,
        iron_rules_count=2,
        ledger_injected=True,
        ledger_chapters=(1, 2),
        reconcile_action="redirect",
        reconcile_target=4,
        reconcile_redirected_to=1,
        modules_selected=("clues-main",),
        checklist=(LooseCheckRow(item="铜哨出现", keywords=("铜哨",), hits=()),),
    )

    payload = snapshot.to_dict()
    assert payload["structure_injected"] is True
    assert payload["iron_rules_degraded"] is False
    assert payload["reconcile"] == {
        "action": "redirect",
        "target": 4,
        "redirected_to": 1,
        "skipped": [],
    }
    assert payload["modules_selected"] == ["clues-main"]
    assert payload["checklist"] == [
        {"item": "铜哨出现", "keywords": ["铜哨"], "hits": [], "missing": ["铜哨"]}
    ]


def test_snapshot_without_reconcile_omits_the_block() -> None:
    payload = StructureSnapshot(structure_injected=False).to_dict()

    assert payload["reconcile"] is None
    assert payload["ledger_chapters"] == []
    assert payload["checklist"] == []
