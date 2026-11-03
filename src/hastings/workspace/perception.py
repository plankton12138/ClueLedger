"""感知区分（v1.1 M4，纯函数先行）。

契约（q0-contracts 第 5 节）：入档更新项由模型在入档时分类标注——
**记账**（伏笔标记已埋、章节状态更新）静默，仅入档回执可见；
**计划性变化**（改回收点位、新增叙事线、改铁律、删模块）在对话中单独一行说明，
附「不同意，改回来」。装配层（确定性代码）校验标注完整性：分类必须是两个已知值之一、
计划性变化必须带说明、同一更新不重复标注；没有标注到的变更如实报告，不猜、不替模型补。

差异回显三要素：改了什么（label）、影响哪里（affected）、基于哪个修订（base revision）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = [
    "UpdateNote",
    "planned_change_lines",
    "receipt_rows",
    "validate_update_notes",
]

Category = Literal["bookkeeping", "planned"]

#: 两个已知分类；其余值在入档时拒绝（标注完整性校验）。
CATEGORIES: tuple[str, ...] = ("bookkeeping", "planned")

_CATEGORY_LABELS = {"bookkeeping": "记账", "planned": "计划性变化"}

_REVERT_HINT = "不同意，改回来"


@dataclass(frozen=True, slots=True)
class UpdateNote:
    """一条入档更新的分类标注。

    ``label`` 对应变更对象的可读名（与提交收据 ``labels`` 同一粒度）；
    ``affected`` 说明影响哪里（哪些章的骨架/点位随之移动），可为空——
    纯记账通常没有波及面。
    """

    label: str
    category: str
    note: str = ""
    affected: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "label": self.label,
            "category": self.category,
            "category_label": _CATEGORY_LABELS.get(self.category, self.category),
            "note": self.note,
            "affected": self.affected,
        }

    @classmethod
    def from_dict(cls, raw: object) -> UpdateNote | None:
        if not isinstance(raw, dict):
            return None
        label = str(raw.get("label") or "").strip()
        category = str(raw.get("category") or "").strip()
        if not label or category not in CATEGORIES:
            return None
        return cls(
            label=label,
            category=category,
            note=str(raw.get("note") or "").strip(),
            affected=str(raw.get("affected") or "").strip(),
        )


def validate_update_notes(
    notes: list[UpdateNote],
    changed_labels: list[str],
) -> tuple[str, ...]:
    """标注完整性校验（装配层确定性执行）。

    返回问题列表（中文、可直接进回执）：未知分类在 ``from_dict`` 已拦截，这里查
    计划性变化必须带说明、同一更新不重复标注。没有标注到的变更不在这里报错——
    由调用方把 ``unannotated`` 如实写进回执，不阻断入档（标注是感知层，不是授权层）。
    """
    issues: list[str] = []
    seen: set[str] = set()
    for note in notes:
        if note.label in seen:
            issues.append(f"同一更新重复标注：{note.label}")
        seen.add(note.label)
        if note.category == "planned" and not note.note:
            issues.append(f"计划性变化必须带说明：{note.label}")
    return tuple(issues)


def unannotated_labels(notes: list[UpdateNote], changed_labels: list[str]) -> tuple[str, ...]:
    """变更了但没有标注的更新；装配回执如实列出，不替模型补分类。"""
    covered = {note.label for note in notes}
    return tuple(label for label in changed_labels if label not in covered)


def planned_change_lines(notes: list[UpdateNote], base_revision: str) -> str:
    """计划性变化的对话说明文本；没有计划性变化返回空（记账静默）。

    每条计划性变化单独一行：改了什么 + 影响哪里 + 基于哪个修订，
    行尾统一附「不同意，改回来」——改回由模型改写或走事务整体撤销。
    """
    planned = [note for note in notes if note.category == "planned"]
    if not planned:
        return ""
    revision = f"（基于修订 {base_revision[:12]}）" if base_revision else ""
    lines = []
    for note in planned:
        parts = [note.note or note.label]
        if note.affected:
            parts.append(f"影响：{note.affected}")
        parts.append(_REVERT_HINT)
        lines.append(f"- {'；'.join(parts)}")
    return "本轮有计划性结构变化，请核对：" + revision + "\n" + "\n".join(lines)


def receipt_rows(notes: list[UpdateNote]) -> list[dict[str, str]]:
    """全部标注进回执（含记账）；记账只在这里可见，不进对话回显。"""
    return [note.to_dict() for note in notes]
