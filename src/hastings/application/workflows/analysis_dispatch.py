"""分析调度消费者（Phase 14 T14.2 修订）：把「章正文已提交」事件转成分析任务。

评审 #3 的核心：保存路径不再创建任务，调度改由**持久化 Outbox 的消费者**独立承担。
本消费者实现 ``PublishSink``，接在 ``OutboxPublisher`` 之后：

- 只处理 ``manuscript.chapter.committed`` 事件，其余事件直接放行（同一 Outbox 服务
  多种读者，各取所需）；
- 每个 ``event_id`` 至多创建一个分析 Job。去重分两层：进程内 ``_dispatched`` 挡住
  同一进程里的并发重复投递；``mark_published`` 是持久标记，跨进程重启后已发布事件
  不再出现在 pending 列表，因此不会重复调度；
- 消费失败（JobStore 故障）就抛异常：事件保持待发布，Publisher 下轮重投。补偿由
  持久化 Outbox 承载，而不是某个只在返回值里活着的字段（评审 #3）。

残余窗口（有意留白）：消费成功与 ``mark_published`` 之间进程崩溃时，事件会被重投；
payload 里的 ``idempotency_key`` 由 event_id 确定性派生，供 JobStore 侧幂等去重。
"""

from __future__ import annotations

from collections.abc import Mapping

from hastings.application.ports.jobs import JobStore
from hastings.domain.manuscript.models import EVENT_CHAPTER_COMMITTED

__all__ = ["ANALYSIS_JOB_TYPE", "AnalysisDispatcher"]

ANALYSIS_JOB_TYPE = "manuscript.analysis"


class AnalysisDispatcher:
    """Outbox 消费端：把章提交事件转成分析任务，按 event_id 幂等。"""

    def __init__(
        self,
        jobs: JobStore,
        *,
        job_type: str = ANALYSIS_JOB_TYPE,
        event_type: str = EVENT_CHAPTER_COMMITTED,
    ) -> None:
        self._jobs = jobs
        self._job_type = job_type
        self._event_type = event_type
        self._dispatched: set[str] = set()

    async def publish(self, event: Mapping[str, object]) -> None:
        """投递单个事件：仅章提交事件触发分析 Job；重复投递不产生第二次调度。"""
        if str(event.get("type", "")) != self._event_type:
            return
        event_id = str(event.get("id", ""))
        if not event_id:
            raise ValueError(
                "章提交事件缺少 id（哪里错：event['id'] 为空；"
                "为什么错：没有 event_id 就无法去重与补偿；"
                "怎样算对：事件必须由 Commit Service 分配稳定 id）"
            )
        if event_id in self._dispatched:
            return  # 进程内重复投递：不再创建第二个 Job
        await self._jobs.create(self._job_type, _payload(event, event_id=event_id))
        self._dispatched.add(event_id)  # 成功后才入账：失败的事件保持待重投


def _payload(event: Mapping[str, object], *, event_id: str) -> dict[str, object]:
    """由事件派生分析任务载荷：稳定 ID + 确定性幂等键，不含正文。"""
    inner = event.get("payload")
    chapter_id = ""
    if isinstance(inner, Mapping):
        chapter_id = str(inner.get("chapter_id", ""))
    return {
        "project_id": event.get("project_id"),
        "commit_id": event.get("commit_id"),
        "chapter_id": chapter_id,
        "idempotency_key": f"analysis:{event_id}",
    }
