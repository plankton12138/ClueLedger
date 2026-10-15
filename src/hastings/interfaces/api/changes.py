"""Change Center 最小出口；作者命令与 Worker 提案使用不同入口。"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Protocol

from fastapi import APIRouter
from pydantic import BaseModel, Field

from hastings.application.errors import EnvironmentFailure
from hastings.application.handlers.change_commands import (
    ChangeSetCommandHandler,
    change_set_command,
)
from hastings.application.ports.clock import Clock
from hastings.application.services.commit_service import CommitService
from hastings.application.workflows.audit import AuditWorkflow
from hastings.application.workflows.change_analysis import AnalyzeChangeImpact, AnalyzeIntent
from hastings.application.workflows.change_support import branch_snapshot, content, reject
from hastings.application.workflows.plan_revision import PlanRevision
from hastings.application.workflows.review import ReviewRevision
from hastings.application.workflows.revision import ExecuteIntent, ExecuteRevisionPlan
from hastings.application.workflows.rewrite import RewriteWorkflow
from hastings.domain.changes.commands import (
    advance_change_set,
    issue_approval_receipt,
    open_change_set,
)
from hastings.domain.changes.execution import PatchProposal, PatchUnit, StepContext
from hastings.domain.changes.models import ChangeScope, SeedChange
from hastings.domain.repository.ids import Actor, CommitId, ProjectId


class KeyRequest(BaseModel):
    idempotency_key: str = Field(min_length=1)


class OpenChangeRequest(KeyRequest):
    intent: str = Field(min_length=1)
    base_commit_id: str
    seed_ids: list[str] = Field(min_length=1)
    scope: dict[str, Any] = Field(default_factory=dict)
    seed_patches: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ExecuteRequest(KeyRequest):
    patches: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ApproveRequest(KeyRequest):
    expected_head: str


class MergeRequest(KeyRequest):
    expected_head: str
    selected: list[str] = Field(default_factory=list)
    risks: dict[str, str] = Field(default_factory=dict)


class OpenRewriteRequest(KeyRequest):
    base_commit_id: str
    locked_unit_ids: list[str] = Field(default_factory=list)


class ManualPatchWorker:
    """人工提交的候选内容；范围仍由执行器检查，不直接落 main。"""

    def __init__(self, patches: dict[str, dict[str, Any]]) -> None:
        self.patches = patches

    def propose(self, context: StepContext) -> PatchProposal:
        return PatchProposal(
            tuple(
                PatchUnit(t.artifact_id, self.patches[t.artifact_id])
                for t in context.targets
                if t.artifact_id in self.patches
            )
        )


class ChangeCenterRuntime(Protocol):
    service: CommitService

    @property
    def clock(self) -> Clock: ...

    changes: ChangeSetCommandHandler
    change_analysis: AnalyzeChangeImpact
    change_planner: PlanRevision
    revision: ExecuteRevisionPlan
    review: ReviewRevision
    rewrite: RewriteWorkflow
    audit: AuditWorkflow


def create_changes_router(runtime: ChangeCenterRuntime) -> APIRouter:
    router = APIRouter(prefix="/projects/{project_id}", tags=["change-center"])
    author = Actor("human", "author")

    def detail(project_id: str, cs_id: str) -> dict[str, Any]:
        _, snapshot = branch_snapshot(runtime.service, ProjectId(project_id))
        model = content(snapshot, cs_id)
        if not any(
            v.artifact.id.value == cs_id and v.artifact.kind == "changes.change_set"
            for v in snapshot.artifacts.values()
        ):
            reject(
                "CHANGES_UNKNOWN_CHANGE_SET",
                "changes.read",
                "对象不是变更集",
                "使用打开变更集返回的 change_set_id",
            )
        objects = [
            {
                "artifact_id": v.artifact.id.value,
                "kind": v.artifact.kind,
                "content": v.revision.content,
            }
            for v in snapshot.artifacts.values()
            if v.revision and v.revision.content.get("change_set_id") == cs_id
        ]
        return {"head_commit_id": snapshot.commit_id.value, "change_set": model, "objects": objects}

    @router.get("/changes")
    def list_changes(project_id: str) -> dict[str, Any]:
        branch = runtime.service.get_branch(ProjectId(project_id), "main")
        if branch is not None and branch.head_commit_id is None:
            return {"head_commit_id": None, "changes": []}
        _, snapshot = branch_snapshot(runtime.service, ProjectId(project_id))
        return {
            "head_commit_id": snapshot.commit_id.value,
            "changes": [
                v.revision.content
                for v in snapshot.artifacts.values()
                if v.revision and v.artifact.kind == "changes.change_set"
            ],
        }

    @router.post("/changes")
    def open_change(project_id: str, req: OpenChangeRequest) -> dict[str, Any]:
        pid = ProjectId(project_id)
        base = runtime.service.get_snapshot(pid, CommitId(req.base_commit_id))
        if not set(req.seed_patches) <= set(req.seed_ids):
            reject(
                "CHANGES_INVALID_COMMAND",
                "changes.open",
                "种子补丁包含未声明对象",
                "seed_patches 只能包含 seed_ids 中的对象",
            )
        for aid in req.seed_ids:
            content(base, aid)
        command = change_set_command(
            open_change_set,
            intent=req.intent,
            source="manual",
            base_commit_id=req.base_commit_id,
            seed_changes=tuple(
                SeedChange(aid, proposed_content=req.seed_patches.get(aid)) for aid in req.seed_ids
            ),
            scope=ChangeScope.from_content(req.scope),
            actor=author,
            at=runtime.clock.now(),
            reason=req.intent,
        )
        result = runtime.changes.execute(
            command, project_id=pid, idempotency_key=req.idempotency_key, summary="打开变更集"
        )
        return dict(result.change.artifact_changes[0].content)

    @router.get("/changes/{cs_id}")
    def read_change(project_id: str, cs_id: str) -> dict[str, Any]:
        return detail(project_id, cs_id)

    @router.post("/changes/{cs_id}/analyze")
    def analyze(project_id: str, cs_id: str, req: KeyRequest) -> dict[str, Any]:
        return asdict(
            runtime.change_analysis.analyze(
                AnalyzeIntent(ProjectId(project_id), cs_id, req.idempotency_key)
            )
        )

    @router.post("/changes/{cs_id}/plan")
    def plan(project_id: str, cs_id: str, req: KeyRequest) -> dict[str, Any]:
        from hastings.application.workflows.plan_revision import PlanIntent

        runtime.change_planner.plan(PlanIntent(ProjectId(project_id), cs_id, req.idempotency_key))
        return detail(project_id, cs_id)

    @router.post("/changes/{cs_id}/approve")
    def approve(project_id: str, cs_id: str, req: ApproveRequest) -> dict[str, Any]:
        pid = ProjectId(project_id)
        if runtime.service.find_commit_result(pid, req.idempotency_key) is None:
            receipt_commit = runtime.service.find_commit_result(
                pid, f"{req.idempotency_key}:receipt"
            )
            if (
                receipt_commit is None
                and detail(project_id, cs_id)["head_commit_id"] != req.expected_head
            ):
                reject(
                    "CHANGES_REVIEW_CONFLICT",
                    "changes.approve",
                    "作者确认页已过期",
                    "重新读取报告和计划后批准",
                )
            result = runtime.changes.execute(
                change_set_command(
                    issue_approval_receipt,
                    change_set_id=cs_id,
                    actor=author,
                    at=runtime.clock.now(),
                    reason="作者确认页面范围",
                ),
                project_id=pid,
                idempotency_key=f"{req.idempotency_key}:receipt",
                summary="签发批准凭证",
            )
            receipt = result.change.artifact_changes[0].artifact_id.value
            runtime.changes.execute(
                change_set_command(
                    advance_change_set,
                    change_set_id=cs_id,
                    target="approved",
                    approval_ref=receipt,
                    actor=author,
                    at=runtime.clock.now(),
                    reason="作者批准执行计划",
                ),
                project_id=pid,
                idempotency_key=req.idempotency_key,
                summary="作者批准计划",
            )
        return detail(project_id, cs_id)

    @router.post("/changes/{cs_id}/execute")
    def execute(project_id: str, cs_id: str, req: ExecuteRequest) -> dict[str, Any]:
        data = detail(project_id, cs_id)
        plans = [o["content"] for o in data["objects"] if o["kind"] == "changes.revision_plan"]
        allowed = {aid for p in plans for step in p["steps"] for aid in step["target_artifact_ids"]}
        if not set(req.patches) <= allowed:
            reject(
                "CHANGES_PROPOSAL_OUT_OF_SCOPE",
                "changes.execute",
                "人工 Patch 超出批准范围",
                "仅提交修订计划中的目标对象",
            )
        return asdict(
            runtime.revision.execute(
                ExecuteIntent(ProjectId(project_id), cs_id, req.idempotency_key),
                ManualPatchWorker(req.patches),
            )
        )

    @router.post("/changes/{cs_id}/review")
    def prepare(project_id: str, cs_id: str, req: KeyRequest) -> dict[str, Any]:
        return runtime.review.prepare(ProjectId(project_id), cs_id, req.idempotency_key)

    @router.post("/changes/{cs_id}/merge")
    def merge(project_id: str, cs_id: str, req: MergeRequest) -> dict[str, Any]:
        result = runtime.review.merge(
            ProjectId(project_id),
            cs_id,
            tuple(req.selected),
            req.risks,
            req.idempotency_key,
            req.expected_head,
        )
        try:
            runtime.audit.run(ProjectId(project_id), f"{req.idempotency_key}:audit")
        except (EnvironmentFailure, OSError):
            # 合并 Commit 已成功；审计故障保留为可重跑项，不能让作者以为保存失败。
            return {**result, "audit_status": "pending"}
        return result

    @router.post("/audit")
    def audit(project_id: str, req: KeyRequest) -> dict[str, Any]:
        return runtime.audit.run(ProjectId(project_id), req.idempotency_key)

    @router.post("/rewrites")
    def open_rewrite(project_id: str, req: OpenRewriteRequest) -> dict[str, Any]:
        return runtime.rewrite.open(
            ProjectId(project_id),
            req.base_commit_id,
            tuple(req.locked_unit_ids),
            req.idempotency_key,
        )

    @router.get("/rewrites/{run_id:path}/compare")
    def compare(project_id: str, run_id: str) -> dict[str, Any]:
        return runtime.rewrite.compare(ProjectId(project_id), run_id)

    @router.post("/rewrites/{run_id:path}/execute")
    def execute_rewrite(project_id: str, run_id: str, req: ExecuteRequest) -> dict[str, Any]:
        return runtime.rewrite.execute(
            ProjectId(project_id),
            run_id,
            ManualPatchWorker(req.patches).propose,
            req.idempotency_key,
        )

    @router.post("/rewrites/{run_id:path}/{action}")
    def control_rewrite(
        project_id: str, run_id: str, action: str, req: KeyRequest
    ) -> dict[str, Any]:
        if action not in ("abort", "abandon", "resume"):
            reject(
                "CHANGES_INVALID_COMMAND",
                "rewrite.control",
                "未知控制操作",
                "使用 abort/abandon/resume",
            )
        return dict(
            getattr(runtime.rewrite, action)(ProjectId(project_id), run_id, req.idempotency_key)
        )

    return router
