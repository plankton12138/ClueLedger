"""Change Center 最小出口；作者命令与 Worker 提案使用不同入口。

V03-8.6：只读列表/详情与变更集创建保留；逐章审批流、独立审计与重写工作流
已退役（HTTP 410），底层服务保留但 0.3 不提供启动。
"""

from __future__ import annotations

from typing import Any, Protocol

from fastapi import APIRouter
from pydantic import BaseModel, Field

from hastings.application.handlers.change_commands import (
    ChangeSetCommandHandler,
    change_set_command,
)
from hastings.application.ports.clock import Clock
from hastings.application.services.commit_service import CommitService
from hastings.application.workflows.change_support import branch_snapshot, content, reject
from hastings.domain.changes.commands import open_change_set
from hastings.domain.changes.models import ChangeScope, SeedChange
from hastings.domain.repository.ids import Actor, CommitId, ProjectId
from hastings.interfaces.api._retired import retired


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


class ChangeCenterRuntime(Protocol):
    service: CommitService

    @property
    def clock(self) -> Clock: ...

    changes: ChangeSetCommandHandler


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

    # ---- 以下为 0.3 已退役入口（V03-8.6）------------------------------------
    # 逐章审批流与独立审计/重写工作流在 0.3 不提供启动：底层服务与历史数据
    # 保留，HTTP 层统一 410 + FEATURE_RETIRED_IN_03，旧 UI/API 都不能绕过。
    # 保留只读的 GET /changes 与 GET /changes/{cs_id}（历史可回看）。

    @router.post("/changes/{cs_id}/analyze")
    def analyze(project_id: str, cs_id: str, req: KeyRequest) -> dict[str, Any]:
        del project_id, cs_id, req
        retired(
            where="changes.analyze",
            why="0.3 不保留逐章审批流（变更集分析已退出日常界面）",
            fix="用对话整体调整（意图区分）或写作台手工修改/修订任务完成同一目标",
        )

    @router.post("/changes/{cs_id}/plan")
    def plan(project_id: str, cs_id: str, req: KeyRequest) -> dict[str, Any]:
        del project_id, cs_id, req
        retired(
            where="changes.plan",
            why="0.3 不保留逐章审批流（修订计划已退出日常界面）",
            fix="用对话整体调整或修订任务（revision-tasks）承载计划与执行",
        )

    @router.post("/changes/{cs_id}/approve")
    def approve(project_id: str, cs_id: str, req: ApproveRequest) -> dict[str, Any]:
        del project_id, cs_id, req
        retired(
            where="changes.approve",
            why="0.3 不保留逐章审批页与批准凭证",
            fix="用对话意图区分（讨论/候选/执行）授权，或手工修改后一次发布",
        )

    @router.post("/changes/{cs_id}/execute")
    def execute(project_id: str, cs_id: str, req: ExecuteRequest) -> dict[str, Any]:
        del project_id, cs_id, req
        retired(
            where="changes.execute",
            why="0.3 不保留按变更集执行 Patch 的旧写路径",
            fix="用统一发布（试写采用/手工修改完成/撤销）写入正文",
        )

    @router.post("/changes/{cs_id}/review")
    def prepare(project_id: str, cs_id: str, req: KeyRequest) -> dict[str, Any]:
        del project_id, cs_id, req
        retired(
            where="changes.review",
            why="0.3 不保留独立修订审阅页",
            fix="用写作台手工修改（跨章副本/三方合并）或版本历史完成审阅",
        )

    @router.post("/changes/{cs_id}/merge")
    def merge(project_id: str, cs_id: str, req: MergeRequest) -> dict[str, Any]:
        del project_id, cs_id, req
        retired(
            where="changes.merge",
            why="0.3 不保留变更集合并写路径（旧写入不再直接动正式稿）",
            fix="用编辑会话完成（三方合并 + 一次发布），或撤销回到目标版本",
        )

    @router.post("/audit")
    def audit(project_id: str, req: KeyRequest) -> dict[str, Any]:
        del project_id, req
        retired(
            where="audit.start",
            why="本版不提供独立一致性审计的启动入口（属后续版本能力）",
            fix="旧报告仍可在历史中回看；需要完整审计时在后续版本明确范围后再启动",
        )

    @router.post("/rewrites")
    def open_rewrite(project_id: str, req: OpenRewriteRequest) -> dict[str, Any]:
        del project_id, req
        retired(
            where="rewrites.open",
            why="修订工作流（rewrites）属旧审阅链，0.3 不提供",
            fix="用写作台手工修改或修订任务（revision-tasks）",
        )

    @router.get("/rewrites/{run_id:path}/compare")
    def compare(project_id: str, run_id: str) -> dict[str, Any]:
        del project_id, run_id
        retired(
            where="rewrites.compare",
            why="修订工作流已退出 0.3，compare 无处可指向",
            fix="在版本历史或本次影响面板查看实际版本差异",
        )

    @router.post("/rewrites/{run_id:path}/execute")
    def execute_rewrite(project_id: str, run_id: str, req: ExecuteRequest) -> dict[str, Any]:
        del project_id, run_id, req
        retired(
            where="rewrites.execute",
            why="修订工作流已退出 0.3，不接受按 run 执行 Patch",
            fix="用统一发布（试写采用/手工修改完成/撤销）写入正文",
        )

    @router.post("/rewrites/{run_id:path}/{action}")
    def control_rewrite(
        project_id: str, run_id: str, action: str, req: KeyRequest
    ) -> dict[str, Any]:
        del project_id, run_id, action, req
        retired(
            where="rewrites.control",
            why="修订工作流已退出 0.3，控制动作（abort/abandon/resume）无对象",
            fix="用修订任务（revision-tasks）的停止/继续控制执行",
        )

    return router
