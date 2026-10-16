"""Conversation Domain 违规错误类型与构造助手（与 Manuscript/Changes 同构）。

领域层不依赖 application 的错误信封；application/接口边界把
``ConversationViolation`` 转成 ``ViolationError``，四要素原样透传。
"""

from __future__ import annotations

from typing import Any

__all__ = ["ConversationViolation", "violation"]


class ConversationViolation(Exception):
    """对话领域硬不变量被违反；不得产生可发布的轮次或消息。"""

    def __init__(
        self,
        code: str,
        where: str,
        why: str,
        fix: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.where = where
        self.why = why
        self.fix = fix
        self.details = dict(details or {})
        super().__init__(f"[{code}] {where}: {why} -> {fix}")


def violation(code: str, where: str, why: str, fix: str, **details: Any) -> ConversationViolation:
    """三要素错误入口：哪里错 / 为什么错 / 怎样算对。"""
    return ConversationViolation(code=code, where=where, why=why, fix=fix, details=details)
