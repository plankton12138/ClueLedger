"""ExecutionLog 实现（V03-1.6）：内存与 SQLite 双后端。

与 SqliteConversationStore 同思路：公开方法都是 ``async``，但临界区内不
``await``，进程级锁即可保证「读-判-写 + 序号分配」原子；表用
``CREATE TABLE IF NOT EXISTS`` 增量创建，不触碰 Repository 的 schema 版本。

序号语义：``sequence`` 是作品内单调游标（每 project 独立 MAX+1），跨项目、
跨事件流（Outbox/Job/对话 SSE）不可比较（M13）。``subscribe`` 用游标轮询
实现——与 SSE 端点、断线补齐共用同一事件 ID 空间，单进程下简单且足够。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from collections.abc import AsyncIterator
from dataclasses import replace

from hastings.application.ports.execution_log import LogPage
from hastings.domain.execution_log import ExecutionEvent

__all__ = ["MemoryExecutionLog", "SqliteExecutionLog"]

_POLL_INTERVAL = 0.25
_PAGE_BATCH = 200


def _with_sequence(event: ExecutionEvent, sequence: int) -> ExecutionEvent:
    return replace(event, sequence=sequence)


class MemoryExecutionLog:
    """进程内执行日志：测试与纯 API 模式；重启清零（JobStore 同先例）。"""

    def __init__(self, poll_interval: float = _POLL_INTERVAL) -> None:
        self._events: dict[str, list[ExecutionEvent]] = {}
        self._seen: set[tuple[str, str]] = set()  # (project_id, source_event_id)
        self._lock = threading.RLock()
        self._poll_interval = poll_interval

    async def append_once(self, source_event_id: str, event: ExecutionEvent) -> bool:
        key = (event.project_id, source_event_id)
        with self._lock:
            if key in self._seen:
                return False
            self._seen.add(key)
            sequence = len(self._events.get(event.project_id, [])) + 1
            self._events.setdefault(event.project_id, []).append(_with_sequence(event, sequence))
        return True

    async def page(
        self,
        project_id: str,
        after_sequence: int,
        limit: int,
        *,
        task_id: str | None = None,
        event_type: str | None = None,
    ) -> LogPage:
        with self._lock:
            matched = [
                e
                for e in self._events.get(project_id, [])
                if e.sequence > after_sequence
                and (task_id is None or e.task_id == task_id)
                and (event_type is None or e.event_type == event_type)
            ]
        batch = tuple(matched[: max(0, limit)])
        next_cursor = batch[-1].sequence if len(matched) > len(batch) and batch else None
        return LogPage(events=batch, next_cursor=next_cursor)

    async def subscribe(
        self, project_id: str, after_sequence: int
    ) -> AsyncIterator[ExecutionEvent]:
        cursor = after_sequence
        while True:
            page = await self.page(project_id, cursor, _PAGE_BATCH)
            for event in page.events:
                cursor = event.sequence
                yield event
            await asyncio.sleep(self._poll_interval)


class SqliteExecutionLog:
    """SQLite 持久执行日志：与 Repository/Job/对话同 DB 文件、独立表。"""

    def __init__(self, path: str, poll_interval: float = _POLL_INTERVAL) -> None:
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS execution_events ("
            "project_id TEXT NOT NULL, "
            "sequence INTEGER NOT NULL, "
            "event_id TEXT NOT NULL UNIQUE, "
            "source_event_id TEXT NOT NULL, "
            "task_id TEXT NOT NULL, "
            "attempt_id TEXT NOT NULL, "
            "event_type TEXT NOT NULL, "
            "body TEXT NOT NULL, "
            "PRIMARY KEY(project_id, sequence), "
            "UNIQUE(project_id, source_event_id))"
        )
        self._lock = threading.RLock()
        self._poll_interval = poll_interval

    async def append_once(self, source_event_id: str, event: ExecutionEvent) -> bool:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                sequence = self._db.execute(
                    "SELECT COALESCE(MAX(sequence),0)+1 FROM execution_events WHERE project_id=?",
                    (event.project_id,),
                ).fetchone()[0]
                stored = _with_sequence(event, int(sequence))
                self._db.execute(
                    "INSERT INTO execution_events "
                    "(project_id, sequence, event_id, source_event_id, task_id, attempt_id,"
                    " event_type, body) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        stored.project_id,
                        stored.sequence,
                        stored.event_id,
                        source_event_id,
                        stored.task_id,
                        stored.attempt_id,
                        stored.event_type,
                        json.dumps(stored.to_payload(), ensure_ascii=False),
                    ),
                )
                self._db.execute("COMMIT")
                return True
            except sqlite3.IntegrityError:
                # 源事件 ID 或 event_id 重复：幂等去重，不产生第二条。
                self._db.execute("ROLLBACK")
                return False
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    async def page(
        self,
        project_id: str,
        after_sequence: int,
        limit: int,
        *,
        task_id: str | None = None,
        event_type: str | None = None,
    ) -> LogPage:
        where = ["project_id=?", "sequence>?"]
        params: list[object] = [project_id, after_sequence]
        if task_id is not None:
            where.append("task_id=?")
            params.append(task_id)
        if event_type is not None:
            where.append("event_type=?")
            params.append(event_type)
        with self._lock:
            rows = self._db.execute(
                f"SELECT body, sequence FROM execution_events WHERE {' AND '.join(where)} "
                "ORDER BY sequence LIMIT ?",
                (*params, max(0, limit) + 1),
            ).fetchall()
        events = tuple(ExecutionEvent.from_payload(json.loads(row[0])) for row in rows[:limit])
        next_cursor = int(rows[limit - 1][1]) if len(rows) > limit and events else None
        return LogPage(events=events, next_cursor=next_cursor)

    async def subscribe(
        self, project_id: str, after_sequence: int
    ) -> AsyncIterator[ExecutionEvent]:
        cursor = after_sequence
        while True:
            page = await self.page(project_id, cursor, _PAGE_BATCH)
            for event in page.events:
                cursor = event.sequence
                yield event
            await asyncio.sleep(self._poll_interval)
