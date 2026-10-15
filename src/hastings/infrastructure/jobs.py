"""JobStore 实现：V2 使用 SQLite 持久任务与事件；保留旧内存实现供端口测试。"""

from __future__ import annotations

import itertools
import json
import sqlite3
import threading
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from hastings.domain.repository.serialization import content_hash

__all__ = ["MemoryJobStore", "SqliteJobStore"]

STATES = {
    "queued",
    "running",
    "waiting",
    "retryable_failed",
    "terminal_failed",
    "completed",
    "cancelled",
}


class SqliteJobStore:
    """持久状态与单调事件同事务；generation 阻止旧 owner/取消后的迟到结果。"""

    def __init__(self, path: str) -> None:
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS workflow_jobs "
            "(id TEXT PRIMARY KEY, dedup TEXT UNIQUE, body TEXT NOT NULL)"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS workflow_job_events "
            "(job_id TEXT, sequence INTEGER, body TEXT NOT NULL, PRIMARY KEY(job_id, sequence))"
        )
        self._lock = threading.RLock()
        self.created: list[tuple[str, dict[str, Any]]] = []

    def _read(self, job_id: str) -> dict[str, Any]:
        row = self._db.execute("SELECT body FROM workflow_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ValueError(f"Job {job_id} 不存在；使用 create 返回的 job_id")
        return dict(json.loads(row[0]))

    def _write(
        self, job: dict[str, Any], event: str, payload: dict[str, Any] | None = None
    ) -> None:
        self._db.execute(
            "UPDATE workflow_jobs SET body=? WHERE id=?",
            (json.dumps(job, ensure_ascii=False), job["job_id"]),
        )
        sequence = self._db.execute(
            "SELECT COALESCE(MAX(sequence),0)+1 FROM workflow_job_events WHERE job_id=?",
            (job["job_id"],),
        ).fetchone()[0]
        body = {
            "job_id": job["job_id"],
            "sequence": sequence,
            "type": event,
            "commit_id": None,
            "project_sequence": None,
            "payload": payload or {"state": job["state"]},
        }
        self._db.execute(
            "INSERT INTO workflow_job_events VALUES (?,?,?)",
            (job["job_id"], sequence, json.dumps(body)),
        )

    async def create(self, job_type: str, payload: Mapping[str, object]) -> str:
        body = json.loads(json.dumps(dict(payload)))
        key = body.get("idempotency_key")
        dedup = content_hash([body.get("project_id"), job_type, key]) if key else None
        fingerprint = content_hash(body)
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = (
                    self._db.execute(
                        "SELECT body FROM workflow_jobs WHERE dedup=?", (dedup,)
                    ).fetchone()
                    if dedup
                    else None
                )
                if row:
                    old = json.loads(row[0])
                    if old["fingerprint"] != fingerprint:
                        raise ValueError("Job 请求键的输入已变化；新意图使用新的 idempotency_key")
                    self._db.execute("COMMIT")
                    return str(old["job_id"])
                jid = f"job_{uuid.uuid4().hex}"
                job = {
                    "job_id": jid,
                    "job_type": job_type,
                    "payload": body,
                    "state": "queued",
                    "fingerprint": fingerprint,
                    "generation": 0,
                    "attempts": 0,
                    "checkpoint": None,
                    "output": None,
                    "retry": None,
                    "lease_expires_at": None,
                    "retry_at": None,
                }
                self._db.execute(
                    "INSERT INTO workflow_jobs VALUES (?,?,?)", (jid, dedup, json.dumps(job))
                )
                self._write(job, "job.queued")
                self._db.execute("COMMIT")
                self.created.append((job_type, body))
                return jid
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def _mutate(self, jid: str, fn: Any) -> dict[str, Any]:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                job = self._read(jid)
                description = fn(job)
                event, payload = (
                    description if isinstance(description, tuple) else (description, None)
                )
                self._write(job, event, payload)
                self._db.execute("COMMIT")
                return job
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    async def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            try:
                return self._read(job_id)
            except ValueError:
                return None

    async def claim(self, job_id: str, owner: str, *, now: datetime, ttl: float) -> dict[str, Any]:
        def apply(job: dict[str, Any]) -> str:
            if job["state"] not in ("queued", "retryable_failed") or (
                job["retry_at"] and datetime.fromisoformat(job["retry_at"]) > now
            ):
                raise ValueError("任务未到可执行状态；先恢复过期任务或等待退避时间")
            job.update(
                state="running",
                owner=owner,
                generation=job["generation"] + 1,
                attempts=job["attempts"] + 1,
                lease_expires_at=(now + timedelta(seconds=ttl)).isoformat(),
            )
            return "job.running"

        return self._mutate(job_id, apply)

    async def checkpoint(self, job_id: str, generation: int, output: Mapping[str, object]) -> None:
        def apply(job: dict[str, Any]) -> str:
            self._fence(job, generation)
            job["checkpoint"] = dict(output)
            return "agent.output.proposed"

        self._mutate(job_id, apply)

    async def append_event(self, job_id: str, generation: int, event: Mapping[str, object]) -> None:
        def apply(job: dict[str, Any]) -> Any:
            self._fence(job, generation)
            return str(event["type"]), event.get("payload", {})

        # 流事件仅保存类型与脱敏进度，完整正文不进入日志。
        self._mutate(job_id, apply)

    async def bind_lease(self, job_id: str, generation: int, lease: Mapping[str, object]) -> None:
        def apply(job: dict[str, Any]) -> str:
            self._fence(job, generation)
            job["repository_lease"] = dict(lease)
            return "job.lease_acquired"

        self._mutate(job_id, apply)

    async def renew(self, job_id: str, generation: int, expires_at: datetime) -> None:
        def apply(job: dict[str, Any]) -> str:
            self._fence(job, generation)
            job["lease_expires_at"] = expires_at.isoformat()
            return "job.heartbeat"

        self._mutate(job_id, apply)

    @staticmethod
    def _fence(job: dict[str, Any], generation: int) -> None:
        if job["state"] != "running" or job["generation"] != generation:
            raise ValueError("任务已取消或租约已被接管；旧执行不得发布结果")

    async def finish(
        self,
        job_id: str,
        generation: int,
        state: str,
        output: Mapping[str, object],
        *,
        retry_at: datetime | None = None,
    ) -> dict[str, Any]:
        if state not in STATES - {"queued", "running", "cancelled"}:
            raise ValueError("无效结束状态；使用 waiting/failed/completed")

        def apply(job: dict[str, Any]) -> str:
            self._fence(job, generation)
            job.update(
                state=state,
                output=dict(output),
                retry_at=retry_at.isoformat() if retry_at else None,
            )
            if state == "retryable_failed":
                job["checkpoint"] = None
            return f"job.{state}"

        return self._mutate(job_id, apply)

    async def resolve(self, job_id: str, generation: int, result: Mapping[str, object]) -> None:
        def apply(job: dict[str, Any]) -> str:
            if job["state"] != "waiting" or job["generation"] != generation:
                raise ValueError("只有当前 waiting 提案能接受；取消或替换后不能提交旧结果")
            job.update(state="completed", acceptance=dict(result))
            return "job.completed"

        self._mutate(job_id, apply)

    async def record_analysis(self, job_id: str, result: Mapping[str, object]) -> None:
        def apply(job: dict[str, Any]) -> tuple[str, dict[str, object]]:
            if job["state"] != "completed":
                raise ValueError("分析结果尚未完成；先恢复 Worker Job")
            job["analysis"] = dict(result)
            return "job.analysis_recorded", dict(result)

        self._mutate(job_id, apply)

    async def cancel(self, job_id: str) -> dict[str, Any]:
        def apply(job: dict[str, Any]) -> str:
            if job["state"] not in ("completed", "terminal_failed", "cancelled"):
                job.update(state="cancelled", generation=job["generation"] + 1)
            return f"job.{job['state']}"

        return self._mutate(job_id, apply)

    async def mark_retryable(self, job_id: str, reason: str) -> None:
        def apply(job: dict[str, Any]) -> str:
            if job["state"] == "cancelled":
                raise ValueError("已取消任务不能重新排队")
            job.update(state="retryable_failed", retry=reason)
            return "job.retryable_failed"

        self._mutate(job_id, apply)

    async def recover(self, now: datetime) -> int:
        count = 0
        for job in await self.list_jobs():
            if job["state"] == "running" and datetime.fromisoformat(job["lease_expires_at"]) <= now:

                def apply(current: dict[str, Any]) -> str:
                    if (
                        current["state"] == "running"
                        and datetime.fromisoformat(current["lease_expires_at"]) <= now
                    ):
                        current.update(state="queued", generation=current["generation"] + 1)
                    return "job.recovered"

                self._mutate(job["job_id"], apply)
                count += 1
        return count

    async def list_jobs(self, project_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT body FROM workflow_jobs ORDER BY rowid").fetchall()
            return [
                json.loads(row[0])
                for row in rows
                if project_id is None
                or json.loads(row[0])["payload"].get("project_id") == project_id
            ]

    async def events(self, job_id: str, after: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            return [
                json.loads(row[0])
                for row in self._db.execute(
                    "SELECT body FROM workflow_job_events "
                    "WHERE job_id=? AND sequence>? ORDER BY sequence",
                    (job_id, after),
                )
            ]


class MemoryJobStore:
    """dict 背书的任务存储：创建 / 标记可重试 / 按 id 读取。"""

    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._seq = itertools.count(1)
        # 测试与审计用：按创建顺序记录 (job_type, payload)
        self.created: list[tuple[str, dict[str, Any]]] = []

    async def create(self, job_type: str, payload: Mapping[str, object]) -> str:
        job_id = f"job_{next(self._seq):04d}"
        record = {"job_id": job_id, "job_type": job_type, "payload": dict(payload), "retry": None}
        self._jobs[job_id] = record
        self.created.append((job_type, dict(payload)))
        return job_id

    async def mark_retryable(self, job_id: str, reason: str) -> None:
        record = self._jobs.get(job_id)
        if record is not None:
            record["retry"] = reason

    async def get(self, job_id: str) -> Mapping[str, object] | None:
        record = self._jobs.get(job_id)
        return dict(record) if record is not None else None
