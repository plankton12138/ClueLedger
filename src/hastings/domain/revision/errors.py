"""整体调整领域违规错误类型与构造助手（V03-7.2）。

与 ``domain/manuscript/errors.py`` 同构：领域层不依赖 application 错误信封
（依赖方向：domain 不得 import application），因此这里定义纯领域的
``CoverageViolation``；application 边界再把它原样透传为 ``ViolationError``。
"""

from __future__ import annotations

from typing import Any

__all__ = ["CoverageViolation", "violation"]


class CoverageViolation(Exception):
    """原文覆盖台账硬不变量被违反；不得产生可提交的覆盖记录。"""

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


def violation(code: str, where: str, why: str, fix: str, **details: Any) -> CoverageViolation:
    """三要素错误入口：哪里错 / 为什么错 / 怎样算对。"""
    return CoverageViolation(code=code, where=where, why=why, fix=fix, details=details)
