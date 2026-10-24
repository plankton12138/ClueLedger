"""故事时间线（0.6 V06-E4）。

契约：一章一个锚点，带原文依据；失效即不采用，缺锚点的章要明说「时间未知」。
不做日期运算——`when` 是原文的说法，代码不据此判断先后，先后矛盾由检查任务对着原文报。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hastings.workspace.assembly import Exclusion
from hastings.workspace.disclosure import ObservedEvidence
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.timeline import (
    TimelineEntry,
    TimelineStore,
    build_timeline,
    publish_timeline,
    timeline_section,
    timeline_view,
)

_CHAPTERS = ("雨夜", "来信", "旧怨")


def _workspace(tmp_path: Path, *, chapters: int = 3) -> Workspace:
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    for index, name in enumerate(_CHAPTERS[:chapters], start=1):
        workspace.write(
            f"正文/{index:04d} {name}.md",
            f"第{index}章 {name}\n\n十月二十一日深夜。",
            expected_digest=None,
        )
    return workspace


def _entry(
    workspace: Workspace,
    *,
    chapter: int = 3,
    when: str = "10月21日深夜",
    span: str = "约4小时",
    location: str = "雪山旅馆",
) -> TimelineEntry:
    source = workspace.read(f"正文/{chapter:04d} {_CHAPTERS[chapter - 1]}.md")
    quote = "十月二十一日深夜。"
    start = source.text.index(quote)
    return TimelineEntry(
        chapter=chapter,
        when=when,
        span=span,
        location=location,
        evidence=ObservedEvidence(
            path=source.path,
            digest=source.digest,
            start=start,
            end=start + len(quote),
            quote=quote,
        ),
    )


def test_published_anchor_is_injected_with_its_chapter(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = TimelineStore(tmp_path / "state")
    publish_timeline(workspace, store, (_entry(workspace),))

    state = build_timeline(workspace, store)
    section = timeline_section(state)

    assert "第3章：10月21日深夜" in section
    assert "下一章从第 3 章之后接着写" in section


def test_changed_chapter_invalidates_its_anchor(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = TimelineStore(tmp_path / "state")
    publish_timeline(workspace, store, (_entry(workspace, chapter=2), _entry(workspace)))
    chapter = workspace.read("正文/0003 旧怨.md")
    workspace.write(
        "正文/0003 旧怨.md", chapter.text + "\n天亮了。", expected_digest=chapter.digest
    )

    state = build_timeline(workspace, store)

    assert [item.chapter for item in state.entries] == [2]
    assert 3 in state.missing
    assert any("失效" in warning for warning in state.warnings)


def test_chapters_without_anchor_are_reported(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = TimelineStore(tmp_path / "state")
    publish_timeline(workspace, store, (_entry(workspace),))

    state = build_timeline(workspace, store)

    assert state.missing == (1, 2)
    assert any("没有时间锚点" in warning for warning in state.warnings)
    assert "第1章、第2章" in timeline_section(state)


def test_section_keeps_the_latest_anchor_when_budget_is_tight(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = TimelineStore(tmp_path / "state")
    publish_timeline(
        workspace,
        store,
        (
            _entry(workspace, chapter=1, when="十月二十日凌晨", span="整夜"),
            _entry(workspace, chapter=2, when="十月二十一日傍晚", span="约6小时"),
            _entry(workspace, chapter=3, when="十月二十一日深夜", span="约4小时"),
        ),
    )

    excluded: list[Exclusion] = []
    section = timeline_section(build_timeline(workspace, store), budget=60, excluded=excluded)

    assert "第3章" in section
    assert "第1章" not in section
    assert "下一章从第 3 章之后接着写" in section
    assert [(item.id, item.reason) for item in excluded] == [
        ("timeline:1", "budget"),
        ("timeline:2", "budget"),
    ]


def test_anchor_must_match_source_range(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = TimelineStore(tmp_path / "state")
    good = _entry(workspace)
    wrong = TimelineEntry(
        chapter=good.chapter,
        when=good.when,
        span=good.span,
        location=good.location,
        evidence=ObservedEvidence(
            path=good.evidence.path,
            digest=good.evidence.digest,
            start=good.evidence.start,
            end=good.evidence.end,
            quote="十月二十二日清晨。",
        ),
    )
    with pytest.raises(WorkspaceError) as excinfo:
        publish_timeline(workspace, store, (wrong,))
    assert excinfo.value.code == "STATE_EVIDENCE_INVALID"
    assert store.entries() == ()


def test_blank_anchor_is_rejected(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = TimelineStore(tmp_path / "state")
    with pytest.raises(WorkspaceError) as excinfo:
        publish_timeline(workspace, store, (_entry(workspace, when="  "),))
    assert excinfo.value.code == "STATE_EVIDENCE_INVALID"


def test_same_chapter_is_replaced_not_duplicated(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = TimelineStore(tmp_path / "state")
    publish_timeline(workspace, store, (_entry(workspace, when="十月二十一日深夜"),))
    publish_timeline(workspace, store, (_entry(workspace, when="十月二十一日雨夜"),))

    state = build_timeline(workspace, store)

    assert [item.when for item in state.entries] == ["十月二十一日雨夜"]


def test_view_lists_entries_and_missing_chapters(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, chapters=2)
    store = TimelineStore(tmp_path / "state")
    publish_timeline(workspace, store, (_entry(workspace, chapter=2),))

    view = timeline_view(build_timeline(workspace, store))

    assert view["domain"] == "timeline"
    assert view["entries"] == [
        {"chapter": 2, "when": "10月21日深夜", "span": "约4小时", "location": "雪山旅馆"}
    ]
    assert view["missing"] == [1]
