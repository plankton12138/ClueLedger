"""TaskDraftStore 端口（Phase 19.4）：任务建议的持久化。

draft 是项目辅助数据，与 Repository Revision 无关；修订冲突（CAS）由应用
服务在进程锁内裁决——存储只提供读改写原语。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from hastings.domain.conversation.task_drafts import TaskDraft

__all__ = ["TaskDraftStore"]


@runtime_checkable
class TaskDraftStore(Protocol):
    """任务建议存储端口（异步 I/O；实现落在 infrastructure）。"""

    async def create_draft(self, draft: TaskDraft) -> None: ...

    async def get_draft(self, draft_id: str) -> TaskDraft | None:
        """按 id 读取；不存在返回 None（合法空结果，非异常）。"""

    async def update_draft(self, draft: TaskDraft) -> None:
        """整体替换（含修订号）；调用方负责修订冲突检查。"""

    async def list_drafts(self, conversation_id: str) -> list[TaskDraft]: ...

    async def list_by_status(self, status: str) -> list[TaskDraft]:
        """按状态列出（投影扫描用）；跨项目全局视图，调用方注意归属。"""
