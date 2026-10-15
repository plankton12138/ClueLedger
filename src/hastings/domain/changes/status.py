"""ChangeSet 状态机（modules/change-impact.md §3）。

合法迁移表是**硬约束**：命令层在产生任何可提交变化之前拒绝非法迁移，
不依赖调用方自觉（四透镜②）。终态（committed/abandoned）不可迁出；
状态变化全部记录 actor、时间、原因与基础 Commit（T15.1）。
"""

from __future__ import annotations

from hastings.domain.changes import error_codes as ec
from hastings.domain.changes.errors import violation

__all__ = [
    "CHANGE_ABANDONED",
    "CHANGE_ANALYZED",
    "CHANGE_ANALYZING",
    "CHANGE_APPROVED",
    "CHANGE_COMMITTED",
    "CHANGE_EXECUTING",
    "CHANGE_FAILED",
    "CHANGE_OPENED",
    "CHANGE_REVIEW",
    "LEGAL_TRANSITIONS",
    "TERMINAL_STATUSES",
    "require_transition",
]

CHANGE_OPENED = "opened"
CHANGE_ANALYZING = "analyzing"
CHANGE_ANALYZED = "analyzed"
CHANGE_APPROVED = "approved"
CHANGE_EXECUTING = "executing"
CHANGE_REVIEW = "review"
CHANGE_COMMITTED = "committed"
CHANGE_ABANDONED = "abandoned"
CHANGE_FAILED = "failed"

# 与模块文档 §3 状态图一致；终态为空集
LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    CHANGE_OPENED: frozenset({CHANGE_ANALYZING}),
    CHANGE_ANALYZING: frozenset({CHANGE_ANALYZED, CHANGE_FAILED}),
    CHANGE_ANALYZED: frozenset({CHANGE_APPROVED, CHANGE_ABANDONED}),
    CHANGE_APPROVED: frozenset({CHANGE_EXECUTING}),
    CHANGE_EXECUTING: frozenset({CHANGE_REVIEW, CHANGE_FAILED}),
    CHANGE_REVIEW: frozenset({CHANGE_EXECUTING, CHANGE_COMMITTED, CHANGE_ABANDONED}),
    CHANGE_FAILED: frozenset({CHANGE_ANALYZING}),
    CHANGE_COMMITTED: frozenset(),
    CHANGE_ABANDONED: frozenset(),
}

TERMINAL_STATUSES = frozenset({CHANGE_COMMITTED, CHANGE_ABANDONED})


def require_transition(current: str, target: str, *, where: str) -> None:
    """校验一次状态迁移是否合法；非法即拒绝（不产生可提交变化）。"""
    legal = LEGAL_TRANSITIONS.get(current, frozenset())
    if target not in legal:
        raise violation(
            ec.CHANGES_INVALID_TRANSITION,
            where,
            f"不允许从 {current!r} 迁移到 {target!r}",
            f"当前状态的合法迁移是 {sorted(legal)}；若需重开请建立新的 ChangeSet"
            f"（终态 {sorted(TERMINAL_STATUSES)} 不可迁出）",
            current=current,
            target=target,
        )
