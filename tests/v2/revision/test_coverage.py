"""原文覆盖台账纯函数（V03-7.2）：状态迁移、读取区间与「摘要不得记 checked_unchanged」。

计划 M10 第 5 条与阶段执行指南 §11.2 的核心硬约束是：**只读摘要/目录不能记为
``checked_unchanged``**。这些用例盯的就是这条边界，以及逐章读取区间的合并与来源变化
重置，避免「目录里看过一眼」被当成「全文检查过」。
"""

from __future__ import annotations

import pytest

from hastings.domain.revision import error_codes as ec
from hastings.domain.revision.coverage import (
    READ_KIND_FULL,
    READ_KIND_SUMMARY,
    STATUS_BLOCKED,
    STATUS_CHANGED,
    STATUS_CHECKED_UNCHANGED,
    STATUS_READ,
    STATUS_UNREAD,
    ChapterCoverage,
    CoverageLedger,
    ReadRange,
    uncovered_reason,
)
from hastings.domain.revision.errors import CoverageViolation

CHAPTERS = ("ch1", "ch2", "ch3")


def _ledger() -> CoverageLedger:
    return CoverageLedger.create("rwk_1", "prj_1", "cmt_base", CHAPTERS, at="t0")


def test_create_declares_all_chapters_unread() -> None:
    ledger = _ledger()
    assert ledger.chapter_ids == CHAPTERS
    assert [e.status for e in ledger.entries] == [STATUS_UNREAD] * 3
    assert ledger.covered == ()
    assert not ledger.complete
    assert [e.chapter_id for e in ledger.uncovered()] == list(CHAPTERS)


def test_read_range_rejects_inverted_bounds() -> None:
    with pytest.raises(CoverageViolation) as caught:
        ReadRange(5, 2)
    assert caught.value.code == ec.COVERAGE_INVALID_TRANSITION


def test_overlapping_ranges_are_merged() -> None:
    entry = ChapterCoverage(
        chapter_id="ch1",
        status=STATUS_READ,
        text_length=100,
        read_kind=READ_KIND_FULL,
        ranges=(ReadRange(0, 30), ReadRange(20, 60), ReadRange(60, 100)),
    )
    assert entry.ranges == (ReadRange(0, 100),)
    assert entry.fully_read


def test_partial_full_read_does_not_cover_whole_chapter() -> None:
    entry = ChapterCoverage(
        chapter_id="ch1",
        status=STATUS_READ,
        text_length=100,
        read_kind=READ_KIND_FULL,
        ranges=(ReadRange(0, 40),),
    )
    assert not entry.fully_read
    assert not entry.covered


def test_summary_read_never_becomes_checked_unchanged() -> None:
    """硬约束（四透镜②）：登记摘要读取后确认无需修改必须被拒绝。"""
    ledger = _ledger().with_summary_read(
        "ch1", source_revision_id="base_1", text_length=120, at="t1"
    )
    entry = ledger.entry("ch1")
    assert entry is not None
    assert entry.status == STATUS_READ
    assert entry.read_kind == READ_KIND_SUMMARY

    with pytest.raises(CoverageViolation) as caught:
        ledger.with_confirmed_unchanged("ch1", at="t2")
    assert caught.value.code == ec.COVERAGE_NOT_FULLY_READ
    # 错误三要素里明确「目录/摘要读取不算全文检查」。
    assert "全文检查" in caught.value.fix


def test_partial_full_read_still_cannot_be_confirmed() -> None:
    ledger = _ledger().with_full_read(
        "ch1", source_revision_id="base_1", text_length=100, start=0, end=40, at="t1"
    )
    with pytest.raises(CoverageViolation) as caught:
        ledger.with_confirmed_unchanged("ch1", at="t2")
    assert caught.value.code == ec.COVERAGE_NOT_FULLY_READ


def test_full_read_over_whole_chapter_confirms_unchanged() -> None:
    ledger = (
        _ledger()
        .with_full_read(
            "ch1", source_revision_id="base_1", text_length=100, start=0, end=60, at="t1"
        )
        .with_full_read(
            "ch1", source_revision_id="base_1", text_length=100, start=60, end=100, at="t2"
        )
        .with_confirmed_unchanged("ch1", at="t3")
    )
    entry = ledger.entry("ch1")
    assert entry is not None
    assert entry.status == STATUS_CHECKED_UNCHANGED
    assert entry.fully_read
    assert ledger.covered == (entry,)


