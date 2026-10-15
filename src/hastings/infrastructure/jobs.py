"""内存 JobStore：``JobStore`` 端口的进程内实现（Phase 14 T14.8）。

V2 服务面最小装配用：分析调度消费者（``AnalysisDispatcher``）创建的任务先落
这里。进程内生命周期——跨进程持久化与重启恢复属 JobStore 的 SQLite 落地，
当前为留白（正文与分析的去重保证由持久化 Outbox 承载，不依赖本实现）。
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping
from typing import Any

__all__ = ["MemoryJobStore"]


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
