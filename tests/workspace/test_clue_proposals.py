"""v1.3 P5：模型侧线索登记（解析、定位、编号与拒绝）。"""

from __future__ import annotations

from hastings.workspace.chapter_clues import ClueEntry, resolve_anchor
from hastings.workspace.clue_proposals import (
    ClueProposal,
    materialize_proposals,
    next_clue_id,
    split_clue_proposals,
)

PROSE = (
    "林砚推开门，柜台上放着一双湿手套。\n\n"
    "老人从抽屉里摸出一把钥匙，铜牌上缺了一角。\n\n"
    "墙上的钟停在三点十分。"
)


def test_split_takes_section_out_and_parses_three_fields() -> None:
    text = (
        "林砚推开门。\n\n### 自查\n1. 必发生核对：无。\n"
        "### 线索登记\n"
        "- 伏笔｜铜牌缺角｜老人开抽屉时露出，先埋下｜「老人从抽屉里摸出一把钥匙，铜牌上缺了一角」\n"
        "- 物件｜停摆的钟｜时间线可疑｜「墙上的钟停在三点十分」"
    )

    body, proposals, rejects = split_clue_proposals(text)

    assert "### 线索登记" not in body
    assert "### 自查" in body
    assert rejects == []
    assert [p.kind for p in proposals] == ["伏笔", "物件"]
    assert proposals[0].note == "铜牌缺角｜老人开抽屉时露出，先埋下"
    assert proposals[0].quote.startswith("老人从抽屉里")


def test_split_accepts_half_width_separator_and_bullets() -> None:
    _, proposals, rejects = split_clue_proposals(
        "### 线索登记\n1. 悬念 | 钟停了 | 时间不对 | 「墙上的钟停在三点十分」"
    )

    assert rejects == []
    assert proposals[0].kind == "悬念"
    assert proposals[0].quote == "墙上的钟停在三点十分"


def test_split_reports_bad_lines_with_reasons() -> None:
    _, proposals, rejects = split_clue_proposals(
        "### 线索登记\n"
        "- 伏笔｜只有两段\n"
        "- 传闻｜类型不在清单｜「墙上的钟停在三点十分」\n"
        "- 物件｜没有引文｜\n"
    )

    assert proposals == []
    assert len(rejects) == 3
    assert "字段不足" in rejects[0]
    assert "类型" in rejects[1]
    assert "字段不足" in rejects[2] or "短引" in rejects[2]


def test_split_without_section_returns_text_unchanged() -> None:
    body, proposals, rejects = split_clue_proposals("正文。\n\n### 自查\n1. 必发生核对：无。")

    assert body.startswith("正文。")
    assert proposals == [] and rejects == []


def test_next_clue_id_follows_existing_max() -> None:
    assert next_clue_id([]) == "CL-01"
    assert next_clue_id({"CL-02", "CL-07"}) == "CL-08"


def test_materialize_anchors_quote_to_its_paragraph() -> None:
    entries, refusals = materialize_proposals(
        [ClueProposal(kind="伏笔", note="铜牌缺角", quote="铜牌上缺了一角")],
        PROSE,
        revision="candidate:c1",
        taken={"CL-02"},
    )

    assert refusals == []
    assert len(entries) == 1
    entry = entries[0]
    assert entry.id == "CL-03"
    assert entry.status == "planted"
    assert entry.origin == "emergent"
    assert entry.anchor is not None
    assert entry.anchor.para_prefix.startswith("老人从抽屉里")
    assert resolve_anchor(entry.anchor, PROSE)


def test_materialize_refuses_quote_not_in_prose() -> None:
    entries, refusals = materialize_proposals(
        [ClueProposal(kind="物件", note="停摆的钟", quote="钟面裂了一道缝")],
        PROSE,
        revision="candidate:c1",
    )

    assert entries == []
    assert len(refusals) == 1
    assert "找不到" in refusals[0].reason


def test_materialize_keeps_existing_and_skips_repeat() -> None:
    existing = ClueEntry(
        id="CL-01",
        kind="伏笔",
        status="planted",
        origin="planned",
        note="旧线索",
        anchor=None,
    )
    proposal = ClueProposal(kind="物件", note="停摆的钟", quote="墙上的钟停在三点十分")

    once, _ = materialize_proposals(
        [proposal], PROSE, revision="r1", existing=[existing], taken={"CL-01"}
    )
    twice, _ = materialize_proposals(
        [proposal], PROSE, revision="r1", existing=once, taken={"CL-01"}
    )

    assert [entry.id for entry in once] == ["CL-01", "CL-02"]
    assert [entry.id for entry in twice] == ["CL-01", "CL-02"]
