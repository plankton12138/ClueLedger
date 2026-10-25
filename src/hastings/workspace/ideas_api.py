"""灵感本的作品接口（0.8 C）。

挂在作品前缀下：作者能浏览与编辑这个作品的灵感；agent 不能借作者的 HTTP 权限读灵感，
它要经过专用工具与当轮授权。响应字段统一 snake_case，错误沿用统一信封。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from hastings.workspace.ideas import IdeaStore, IdeaUseDraft
from hastings.workspace.state import RecentWorkspaces

__all__ = ["create_ideas_router"]


class IdeaOriginPayload(BaseModel):
    role: Literal["user", "assistant"] = "user"
    conversation_id: str = ""
    message_seq: int = 0
    quote: str = ""
    #: 产生这段文字的那一轮；比 message_seq 更稳，旧客户端可以不传。
    turn_id: str = ""


class CreateIdeaRequest(BaseModel):
    title: str = ""
    body: str
    tags: list[str] = Field(default_factory=list)
    origin: IdeaOriginPayload | None = None
    request_key: str


class UpdateIdeaRequest(BaseModel):
    expected_digest: str
    expected_revision: str
    title: str | None = None
    body: str | None = None
    tags: list[str] | None = None
    request_key: str


class TrashIdeaRequest(BaseModel):
    expected_digest: str
    expected_revision: str
    request_key: str


class RestoreIdeaRequest(BaseModel):
    target_revision: str
    expected_digest: str
    request_key: str


class LinkIdeaRequest(BaseModel):
    idea_revision_id: str
    kind: Literal["author_link", "applied"]
    target_record_id: str = ""
    target_path: str = ""
    target_digest: str = ""
    quote: str = ""
    start: int | None = None
    end: int | None = None
    turn_id: str = ""
    expected_digest: str
    request_key: str


def create_ideas_router(workspaces: RecentWorkspaces) -> APIRouter:
    router = APIRouter(prefix="/api/file-workspaces", tags=["ideas"])

    def store_for(workspace_id: str) -> IdeaStore:
        return IdeaStore(workspaces.workspace(workspace_id))

    @router.get("/{workspace_id}/ideas")
    def list_ideas(
        workspace_id: str,
        q: str = "",
        deleted: bool = False,
        cursor: str | None = None,
        limit: int = 20,
        order: Literal["created", "updated"] = "updated",
    ) -> dict[str, object]:
        # 新界面显式传 created；默认 updated 保持旧客户端行为。
        return store_for(workspace_id).list(
            q=q, deleted=deleted, cursor=cursor, limit=limit, order=order
        )

    @router.post("/{workspace_id}/ideas")
    def create_idea(workspace_id: str, payload: CreateIdeaRequest) -> dict[str, object]:
        return store_for(workspace_id).create(
            title=payload.title,
            body=payload.body,
            tags=payload.tags,
            origin=payload.origin.model_dump() if payload.origin is not None else None,
            request_key=payload.request_key,
        )

    @router.get("/{workspace_id}/ideas/{idea_id}")
    def get_idea(workspace_id: str, idea_id: str) -> dict[str, object]:
        return store_for(workspace_id).get(idea_id)

    @router.patch("/{workspace_id}/ideas/{idea_id}")
    def update_idea(
        workspace_id: str, idea_id: str, payload: UpdateIdeaRequest
    ) -> dict[str, object]:
        return store_for(workspace_id).update(
            idea_id,
            expected_digest=payload.expected_digest,
            expected_revision=payload.expected_revision,
            title=payload.title,
            body=payload.body,
            tags=payload.tags,
            request_key=payload.request_key,
        )

    @router.post("/{workspace_id}/ideas/{idea_id}/trash")
    def trash_idea(workspace_id: str, idea_id: str, payload: TrashIdeaRequest) -> dict[str, object]:
        return store_for(workspace_id).trash(
            idea_id,
            expected_digest=payload.expected_digest,
            expected_revision=payload.expected_revision,
            request_key=payload.request_key,
        )

    @router.post("/{workspace_id}/ideas/{idea_id}/restore")
    def restore_idea(
        workspace_id: str, idea_id: str, payload: RestoreIdeaRequest
    ) -> dict[str, object]:
        return store_for(workspace_id).restore(
            idea_id,
            target_revision=payload.target_revision,
            expected_digest=payload.expected_digest,
            request_key=payload.request_key,
        )

    @router.get("/{workspace_id}/ideas/{idea_id}/revisions")
    def list_revisions(
        workspace_id: str, idea_id: str, cursor: str | None = None, limit: int = 20
    ) -> dict[str, object]:
        return store_for(workspace_id).revisions(idea_id, cursor=cursor, limit=limit)

    @router.get("/{workspace_id}/ideas/{idea_id}/revisions/{revision_id}")
    def get_revision(workspace_id: str, idea_id: str, revision_id: str) -> dict[str, object]:
        return store_for(workspace_id).revision(idea_id, revision_id)

    @router.post("/{workspace_id}/ideas/{idea_id}/links")
    def link_idea(workspace_id: str, idea_id: str, payload: LinkIdeaRequest) -> dict[str, object]:
        use = IdeaUseDraft(
            idea_revision_id=payload.idea_revision_id,
            kind=payload.kind,
            target_record_id=payload.target_record_id,
            target_path=payload.target_path,
            target_digest=payload.target_digest,
            quote=payload.quote,
            start=payload.start,
            end=payload.end,
            turn_id=payload.turn_id,
        )
        return store_for(workspace_id).add_use(
            idea_id,
            expected_digest=payload.expected_digest,
            use=use,
            request_key=payload.request_key,
        )

    return router
