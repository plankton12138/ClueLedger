"""Job 是执行事实，Proposal 是候选；两者都不能冒充正文 Commit。"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any

from hastings.application.errors import EnvironmentFailure, ViolationError
from hastings.application.guards_worker_output import validate_worker_output
from hastings.application.ports.agents import AgentRunner
from hastings.application.ports.clock import Clock
from hastings.application.ports.ids import IdGenerator
from hastings.application.ports.jobs import PersistentJobs
from hastings.application.ports.repository import LeaseRepository, RepositoryReader
from hastings.domain.repository.ids import JobId
from hastings.domain.repository.models import Lease
from hastings.domain.worker import ContextPackage


class WorkerJobRunner:
    def __init__(
        self,
        jobs: PersistentJobs,
        runner: AgentRunner,
        leases: LeaseRepository,
        ids: IdGenerator,
        clock: Clock,
        *,
        poll_seconds: float = 0.1,
        lease_seconds: float = 180,
        max_attempts: int = 3,
        reader: RepositoryReader | None = None,
    ) -> None:
        self.jobs, self.runner, self.leases, self.ids, self.clock = jobs, runner, leases, ids, clock
        self.poll_seconds, self.lease_seconds, self.max_attempts = (
            poll_seconds,
            lease_seconds,
            max_attempts,
        )
        repository_reader = reader or getattr(leases, "service", None)
        if repository_reader is None:
            raise ValueError("WorkerJobRunner 需要固定版本读取端口")
        self.reader: RepositoryReader = repository_reader

    async def submit(
        self,
        context: ContextPackage,
        key: str,
        model_binding: Mapping[str, object] | None = None,
        grant: Mapping[str, object] | None = None,
    ) -> str:
        binding_provider = getattr(self.runner, "model_binding", None)
        if model_binding is None and binding_provider is not None:
            model_binding = binding_provider()
        payload: dict[str, object] = {
            "project_id": context.project_id,
            "idempotency_key": key,
            "context": context.model_dump(mode="json"),
        }
        if model_binding is not None:
            # 任务启动时的模型快照（§19.5）：执行与重试读取绑定，不随当前选择漂移。
            payload["model_binding"] = dict(model_binding)
        if grant is not None:
            # 显式启动凭据（V03-0.3，I05）：派发门禁按它放行；无凭据任务不派发。
            payload["grant"] = dict(grant)
        return await self.jobs.create(context.task_kind, payload)

    async def cancel(self, job_id: str) -> dict[str, Any]:
        record = await self.jobs.get(job_id)
        if record is None:
            raise ValueError("Job 不存在；使用 submit 返回的 job_id")
        saved = record.get("repository_lease")
        if isinstance(saved, dict) and record["state"] == "running":
            with suppress(ViolationError):
                self.leases.release(
                    Lease(
                        resource_key=saved["resource_key"],
                        owner_job_id=JobId(saved["owner"]),
                        expires_at=datetime.fromisoformat(saved["expires_at"]),
                        version=saved["version"],
                    )
                )
        return await self.jobs.cancel(job_id)

    async def execute(self, job_id: str) -> dict[str, Any]:
        old = await self.jobs.get(job_id)
        if old is None:
            raise ValueError("Job 不存在；先 submit")
        if old["state"] in ("completed", "waiting", "terminal_failed", "cancelled"):
            return dict(old)
        owner = self.ids.new_id("run")
        job = await self.jobs.claim(job_id, owner, now=self.clock.now(), ttl=self.lease_seconds)
        generation = job["generation"]
        lease: Lease | None = None
        task: asyncio.Task[Any] | None = None
        try:
            lease = self.leases.acquire(f"worker-job:{job_id}", JobId(owner), self.lease_seconds)
            await self.jobs.bind_lease(
                job_id,
                generation,
                {
                    "resource_key": lease.resource_key,
                    "owner": owner,
                    "version": lease.version,
                    "expires_at": lease.expires_at.isoformat(),
                },
            )
            ctx = ContextPackage.model_validate(job["payload"]["context"])
            output = job["checkpoint"]
            if output is None:

                async def sink(event: dict[str, Any]) -> None:
                    await self.jobs.append_event(job_id, generation, event)

                streaming = getattr(self.runner, "run_with_events", None)
                bound = getattr(self.runner, "run_with_events_bound", None)
                binding = job["payload"].get("model_binding")
                if binding and bound is not None:
                    call = bound(ctx.task_kind, ctx.model_dump(), sink, binding)
                elif streaming:
                    call = streaming(ctx.task_kind, ctx.model_dump(), sink)
                else:
                    call = self.runner.run(ctx.task_kind, ctx.model_dump())
                task = asyncio.create_task(call)
                renewed = self.clock.now()
                while not task.done():
                    await asyncio.wait({task}, timeout=self.poll_seconds)
                    current = await self.jobs.get(job_id)
                    assert current is not None
                    if current["state"] != "running" or current["generation"] != generation:
                        task.cancel()
                        with suppress(asyncio.CancelledError):
                            await task
                        return dict(current)
                    if self.clock.now() - renewed > timedelta(seconds=self.lease_seconds / 3):
                        lease = self.leases.renew(lease, self.lease_seconds)
                        await self.jobs.renew(job_id, generation, lease.expires_at)
                        await self.jobs.bind_lease(
                            job_id,
                            generation,
                            {
                                "resource_key": lease.resource_key,
                                "owner": owner,
                                "version": lease.version,
                                "expires_at": lease.expires_at.isoformat(),
                            },
                        )
                        renewed = self.clock.now()
                output = validate_worker_output(ctx, self.reader, await task)
                await self.jobs.checkpoint(job_id, generation, output)
            output = validate_worker_output(ctx, self.reader, output)
            lease = self.leases.renew(lease, self.lease_seconds)
            kind = output["kind"]
            if kind == "worker_failure":
                retry = output["category"] == "environment" and job["attempts"] < self.max_attempts
                return await self.jobs.finish(
                    job_id,
                    generation,
                    "retryable_failed" if retry else "terminal_failed",
                    output,
                    retry_at=self.clock.now() + timedelta(seconds=2 ** job["attempts"])
                    if retry
                    else None,
                )
            state = (
                "completed"
                if kind in ("no_result", "finding_set", "observation_set")
                else "waiting"
            )
            return await self.jobs.finish(job_id, generation, state, output)
        except asyncio.CancelledError:
            current = await self.jobs.get(job_id)
            if current and current["state"] == "running" and current["generation"] == generation:
                await self.jobs.mark_retryable(job_id, "服务停止；保留上下文和检查点，重启后恢复")
            raise
        except (EnvironmentFailure, OSError, TimeoutError, ViolationError) as exc:
            current = await self.jobs.get(job_id)
            if current and current["state"] == "cancelled":
                return dict(current)
            retry = not isinstance(exc, ViolationError) and job["attempts"] < self.max_attempts
            return await self.jobs.finish(
                job_id,
                generation,
                "retryable_failed" if retry else "terminal_failed",
                {
                    "kind": "worker_failure",
                    "category": "violation" if isinstance(exc, ViolationError) else "environment",
                    "reason": getattr(exc, "message", str(exc)),
                    "errors": [
                        {
                            "where": getattr(exc, "where", "worker.run"),
                            "why": getattr(exc, "why", str(exc)),
                            "expected": getattr(exc, "fix", "检查任务后重试"),
                        }
                    ],
                    "raw_outputs": [],
                },
                retry_at=self.clock.now() + timedelta(seconds=2 ** job["attempts"])
                if retry
                else None,
            )
        except ValueError as exc:
            current = await self.jobs.get(job_id)
            if current and (current["state"] != "running" or current["generation"] != generation):
                return dict(current)
            return await self.jobs.finish(
                job_id,
                generation,
                "terminal_failed",
                {"kind": "worker_failure", "category": "violation", "reason": str(exc)},
            )
        finally:
            if task and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            if lease:
                with suppress(ViolationError):
                    self.leases.release(lease)
