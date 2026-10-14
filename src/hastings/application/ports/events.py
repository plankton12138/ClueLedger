"""EventOutbox 端口：与业务提交同事务的事件出口（Phase 11 冻结名称，T11.5）。

事件与 Commit 同事务写入、由独立发布器异步投递（ADR-007）；
无 Commit 的任务进度事件不得伪装成业务完成事件（modules/README.md §4 约束 7）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

__all__ = ["EventOutbox"]


@runtime_checkable
class EventOutbox(Protocol):
    """Outbox 端口。"""

    async def append(
        self, event_type: str, payload: Mapping[str, object], commit_id: str | None
    ) -> str:
        """写入一条待发布事件，返回 event_id；``commit_id`` 为 None 表示任务进度事件。"""

    async def pending(self, limit: int) -> list[Mapping[str, object]]:
        """按提交序列读取未发布事件，最多 limit 条。"""

    async def mark_published(self, event_id: str) -> None:
        """标记事件已发布；重复标记必须幂等（发布器崩溃重投场景）。"""
