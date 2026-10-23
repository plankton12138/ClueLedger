"""线索与伏笔台账（0.6 V06-E5）。

契约：线索只从原文登记，埋设与回收都要有依据；失效即不采用；只有未回收的进上下文，
超期项排在最前。已回收与已废弃只给作者看，不占预算。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hastings.workspace.clues import (
    ClueEntry,
    ClueStore,
    build_clues,
    clues_section,
    clues_view,
    publish_clues,
)
from hastings.workspace.disclosure import ObservedEvidence
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace

_CHAPTERS = ("雨夜", "来信", "旧怨")
_QUOTE = "雪还在下。"


def _workspace(tmp_path: Path, *, chapters: int = 3) -> Workspace:
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    for index, name in enumerate(_CHAPTERS[:chapters], start=1):
        workspace.write(
            f"正文/{index:04d} {name}.md",
            f"第{index}章 {name}\n\n{_QUOTE}",
            expected_digest=None,
        )
    return workspace


def _evidence(workspace: Workspace, chapter: int) -> ObservedEvidence:
    source = workspace.read(f"正文/{chapter:04d} {_CHAPTERS[chapter - 1]}.md")
    start = source.text.index(_QUOTE)
    return ObservedEvidence(
        path=source.path,
        digest=source.digest,
        start=start,
        end=start + len(_QUOTE),
        quote=_QUOTE,
    )


def _clue(
    workspace: Workspace,
    *,
    clue: str = "门锁内侧有擦痕",
    planted: int = 1,
    due: int | None = 3,
    status: str = "open",
    resolution: int | None = None,
    supports: str = "",
) -> ClueEntry:
    return ClueEntry(
        clue=clue,
        planted_chapter=planted,
        due_chapter=due,
        status=status,  # type: ignore[arg-type]
        supports=supports,
        evidence=_evidence(workspace, planted),
        resolution_chapter=resolution,
        resolution_evidence=_evidence(workspace, resolution) if resolution else None,
    )


def test_open_clues_are_injected_with_planting_chapter(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = ClueStore(tmp_path / "state")
    publish_clues(workspace, store, (_clue(workspace, supports="凶手从内侧开门"),))

    state = build_clues(workspace, store)
    section = clues_section(state)

    assert "第1章埋下：门锁内侧有擦痕" in section
    assert "计划第3章回收" in section
    assert "指向：凶手从内侧开门" in section


def test_overdue_clue_comes_first(tmp_path: Path) -> None:
    """计划章已过仍未回收：排在最前，并写明已超期。"""
    workspace = _workspace(tmp_path)
    store = ClueStore(tmp_path / "state")
    publish_clues(
        workspace,
        store,
        (
            _clue(workspace, clue="走廊尽头的水渍", planted=3, due=None),
            _clue(workspace, clue="门锁内侧有擦痕", planted=1, due=2),
        ),
    )

    state = build_clues(workspace, store)
    section = clues_section(state)

    assert [item.clue for item in state.overdue()] == ["门锁内侧有擦痕"]
    assert section.index("门锁内侧有擦痕") < section.index("走廊尽头的水渍")
    assert "已超期" in section


def test_resolved_and_dropped_are_not_injected(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = ClueStore(tmp_path / "state")
    publish_clues(
        workspace,
        store,
        (
            _clue(workspace, clue="已回收的伞", planted=1, due=2, status="resolved", resolution=2),
            _clue(workspace, clue="放弃的脚印", planted=1, due=None, status="dropped"),
        ),
    )

    state = build_clues(workspace, store)

    assert state.open == ()
    assert clues_section(state) == ""
    assert {item.status for item in state.closed} == {"resolved", "dropped"}


def test_resolved_needs_resolution_evidence(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = ClueStore(tmp_path / "state")

    with pytest.raises(WorkspaceError) as excinfo:
        publish_clues(workspace, store, (_clue(workspace, status="resolved"),))
    assert excinfo.value.code == "STATE_EVIDENCE_INVALID"
    assert store.entries() == ()


def test_due_chapter_cannot_precede_planting(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = ClueStore(tmp_path / "state")

    with pytest.raises(WorkspaceError) as excinfo:
        publish_clues(workspace, store, (_clue(workspace, planted=3, due=1),))
    assert excinfo.value.code == "STATE_EVIDENCE_INVALID"


def test_evidence_must_match_source_range(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = ClueStore(tmp_path / "state")
    good = _clue(workspace)
    wrong = ClueEntry(
        clue=good.clue,
        planted_chapter=good.planted_chapter,
        due_chapter=good.due_chapter,
        status="open",
        supports="",
        evidence=ObservedEvidence(
            path=good.evidence.path,
            digest=good.evidence.digest,
            start=good.evidence.start,
            end=good.evidence.end,
            quote="雪停了。",
        ),
    )
    with pytest.raises(WorkspaceError) as excinfo:
        publish_clues(workspace, store, (wrong,))
    assert excinfo.value.code == "STATE_EVIDENCE_INVALID"


def test_changed_chapter_invalidates_the_clue(tmp_path: Path) -> None:
    """埋设处被改过：整条不采用，并说明失效了几条。"""
    workspace = _workspace(tmp_path)
    store = ClueStore(tmp_path / "state")
    publish_clues(workspace, store, (_clue(workspace),))
    chapter = workspace.read("正文/0001 雨夜.md")
    workspace.write(
        "正文/0001 雨夜.md", chapter.text + "\n门开了。", expected_digest=chapter.digest
    )

    state = build_clues(workspace, store)

    assert state.open == ()
    assert any("失效" in warning for warning in state.warnings)


def test_clue_whose_chapter_was_deleted_is_not_injected(tmp_path: Path) -> None:
    """埋设章被删掉：原文不在了，这条登记整条不采用。"""
    seed = _workspace(tmp_path / "seed")
    store = ClueStore(tmp_path / "state")
    store.replace((_clue(seed, planted=3, due=None),))
    workspace = _workspace(tmp_path, chapters=2)

    state = build_clues(workspace, store)

    assert state.open == ()
    assert any("失效" in warning for warning in state.warnings)


def test_same_clue_is_replaced_not_duplicated(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = ClueStore(tmp_path / "state")
    publish_clues(workspace, store, (_clue(workspace, due=3),))
    publish_clues(workspace, store, (_clue(workspace, due=2),))

    state = build_clues(workspace, store)

    assert [item.due_chapter for item in state.open] == [2]


def test_section_reports_hidden_entries_instead_of_dropping_all(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = ClueStore(tmp_path / "state")
    publish_clues(
        workspace,
        store,
        tuple(_clue(workspace, clue=f"线索{index}号", planted=1, due=3) for index in range(6)),
    )

    section = clues_section(build_clues(workspace, store), budget=160)

    assert "线索" in section
    assert "还有" in section
    assert "read_state" in section


def test_view_marks_overdue_and_quotes_the_source(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = ClueStore(tmp_path / "state")
    publish_clues(workspace, store, (_clue(workspace, due=2),))

    view = clues_view(build_clues(workspace, store))

    assert view["domain"] == "clues"
    assert view["overdue"] == ["门锁内侧有擦痕"]
    row = view["entries"][0]  # type: ignore[index]
    assert row["overdue"] is True
    assert row["quote"] == _QUOTE
    assert row["label"] == "未回收"
