"""JobStore 端口：后台任务状态的持久化（Phase 11 冻结名称，T11.5）。

失败任务保留可重试状态，不产生 Commit（ADR-005；DESIGN.md §10.3 恢复规则）。
Lease 不在本端口——它属于仓库（repository-kernel.md §3），Phase 12 实现。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

__all__ = ["JobStore"]


@runtime_checkable
class JobStore(Protocol):
    """后台任务存储端口。"""

    async def create(self, job_type: str, payload: Mapping[str, object]) -> str:
        """创建任务并返回 job_id；任务初始为待执行状态。"""

    async def mark_retryable(self, job_id: str, reason: str) -> None:
        """把失败任务标回可重试，保留原因供展示与诊断。"""

    async def get(self, job_id: str) -> Mapping[str, object] | None:
        """按 id 读取任务状态；不存在返回 None（合法空结果，非异常）。"""
