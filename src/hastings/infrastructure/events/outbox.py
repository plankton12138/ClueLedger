"""Outbox Publisher：按项目 sequence 发布、成功后标记（Phase 12 T12.7，ADR-007）。

职责边界（计划「禁止提前做的事」）：Publisher 只消费，绝不分配项目 sequence。
发布与标记之间存在天然崩溃窗口——进程可能在 sink 返回后、mark_published 前
死掉，事件会被再次投递；因此消费方必须按 event_id 去重
（DeduplicatingSink 是参考实现），重复投递不允许产生重复业务效果。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol, runtime_checkable

from hastings.application.ports.events import EventOutbox

__all__ = ["DeduplicatingSink", "OutboxPublisher", "PublishSink"]


@runtime_checkable
class PublishSink(Protocol):
    """事件出口：消息队列、事件总线或任何下游消费者。"""

    async def publish(self, event: Mapping[str, object]) -> None:
        """投递单个事件；抛异常表示本轮发布失败，事件保持待发布。"""


class OutboxPublisher:
    """可重试发布器：pending → publish → mark_published，逐事件推进。"""

    def __init__(self, outbox: EventOutbox, sink: PublishSink) -> None:
        self._outbox = outbox
        self._sink = sink

    async def publish_pending(self, project_id: str, limit: int = 100) -> int:
        """发布指定项目的待发布事件，返回成功发布并标记的数量。

        单事件失败立即停止（保持顺序），该事件保持未发布，下次重试；
        已发布但未标记的事件会因重试被再次投递——去重由消费方负责。
        """
        published = 0
        while published < limit:
            batch = await self._outbox.pending(project_id, limit - published)
            if not batch:
                break
            for event in batch:
                await self._sink.publish(event)
                await self._outbox.mark_published(str(event["id"]))
                published += 1
        return published


class DeduplicatingSink:
    """按 event_id 去重的参考消费端：重复投递只产生一次业务效果。

    记账时机：handler 成功返回后才写入 ``seen_event_ids`` 与 ``effects``——
    若在调用前记账，handler 失败的事件会在重试时被去重拦截、永不执行，
    随后又被 Publisher 标记为已发布，事件被静默丢失。

    这是**参考实现**：内存 seen 集合进程重启即失效。生产消费端必须把
    「业务副作用」与「event_id 已消费记录」放进同一事务，才能证明可靠去重。
    """

    def __init__(
        self,
        handler: Callable[[Mapping[str, object]], Awaitable[None]] | None = None,
    ) -> None:
        self._handler = handler
        self.seen_event_ids: set[str] = set()
        self.effects: list[Mapping[str, object]] = []

    async def publish(self, event: Mapping[str, object]) -> None:
        event_id = str(event["id"])
        if event_id in self.seen_event_ids:
            return  # 重复投递：不产生业务效果
        if self._handler is not None:
            await self._handler(event)  # 失败则不入账，事件保持待发布待重试
        self.seen_event_ids.add(event_id)
        self.effects.append(event)
