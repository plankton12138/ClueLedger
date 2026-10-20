"""ConversationStore 端口（Phase 19.3 T19.3b）：对话状态、轮次与流式事件的持久化。

对话是项目辅助数据，与版本化故事内容分开存储：本端口不产生 Repository
Revision，也不接触 Job 或领域写端口（四透镜②：聊天拿不到写能力）。

单轮执行与「停止后迟到分片无效」由存储层在其事务内裁决：``open_turn`` 原子
创建用户消息与轮次并检查同会话是否已有活动轮次；``append_delta``/``settle_turn``
只在该轮次仍运行时生效，终态后一律丢弃（计划 §4.1）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from hastings.domain.conversation.models import ChatEvent, ChatMessage, ChatTurn, Conversation

__all__ = ["ConversationStore", "OpenTurn"]


@dataclass(frozen=True)
class OpenTurn:
    """``open_turn`` 结果：幂等重放时 ``created`` 为 False 且返回既有轮次。"""

    turn: ChatTurn
    user_message: ChatMessage
    created: bool


@runtime_checkable
class ConversationStore(Protocol):
    """对话存储端口（异步 I/O；实现落在 infrastructure）。"""

    async def create_conversation(
        self,
        project_id: str,
        title: str,
        conversation_id: str | None = None,
    ) -> Conversation:
        """创建会话。

        ``conversation_id`` 显式给出时使用该身份（V03-2.1 创作记录：确定性
        会话 ID）；已存在时幂等返回既有会话，不覆盖标题。
        """

    async def get_conversation(self, conversation_id: str) -> Conversation | None:
        """按 id 读取；不存在返回 None（合法空结果，非异常）。"""

    async def list_conversations(self, project_id: str) -> list[Conversation]: ...

    async def open_turn(
        self,
        *,
        project_id: str,
        conversation_id: str,
        idempotency_key: str,
        fingerprint: str,
        user_message: ChatMessage,
        turn: ChatTurn,
    ) -> OpenTurn:
        """原子创建用户消息与轮次并发出 ``turn.accepted``。

        同一会话已有活动轮次时抛出 ``CHAT_CONVERSATION_BUSY``；同键不同输入
        抛出 ``CHAT_IDEMPOTENCY_CONFLICT``；同键同输入返回既有轮次（created=False）。
        """

    async def get_turn(self, turn_id: str) -> ChatTurn | None: ...

    async def start_turn(
        self, turn_id: str, *, assistant_message: ChatMessage, model: str
    ) -> bool:
        """``queued → running``：插入流式助手消息并发出 ``turn.started``。

        仅当轮次仍为排队态才生效；已被取消时返回 False（执行方应放弃）。
        """

    async def append_delta(self, turn_id: str, text: str) -> bool:
        """把片段追加到该轮流式消息并发出 ``message.delta``；非运行态返回 False。"""

    async def settle_turn(
        self,
        turn_id: str,
        *,
        status: str,
        end_reason: str,
        text: str | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> bool:
        """把轮次收束到终态并发出对应终态事件；已是终态返回 False（不复活）。"""

    async def get_message(self, message_id: str) -> ChatMessage | None: ...

    async def list_messages(self, conversation_id: str) -> list[ChatMessage]: ...

    async def append_message(self, message: ChatMessage) -> None:
        """追加一条非轮次消息（任务卡等结构化载体）；同 id 覆盖为更新。"""

    async def append_event(
        self,
        *,
        project_id: str,
        conversation_id: str,
        turn_id: str | None,
        event: Mapping[str, Any],
    ) -> ChatEvent:
        """追加一条会话事件并分配序号（仅在会话内递增）。"""

    async def events_after(self, conversation_id: str, after: int) -> list[ChatEvent]: ...

    async def mark_interrupted(self) -> int:
        """把遗留的排队/运行轮次标为中断，返回处理条数（启动恢复）。"""
