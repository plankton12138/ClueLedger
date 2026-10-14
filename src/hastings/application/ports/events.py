"""EventOutbox 端口：业务事件的消费出口（Phase 11 冻结名称，T11.5；评审收口修订边界）。

事件写入的唯一入口是 ``CommitRepository.commit``：CommitRequest 携带待发布事件，
与 Commit 同事务落盘（ADR-007）。应用层不得直接向 Outbox 追加事件——独立的
append 通道会重新制造 ADR-007 要消灭的「数据已写入但事件丢失」双写窗口。
无 Commit 的任务进度事件走 JobStore 任务通道，不得进入业务 Outbox
（modules/README.md §4 约束 7）。事件 sequence 由仓库在提交时分配；
发布器按序消费，不分配序号（Phase 12 T12.7）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

__all__ = ["EventOutbox"]


@runtime_checkable
class EventOutbox(Protocol):
    """Outbox 消费端口（发布器视角）。"""

    async def pending(self, project_id: str, limit: int) -> list[Mapping[str, object]]:
        """按提交序列读取指定项目的未发布事件，最多 limit 条。"""

    async def mark_published(self, event_id: str) -> None:
        """标记事件已发布；重复标记必须幂等（发布器崩溃重投场景）。"""
