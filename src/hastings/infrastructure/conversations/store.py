"""ConversationStore 实现（Phase 19.3 T19.3b）：内存与 SQLite 双后端。

状态转换与事件分配在共享基类里写一次，两个后端只提供同步存储原语；
公开方法都是 ``async``，但临界区内不 ``await``，因此一个进程级锁即可保证
「读-判-写 + 事件追加」原子（与 SqliteJobStore 同思路）。

与 Repository 内核使用同一物理 DB 文件但独立连接与表；表用
``CREATE TABLE IF NOT EXISTS`` 增量创建（不触碰 Repository 的 schema 版本）。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from hastings.application.ports.chat import ChatRun, ChatRunner, ChatStreamSink  # noqa: F401
from hastings.application.ports.conversations import OpenTurn
from hastings.domain.conversation import error_codes as ec
from hastings.domain.conversation import models as m
from hastings.domain.conversation.errors import violation
from hastings.domain.repository.serialization import content_hash

__all__ = [
    "MemoryConversationStore",
    "SqliteConversationStore",
    "SystemClockAdapter",
]


class SystemClockAdapter:
    """默认时间源：带时区 UTC ISO 字符串（避免可选的 Clock 依赖）。"""

    def now_iso(self) -> str:
        return datetime.now().astimezone().isoformat()


_Clock = Callable[[], datetime]


class _BaseConversationStore:
    """共享状态机与事件分配；子类实现同步存储原语。"""

    def __init__(self, clock: _Clock | None = None) -> None:
        self._clock: _Clock = clock or (lambda: datetime.now().astimezone())

    # -- 同步原语（子类实现） ------------------------------------------------
    def _begin(self) -> Any:
        raise NotImplementedError

    def _get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def _put_conversation(self, record: dict[str, Any]) -> None:
        raise NotImplementedError

    def _list_conversations(self, project_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def _get_turn(self, turn_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def _put_turn(self, record: dict[str, Any]) -> None:
        raise NotImplementedError

    def _find_turn(self, conversation_id: str, idempotency_key: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def _all_turns(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def _get_message(self, message_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def _put_message(self, record: dict[str, Any]) -> None:
        raise NotImplementedError

    def _list_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def _append_event_row(self, conversation_id: str, body: dict[str, Any]) -> int:
        raise NotImplementedError

    def _events_after(self, conversation_id: str, after: int) -> list[dict[str, Any]]:
        raise NotImplementedError

    # -- 助手 ----------------------------------------------------------------
    def _now(self) -> str:
        return self._clock().isoformat()

    def _assistant_of(self, conversation_id: str, turn_id: str) -> dict[str, Any] | None:
        for record in self._list_messages(conversation_id):
            if record["turn_id"] == turn_id and record["role"] == m.ROLE_ASSISTANT:
                return record
        return None

    def _emit(
        self, project_id: str, conversation_id: str, turn_id: str | None, event: dict[str, Any]
    ) -> int:
        body = {
            "project_id": project_id,
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "event": event,
        }
        return self._append_event_row(conversation_id, body)

    # -- 公开 API ------------------------------------------------------------
    async def create_conversation(self, project_id: str, title: str) -> m.Conversation:
        with self._begin():
            now = self._now()
            conversation = m.Conversation(
                # 同一微秒可能创建多个会话：ID 必须带随机分量，不能只哈希输入。
                conversation_id=f"conv_{uuid.uuid4().hex[:16]}",
                project_id=project_id,
                title=title,
                created_at=now,
                updated_at=now,
            )
            self._put_conversation(conversation.to_dict())
            return conversation

    async def get_conversation(self, conversation_id: str) -> m.Conversation | None:
        record = self._get_conversation(conversation_id)
        return None if record is None else m.Conversation.from_dict(record)

    async def list_conversations(self, project_id: str) -> list[m.Conversation]:
        return [m.Conversation.from_dict(r) for r in self._list_conversations(project_id)]

    async def open_turn(
        self,
        *,
        project_id: str,
        conversation_id: str,
        idempotency_key: str,
        fingerprint: str,
        user_message: m.ChatMessage,
        turn: m.ChatTurn,
    ) -> OpenTurn:
        with self._begin():
            conversation = self._get_conversation(conversation_id)
            if conversation is None or conversation["project_id"] != project_id:
                raise violation(
                    ec.CHAT_CONVERSATION_NOT_FOUND,
                    "chat.conversation",
                    f"项目 {project_id} 下没有会话 {conversation_id}",
                    "在当前项目里新建或选择一个会话再发送",
                )
            existing = self._find_turn(conversation_id, idempotency_key)
            if existing is not None:
                if existing.get("fingerprint") != fingerprint:
                    raise violation(
                        ec.CHAT_IDEMPOTENCY_CONFLICT,
                        "chat.turn.idempotency",
                        "同一请求键对应的输入已变化",
                        "改变内容时使用新的 idempotency_key",
                    )
                message = self._find_user_message(conversation_id, existing["turn_id"])
                assert message is not None
                return OpenTurn(
                    turn=m.ChatTurn.from_dict(existing),
                    user_message=m.ChatMessage.from_dict(message),
                    created=False,
                )
            active = [
                t
                for t in self._all_turns()
                if t["conversation_id"] == conversation_id
                and t["status"] in (m.TURN_QUEUED, m.TURN_RUNNING)
            ]
            if active:
                raise violation(
                    ec.CHAT_CONVERSATION_BUSY,
                    "chat.conversation",
                    "同一会话已有进行中的轮次",
                    "等待当前回复结束或停止后再发送下一轮",
                )
            self._put_message(user_message.to_dict())
            record = turn.to_dict()
            record["fingerprint"] = fingerprint
            self._put_turn(record)
            conversation["updated_at"] = self._now()
            self._put_conversation(conversation)
            self._emit(
                project_id,
                conversation_id,
                turn.turn_id,
                {"type": "turn.accepted", "turn": turn.to_dict()},
            )
            return OpenTurn(turn=turn, user_message=user_message, created=True)

    def _find_user_message(self, conversation_id: str, turn_id: str) -> dict[str, Any] | None:
        for record in self._list_messages(conversation_id):
            if record["turn_id"] == turn_id and record["role"] == m.ROLE_AUTHOR:
                return record
        return None

    async def get_turn(self, turn_id: str) -> m.ChatTurn | None:
        record = self._get_turn(turn_id)
        return None if record is None else m.ChatTurn.from_dict(record)

    async def start_turn(
        self, turn_id: str, *, assistant_message: m.ChatMessage, model: str
    ) -> bool:
        with self._begin():
            turn = self._get_turn(turn_id)
            if turn is None or turn["status"] != m.TURN_QUEUED:
                return False
            conversation = self._get_conversation(turn["conversation_id"])
            assert conversation is not None
            turn["status"] = m.TURN_RUNNING
            turn["model"] = model
            self._put_turn(turn)
            self._put_message(assistant_message.to_dict())
            self._emit(
                conversation["project_id"],
                turn["conversation_id"],
                turn_id,
                {"type": "turn.started", "turn_id": turn_id,
                 "message_id": assistant_message.message_id, "model": model},
            )
            return True

    async def append_delta(self, turn_id: str, text: str) -> bool:
        with self._begin():
            turn = self._get_turn(turn_id)
            if turn is None or turn["status"] != m.TURN_RUNNING:
                return False
            conversation = self._get_conversation(turn["conversation_id"])
            assert conversation is not None
            message = self._assistant_of(turn["conversation_id"], turn_id)
            if message is None:
                return False
            message["text"] = str(message.get("text", "")) + text
            self._put_message(message)
            self._emit(
                conversation["project_id"],
                turn["conversation_id"],
                turn_id,
                {"type": "message.delta", "turn_id": turn_id, "text": text},
            )
            return True

    async def settle_turn(
        self,
        turn_id: str,
        *,
        status: str,
        end_reason: str,
        text: str | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> bool:
        with self._begin():
            turn = self._get_turn(turn_id)
            if turn is None or m.is_terminal_turn(turn["status"]):
                return False
            conversation_id = turn["conversation_id"]
            conversation = self._get_conversation(conversation_id)
            assert conversation is not None
            turn["status"] = status
            turn["end_reason"] = end_reason
            self._put_turn(turn)
            message = self._assistant_of(conversation_id, turn_id)
            if message is not None:
                if text is not None:
                    message["text"] = text
                message["status"] = m.message_status_for_turn(status)
                message["error"] = dict(error) if error is not None else None
                self._put_message(message)
            self._emit(
                conversation["project_id"],
                conversation_id,
                turn_id,
                self._terminal_event(turn, message, status, end_reason, error),
            )
            return True

    @staticmethod
    def _terminal_event(
        turn: dict[str, Any],
        message: dict[str, Any] | None,
        status: str,
        end_reason: str,
        error: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        turn_id = turn["turn_id"]
        if status == m.TURN_COMPLETED:
            return {
                "type": "message.completed",
                "turn_id": turn_id,
                "text": "" if message is None else str(message.get("text", "")),
                "end_reason": end_reason,
            }
        if status == m.TURN_STOPPED:
            return {"type": "turn.cancelled", "turn_id": turn_id, "reason": end_reason}
        if status == m.TURN_INTERRUPTED:
            return {"type": "turn.interrupted", "turn_id": turn_id, "reason": end_reason}
        event: dict[str, Any] = {"type": "turn.failed", "turn_id": turn_id, "reason": end_reason}
        if error is not None:
            event["error"] = dict(error)
        return event

    async def get_message(self, message_id: str) -> m.ChatMessage | None:
        record = self._get_message(message_id)
        return None if record is None else m.ChatMessage.from_dict(record)

    async def list_messages(self, conversation_id: str) -> list[m.ChatMessage]:
        return [m.ChatMessage.from_dict(r) for r in self._list_messages(conversation_id)]

    async def append_message(self, message: m.ChatMessage) -> None:
        with self._begin():
            self._put_message(message.to_dict())

    async def append_event(
        self,
        *,
        project_id: str,
        conversation_id: str,
        turn_id: str | None,
        event: Mapping[str, Any],
    ) -> m.ChatEvent:
        with self._begin():
            sequence = self._emit(project_id, conversation_id, turn_id, dict(event))
            return m.ChatEvent(
                project_id=project_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                sequence=sequence,
                event=dict(event),
            )

    async def events_after(self, conversation_id: str, after: int) -> list[m.ChatEvent]:
        return [m.ChatEvent.from_dict(r) for r in self._events_after(conversation_id, after)]

    async def mark_interrupted(self) -> int:
        with self._begin():
            count = 0
            for turn in self._all_turns():
                if turn["status"] not in (m.TURN_QUEUED, m.TURN_RUNNING):
                    continue
                conversation = self._get_conversation(turn["conversation_id"])
                assert conversation is not None
                turn["status"] = m.TURN_INTERRUPTED
                turn["end_reason"] = "service_restart"
                self._put_turn(turn)
                message = self._assistant_of(turn["conversation_id"], turn["turn_id"])
                if message is not None:
                    message["status"] = m.MESSAGE_FAILED
                    self._put_message(message)
                self._emit(
                    conversation["project_id"],
                    turn["conversation_id"],
                    turn["turn_id"],
                    {"type": "turn.interrupted", "turn_id": turn["turn_id"],
                     "reason": "service_restart"},
                )
                count += 1
            return count


class MemoryConversationStore(_BaseConversationStore):
    """进程内实现（测试与端口契约）。"""

    def __init__(self, clock: _Clock | None = None) -> None:
        super().__init__(clock)
        self._conversations: dict[str, dict[str, Any]] = {}
        self._turns: dict[str, dict[str, Any]] = {}
        self._messages: dict[str, dict[str, Any]] = {}
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._lock = threading.RLock()

    @contextmanager
    def _begin(self) -> Iterator[None]:
        with self._lock:
            yield

    def _get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        record = self._conversations.get(conversation_id)
        return None if record is None else dict(record)

    def _put_conversation(self, record: dict[str, Any]) -> None:
        self._conversations[str(record["conversation_id"])] = dict(record)

    def _list_conversations(self, project_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self._conversations.values() if r["project_id"] == project_id]

    def _get_turn(self, turn_id: str) -> dict[str, Any] | None:
        record = self._turns.get(turn_id)
        return None if record is None else dict(record)

    def _put_turn(self, record: dict[str, Any]) -> None:
        self._turns[str(record["turn_id"])] = dict(record)

    def _find_turn(self, conversation_id: str, idempotency_key: str) -> dict[str, Any] | None:
        for record in self._turns.values():
            if (
                record["conversation_id"] == conversation_id
                and record.get("idempotency_key") == idempotency_key
            ):
                return dict(record)
        return None

    def _all_turns(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._turns.values()]

    def _get_message(self, message_id: str) -> dict[str, Any] | None:
        record = self._messages.get(message_id)
        return None if record is None else dict(record)

    def _put_message(self, record: dict[str, Any]) -> None:
        self._messages[str(record["message_id"])] = dict(record)

    def _list_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self._messages.values() if r["conversation_id"] == conversation_id]

    def _append_event_row(self, conversation_id: str, body: dict[str, Any]) -> int:
        events = self._events.setdefault(conversation_id, [])
        sequence = len(events) + 1
        body = {**body, "sequence": sequence}
        events.append(body)
        return sequence

    def _events_after(self, conversation_id: str, after: int) -> list[dict[str, Any]]:
        return [
            dict(r) for r in self._events.get(conversation_id, []) if r["sequence"] > after
        ]


class SqliteConversationStore(_BaseConversationStore):
    """SQLite 实现：与 Repository/Job 共用 DB 文件，独立连接与 ``IF NOT EXISTS`` 建表。"""

    def __init__(self, path: str, clock: _Clock | None = None) -> None:
        super().__init__(clock)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS chat_conversations ("
            "conversation_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, body TEXT NOT NULL);"
            "CREATE INDEX IF NOT EXISTS idx_chat_conversations_project "
            "ON chat_conversations(project_id);"
            "CREATE TABLE IF NOT EXISTS chat_messages ("
            "message_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, turn_id TEXT, "
            "body TEXT NOT NULL);"
            "CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation "
            "ON chat_messages(conversation_id);"
            "CREATE TABLE IF NOT EXISTS chat_turns ("
            "turn_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, dedup TEXT UNIQUE, "
            "status TEXT NOT NULL, body TEXT NOT NULL);"
            "CREATE INDEX IF NOT EXISTS idx_chat_turns_conversation ON chat_turns(conversation_id);"
            "CREATE TABLE IF NOT EXISTS chat_events ("
            "conversation_id TEXT NOT NULL, sequence INTEGER NOT NULL, body TEXT NOT NULL, "
            "PRIMARY KEY(conversation_id, sequence));"
        )
        self._lock = threading.RLock()

    @contextmanager
    def _begin(self) -> Iterator[None]:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            else:
                self._db.execute("COMMIT")

    @staticmethod
    def _dedup(conversation_id: str, idempotency_key: str) -> str:
        return content_hash([conversation_id, idempotency_key])

    def _get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT body FROM chat_conversations WHERE conversation_id=?", (conversation_id,)
        ).fetchone()
        return None if row is None else dict(json.loads(row[0]))

    def _put_conversation(self, record: dict[str, Any]) -> None:
        self._db.execute(
            "INSERT INTO chat_conversations(conversation_id, project_id, body) VALUES(?,?,?) "
            "ON CONFLICT(conversation_id) DO UPDATE SET project_id=excluded.project_id, "
            "body=excluded.body",
            (
                record["conversation_id"],
                record["project_id"],
                json.dumps(record, ensure_ascii=False),
            ),
        )

    def _list_conversations(self, project_id: str) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT body FROM chat_conversations WHERE project_id=? ORDER BY rowid", (project_id,)
        ).fetchall()
        return [dict(json.loads(r[0])) for r in rows]

    def _get_turn(self, turn_id: str) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT body FROM chat_turns WHERE turn_id=?", (turn_id,)
        ).fetchone()
        return None if row is None else dict(json.loads(row[0]))

    def _put_turn(self, record: dict[str, Any]) -> None:
        self._db.execute(
            "INSERT INTO chat_turns(turn_id, conversation_id, dedup, status, body) "
            "VALUES(?,?,?,?,?) ON CONFLICT(turn_id) DO UPDATE SET status=excluded.status, "
            "body=excluded.body",
            (
                record["turn_id"],
                record["conversation_id"],
                self._dedup(record["conversation_id"], str(record.get("idempotency_key", ""))),
                record["status"],
                json.dumps(record, ensure_ascii=False),
            ),
        )

    def _find_turn(self, conversation_id: str, idempotency_key: str) -> dict[str, Any] | None:
        dedup = self._dedup(conversation_id, idempotency_key)
        row = self._db.execute(
            "SELECT body FROM chat_turns WHERE dedup=?", (dedup,)
        ).fetchone()
        return None if row is None else dict(json.loads(row[0]))

    def _all_turns(self) -> list[dict[str, Any]]:
        rows = self._db.execute("SELECT body FROM chat_turns ORDER BY rowid").fetchall()
        return [dict(json.loads(r[0])) for r in rows]

    def _get_message(self, message_id: str) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT body FROM chat_messages WHERE message_id=?", (message_id,)
        ).fetchone()
        return None if row is None else dict(json.loads(row[0]))

    def _put_message(self, record: dict[str, Any]) -> None:
        self._db.execute(
            "INSERT INTO chat_messages(message_id, conversation_id, turn_id, body) VALUES(?,?,?,?) "
            "ON CONFLICT(message_id) DO UPDATE SET body=excluded.body",
            (
                record["message_id"],
                record["conversation_id"],
                record.get("turn_id"),
                json.dumps(record, ensure_ascii=False),
            ),
        )

    def _list_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT body FROM chat_messages WHERE conversation_id=? ORDER BY rowid",
            (conversation_id,),
        ).fetchall()
        return [dict(json.loads(r[0])) for r in rows]

    def _append_event_row(self, conversation_id: str, body: dict[str, Any]) -> int:
        sequence = self._db.execute(
            "SELECT COALESCE(MAX(sequence),0)+1 FROM chat_events WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()[0]
        stored = {**body, "sequence": sequence}
        self._db.execute(
            "INSERT INTO chat_events(conversation_id, sequence, body) VALUES(?,?,?)",
            (conversation_id, sequence, json.dumps(stored, ensure_ascii=False)),
        )
        return int(sequence)

    def _events_after(self, conversation_id: str, after: int) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT body FROM chat_events WHERE conversation_id=? AND sequence>? ORDER BY sequence",
            (conversation_id, after),
        ).fetchall()
        return [dict(json.loads(r[0])) for r in rows]
