"""任务进度与作者审阅出口：流事件只表示执行进展，Commit 才证明接受。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from hastings.agents.coordinator import route_intent
from hastings.application.errors import ViolationError
from hastings.application.execution import (
    ConnectionSnapshot,
    build_grant,
)
from hastings.application.workflows.change_support import reject
from hastings.domain.repository.ids import Actor, ArtifactId, CommitId, ProjectId
from hastings.domain.repository.requests import ArtifactChange, CommitRequest
from hastings.domain.repository.serialization import content_hash
from hastings.domain.worker import WorkerRole

if TYPE_CHECKING:
    from hastings.server.v2 import V2Runtime


class SubmitJob(BaseModel):
    role: WorkerRole
    idempotency_key: str = Field(min_length=1)
    base_commit_id: str | None = None
    target_ids: list[str] = Field(default_factory=list)
    instruction: str = ""
    chapter_id: str = ""
    before_commit_id: str = ""
    token_budget: int = Field(default=16000, ge=1000, le=64000)


class AcceptJob(BaseModel):
    expected_head: str


class ExecuteRepair(BaseModel):
    idempotency_key: str = Field(min_length=1)


#: 角色 → 凭据功能（V03-0.3）：显式启动即授权；style_observer 属 0.3 排除的
#: 作者模型自动链路，API 不签发凭据（不能绕过 UI 创建此类 Job，计划 §8.3）。
ROLE_FUNCTIONS: dict[str, str] = {
    "planner": "draft",
    "writer": "draft",
    "auditor": "suggestion",
    "coordinator": "suggestion",
}


class CoordinatorRequest(BaseModel):
    instruction: str
    change_set_id: str | None = None


def create_agents_router(runtime: V2Runtime) -> APIRouter:
    router = APIRouter(prefix="/projects/{project_id}", tags=["agents"])

    async def require_job(project: str, jid: str) -> dict[str, Any]:
        job = await runtime.jobs.get(jid)
        if job is None or job["payload"].get("project_id") != project:
            reject(
                "AGENT_JOB_NOT_FOUND",
                "agent.job",
                "当前项目没有这个任务",
                "在当前项目的任务列表选择 job_id",
            )
        assert job is not None
        return job

    @router.get("/story")
    def story(project_id: str) -> dict[str, Any]:
        pid = ProjectId(project_id)
        head = runtime.head(pid)
        return {
            "commit_id": head.value if head else None,
            "artifacts": []
            if head is None
            else [
                {
                    "artifact_id": v.artifact.id.value,
                    "kind": v.artifact.kind,
                    "content": v.revision.content,
                }
                for v in runtime.service.get_snapshot(pid, head).artifacts.values()
                if v.revision and v.artifact.kind.startswith(("story.", "narrative."))
            ],
        }

    @router.post("/coordinate")
    def coordinate(project_id: str, req: CoordinatorRequest) -> dict[str, Any]:
        snapshot = story(project_id)
        return {
            "decision": route_intent(req.instruction, change_set_id=req.change_set_id),
            "snapshot": snapshot,
        }

    @router.post("/agent-jobs")
    async def submit(project_id: str, req: SubmitJob) -> dict[str, Any]:
        pid = ProjectId(project_id)
        head = runtime.head(pid)
        if head is None:
            branch = runtime.service.get_branch(pid, "main")
            assert branch is not None
            head = runtime.service.commit(
                CommitRequest(
                    pid,
                    branch.id,
                    None,
                    Actor("human", "author"),
                    "agent-workspace:init",
                    "初始化创作工作流",
                    changes=(
                        ArtifactChange(
                            ArtifactId(f"{project_id}:workspace"),
                            {"project_id": project_id},
                            kind="agents.workspace",
                        ),
                    ),
                )
            ).commit_id
        assert head is not None
        base = req.base_commit_id or head.value
        app = runtime.agent_workflows
        targets = tuple(req.target_ids)
        function = ROLE_FUNCTIONS.get(str(req.role))
        if function is None:
            raise ViolationError(
                code="EXECUTION_NOT_AUTHORIZED",
                where="agent_jobs.submit",
                why=f"角色 {req.role!r} 没有可签发的启动凭据（0.3 排除的自动链路）",
                fix="使用 planner / writer / auditor / coordinator；风格观察等旧能力不再启动",
                details={"role": str(req.role)},
            )
        binding = runtime.model_binding_provider()
        connection = ConnectionSnapshot(
            connection_id=str(binding.get("profile") or ""),
            revision=0,
            provider=str(binding.get("api") or binding.get("profile") or ""),
            model=str(binding.get("model") or ""),
        )
        grant = build_grant(
            grant_id=f"grant_{content_hash([req.idempotency_key])[:16]}",
            source="author_button",
            function=function,  # type: ignore[arg-type]
            analysis_scope=targets,
            write_scope=None,
            connection=connection,
        ).to_wire()
        if req.role == "planner":
            jid = await app.plan(
                pid,
                base,
                targets,
                req.idempotency_key,
                req.instruction,
                token_budget=req.token_budget,
                grant=grant,
            )
        elif req.role == "writer":
            if not req.chapter_id:
                raise ValueError("draft.chapter_id 缺失；为这一章指定稳定编号")
            jid = await app.draft(
                pid,
                base,
                targets,
                req.chapter_id,
                req.idempotency_key,
                req.instruction,
                token_budget=req.token_budget,
                grant=grant,
            )
        elif req.role == "auditor":
            if not targets:
                snapshot = runtime.service.get_snapshot(pid, CommitId(base))
                targets = tuple(
                    v.artifact.id.value
                    for v in snapshot.artifacts.values()
                    if v.artifact.kind.startswith(("story.", "narrative.", "manuscript."))
                )
            jid = await app.audit(
                pid, base, targets, req.idempotency_key, token_budget=req.token_budget, grant=grant
            )
        elif req.role == "style_observer":
            if not req.before_commit_id or not req.chapter_id:
                raise ValueError(
                    "Style 需要 before_commit_id 与 chapter_id；只能观察已提交的真实修改"
                )
            jid = await app.style(
                pid,
                req.before_commit_id,
                base,
                req.chapter_id,
                req.idempotency_key,
                token_budget=req.token_budget,
            )
        elif req.role == "coordinator":
            if not targets:
                snapshot = runtime.service.get_snapshot(pid, CommitId(base))
                targets = tuple(
                    v.artifact.id.value
                    for v in sorted(snapshot.artifacts.values(), key=lambda v: v.artifact.id.value)
                    if v.artifact.kind.startswith(("story.", "narrative."))
                )[:12]
            ctx = app.contexts.build(
                "coordinator",
                pid,
                base,
                targets,
                instruction=req.instruction,
                token_budget=req.token_budget,
            )
            jid = await runtime.worker.submit(ctx, req.idempotency_key, grant=grant)
        else:
            raise ValueError("Reviser 需要已批准的修订步骤；从变更中心执行 Agent 修复")
        job = await require_job(project_id, jid)
        await runtime.wake_workers(project_id)
        return job

    @router.get("/agent-jobs")
    async def listing(project_id: str) -> dict[str, Any]:
        runtime.head(ProjectId(project_id))
        return {"jobs": await runtime.jobs.list_jobs(project_id)}

    @router.get("/agent-jobs/{job_id}")
    async def detail(project_id: str, job_id: str) -> dict[str, Any]:
        return await require_job(project_id, job_id)

    @router.get("/agent-jobs/{job_id}/events")
    async def events(project_id: str, job_id: str, after: int = 0) -> dict[str, Any]:
        await require_job(project_id, job_id)
        return {"events": await runtime.jobs.events(job_id, max(0, after))}

    @router.post("/agent-jobs/{job_id}/run")
    async def run(project_id: str, job_id: str) -> dict[str, Any]:
        job = await require_job(project_id, job_id)
        if job["state"] == "running":
            return job
        return await runtime.execute_agent_job(job_id)

    @router.post("/agent-jobs/{job_id}/cancel")
    async def cancel(project_id: str, job_id: str) -> dict[str, Any]:
        await require_job(project_id, job_id)
        return await runtime.worker.cancel(job_id)

    @router.post("/agent-jobs/{job_id}/accept")
    async def accept(project_id: str, job_id: str, req: AcceptJob) -> dict[str, Any]:
        await require_job(project_id, job_id)
        return await runtime.agent_workflows.accept(job_id, req.expected_head)

    @router.post("/changes/{cs_id}/execute-agent")
    async def repair(project_id: str, cs_id: str, req: ExecuteRepair) -> dict[str, Any]:
        drafts = await runtime.task_draft_store.list_by_status("started")
        binding = next(
            (
                d.model_binding
                for d in drafts
                if d.project_id == project_id and d.job_ref == f"change:{cs_id}"
            ),
            None,
        )
        async with runtime.project_execution_lock(project_id):
            return await runtime.agent_workflows.repair(
                ProjectId(project_id),
                cs_id,
                req.idempotency_key,
                runtime.revision,
                model_binding=binding,
            )

    return router
