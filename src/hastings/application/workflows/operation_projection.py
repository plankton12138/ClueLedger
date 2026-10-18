"""固定讨论投影消费者（V03-2.1，计划 §4.2 / §6）。

消费 ``manuscript.operation.committed`` Outbox 事件，把操作结果以**展示引用**
补进本作品「创作记录」讨论：

- 会话 ID 由作品确定性派生（``creation_log_conversation_id``），默认讨论唯一；
  同一作品的全部操作落同一会话，投影重试/重启都幂等回到同一位置。
- **不能投递到当时正在打开的会话**：消费者只写固定会话，作者会话零接触——
  异步结果到达不夺走作者的阅读与输入位置（M01）。
- 幂等：引用消息 ID 由 operation_id 确定性派生，``append_message`` 同 ID
  覆盖为更新——Outbox 崩溃窗口的重复投递不产生重复消息。
- 失败语义（§4.2）：本消费者抛异常只让事件保持待发布（Outbox 重试），
  **不回滚已经原子生效的正文**；恢复后只补展示，不再次运行模型、不创建版本。

与 ``CountingProjectionSink`` 通过 ``FanOutSink`` 并联：一条事件同时进计数
端与投影端，任一端失败该轮发布停止、事件保持待发布。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from hastings.application.ports.conversations import ConversationStore
from hastings.domain.conversation import models as m
from hastings.domain.manuscript.operation_record import (
    OPERATION_EVENT_COMMITTED,
    creation_log_conversation_id,
    operation_reference_message_id,
)

__all__ = ["FanOutSink", "OperationDiscussionProjector"]

logger = logging.getLogger(__name__)

_CREATION_LOG_TITLE = "创作记录"


class FanOutSink:
    """把同一事件扇出给多个消费端；异常向上传播（发布器保持事件待发布）。"""

    def __init__(self, *sinks: Any) -> None:
        if not sinks:
            raise ValueError("FanOutSink 至少需要一个消费端")
        self._sinks = sinks

    async def publish(self, event: Mapping[str, Any]) -> None:
        for sink in self._sinks:
            await sink.publish(event)


class OperationDiscussionProjector:
    """把操作结果投影为「创作记录」讨论里的展示引用（幂等、零模型调用）。"""

    def __init__(self, conversations: ConversationStore) -> None:
        self._conversations = conversations

    async def publish(self, event: Mapping[str, Any]) -> None:
        if str(event.get("type")) != OPERATION_EVENT_COMMITTED:
            return  # 只消费操作事件；其余事件留给对应消费者
        payload = event.get("payload")
        body = payload if isinstance(payload, Mapping) else event
        operation_id = str(body.get("operation_id", ""))
        project_id = str(body.get("project_id", ""))
        if not operation_id or not project_id:
            logger.warning("操作事件缺少 operation_id/project_id，跳过投影：%s", dict(event))
            return
        # 归属会话取**提交时记录的**来源讨论（V03-4.5），不是「现在打开的那个」：
        # 事件里的 conversation_id 在操作提交那一刻固化，之后切讨论不会改变它。
        # 给了来源但会话不存在或不属于本作品时回落到创作记录——宁可归错到默认
        # 讨论，也不能把一条结果投进别的作品。
        conversation_id = creation_log_conversation_id(project_id)
        recorded = str(body.get("conversation_id") or "")
        if recorded and recorded != conversation_id:
            origin = await self._conversations.get_conversation(recorded)
            if origin is None or origin.project_id != project_id:
                logger.warning(
                    "操作 %s 的来源讨论 %s 不可用或不属于作品 %s，回落到创作记录",
                    operation_id,
                    recorded,
                    project_id,
                )
            else:
                conversation_id = recorded
        conversation = await self._conversations.get_conversation(conversation_id)
        if conversation is None:
            await self._conversations.create_conversation(
                project_id, _CREATION_LOG_TITLE, conversation_id=conversation_id
            )
        kind = str(body.get("kind", "operation"))
        chapters = body.get("changed_chapter_ids") or []
        summary = body.get("summary")
        if chapters:
            text = f"操作已完成：{kind}（变更 {len(chapters)} 章）"
        else:
            text = f"操作已完成：{kind}"
        if summary:
            text += f"\n{summary}"
        message = m.ChatMessage(
            message_id=operation_reference_message_id(operation_id),
            conversation_id=conversation_id,
            turn_id=None,
            role=m.ROLE_SYSTEM,
            kind=m.KIND_TEXT,
            text=text,
            status=m.MESSAGE_COMPLETED,
            created_at="",
            payload={
                "operation_id": operation_id,
                "project_id": project_id,
                "changed_chapter_ids": list(chapters),
            },
        )
        # 同 ID 覆盖为更新：重复投递不产生重复消息。
        await self._conversations.append_message(message)
