"""对话领域模型：Conversation / ChatMessage / ChatTurn / ChatEvent 信封。

字段与前端契约（`frontend/src/api/chat.ts`）逐字对应，序号仅在会话内递增，
与 Job、业务 Commit 游标分别管理（计划 §6.3）。序列化使用普通 JSON 字典，
时间戳为 ISO 字符串，避免在领域层引入 pydantic 或数据库依赖。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from hastings.domain.conversation import error_codes as ec
from hastings.domain.conversation.errors import ConversationViolation, violation

__all__ = [
    "ChatEvent",
    "ChatMessage",
    "ChatTurn",
    "Conversation",
    "ConversationViolation",
    "KIND_CANDIDATE",
    "KIND_RESULT",
    "KIND_TASK_DRAFT",
    "KIND_TASK_EXECUTION",
    "KIND_TEXT",
    "MESSAGE_COMPLETED",
    "MESSAGE_FAILED",
    "MESSAGE_NOT_REQUESTED",
    "MESSAGE_STOPPED",
    "MESSAGE_STREAMING",
    "ROLE_ASSISTANT",
    "ROLE_AUTHOR",
    "ROLE_SYSTEM",
    "TURN_COMPLETED",
    "TURN_FAILED",
    "TURN_INTERRUPTED",
    "TURN_QUEUED",
    "TURN_RUNNING",
    "TURN_STOPPED",
    "is_terminal_turn",
    "message_status_for_turn",
    "parse_timestamp",
]

# 角色
ROLE_AUTHOR = "author"
ROLE_ASSISTANT = "assistant"
ROLE_SYSTEM = "system"
_ROLES = frozenset((ROLE_AUTHOR, ROLE_ASSISTANT, ROLE_SYSTEM))

# 轮次状态（§4.1 对话轮次状态流）
TURN_QUEUED = "queued"
TURN_RUNNING = "running"
TURN_COMPLETED = "completed"
TURN_STOPPED = "stopped"
TURN_FAILED = "failed"
TURN_INTERRUPTED = "interrupted"
_TURN_STATUSES = frozenset(
    (TURN_QUEUED, TURN_RUNNING, TURN_COMPLETED, TURN_STOPPED, TURN_FAILED, TURN_INTERRUPTED)
)
_TERMINAL_TURNS = frozenset((TURN_COMPLETED, TURN_STOPPED, TURN_FAILED, TURN_INTERRUPTED))

# 消息状态
MESSAGE_STREAMING = "streaming"
MESSAGE_COMPLETED = "completed"
MESSAGE_STOPPED = "stopped"
MESSAGE_FAILED = "failed"
#: 作者消息已保存但从未请求模型（M02/V03-1.2 无模型想法）：
#: 与「失败」区分——这不是错误，是没有发起过调用。
MESSAGE_NOT_REQUESTED = "not_requested"
_MESSAGE_STATUSES = frozenset(
    (
        MESSAGE_STREAMING,
        MESSAGE_COMPLETED,
        MESSAGE_STOPPED,
        MESSAGE_FAILED,
        MESSAGE_NOT_REQUESTED,
    )
)

# 消息类型
KIND_TEXT = "text"
KIND_TASK_DRAFT = "task_draft"
KIND_TASK_EXECUTION = "task_execution"
KIND_CANDIDATE = "candidate"
KIND_RESULT = "result"
_KINDS = frozenset((KIND_TEXT, KIND_TASK_DRAFT, KIND_TASK_EXECUTION, KIND_CANDIDATE, KIND_RESULT))


def is_terminal_turn(status: str) -> bool:
    """轮次是否已到终态；终态后迟到分片不得复活。"""
    return status in _TERMINAL_TURNS


def message_status_for_turn(status: str) -> str:
    """轮次终态映射到流式消息收束状态（部分回复保留）。"""
    if status == TURN_COMPLETED:
        return MESSAGE_COMPLETED
    if status == TURN_STOPPED:
        return MESSAGE_STOPPED
    return MESSAGE_FAILED


def parse_timestamp(value: str) -> datetime:
    """解析 ISO 时间戳；写入存储前拒绝朴素时间（与 serialization._norm 同规矩）。"""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise violation(
            ec.CHAT_UNKNOWN_FIELD_VALUE,
            "chat.timestamp",
            f"时间戳 {value!r} 缺少时区",
            "使用带时区的 UTC ISO 字符串",
        )
    return parsed


@dataclass(frozen=True)
class Conversation:
    conversation_id: str
    project_id: str
    title: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "project_id": self.project_id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Conversation:
        return cls(
            conversation_id=str(data["conversation_id"]),
            project_id=str(data["project_id"]),
            title=str(data["title"]),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
        )


@dataclass(frozen=True)
class ChatMessage:
    message_id: str
    conversation_id: str
    turn_id: str | None
    role: str
    kind: str
    text: str
    refs: list[dict[str, Any]] = field(default_factory=list)
    model: str | None = None
    status: str = MESSAGE_COMPLETED
    error: dict[str, Any] | None = None
    created_at: str = ""
    payload: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "message_id": self.message_id,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "role": self.role,
            "kind": self.kind,
            "text": self.text,
            "refs": [dict(ref) for ref in self.refs],
            "model": self.model,
            "status": self.status,
            "error": dict(self.error) if self.error is not None else None,
            "created_at": self.created_at,
        }
        if self.payload is not None:
            data["payload"] = dict(self.payload)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ChatMessage:
        role = str(data["role"])
        kind = str(data["kind"])
        status = str(data["status"])
        if role not in _ROLES:
            raise violation(
                ec.CHAT_UNKNOWN_FIELD_VALUE,
                "chat.message.role",
                f"未知消息角色 {role!r}",
                f"使用 {sorted(_ROLES)} 之一",
            )
        if kind not in _KINDS:
            raise violation(
                ec.CHAT_UNKNOWN_FIELD_VALUE,
                "chat.message.kind",
                f"未知消息类型 {kind!r}",
                f"使用 {sorted(_KINDS)} 之一",
            )
        if status not in _MESSAGE_STATUSES:
            raise violation(
                ec.CHAT_UNKNOWN_FIELD_VALUE,
                "chat.message.status",
                f"未知消息状态 {status!r}",
                f"使用 {sorted(_MESSAGE_STATUSES)} 之一",
            )
        raw_refs = data.get("refs") or []
        raw_error = data.get("error")
        raw_payload = data.get("payload")
        return cls(
            message_id=str(data["message_id"]),
            conversation_id=str(data["conversation_id"]),
            turn_id=None if data.get("turn_id") is None else str(data["turn_id"]),
            role=role,
            kind=kind,
            text=str(data.get("text", "")),
            refs=[dict(ref) for ref in raw_refs],
            model=None if data.get("model") is None else str(data["model"]),
            status=status,
            error=None if raw_error is None else dict(raw_error),
            created_at=str(data.get("created_at", "")),
            payload=None if raw_payload is None else dict(raw_payload),
        )


@dataclass(frozen=True)
class ChatTurn:
    turn_id: str
    conversation_id: str
    status: str
    model: str | None
    end_reason: str | None
    idempotency_key: str
    retry_of: str | None = None
    model_binding: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "conversation_id": self.conversation_id,
            "status": self.status,
            "model": self.model,
            "end_reason": self.end_reason,
            "idempotency_key": self.idempotency_key,
            "retry_of": self.retry_of,
            "model_binding": self.model_binding,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ChatTurn:
        status = str(data["status"])
        if status not in _TURN_STATUSES:
            raise violation(
                ec.CHAT_UNKNOWN_FIELD_VALUE,
                "chat.turn.status",
                f"未知轮次状态 {status!r}",
                f"使用 {sorted(_TURN_STATUSES)} 之一",
            )
        return cls(
            turn_id=str(data["turn_id"]),
            conversation_id=str(data["conversation_id"]),
            status=status,
            model=None if data.get("model") is None else str(data["model"]),
            end_reason=None if data.get("end_reason") is None else str(data["end_reason"]),
            idempotency_key=str(data.get("idempotency_key", "")),
            retry_of=None if data.get("retry_of") is None else str(data["retry_of"]),
            model_binding=dict(data["model_binding"]) if data.get("model_binding") else None,
        )


@dataclass(frozen=True)
class ChatEvent:
    """流式事件信封：序号仅在会话内递增。"""

    project_id: str
    conversation_id: str
    turn_id: str | None
    sequence: int
    event: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "sequence": self.sequence,
            "event": dict(self.event),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ChatEvent:
        return cls(
            project_id=str(data["project_id"]),
            conversation_id=str(data["conversation_id"]),
            turn_id=None if data.get("turn_id") is None else str(data["turn_id"]),
            sequence=int(data["sequence"]),
            event=dict(data["event"]),
        )
