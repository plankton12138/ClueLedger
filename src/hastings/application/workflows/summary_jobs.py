"""摘要任务的 Job 化（V03-4.3 收尾）：持久状态、可停止、可重试、重启不重放。

为什么单独一类任务：摘要没有「可写范围」——它只写派生资料，不碰正文，因此不需要
正文任务的授权与租约（``ExecutionGrant`` / 仓库 lease）。共用任务表与状态机，
但走自己的执行器，避免把「摘要」硬塞进写作工作者的角色里。

状态语义（与既有任务表一致）：

- ``queued`` → ``running`` → ``completed`` / ``terminal_failed`` / ``retryable_failed``
- ``cancelled`` 由作者停止：``cancel()`` 让 generation 前进，正在跑的那次在
  提交前被 ``TaskStopped`` 拦住——**停止后不落任何摘要版本**。
- 进程重启：``mark_interrupted`` 把运行中的任务标为中断，不自动重放模型调用（I06）。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from hastings.application.errors import HastingsError, ViolationError
from hastings.application.ports.clock import Clock
from hastings.application.ports.jobs import PersistentJobs
from hastings.application.services.summary_service import SummaryService

__all__ = ["SUMMARY_JOB_TYPE", "SummaryJobRunner", "TaskStopped"]

SUMMARY_JOB_TYPE = "summary.chapter"

_OWNER = "summary-worker"
#: 单次执行租约：超过这个时间没结束，恢复逻辑才可以接管（模型调用通常远短于此）。
_LEASE_SECONDS = 600


class TaskStopped(Exception):
    """任务在执行途中被取消：用于在提交前打断，不算失败。"""


def _violation(code: str, where: str, why: str, fix: str, **details: Any) -> ViolationError:
    return ViolationError(code=code, where=where, why=why, fix=fix, details=details)


class SummaryJobRunner:
    def __init__(self, jobs: PersistentJobs, summaries: SummaryService, clock: Clock) -> None:
        self._jobs = jobs
        self._summaries = summaries
        self._clock = clock

    # -- 提交与查询 ---------------------------------------------------------
    async def submit(
        self,
        project_id: str,
        chapter_id: str,
        *,
        request_id: str,
        guidance: str = "",
    ) -> dict[str, Any]:
        """建一条待执行任务；相同 request_id 复用原任务（不重复烧调用）。"""
        job_id = await self._jobs.create(
            SUMMARY_JOB_TYPE,
            {
                "project_id": project_id,
                "chapter_id": chapter_id,
                "request_id": request_id,
                "guidance": guidance,
                "idempotency_key": f"summary:{chapter_id}:{request_id}",
            },
        )
        job = await self._jobs.get(job_id)
        return self._view(job) if job is not None else {"job_id": job_id, "state": "queued"}

    async def get(self, job_id: str) -> dict[str, Any]:
        job = await self._jobs.get(job_id)
        if job is None:
            raise _violation(
                "SUMMARY_JOB_NOT_FOUND",
                f"summary.job({job_id})",
                f"摘要任务 {job_id!r} 不存在",
                "刷新摘要状态后重新发起；任务不会因为刷新而消失",
                job_id=job_id,
            )
        return self._view(job)

    async def stop(self, job_id: str) -> dict[str, Any]:
        job = await self._jobs.get(job_id)
        if job is None:
            raise _violation(
                "SUMMARY_JOB_NOT_FOUND",
                f"summary.stop({job_id})",
                f"摘要任务 {job_id!r} 不存在",
                "刷新摘要状态后重试",
                job_id=job_id,
            )
        if job.get("state") == "completed":
            return self._view(job)  # 已完成：停止是合法空操作，不回滚已有摘要
        cancelled = await self._jobs.cancel(job_id)
        return self._view(cancelled)

    async def retry(self, job_id: str) -> dict[str, Any]:
        """把失败任务放回队列；已取消的任务不允许重试（作者要的是停止）。"""
        job = await self._jobs.get(job_id)
        if job is None:
            raise _violation(
                "SUMMARY_JOB_NOT_FOUND",
                f"summary.retry({job_id})",
                f"摘要任务 {job_id!r} 不存在",
                "刷新摘要状态后重试",
                job_id=job_id,
            )
        if job.get("state") == "cancelled":
            raise _violation(
                "SUMMARY_JOB_CANCELLED",
                f"summary.retry({job_id})",
                "已停止的摘要任务不能重试",
                "重新发起一次摘要更新；停止意味着这次结果不再需要",
                job_id=job_id,
            )
        if job.get("state") not in ("terminal_failed", "retryable_failed"):
            return self._view(job)
        await self._jobs.mark_retryable(job_id, "作者重试")
        current = await self._jobs.get(job_id)
        return self._view(current) if current is not None else {"job_id": job_id}

    # -- 执行 ---------------------------------------------------------------
    async def execute(self, job_id: str) -> dict[str, Any]:
        """执行一次；调用方可以是 worker 循环，也可以是同步入口（等待完成）。"""
        job = await self._jobs.get(job_id)
        if job is None:
            raise _violation(
                "SUMMARY_JOB_NOT_FOUND",
                f"summary.execute({job_id})",
                f"摘要任务 {job_id!r} 不存在",
                "重新发起摘要更新",
                job_id=job_id,
            )
        if job.get("state") in ("cancelled", "completed", "terminal_failed"):
            return self._view(job)

        claimed = await self._jobs.claim(job_id, _OWNER, now=self._clock.now(), ttl=_LEASE_SECONDS)
        generation = int(claimed["generation"])
        payload = claimed["payload"]
        chapter_id = str(payload["chapter_id"])

        async def before_commit() -> None:
            # 停止发生在模型调用期间时，在这里拦下：不写版本，比事后回滚干净。
            current = await self._jobs.get(job_id)
            if current is None:
                raise TaskStopped(chapter_id)
            if current.get("state") == "cancelled" or int(current["generation"]) != generation:
                raise TaskStopped(chapter_id)

        try:
            result = await self._summaries.start(
                str(payload["project_id"]),
                chapter_id,
                request_id=str(payload["request_id"]),
                guidance=str(payload.get("guidance", "")),
                before_commit=before_commit,
            )
        except TaskStopped:
            current = await self._jobs.get(job_id)
            return self._view(current) if current is not None else {"job_id": job_id}
        except HastingsError as exc:
            failed = await self._jobs.finish(
                job_id,
                generation,
                "terminal_failed",
                {
                    "kind": "worker_failure",
                    "category": exc.category.value,
                    "code": exc.code,
                    "why": exc.why,
                    "fix": exc.fix,
                },
            )
            return self._view(failed)
        except (OSError, TimeoutError) as exc:
            failed = await self._jobs.finish(
                job_id,
                generation,
                "retryable_failed",
                {"kind": "worker_failure", "category": "environment", "reason": str(exc)},
                retry_at=self._clock.now() + timedelta(seconds=2),
            )
            return self._view(failed)
        finished = await self._jobs.finish(
            job_id,
            generation,
            "completed",
            {"kind": "summary_result", "result": result},
        )
        return self._view(finished)

    # -- 视图 ---------------------------------------------------------------
    def _view(self, job: dict[str, Any]) -> dict[str, Any]:
        output = job.get("output") or {}
        payload = job.get("payload") or {}
        view: dict[str, Any] = {
            "job_id": str(job.get("job_id", "")),
            "state": str(job.get("state", "")),
            "chapter_id": str(payload.get("chapter_id", "")),
            "attempts": int(job.get("attempts", 0) or 0),
            "retry": job.get("retry"),
        }
        if output.get("kind") == "summary_result":
            view["result"] = output.get("result")
        elif output.get("kind") == "worker_failure":
            view["failure"] = {
                "category": output.get("category", ""),
                "code": output.get("code", ""),
                "why": output.get("why") or output.get("reason", ""),
                "fix": output.get("fix", ""),
            }
        return view