def test_source_change_resets_previous_reads() -> None:
    """正式正文某章改了 → 该章既往读取区间作废，需重读（来源变化即重置）。"""
    ledger = _ledger().with_full_read(
        "ch1", source_revision_id="base_1", text_length=100, start=0, end=100, at="t1"
    )
    assert ledger.entry("ch1").fully_read  # type: ignore[union-attr]

    moved = ledger.with_full_read(
        "ch1", source_revision_id="base_2", text_length=100, start=0, end=30, at="t2"
    )
    entry = moved.entry("ch1")
    assert entry is not None
    assert entry.ranges == (ReadRange(0, 30),)
    assert not entry.fully_read


def test_change_marks_covered_and_clears_reads() -> None:
    ledger = _ledger().with_change("ch2", source_revision_id="base_9", text_length=80, at="t1")
    entry = ledger.entry("ch2")
    assert entry is not None
    assert entry.status == STATUS_CHANGED
    assert entry.read_kind == ""
    assert entry.ranges == ()
    assert entry.covered


def test_changed_chapter_cannot_be_confirmed_unchanged() -> None:
    ledger = _ledger().with_change("ch2", at="t1")
    with pytest.raises(CoverageViolation) as caught:
        ledger.with_confirmed_unchanged("ch2", at="t2")
    assert caught.value.code == ec.COVERAGE_INVALID_TRANSITION


def test_blocked_requires_specific_reason() -> None:
    ledger = _ledger()
    with pytest.raises(CoverageViolation) as caught:
        ledger.with_blocked("ch3", reason="   ", at="t1")
    assert caught.value.code == ec.COVERAGE_INVALID_TRANSITION


def test_blocked_reason_is_queryable() -> None:
    ledger = _ledger().with_blocked("ch3", reason="该章有未结束的手工修改副本", at="t1")
    entry = ledger.entry("ch3")
    assert entry is not None
    assert entry.status == STATUS_BLOCKED
    assert uncovered_reason(entry) == "该章有未结束的手工修改副本"


def test_uncovered_reason_words_by_state() -> None:
    ledger = _ledger()
    entry = ledger.entry("ch1")
    assert entry is not None
    assert uncovered_reason(entry) == "尚未读取该章"

    summary = ledger.with_summary_read("ch1", source_revision_id="s", text_length=50, at="t1")
    assert uncovered_reason(summary.entry("ch1")) == "只读了目录/摘要，未做全文检查"  # type: ignore[arg-type]


def test_unknown_chapter_is_rejected() -> None:
    with pytest.raises(CoverageViolation) as caught:
        _ledger().require_entry("chX")
    assert caught.value.code == ec.COVERAGE_UNKNOWN_CHAPTER


def test_ledger_only_complete_when_every_chapter_covered() -> None:
    ledger = _ledger()
    ledger = ledger.with_full_read(
        "ch1", source_revision_id="s", text_length=10, start=0, end=10, at="t1"
    ).with_confirmed_unchanged("ch1", at="t2")
    ledger = ledger.with_change("ch2", at="t3")
    assert not ledger.complete

    ledger = ledger.with_blocked("ch3", reason="来源不可读", at="t4")
    assert not ledger.complete

    full = _ledger()
    for cid in CHAPTERS:
        full = full.with_full_read(
            cid, source_revision_id="s", text_length=10, start=0, end=10, at="t1"
        ).with_confirmed_unchanged(cid, at="t2")
    assert full.complete
    assert full.covered == full.entries


def test_ledger_wire_roundtrip() -> None:
    ledger = (
        _ledger()
        .with_full_read("ch1", source_revision_id="s", text_length=10, start=0, end=5, at="t1")
        .with_blocked("ch2", reason="环境故障", at="t1")
    )
    assert CoverageLedger.from_wire(ledger.to_wire()) == ledger
