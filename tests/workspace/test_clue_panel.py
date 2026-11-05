"""工作台只读线索面板（v1.3 P4）的单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

from hastings.workspace.chapter_clues import (
    ClueEntry,
    ResolvesRef,
    anchor_paragraph,
    clues_path,
    write_chapter_clues,
)
from hastings.workspace.clue_panel import build_book_clues, build_chapter_clues

CH1 = "老魏把箱子放在桌上。\n箱扣上的漆掉了一块，露出底下的铁色。\n"
STRUCTURE = """# 结构

## 铁律

- 不写心理分析。

## 计划线索

- CL-01 箱扣缺漆：第1章埋、第5章收
- CL-09 灰毛线：第2章埋、第4章收
"""


def _planted() -> ClueEntry:
    return ClueEntry(
        id="CL-01",
        kind="伏笔",
        status="planted",
        origin="planned",
        note="箱扣缺漆",
        anchor=anchor_paragraph("箱扣上的漆掉了一块，露出底下的铁色。", "r1"),
    )


def _resolved() -> ClueEntry:
    return ClueEntry(
        id="CL-01",
        kind="伏笔",
        status="resolved",
        origin="planned",
        note="箱扣缺漆",
        resolves=ResolvesRef(clue_id="CL-01", planted_in=1),
    )


def test_book_clues_empty(tmp_path: Path) -> None:
    view = build_book_clues(tmp_path, "# 结构\n\n## 铁律\n")
    assert view.entries == []
    assert view.findings == []


def test_book_clues_aggregates_states(tmp_path: Path) -> None:
    write_chapter_clues(tmp_path, 1, "r1", [_planted()])
    write_chapter_clues(tmp_path, 5, "r2", [_resolved()])
    view = build_book_clues(tmp_path, STRUCTURE)
    assert len(view.entries) == 1
    entry = view.entries[0]
    assert entry.status == "resolved"
    assert entry.planted_in == 1
    assert entry.resolved_in == 5
    assert entry.anchor_prefix == "箱扣上的漆掉了一块，露出底下的铁"


def test_book_clues_keeps_plans_and_findings(tmp_path: Path) -> None:
    write_chapter_clues(tmp_path, 1, "r1", [_planted()])
    write_chapter_clues(tmp_path, 5, "r2", [_resolved()])
    view = build_book_clues(tmp_path, STRUCTURE)
    assert [plan.id for plan in view.plans] == ["CL-01", "CL-09"]
    codes = [finding.code for finding in view.findings]
    assert "CLUE_NOT_PLANTED" in codes
    assert "CLUE_UNRESOLVED" in codes


def test_chapter_clues_lists_entries_and_chapter_plans(tmp_path: Path) -> None:
    write_chapter_clues(tmp_path, 1, "r1", [_planted()])
    view = build_chapter_clues(tmp_path, STRUCTURE, 1, CH1)
    assert view.chapter == 1
    assert [entry.id for entry in view.entries] == ["CL-01"]
    assert view.entries[0].planted_in == 1
    assert [plan.id for plan in view.plans] == ["CL-01"]
    assert view.findings == []


def test_chapter_clues_reports_orphan_anchor(tmp_path: Path) -> None:
    write_chapter_clues(tmp_path, 1, "r1", [_planted()])
    view = build_chapter_clues(tmp_path, STRUCTURE, 1, "老魏把箱子放在桌上，没再说话。")
    assert [finding.code for finding in view.findings] == ["CLUE_ORPHANED_ANCHOR"]


def test_chapter_clues_without_file_reports_plan_missed(tmp_path: Path) -> None:
    view = build_chapter_clues(tmp_path, STRUCTURE, 2, "")
    assert view.entries == []
    assert [finding.code for finding in view.findings] == ["CLUE_PLAN_MISSED"]


def test_chapter_clues_reports_corrupt_file(tmp_path: Path) -> None:
    path = clues_path(tmp_path, 3)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema": 1, "chapter": 3}), encoding="utf-8")
    view = build_chapter_clues(tmp_path, STRUCTURE, 3, "正文")
    assert view.entries == []
    assert [finding.code for finding in view.findings] == ["CLUE_FILE_CORRUPT"]
