"""账本统计纯函数与聚合测试（P6 T7，计划 D7）：章号解析 / 风险派生 / 全量统计。"""

from pathlib import Path

import pytest

from hastings.bible.models import Clue, Foreshadow, ForeshadowStatus
from hastings.bible.stats import (
    compute_story_stats,
    foreshadow_risk,
    latest_chapter_number,
    parse_chapter_number,
)
from hastings.bible.store import LedgerFormatError, StoryWorkspace, save_ledger


def _ws(tmp_path: Path) -> StoryWorkspace:
    return StoryWorkspace(root=tmp_path / "stories", slug="demo")


def _fs(
    name: str,
    *,
    status: ForeshadowStatus = "planned",
    expected: str = "",
    plant: str = "",
) -> Foreshadow:
    """状态参数用字面量类型：测试构造与模型校验同口径。"""
    return Foreshadow(
        name=name,
        plant_chapter=plant or "ch01",
        expected_resolve_chapter=expected,
        status=status,
    )


# ---------- parse_chapter_number / latest_chapter_number ----------


def test_parse_chapter_number_extracts_digits() -> None:
    assert parse_chapter_number("ch03") == 3
    assert parse_chapter_number("ch1") == 1
    assert parse_chapter_number("CH12") == 12  # 大小写不敏感


def test_parse_chapter_number_non_numeric_returns_none() -> None:
    assert parse_chapter_number("prologue") is None
    assert parse_chapter_number("") is None
    assert parse_chapter_number("chapter3") is None  # 非 chNN 前缀


def test_latest_chapter_number_max_of_parsable() -> None:
    assert latest_chapter_number(["ch01", "ch05", "prologue"]) == 5
    assert latest_chapter_number(["ch09"]) == 9


def test_latest_chapter_number_empty_defaults_zero() -> None:
    assert latest_chapter_number([]) == 0
    assert latest_chapter_number(["prologue"]) == 0


# ---------- foreshadow_risk（D7 派生规则） ----------


def test_risk_resolved_is_normal_even_if_overdue() -> None:
    fs = _fs("已回收", status="resolved", expected="ch03")
    assert foreshadow_risk(fs, 5) == "normal"


def test_risk_overdue_when_expected_leq_latest() -> None:
    assert foreshadow_risk(_fs("到期", expected="ch05"), 5) == "overdue"
    assert foreshadow_risk(_fs("超期", expected="ch03"), 5) == "overdue"


def test_risk_near_when_within_one_chapter() -> None:
    assert foreshadow_risk(_fs("临近", expected="ch05"), 4) == "near"


def test_risk_normal_when_far_or_unparseable() -> None:
    assert foreshadow_risk(_fs("尚早", expected="ch07"), 4) == "normal"
    assert foreshadow_risk(_fs("无目标章", expected=""), 4) == "normal"
    assert foreshadow_risk(_fs("不可解析", expected="结局章"), 4) == "normal"


# ---------- compute_story_stats 聚合 ----------


def test_stats_empty_workspace_is_valid_empty_result(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    ws.ensure_dirs()
    stats = compute_story_stats(ws)
    assert stats.chapters == 0
    assert stats.total_chars == 0
    assert stats.latest_chapter == 0
    assert stats.foreshadow_total == 0
    assert stats.foreshadow_recovery_rate == 0.0
    assert stats.overdue_foreshadows == []
    assert stats.clue_total == 0
    assert stats.clue_red_herring_ratio == 0.0


def test_stats_aggregates_foreshadow_and_clue(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    ws.ensure_dirs()
    save_ledger(
        ws.foreshadows_path,
        [
            _fs("伏笔甲", status="resolved", expected="ch02"),
            _fs("伏笔乙", status="planted", expected="ch03"),
            _fs("伏笔丙", status="planned", expected="ch01"),  # 逾期：ch01 ≤ ch03
        ],
    )
    save_ledger(
        ws.clues_path,
        [
            Clue(name="线索甲", status="discovered", is_red_herring=True),
            Clue(name="线索乙", status="planted"),
            Clue(name="线索丙", status="planted", is_red_herring=True),
        ],
    )
    (ws.chapters_dir / "ch01.md").write_text("# 第1章 来客\n\n雪下得很大。", encoding="utf-8")
    (ws.chapters_dir / "ch02.md").write_text("# 第2章 雪夜\n\n", encoding="utf-8")

    stats = compute_story_stats(ws)
    assert stats.chapters == 2
    assert stats.latest_chapter == 2
    assert stats.total_chars == 18  # "#第1章来客"(6) + "雪下得很大。"(6) + "#第2章雪夜"(6)
    assert stats.foreshadow_total == 3
    assert stats.foreshadow_counts == {"resolved": 1, "planted": 1, "planned": 1}
    assert stats.foreshadow_recovery_rate == 1 / 3
    assert [o.name for o in stats.overdue_foreshadows] == ["伏笔丙"]
    assert stats.overdue_foreshadows[0].expected_resolve_chapter == "ch01"
    assert stats.clue_total == 3
    assert stats.clue_counts == {"discovered": 1, "planted": 2}
    assert stats.clue_red_herring == 2
    assert stats.clue_red_herring_ratio == 2 / 3


def test_stats_broken_ledger_raises_not_swallowed(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    ws.ensure_dirs()
    ws.foreshadows_path.write_text("不是列表", encoding="utf-8")
    with pytest.raises(LedgerFormatError, match="账本文件损坏"):
        compute_story_stats(ws)
