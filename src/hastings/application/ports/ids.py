"""IdGenerator 端口：稳定、不可复用 ID 的唯一来源（Phase 11 冻结名称，T11.5）。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["IdGenerator"]


@runtime_checkable
class IdGenerator(Protocol):
    """ID 生成端口。"""

    def new_id(self, prefix: str) -> str:
        """生成形如 ``<prefix>_<唯一后缀>`` 的 ID。

        ID 必须全局稳定且不可复用；显示名可变，不得作为主键
        （modules/README.md §4 跨模块约束 1）。
        """
