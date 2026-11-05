"""工作台只读线索面板（v1.3 P4）。

把章级线索与聚合视图翻译成前端能直接渲染的视图：只读、不带任何写入入口。
改动线索仍然只有一条路——对话里的章提交（P0）与随后的重锚（P2）。

两个视图：

- **全书**（``build_book_clues``）：聚合状态 + 结构层计划 + 对账发现项。
- **本章**（``build_chapter_clues``）：本章登记条目 + 本章计划 + 本章发现项
  （锚失联、计划未落实）。锚校验需要正文，只做本章，不为面板读全书。

线索是增强层：单章文件坏掉不抛给工作台，转成一条 ``CLUE_FILE_CORRUPT``
发现项显示在面板里。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from hastings.workspace.chapter_clues import (
    CLUES_DIRNAME,
    ClueAnchor,
    ClueEntry,
    ClueFinding,
    CluePlan,
    ClueState,
    aggregate_clues,
    anchor_findings,
    missed_plans,
    parse_planned_clues,
    read_chapter_clues,
)
from hastings.workspace.errors import WorkspaceError

__all__ = [
    "BookCluesView",
    "ChapterCluesView",
    "ClueEntryView",
    "ClueFindingView",
    "CluePlanView",
    "build_book_clues",
    "build_chapter_clues",
    "clue_chapters",
]


class ClueEntryView(BaseModel):
    """一条线索在面板里的样子；``anchor_prefix`` 为空表示没有可显示的锚。"""

    id: str
    kind: str
    status: str
    origin: str
    note: str | None = None
    anchor_prefix: str | None = None
    planted_in: int | None = None
    resolved_in: int | None = None


class CluePlanView(BaseModel):
    id: str
    note: str | None = None
    plant_chapter: int
    resolve_chapter: int | None = None


class ClueFindingView(BaseModel):
    code: str
    message: str


class ChapterCluesView(BaseModel):
    chapter: int
    entries: list[ClueEntryView] = Field(default_factory=list)
    plans: list[CluePlanView] = Field(default_factory=list)
    findings: list[ClueFindingView] = Field(default_factory=list)


class BookCluesView(BaseModel):
    entries: list[ClueEntryView] = Field(default_factory=list)
    plans: list[CluePlanView] = Field(default_factory=list)
    findings: list[ClueFindingView] = Field(default_factory=list)


def clue_chapters(root: Path) -> list[int]:
    """磁盘上已有线索登记的章号（升序）。"""
    directory = Path(root) / CLUES_DIRNAME
    if not directory.is_dir():
        return []
    numbers: list[int] = []
    for path in directory.glob("ch*.json"):
        suffix = path.stem[2:]
        if suffix.isdigit():
            numbers.append(int(suffix))
    return sorted(numbers)


def _entry_anchor(entry: ClueEntry) -> ClueAnchor | None:
    return entry.anchor or (entry.resolves.anchor if entry.resolves else None)


def _entry_view(entry: ClueEntry, chapter: int) -> ClueEntryView:
    """章级条目视图：本章登记即本章埋设（回收章也记在本章）。"""
    anchor = _entry_anchor(entry)
    return ClueEntryView(
        id=entry.id,
        kind=entry.kind,
        status=entry.status,
        origin=entry.origin,
        note=entry.note,
        anchor_prefix=anchor.para_prefix if anchor is not None else None,
        planted_in=chapter,
        resolved_in=chapter if entry.status == "resolved" else None,
    )


def _state_view(state: ClueState) -> ClueEntryView:
    return ClueEntryView(
        id=state.id,
        kind=state.kind,
        status=state.status,
        origin=state.origin,
        note=state.note,
        anchor_prefix=state.anchor_prefix,
        planted_in=state.planted_in,
        resolved_in=state.resolved_in,
    )


def _plan_view(plan: CluePlan) -> CluePlanView:
    return CluePlanView(
        id=plan.id,
        note=plan.note,
        plant_chapter=plan.plant_chapter,
        resolve_chapter=plan.resolve_chapter,
    )


def _finding_view(finding: ClueFinding) -> ClueFindingView:
    return ClueFindingView(code=finding.code, message=finding.message)


def build_book_clues(root: Path, structure_text: str) -> BookCluesView:
    """全书线索视图：聚合状态 + 计划 + 对账。没有线索也没有计划时返回空视图。"""
    plans = parse_planned_clues(structure_text)
    chapters = clue_chapters(root)
    if not plans and not chapters:
        return BookCluesView()
    aggregate = aggregate_clues(root, chapters, plans=plans)
    states = sorted(
        aggregate.states.values(),
        key=lambda state: (state.planted_in if state.planted_in is not None else 10**6, state.id),
    )
    return BookCluesView(
        entries=[_state_view(state) for state in states],
        plans=[_plan_view(plan) for plan in plans],
        findings=[_finding_view(finding) for finding in aggregate.findings],
    )


def build_chapter_clues(
    root: Path,
    structure_text: str,
    chapter: int,
    prose_text: str,
) -> ChapterCluesView:
    """本章线索视图：登记条目 + 本章计划 + 本章发现项（锚失联 / 计划未落实）。"""
    plans = parse_planned_clues(structure_text)
    chapter_plans = [
        plan for plan in plans if chapter in (plan.plant_chapter, plan.resolve_chapter)
    ]
    view = ChapterCluesView(chapter=chapter, plans=[_plan_view(plan) for plan in chapter_plans])
    try:
        document = read_chapter_clues(root, chapter)
    except WorkspaceError as exc:
        if exc.code != "CLUE_FILE_CORRUPT":
            raise
        # 线索是增强层：坏一页只报告，不让工作台打不开。
        view.findings.append(ClueFindingView(code=exc.code, message=exc.why))
        return view
    if document is None:
        view.findings.extend(_finding_view(finding) for finding in missed_plans(plans, chapter, []))
        return view
    view.entries = [_entry_view(entry, chapter) for entry in document.entries]
    findings = [
        *anchor_findings(chapter, document.entries, prose_text),
        *missed_plans(plans, chapter, document.entries),
    ]
    view.findings = [_finding_view(finding) for finding in findings]
    return view
