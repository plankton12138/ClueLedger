"""伏笔账投影（v1.3 P3）的单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from hastings.workspace.chapter_clues import (
    ClueEntry,
    CluePlan,
    ResolvesRef,
    aggregate_clues,
    anchor_paragraph,
    write_chapter_clues,
)
from hastings.workspace.clue_projection import (
    PROJECTION_FILENAME,
    PROJECTION_MARKER,
    refresh_projection,
    render_ledger,
)

CH1 = "老魏把箱子放在桌上。\n箱扣上的漆掉了一块，露出底下的铁色。\n他没再说话。"
CH5 = "「这不是普通的箱子。」老魏终于开口。\n他把箱扣翻过来，内沿刻着一个年份。"


def _planted(id_: str = "CL-01", note: str = "箱扣缺漆") -> ClueEntry:
    return ClueEntry(
        id=id_,
        kind="伏笔",
        status="planted",
        origin="planned",
        note=note,
        anchor=anchor_paragraph("箱扣上的漆掉了一块，露出底下的铁色。", "r1"),
    )


def _resolved(id_: str = "CL-01", planted_in: int = 1) -> ClueEntry:
    return ClueEntry(
        id=id_,
        kind="伏笔",
        status="resolved",
        origin="planned",
        note="箱扣缺漆",
        resolves=ResolvesRef(clue_id=id_, planted_in=planted_in),
    )


def _aggregate(tmp_path: Path):
    write_chapter_clues(tmp_path, 1, "r1", [_planted()])
    write_chapter_clues(tmp_path, 5, "r2", [_resolved()])
    return aggregate_clues(tmp_path, [1, 5])


def test_render_empty_ledger_has_no_table() -> None:
    text = render_ledger(aggregate_clues(Path("/nonexistent"), []))
    assert "暂无线索登记。" in text
    assert "| 编号 |" not in text
    assert "无未决项。" in text


def test_render_table_merges_cross_chapter_resolution(tmp_path: Path) -> None:
    text = render_ledger(_aggregate(tmp_path))
    rows = [line for line in text.splitlines() if line.startswith("| CL-")]
    assert len(rows) == 1
    assert "第 1 章" in rows[0]
    assert "第 5 章" in rows[0]
    assert "已回收" in rows[0]
    assert "共 1 条线索：未回收 0、已推进 0、已回收 1、不收 0。" in text


def test_render_order_by_plant_chapter(tmp_path: Path) -> None:
    write_chapter_clues(
        tmp_path,
        1,
        "r1",
        [
            _planted("CL-02", "铜哨"),
            ClueEntry(
                id="CL-01",
                kind="物件",
                status="planted",
                origin="planned",
                note="旧钥匙",
                anchor=anchor_paragraph("箱扣上的漆掉了一块，露出底下的铁色。", "r1"),
            ),
        ],
    )
    text = render_ledger(aggregate_clues(tmp_path, [1]))
    order = [line.split("|")[1].strip() for line in text.splitlines() if line.startswith("| CL-")]
    assert order == ["CL-01", "CL-02"]


def test_render_pending_plans_section() -> None:
    plans = [CluePlan(id="CL-03", plant_chapter=2, resolve_chapter=6, note="箱扣缺漆")]
    text = render_ledger(aggregate_clues(Path("/nonexistent"), []), plans)
    assert "## 计划线索（结构层已声明，尚未登记）" in text
    assert "| CL-03 | 箱扣缺漆 | 第 2 章 | 第 6 章 |" in text


def test_render_findings_section() -> None:
    plans = [CluePlan(id="CL-03", plant_chapter=1, resolve_chapter=2, note="箱扣缺漆")]
    text = render_ledger(aggregate_clues(Path("/nonexistent"), [1, 2], plans=plans), plans)
    assert "[CLUE_NOT_PLANTED]" in text
    assert "[CLUE_UNRESOLVED]" in text


def test_migrated_entry_without_anchor_is_marked(tmp_path: Path) -> None:
    write_chapter_clues(
        tmp_path,
        1,
        "migration",
        [
            ClueEntry(
                id="CL-01",
                kind="伏笔",
                status="planted",
                origin="migrated",
                note="箱扣缺漆",
            )
        ],
    )
    text = render_ledger(aggregate_clues(tmp_path, [1]))
    row = next(line for line in text.splitlines() if line.startswith("| CL-01"))
    assert "未锚定" in row


def test_refresh_writes_file_with_marker(tmp_path: Path) -> None:
    _aggregate(tmp_path)
    result = refresh_projection(tmp_path, [1, 5])
    assert result.path == tmp_path / PROJECTION_FILENAME
    assert result.changed is True
    content = result.path.read_text(encoding="utf-8")
    assert content.startswith("# 伏笔账\n")
    assert PROJECTION_MARKER in content


def test_refresh_is_idempotent(tmp_path: Path) -> None:
    _aggregate(tmp_path)
    first = refresh_projection(tmp_path, [1, 5])
    stamp = first.path.stat().st_mtime_ns
    second = refresh_projection(tmp_path, [1, 5])
    assert second.changed is False
    assert second.text == first.text
    # 内容没变就不碰文件：mtime 不变，Git 也就不会有空 diff。
    assert second.path.stat().st_mtime_ns == stamp


def test_refresh_rewrites_when_ledger_changes(tmp_path: Path) -> None:
    write_chapter_clues(tmp_path, 1, "r1", [_planted()])
    refresh_projection(tmp_path, [1])
    write_chapter_clues(tmp_path, 5, "r2", [_resolved()])
    third = refresh_projection(tmp_path, [1, 5])
    assert third.changed is True
    assert "已回收" in third.path.read_text(encoding="utf-8")


@pytest.mark.parametrize("chapter", [1])
def test_refresh_accepts_custom_filename(tmp_path: Path, chapter: int) -> None:
    result = refresh_projection(tmp_path, [chapter], filename="设定集/线索投影.md")
    assert result.path == tmp_path / "设定集" / "线索投影.md"
    assert result.path.is_file()
