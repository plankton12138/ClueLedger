"""JobStore 端口：后台任务状态的持久化（Phase 11 冻结名称，T11.5）。

失败任务保留可重试状态，不产生 Commit（ADR-005；DESIGN.md §10.3 恢复规则）。
Lease 不在本端口——它属于仓库（repository-kernel.md §3），Phase 12 实现。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

__all__ = ["JobStore", "PersistentJobs"]


@runtime_checkable
class JobStore(Protocol):
    """后台任务存储端口。"""

    async def create(self, job_type: str, payload: Mapping[str, object]) -> str:
        """创建任务并返回 job_id；任务初始为待执行状态。"""

    async def mark_retryable(self, job_id: str, reason: str) -> None:
        """把失败任务标回可重试，保留原因供展示与诊断。"""

    async def get(self, job_id: str) -> Mapping[str, object] | None:
        """按 id 读取任务状态；不存在返回 None（合法空结果，非异常）。"""


class PersistentJobs(JobStore, Protocol):
    async def record_analysis(self, job_id: str, result: Mapping[str, object]) -> None: ...
    async def get(self, job_id: str) -> dict[str, Any] | None: ...
    async def claim(
        self, job_id: str, owner: str, *, now: datetime, ttl: float
    ) -> dict[str, Any]: ...
    async def checkpoint(
        self, job_id: str, generation: int, output: Mapping[str, object]
    ) -> None: ...
    async def finish(
        self,
        job_id: str,
        generation: int,
        state: str,
        output: Mapping[str, object],
        *,
        retry_at: datetime | None = None,
    ) -> dict[str, Any]: ...
    async def cancel(self, job_id: str) -> dict[str, Any]: ...
    async def recover(self, now: datetime) -> int: ...
    async def list_jobs(self, project_id: str | None = None) -> list[dict[str, Any]]: ...
    async def events(self, job_id: str, after: int = 0) -> list[dict[str, Any]]: ...
    async def append_event(
        self, job_id: str, generation: int, event: Mapping[str, object]
    ) -> None: ...
    async def renew(self, job_id: str, generation: int, expires_at: datetime) -> None: ...
    async def bind_lease(
        self, job_id: str, generation: int, lease: Mapping[str, object]
    ) -> None: ...
    async def resolve(self, job_id: str, generation: int, result: Mapping[str, object]) -> None: ...
