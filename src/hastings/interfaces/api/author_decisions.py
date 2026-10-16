"""作者偏好裁决的 HTTP 适配：复用领域命令，不在界面或路由复制状态机。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from hastings.application.errors import ViolationError
from hastings.application.handlers.author_model_commands import (
    AuthorModelCommandHandler,
    author_model_command,
)
from hastings.domain.author_model.commands import declare_preference, revoke_preference
from hastings.domain.repository.ids import Actor, ProjectId

if TYPE_CHECKING:
    from hastings.server.v2 import V2Runtime


class AuthorDecision(BaseModel):
    idempotency_key: str = Field(min_length=1)
    expected_head: str | None


class DeclarePreference(AuthorDecision):
    category: str
    statement: str = Field(min_length=1)
    scope_kind: str = "project"
    scope_target: str = ""


class RevokePreference(AuthorDecision):
    reason: str = Field(min_length=1)


def create_author_decisions_router(runtime: V2Runtime) -> APIRouter:
    router = APIRouter(prefix="/projects/{project_id}/author-model/preferences")
    handler = AuthorModelCommandHandler(
        runtime.service, runtime.service, runtime.ids, Actor("human", "author")
    )

    def check_head(pid: ProjectId, req: AuthorDecision) -> None:
        if runtime.service.find_commit_result(pid, req.idempotency_key) is not None:
            return
        head = runtime.head(pid)
        if (head.value if head else None) != req.expected_head:
            raise ViolationError(
                code="REPO_HEAD_CONFLICT",
                where="author_model.decision",
                why="偏好裁决页面的项目版本已过期",
                fix="刷新偏好与项目版本，核对证据后重新提交",
                details={},
            )

    @router.post("")
    async def declare(project_id: str, req: DeclarePreference) -> dict[str, Any]:
        pid = ProjectId(project_id)
        check_head(pid, req)
        result = handler.execute(
            author_model_command(
                declare_preference,
                category=req.category,
                statement=req.statement,
                scope_kind=req.scope_kind,
                scope_target=req.scope_target,
            ),
            project_id=pid,
            idempotency_key=req.idempotency_key,
            summary="作者声明写作偏好",
        )
        return {"status": result.status, "commit_id": result.commit_id, "new_head": result.new_head}

    @router.post("/{preference_id}/revoke")
    async def revoke(project_id: str, preference_id: str, req: RevokePreference) -> dict[str, Any]:
        pid = ProjectId(project_id)
        check_head(pid, req)
        result = handler.execute(
            author_model_command(revoke_preference, preference_id=preference_id, reason=req.reason),
            project_id=pid,
            idempotency_key=req.idempotency_key,
            summary="作者撤销写作偏好",
        )
        return {"status": result.status, "commit_id": result.commit_id, "new_head": result.new_head}

    return router
