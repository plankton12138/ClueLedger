"""Story Domain 违规错误类型与构造助手。

领域层不依赖 application 的错误信封（依赖方向：domain 不得 import
application）。因此这里定义纯领域的 `StoryViolation`，携带与
application ViolationError 相同的四要素；application handler 在边界处
将其转换为 ViolationError（应用错误信封），领域内部只认 StoryViolation。
"""

from __future__ import annotations

from typing import Any

__all__ = ["StoryViolation", "violation"]


class StoryViolation(Exception):
    """领域硬不变量被违反；不得产生可提交变化（modules §5 不变量 12）。"""

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


def violation(code: str, where: str, why: str, fix: str, **details: Any) -> StoryViolation:
    """三要素错误入口：哪里错 / 为什么错 / 怎样算对。"""
    return StoryViolation(code=code, where=where, why=why, fix=fix, details=details)
