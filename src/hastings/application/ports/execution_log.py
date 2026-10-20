"""ExecutionLog 端口（V03-1.6）：公共执行日志的写入/分页/订阅。

与业务 Outbox（events.py）的边界：Outbox 是「与 Commit 同事务」的业务事件，
由仓库唯一写入；执行日志是运行事件的公共投影（任务开始/结束、失败/停止等），
由应用服务经 ``append_once`` 幂等追加。日志有自己的作品游标（per-project
sequence），与 Outbox、Job 事件、对话 SSE 的序号互不可比（M13）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from hastings.domain.execution_log import ExecutionEvent

__all__ = ["ExecutionLog", "LogPage"]


@dataclass(frozen=True, slots=True)
class LogPage:
    """一页历史事件；``next_cursor`` 为 None 表示没有更多。"""

    events: tuple[ExecutionEvent, ...]
    next_cursor: int | None


@runtime_checkable
class ExecutionLog(Protocol):
    """执行日志端口：历史分页、实时订阅、断线补齐共用同一事件 ID 空间。"""

    async def append_once(self, source_event_id: str, event: ExecutionEvent) -> bool:
        """按源事件 ID 幂等追加；重复投递返回 False，不产生第二条。

        ``event.sequence`` 是占位值，实际序号由实现在追加时分配。
        """
        ...

    async def page(
        self,
        project_id: str,
        after_sequence: int,
        limit: int,
        *,
        task_id: str | None = None,
        event_type: str | None = None,
    ) -> LogPage:
        """按作品游标取 ``sequence > after_sequence`` 的事件，至多 limit 条。"""
        ...

    def subscribe(self, project_id: str, after_sequence: int) -> AsyncIterator[ExecutionEvent]:
        """从游标起持续产出新事件（实现内部轮询即可）；不回放游标之前的历史。

        声明为同步方法返回 AsyncIterator：实现是 async generator，调用即得
        迭代器（`async def` + `yield` 的调用结果不是协程）。
        """
        ...
