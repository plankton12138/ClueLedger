"""只读故事时间轴：横轴是故事内发生时间，纵向按披露章节分组。

这一层只做投影，不修改任何数据：时间来自作者明确给出的相对分钟，披露位置来自有效正文
关联的真实章序，没有正文时退到计划章节。没有数值时间的节点仍然出现在所属分组里，不能
因为无法定位就被筛掉。
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, Field

from hastings.workspace.files import FileView, Workspace
from hastings.workspace.story_attention import binding_fact, node_status
from hastings.workspace.story_bindings import Anchor, anchor_state
from hastings.workspace.story_models import ProseBinding, StoryNode, StoryVariant
from hastings.workspace.story_sync import contract_digest

__all__ = ["StoryTimelineView", "TimelineGroup", "TimelineNode", "build_timeline"]

ChapterKind = Literal["actual", "planned", "unassigned"]


class TimelineNode(BaseModel):
    node_id: str
    title: str
    time_note: str = ""
    start_minute: int | None = None
    end_minute: int | None = None
    approximate: bool = False
    status: Literal["ready", "draft", "attention"] = "draft"
    reasons: list[str] = Field(default_factory=list)
    binding_ids: list[str] = Field(default_factory=list)
    chapter_paths: list[str] = Field(default_factory=list)


class TimelineGroup(BaseModel):
    chapter_number: int | None = None
    chapter_kind: ChapterKind = "unassigned"
    title: str = ""
    nodes: list[TimelineNode] = Field(default_factory=list)


class TimelineDomain(BaseModel):
    min: int
    max: int


class StoryTimelineView(BaseModel):
    variant_id: str
    variant_revision: str
    ledger_revision: str = ""
    view_revision: str = ""
    time_origin_label: str = ""
    domain: TimelineDomain | None = None
    groups: list[TimelineGroup] = Field(default_factory=list)


def _chapter_index(workspace: Workspace) -> dict[str, int]:
    return {
        entry.path: entry.chapter_number
        for entry in workspace.catalog().chapters
        if entry.exists and entry.chapter_number is not None and entry.path
    }


def _reveal(
    node: StoryNode, bindings: list[ProseBinding], chapters: dict[str, int]
) -> tuple[int | None, ChapterKind]:
    """节点最早在哪一章披露；以有效正文关联为准，其次计划章节。"""
    actual = sorted(chapters[binding.path] for binding in bindings if binding.path in chapters)
    if actual:
        return actual[0], "actual"
    if node.planned_chapter_number is not None:
        return node.planned_chapter_number, "planned"
    return None, "unassigned"


def _expand(minute: int, max_minute: int) -> tuple[int, int]:
    """给显示域留出可读边距；单个时刻也要有非零宽度。"""
    if minute == max_minute:
        return minute - 1, max_minute + 1
    padding = max(1, round((max_minute - minute) * 0.05))
    return minute - padding, max_minute + padding


def _view_revision(variant: StoryVariant, groups: list[TimelineGroup]) -> str:
    payload = {
        "variant": variant.revision,
        "origin": variant.time_origin_label,
        "groups": [
            {
                "chapter": group.chapter_number,
                "kind": group.chapter_kind,
                "nodes": [
                    [row.node_id, row.start_minute, row.end_minute, row.status]
                    for row in group.nodes
                ],
            }
            for group in groups
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def build_timeline(
    workspace: Workspace, variant: StoryVariant, *, ledger_revision: str = ""
) -> StoryTimelineView:
    chapters = _chapter_index(workspace)
    by_node: dict[str, list[ProseBinding]] = defaultdict(list)
    for binding in variant.bindings.values():
        if binding.node_id is not None:
            by_node[binding.node_id].append(binding)

    files: dict[str, FileView | None] = {}
    #: (披露章节, 这一章是否有真实正文出处, 节点投影)
    rows: list[tuple[int | None, bool, TimelineNode]] = []
    for node in variant.nodes.values():
        bindings = sorted(by_node.get(node.id, []), key=lambda row: row.id)
        facts = []
        for binding in bindings:
            if binding.path not in files:
                try:
                    files[binding.path] = workspace.read(binding.path)
                except Exception:  # noqa: BLE001 - 读不到正文仍要展示时间轴
                    files[binding.path] = None
            file = files[binding.path]
            anchor = (
                Anchor(state="unavailable")
                if file is None
                else anchor_state(binding, file.text, file.digest)
            )
            facts.append(binding_fact(binding, anchor))
        status, reasons = node_status(
            facts,
            has_open_finding=False,
            current_contract=contract_digest(variant, node),
        )
        chapter, kind = _reveal(node, bindings, chapters)
        span = node.event_span
        rows.append(
            (
                chapter,
                kind == "actual",
                TimelineNode(
                    node_id=node.id,
                    title=node.title,
                    time_note=node.time_note,
                    start_minute=span.start_minute if span is not None else None,
                    end_minute=span.end_minute if span is not None else None,
                    approximate=span.approximate if span is not None else False,
                    status=status,
                    reasons=list(reasons),
                    binding_ids=[binding.id for binding in bindings],
                    chapter_paths=sorted({binding.path for binding in bindings}),
                ),
            )
        )

    grouped: dict[int | None, list[TimelineNode]] = defaultdict(list)
    actual: dict[int | None, bool] = defaultdict(bool)
    for chapter, from_prose, row in rows:
        grouped[chapter].append(row)
        actual[chapter] = actual[chapter] or from_prose

    def kind_of(chapter: int | None) -> ChapterKind:
        if chapter is None:
            return "unassigned"
        return "actual" if actual[chapter] else "planned"

    start_order = {"actual": 0, "planned": 1, "unassigned": 2}
    groups: list[TimelineGroup] = []
    for chapter in sorted(
        grouped,
        key=lambda value: (start_order[kind_of(value)], value if value is not None else 10**9),
    ):
        nodes = sorted(
            grouped[chapter],
            key=lambda row: (
                variant.nodes[row.node_id].reveal_order
                if variant.nodes[row.node_id].reveal_order is not None
                else 10**9,
                row.node_id,
            ),
        )
        groups.append(
            TimelineGroup(
                chapter_number=chapter,
                chapter_kind=kind_of(chapter),
                title=f"第 {chapter} 章" if chapter is not None else "尚未安排章节",
                nodes=nodes,
            )
        )

    points = [
        (row.start_minute, row.end_minute)
        for _, _, row in rows
        if row.start_minute is not None and row.end_minute is not None
    ]
    domain = None
    if points:
        low, high = _expand(min(item[0] for item in points), max(item[1] for item in points))
        domain = TimelineDomain(min=low, max=high)

    return StoryTimelineView(
        variant_id=variant.id,
        variant_revision=variant.revision,
        ledger_revision=ledger_revision,
        view_revision=_view_revision(variant, groups),
        time_origin_label=variant.time_origin_label,
        domain=domain,
        groups=groups,
    )
