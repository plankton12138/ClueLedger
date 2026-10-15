"""Changes Domain 违规错误类型与构造助手（与 Manuscript/Story 同构）。

领域层不依赖 application 的错误信封；application handler 在边界处把
``ChangesViolation`` 转成 ``ViolationError``，四要素原样透传（四透镜①）。
"""

from __future__ import annotations

from typing import Any

__all__ = ["ChangesViolation", "violation"]


class ChangesViolation(Exception):
    """变更工作流硬不变量被违反；不得产生可提交变化。"""

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


def violation(code: str, where: str, why: str, fix: str, **details: Any) -> ChangesViolation:
    """三要素错误入口：哪里错 / 为什么错 / 怎样算对。"""
    return ChangesViolation(code=code, where=where, why=why, fix=fix, details=details)
