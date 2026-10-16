"""TaskDraftStore 实现（Phase 19.4）：内存与 SQLite 双后端。

与 ConversationStore 同一套思路：公开方法 async、临界区内不 await、一个进程
锁保证读改写原子；SQLite 与 Repository/Conversation 共用 DB 文件、独立连接
与 ``IF NOT EXISTS`` 建表，不触碰其他模块的 schema 版本。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from hastings.domain.conversation.task_drafts import TaskDraft

__all__ = ["MemoryTaskDraftStore", "SqliteTaskDraftStore"]


class _BaseTaskDraftStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()

    @contextmanager
    def _begin(self) -> Iterator[None]:
        with self._lock:
            yield

    # -- 同步原语（子类实现） ------------------------------------------------
    def _get(self, draft_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def _put(self, record: dict[str, Any]) -> None:
        raise NotImplementedError

    def _list(self, conversation_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def _list_by_status(self, status: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    # -- 公开 API ------------------------------------------------------------
    async def create_draft(self, draft: TaskDraft) -> None:
        with self._begin():
            self._put(draft.to_dict())

    async def get_draft(self, draft_id: str) -> TaskDraft | None:
        record = self._get(draft_id)
        return None if record is None else TaskDraft.from_dict(record)

    async def update_draft(self, draft: TaskDraft) -> None:
        with self._begin():
            self._put(draft.to_dict())

    async def list_drafts(self, conversation_id: str) -> list[TaskDraft]:
        return [TaskDraft.from_dict(r) for r in self._list(conversation_id)]

    async def list_by_status(self, status: str) -> list[TaskDraft]:
        return [TaskDraft.from_dict(r) for r in self._list_by_status(status)]


class MemoryTaskDraftStore(_BaseTaskDraftStore):
    """进程内实现（测试与端口契约）。"""

    def __init__(self) -> None:
        super().__init__()
        self._drafts: dict[str, dict[str, Any]] = {}

    def _get(self, draft_id: str) -> dict[str, Any] | None:
        record = self._drafts.get(draft_id)
        return None if record is None else dict(record)

    def _put(self, record: dict[str, Any]) -> None:
        self._drafts[str(record["draft_id"])] = dict(record)

    def _list(self, conversation_id: str) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self._drafts.values()
            if r["conversation_id"] == conversation_id
        ]

    def _list_by_status(self, status: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self._drafts.values() if r["status"] == status]


class SqliteTaskDraftStore(_BaseTaskDraftStore):
    """SQLite 实现：独立表 ``task_drafts``，按会话列出。"""

    def __init__(self, path: str) -> None:
        super().__init__()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS task_drafts ("
            "draft_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, "
            "status TEXT NOT NULL, body TEXT NOT NULL);"
            "CREATE INDEX IF NOT EXISTS idx_task_drafts_conversation "
            "ON task_drafts(conversation_id);"
            "CREATE INDEX IF NOT EXISTS idx_task_drafts_status ON task_drafts(status);"
        )

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

    def _get(self, draft_id: str) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT body FROM task_drafts WHERE draft_id=?", (draft_id,)
        ).fetchone()
        return None if row is None else dict(json.loads(row[0]))

    def _put(self, record: dict[str, Any]) -> None:
        self._db.execute(
            "INSERT INTO task_drafts(draft_id, conversation_id, status, body) VALUES(?,?,?,?) "
            "ON CONFLICT(draft_id) DO UPDATE SET status=excluded.status, body=excluded.body",
            (
                record["draft_id"],
                record["conversation_id"],
                record["status"],
                json.dumps(record, ensure_ascii=False),
            ),
        )

    def _list(self, conversation_id: str) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT body FROM task_drafts WHERE conversation_id=? ORDER BY rowid",
            (conversation_id,),
        ).fetchall()
        return [dict(json.loads(r[0])) for r in rows]

    def _list_by_status(self, status: str) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT body FROM task_drafts WHERE body LIKE ?",
            (f'%"status": "{status}"%',),
        ).fetchall()
        return [dict(json.loads(r[0])) for r in rows]
