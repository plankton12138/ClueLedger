"""感知区分测试（v1.1 M4，Q3-C）。

契约（q0-contracts 第 5 节）：更新项由模型入档时分类标注；记账静默仅回执可见，
计划性变化单独一行说明附「不同意，改回来」；装配层校验标注完整性；
差异回显三要素：改了什么、影响哪里、基于哪个修订。
"""

from __future__ import annotations

import json
from pathlib import Path

from hastings.workspace.perception import (
    UpdateNote,
    planned_change_lines,
    receipt_rows,
    unannotated_labels,
    validate_update_notes,
)

# -- 纯函数 -----------------------------------------------------------------


def _note(label: str, category: str, note: str = "", affected: str = "") -> UpdateNote:
    return UpdateNote(label=label, category=category, note=note, affected=affected)


def test_planned_without_note_is_incomplete() -> None:
    issues = validate_update_notes([_note("回收点位", "planned")], [])
    assert issues and "计划性变化必须带说明" in issues[0]


def test_planned_with_note_and_duplicates_pass() -> None:
    assert validate_update_notes([_note("铜哨回收", "planned", note="提前到第 4 章")], []) == ()
    issues = validate_update_notes(
        [_note("铜哨回收", "planned", note="a"), _note("铜哨回收", "planned", note="b")], []
    )
    assert issues and "重复标注" in issues[0]


def test_bookkeeping_is_silent_and_planned_gets_lines() -> None:
    assert planned_change_lines([_note("伏笔标记", "bookkeeping")], "rev-1") == ""

    text = planned_change_lines(
        [_note("铜哨回收", "planned", note="回收提前", affected="第 4 章骨架")], "rev-abcdef123456"
    )
    assert "计划性结构变化" in text
    assert "回收提前" in text
    assert "影响：第 4 章骨架" in text
    assert "基于修订 rev-abcdef12" in text
    assert "不同意，改回来" in text


def test_unannotated_labels_report_not_filled() -> None:
    labels = ["铜哨回收", "林静状态"]
    notes = [_note("铜哨回收", "planned", note="提前")]
    assert unannotated_labels(notes, labels) == ("林静状态",)
    assert unannotated_labels([], labels) == ("铜哨回收", "林静状态")


def test_receipt_rows_include_bookkeeping() -> None:
    rows = receipt_rows(
        [_note("伏笔标记", "bookkeeping"), _note("铜哨回收", "planned", note="提前")]
    )
    assert [row["category_label"] for row in rows] == ["记账", "计划性变化"]
    assert rows[0]["note"] == ""


def test_from_dict_rejects_unknown_category() -> None:
    assert UpdateNote.from_dict({"label": "x", "category": "secret"}) is None
    assert UpdateNote.from_dict({"label": "", "category": "planned"}) is None
    note = UpdateNote.from_dict({"label": "改铁律", "category": "planned", "note": "加一条"})
    assert note is not None and note.category == "planned"


# -- 工具接线 ----------------------------------------------------------------


def test_update_story_rejects_incomplete_annotation() -> None:
    """计划性变化缺 note：入档前拒绝，不让标注流于形式。"""
    import pytest

    from hastings.workspace.errors import WorkspaceError
    from hastings.workspace.story_tools import _parse_update_notes

    with pytest.raises(WorkspaceError) as exc:
        _parse_update_notes([{"label": "改铁律", "category": "planned"}])
    assert exc.value.code == "STORY_CHANGE_INVALID"


def test_update_story_parses_valid_annotations() -> None:
    from hastings.workspace.story_tools import _parse_update_notes

    notes = _parse_update_notes(
        [
            {"label": "伏笔标记", "category": "bookkeeping"},
            {"label": "铜哨回收", "category": "planned", "note": "提前到第 4 章"},
        ]
    )
    assert [note.category for note in notes] == ["bookkeeping", "planned"]


def test_echo_event_carries_three_elements(tmp_path: Path) -> None:
    """回显事件包含三要素与计划性行文本；记账只进提交回执不进回显。"""
    from hastings.workspace.perception import planned_change_lines

    updates = [
        {"label": "伏笔标记", "category": "bookkeeping"},
        {
            "label": "铜哨回收",
            "category": "planned",
            "note": "回收提前到第 4 章",
            "affected": "第 4—5 章骨架",
        },
    ]
    notes = [note for note in (UpdateNote.from_dict(item) for item in updates) if note]
    planned_text = planned_change_lines(notes, "rev-1" * 8)
    event = {
        "type": "structure_updates_noted",
        "updates": receipt_rows(notes),
        "unannotated": list(unannotated_labels(notes, ["铜哨回收", "伏笔标记", "额外改动"])),
        "planned_text": planned_text,
    }
    payload = json.dumps(event, ensure_ascii=False)
    assert "计划性变化" in payload
    assert "bookkeeping" in payload  # 回执可见
    assert "额外改动" in event["unannotated"]  # 没标注到的如实列出
    assert "不同意，改回来" in event["planned_text"]
