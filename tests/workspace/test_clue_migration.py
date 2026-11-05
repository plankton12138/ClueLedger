"""手写伏笔账迁移（v1.3 P3）的单元测试。"""

from __future__ import annotations

from pathlib import Path

from hastings.workspace.chapter_clues import (
    ClueEntry,
    aggregate_clues,
    anchor_findings,
    anchor_paragraph,
    read_chapter_clues,
    write_chapter_clues,
)
from hastings.workspace.clue_migration import (
    apply_draft,
    build_draft,
    existing_ids,
    parse_ledger_table,
    suggest_plan_section,
)

LEDGER = """# 伏笔账

| 项 | 埋 | 收 | 状态 |
|---|---|---|---|
| 箱扣缺漆 | 1 | 5（老魏说出箱子来历） | 已埋 |
| 铜哨 | 2 | — | 未收 |
| 旧钥匙 | 3 | 3 | 已收 |
| 委托信 | 4 | 6 | 待埋 |
| 磨刀石 | 5 | — | 不知道 |

上面这段不是表格。
"""


def _rows():
    return parse_ledger_table(LEDGER)


def test_parse_sample_table() -> None:
    rows = _rows()
    assert [row.title for row in rows] == ["箱扣缺漆", "铜哨", "旧钥匙", "委托信", "磨刀石"]
    first = rows[0]
    assert (first.plant_chapter, first.resolve_chapter, first.status) == (1, 5, "planted")
    assert first.detail == "老魏说出箱子来历"


def test_parse_stops_at_table_end() -> None:
    assert all("不是表格" not in row.title for row in _rows())


def test_parse_dash_resolve_is_none() -> None:
    assert _rows()[1].resolve_chapter is None


def test_parse_unknown_status_kept_as_none() -> None:
    assert _rows()[4].status is None


def test_parse_planned_status() -> None:
    row = _rows()[3]
    assert row.status is None
    assert row.plant_chapter == 4
    assert row.resolve_chapter == 6


def test_parse_alias_header_and_extra_column() -> None:
    text = """| 线索 | 埋设 | 回收 | 备注 | 状态 |
|---|---|---|---|---|
| 铜哨 | 第 2 章 | 第 6 章 | 挂在脖子上 | 已埋 |
"""
    rows = parse_ledger_table(text)
    assert len(rows) == 1
    assert rows[0].plant_chapter == 2
    assert rows[0].resolve_chapter == 6
    assert rows[0].status == "planted"


def test_parse_free_format_returns_empty() -> None:
    assert parse_ledger_table("# 伏笔账\n\n没有表格，只有一段话。\n") == []


def test_build_draft_planted_entry_is_migrated_without_anchor() -> None:
    draft = build_draft(_rows())
    planted = [item for item in draft.entries if item.chapter == 1]
    assert len(planted) == 1
    entry = planted[0].entry
    assert entry.origin == "migrated"
    assert entry.anchor is None
    assert entry.status == "planted"
    assert entry.note == "箱扣缺漆：老魏说出箱子来历"


def test_build_draft_resolved_spans_two_chapters() -> None:
    rows = parse_ledger_table(
        "| 项 | 埋 | 收 | 状态 |\n|---|---|---|---|\n| 铜哨 | 2 | 6 | 已收 |\n"
    )
    draft = build_draft(rows)
    chapters = [item.chapter for item in draft.entries]
    assert chapters == [2, 6]
    resolved = draft.entries[1].entry
    assert resolved.status == "resolved"
    assert resolved.resolves is not None
    assert resolved.resolves.planted_in == 2
    assert resolved.resolves.clue_id == resolved.id


def test_build_draft_planned_row_has_only_plan() -> None:
    draft = build_draft(_rows())
    planned_ids = {plan.id for plan in draft.plans}
    assert "CL-03" in planned_ids
    assert all(item.entry.id != "CL-03" for item in draft.entries)
    plan = next(plan for plan in draft.plans if plan.id == "CL-03")
    assert (plan.plant_chapter, plan.resolve_chapter) == (4, 6)


def test_build_draft_skips_same_chapter_plant_and_resolve() -> None:
    skipped = {item.title: item.reason for item in build_draft(_rows()).skipped}
    assert "埋收同章" in skipped["旧钥匙"]


def test_build_draft_skips_unknown_status() -> None:
    skipped = {item.title: item.reason for item in build_draft(_rows()).skipped}
    assert "状态无法识别" in skipped["磨刀石"]


def test_build_draft_respects_reserved_ids() -> None:
    draft = build_draft(_rows(), reserved={"CL-01"})
    assert draft.entries[0].entry.id == "CL-02"


def test_apply_writes_chapter_files(tmp_path: Path) -> None:
    report = apply_draft(tmp_path, build_draft(_rows()))
    assert report.added == 2
    assert report.files == ("clues/ch1.json", "clues/ch2.json")


def test_apply_roundtrip_aggregates_resolved(tmp_path: Path) -> None:
    rows = parse_ledger_table(
        "| 项 | 埋 | 收 | 状态 |\n|---|---|---|---|\n| 铜哨 | 2 | 6 | 已收 |\n"
    )
    apply_draft(tmp_path, build_draft(rows))
    aggregate = aggregate_clues(tmp_path, [2, 6])
    state = aggregate.states["CL-01"]
    assert state.planted_in == 2
    assert state.resolved_in == 6
    assert state.status == "resolved"


def test_apply_is_idempotent(tmp_path: Path) -> None:
    apply_draft(tmp_path, build_draft(_rows()))
    before = (tmp_path / "clues" / "ch1.json").read_bytes()
    second = apply_draft(tmp_path, build_draft(_rows(), reserved=existing_ids(tmp_path)))
    assert second.added == 0
    assert second.duplicates > 0
    assert (tmp_path / "clues" / "ch1.json").read_bytes() == before


def test_apply_keeps_existing_entries(tmp_path: Path) -> None:
    existing = ClueEntry(
        id="CL-99",
        kind="悬念",
        status="planted",
        origin="planned",
        note="原有线索",
        anchor=anchor_paragraph("老魏把箱子放在桌上。", "r1"),
    )
    write_chapter_clues(tmp_path, 1, "r1", [existing])
    apply_draft(tmp_path, build_draft(_rows(), reserved=existing_ids(tmp_path)))
    document = read_chapter_clues(tmp_path, 1)
    assert document is not None
    assert "CL-99" in {entry.id for entry in document.entries}
    assert len(document.entries) == 2


def test_migrated_entry_without_anchor_reports_orphan() -> None:
    entry = ClueEntry(id="CL-01", kind="伏笔", status="planted", origin="migrated", note="箱扣缺漆")
    findings = anchor_findings(1, [entry], "老魏把箱子放在桌上。")
    assert [finding.code for finding in findings] == ["CLUE_ORPHANED_ANCHOR"]
    assert "尚未重锚" in findings[0].message


def test_migrated_entry_with_anchor_is_clean() -> None:
    entry = ClueEntry(
        id="CL-01",
        kind="伏笔",
        status="planted",
        origin="migrated",
        note="箱扣缺漆",
        anchor=anchor_paragraph("老魏把箱子放在桌上。", "r1"),
    )
    assert anchor_findings(1, [entry], "老魏把箱子放在桌上。") == []


def test_suggest_plan_section() -> None:
    draft = build_draft(_rows())
    text = suggest_plan_section(draft.plans)
    assert text.startswith("## 计划线索\n")
    assert "- CL-03 委托信：第4章埋、第6章收" in text
    assert suggest_plan_section([]) == ""
