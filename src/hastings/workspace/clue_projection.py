"""伏笔账投影（v1.3 P3）：把章级线索聚合成人类可读的 markdown 表。

投影是**派生视图**，不是第二权威（提案 §2 决策 2）。它由各章 ``clues/ch{N}.json``
现算现渲染，随时可重建；坏了删掉重跑即可，不存在双写同步。

三条边界：

- **不写回权威数据。** 生成的文件只读给人看，改它不会改变任何线索状态。
- **幂等。** 同一份聚合结果渲染出的文本逐字节相同；内容没变就不碰文件
  （不改 mtime，不给 Git 制造空 diff）。
- **不猜。** 聚合视图里没有的线索不会出现在表里；计划线索单独一节列出，
  说明「结构层声明了，但还没登记」。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hastings.workspace.atomic_io import atomic_write_bytes
from hastings.workspace.chapter_clues import (
    ClueAggregate,
    CluePlan,
    ClueState,
    aggregate_clues,
)

__all__ = [
    "PROJECTION_FILENAME",
    "PROJECTION_MARKER",
    "ProjectionResult",
    "refresh_projection",
    "render_ledger",
]

#: 投影默认落在作品根目录，与 ``STRUCTURE.md`` 同级。
PROJECTION_FILENAME = "伏笔账.md"

#: 文件头标记：告诉读者这份表是生成的，手工编辑会在下次刷新时被覆盖。
PROJECTION_MARKER = (
    "<!-- 本文件由线索聚合自动生成，请勿手工编辑；权威数据在各章 clues/chN.json。 -->"
)

_STATUS_LABEL: dict[str, str] = {
    "planted": "未回收",
    "advanced": "已推进",
    "resolved": "已回收",
    "retired": "不收",
}

_COUNT_ORDER = ("planted", "advanced", "resolved", "retired")


def _cell(text: str) -> str:
    """表格单元转义：竖线会切断列，换行会切断行。"""
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _chapter_label(chapter: int | None) -> str:
    return f"第 {chapter} 章" if chapter is not None else "—"


def _anchor_label(state: ClueState) -> str:
    if state.anchor_prefix:
        return _cell(state.anchor_prefix) + "…"
    if state.status in {"planted", "advanced"}:
        return "未锚定"
    return "—"


def _sort_key(state: ClueState) -> tuple[int, int, str]:
    planted = state.planted_in if state.planted_in is not None else 10**6
    return (planted, state.last_chapter, state.id)


def _summary(states: list[ClueState]) -> str:
    if not states:
        return "暂无线索登记。"
    counts = {status: 0 for status in _COUNT_ORDER}
    for state in states:
        counts[state.status] = counts.get(state.status, 0) + 1
    parts = [f"{_STATUS_LABEL[status]} {counts[status]}" for status in _COUNT_ORDER]
    return f"共 {len(states)} 条线索：" + "、".join(parts) + "。"


def render_ledger(
    aggregate: ClueAggregate,
    plans: list[CluePlan] | tuple[CluePlan, ...] = (),
    *,
    title: str = "伏笔账",
) -> str:
    """渲染伏笔账 markdown：摘要 + 线索表 + 计划未登记 + 对账发现项。"""
    states = sorted(aggregate.states.values(), key=_sort_key)
    lines: list[str] = [f"# {title}", "", PROJECTION_MARKER, "", _summary(states), ""]

    if states:
        lines.extend(
            [
                "| 编号 | 线索 | 类型 | 埋设 | 回收 | 状态 | 锚 |",
                "|---|---|---|---|---|---|---|",
            ]
        )
        plan_by_id = {plan.id: plan for plan in plans}
        for state in states:
            plan = plan_by_id.get(state.id)
            resolved_in = state.resolved_in
            if resolved_in is None and plan is not None:
                resolved_in = plan.resolve_chapter
            note = state.note or ""
            lines.append(
                "| "
                + " | ".join(
                    [
                        _cell(state.id),
                        _cell(note),
                        _cell(state.kind),
                        _chapter_label(state.planted_in),
                        _chapter_label(resolved_in),
                        _STATUS_LABEL.get(state.status, state.status),
                        _anchor_label(state),
                    ]
                )
                + " |"
            )
        lines.append("")

    pending = [plan for plan in plans if plan.id not in aggregate.states]
    if pending:
        lines.extend(
            [
                "## 计划线索（结构层已声明，尚未登记）",
                "",
                "| 编号 | 线索 | 埋设 | 回收 |",
                "|---|---|---|---|",
            ]
        )
        for plan in sorted(pending, key=lambda item: (item.plant_chapter, item.id)):
            lines.append(
                "| "
                + " | ".join(
                    [
                        _cell(plan.id),
                        _cell(plan.note or ""),
                        _chapter_label(plan.plant_chapter),
                        _chapter_label(plan.resolve_chapter),
                    ]
                )
                + " |"
            )
        lines.append("")

    lines.extend(["## 对账", ""])
    if aggregate.findings:
        lines.extend(f"- [{finding.code}] {finding.message}" for finding in aggregate.findings)
    else:
        lines.append("无未决项。")
    lines.append("")
    return "\n".join(lines)


@dataclass(frozen=True)
class ProjectionResult:
    """一次刷新的结果：写到哪、有没有变。"""

    path: Path
    changed: bool
    text: str


def refresh_projection(
    root: Path,
    chapters: list[int] | tuple[int, ...],
    *,
    plans: list[CluePlan] | tuple[CluePlan, ...] = (),
    prose_by_chapter: dict[int, str] | None = None,
    filename: str = PROJECTION_FILENAME,
) -> ProjectionResult:
    """重算聚合视图并刷新投影文件；内容没变时不写盘（幂等）。"""
    aggregate = aggregate_clues(
        root, chapters, prose_by_chapter=prose_by_chapter, plans=list(plans)
    )
    text = render_ledger(aggregate, plans)
    path = Path(root) / filename
    data = text.encode("utf-8")
    if path.is_file():
        try:
            if path.read_bytes() == data:
                return ProjectionResult(path=path, changed=False, text=text)
        except OSError:
            pass
    atomic_write_bytes(path, data)
    return ProjectionResult(path=path, changed=True, text=text)
