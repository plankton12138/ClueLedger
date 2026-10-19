"""内部工作版本暂存区的领域约束（V03-7.4）：覆盖语义与必填基础。"""

from __future__ import annotations

import pytest

from hastings.domain.revision.errors import CoverageViolation
from hastings.domain.revision.staging import (
    RevisionStaging,
    StagedChapter,
    StagedLedgerChange,
)


def _chapter(chapter_id: str, text: str = "正文", base: str = "rev_1") -> StagedChapter:
    return StagedChapter(chapter_id=chapter_id, base_revision_id=base, text=text)


def test_stage_chapter_upserts_by_chapter_id() -> None:
    staging = RevisionStaging.empty(task_id="rtk_1", project_id="p1")
    staging = staging.with_chapter(_chapter("ch1", "第一版"))
    staging = staging.with_chapter(_chapter("ch2", "第二章"))
    staging = staging.with_chapter(_chapter("ch1", "第二版"))
    assert [c.chapter_id for c in staging.chapters] == ["ch1", "ch2"]
    assert staging.chapter("ch1").text == "第二版"  # type: ignore[union-attr]
    assert staging.without_chapter("ch1").chapter("ch1") is None


def test_stage_ledger_upserts_by_stage_id() -> None:
    staging = RevisionStaging.empty(task_id="rtk_1", project_id="p1")
    first = StagedLedgerChange(stage_id="s1", kind="story.character", fields={"name": "甲"})
    second = StagedLedgerChange(stage_id="s1", kind="story.character", fields={"name": "乙"})
    staging = staging.with_ledger(first).with_ledger(second)
    assert len(staging.ledger) == 1
    assert staging.ledger[0].fields["name"] == "乙"
    assert staging.without_ledger("s1").ledger == ()


def test_empty_text_or_missing_base_is_rejected() -> None:
    with pytest.raises(CoverageViolation) as empty:
        StagedChapter(chapter_id="ch1", base_revision_id="rev_1", text="   ")
    assert empty.value.code == "REVISION_STAGE_INVALID"
    with pytest.raises(CoverageViolation) as no_base:
        StagedChapter(chapter_id="ch1", base_revision_id="", text="正文")
    assert no_base.value.code == "REVISION_STAGE_INVALID"


def test_ledger_stage_requires_kind_or_current_revision() -> None:
    with pytest.raises(CoverageViolation) as no_kind:
        StagedLedgerChange(stage_id="s1", fields={"name": "甲"})
    assert no_kind.value.code == "REVISION_STAGE_INVALID"
    with pytest.raises(CoverageViolation) as no_revision:
        StagedLedgerChange(stage_id="s1", fields={"name": "甲"}, entry_id="e1")
    assert no_revision.value.code == "REVISION_STAGE_INVALID"


def test_empty_and_round_trip() -> None:
    staging = RevisionStaging.empty(task_id="rtk_1", project_id="p1")
    assert staging.is_empty
    ledger = StagedLedgerChange(stage_id="s1", kind="story.character", fields={"name": "甲"})
    restored = RevisionStaging.from_wire(
        staging.with_chapter(_chapter("ch1")).with_ledger(ledger).to_wire()
    )
    assert not restored.is_empty
    assert restored.chapters[0].chapter_id == "ch1"
    assert restored.ledger[0].stage_id == "s1"
