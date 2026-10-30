"""线索专用接口：只读视图与新建线索身份；不调用模型。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query

from hastings.library.modules import ModuleLibrary
from hastings.workspace.clue_api_models import CreateClueRequest
from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.clue_views import (
    ClueDetail,
    ClueList,
    clue_detail,
    clue_list,
)
from hastings.workspace.clues import create_clue_identity
from hastings.workspace.source_locations import SourceLocation, resolve_source_location
from hastings.workspace.state import RecentWorkspaces, conversation_root
from hastings.workspace.story_clue_views import story_clue_detail, story_clue_list
from hastings.workspace.story_store import StoryStore


def create_clue_router(
    workspaces: RecentWorkspaces, library: ModuleLibrary | None = None
) -> APIRouter:
    router = APIRouter(prefix="/api/file-workspaces", tags=["clues"])

    def story_variant_id(workspace_id: str, variant_id: str | None) -> str:
        if variant_id:
            return variant_id
        snapshot = StoryStore(
            conversation_root(workspaces.root, workspace_id),
            workspaces.workspace(workspace_id),
        ).read()
        if snapshot.document is None:
            raise WorkspaceError(
                code="STORY_VARIANT_NOT_FOUND",
                where="clues.story",
                why="当前作品没有构思",
                fix="先建立构思，再读取全书线索",
            )
        return snapshot.document.primary_variant_id

    @router.get("/{workspace_id}/clues", response_model=ClueList)
    def list_clues(
        workspace_id: str,
        scope: Literal["chapter", "story"] = Query(default="chapter"),
        variant_id: str | None = Query(default=None, max_length=64),
        node_id: str | None = Query(default=None, max_length=64),
        chapter_path: str | None = Query(default=None, max_length=512),
        as_of_chapter: int | None = Query(default=None, ge=0),
        q: str = Query(default="", max_length=200),
        limit: int = Query(default=20, ge=1, le=50),
        cursor: str = Query(default="", max_length=4096),
    ) -> ClueList:
        root = conversation_root(workspaces.root, workspace_id)
        if scope == "story":
            if chapter_path is not None:
                raise WorkspaceError(
                    code="CLUE_SCOPE_INVALID",
                    where="clues.list",
                    why="全书线索不能同时按章界路径过滤",
                    fix="使用 node_id 过滤相关线索，或改用 scope=chapter",
                )
            return story_clue_list(
                workspaces.workspace(workspace_id),
                root,
                variant_id=story_variant_id(workspace_id, variant_id),
                node_id=node_id,
                as_of_chapter=as_of_chapter,
                query=q,
                limit=limit,
                cursor=cursor,
                library=library,
            )
        if node_id is not None or variant_id is not None:
            raise WorkspaceError(
                code="CLUE_SCOPE_INVALID",
                where="clues.list",
                why="章界线索不接受构思节点或方案范围",
                fix="使用 scope=story 查看全书线索，或移除 node_id/variant_id",
            )
        return clue_list(
            workspaces.workspace(workspace_id),
            root,
            chapter_path=chapter_path,
            as_of_chapter=as_of_chapter,
            query=q,
            limit=limit,
            cursor=cursor,
        )

    @router.get("/{workspace_id}/clues/{clue_id}", response_model=ClueDetail)
    def get_clue(
        workspace_id: str,
        clue_id: str,
        scope: Literal["chapter", "story"] = Query(default="chapter"),
        variant_id: str | None = Query(default=None, max_length=64),
        as_of_chapter: int | None = Query(default=None, ge=0),
    ) -> ClueDetail:
        root = conversation_root(workspaces.root, workspace_id)
        if scope == "story":
            return story_clue_detail(
                workspaces.workspace(workspace_id),
                root,
                variant_id=story_variant_id(workspace_id, variant_id),
                clue_id=clue_id,
                as_of_chapter=as_of_chapter,
                library=library,
            )
        if variant_id is not None:
            raise WorkspaceError(
                code="CLUE_SCOPE_INVALID",
                where="clues.detail",
                why="章界线索不接受构思方案范围",
                fix="使用 scope=story 查看全书线索，或移除 variant_id",
            )
        return clue_detail(
            workspaces.workspace(workspace_id),
            root,
            clue_id,
            as_of_chapter=as_of_chapter,
            library=library,
        )

    @router.post("/{workspace_id}/clues", response_model=ClueDetail)
    def create_clue(
        workspace_id: str,
        body: CreateClueRequest,
        as_of_chapter: int | None = Query(default=None, ge=0),
    ) -> ClueDetail:
        workspace = workspaces.workspace(workspace_id)
        root = conversation_root(workspaces.root, workspace_id)
        result = create_clue_identity(
            ClueLedgerStore(root),
            title=body.title,
            request_key=body.request_key,
            expected_revision=body.expected_revision,
            created_by="author",
        )
        return clue_detail(
            workspace,
            root,
            str(result["clue_id"]),
            as_of_chapter=as_of_chapter,
            library=library,
        )

    @router.get("/{workspace_id}/source-locations/{evidence_id}", response_model=SourceLocation)
    def source_location(
        workspace_id: str, evidence_id: str, as_of_chapter: int | None = Query(default=None, ge=0)
    ) -> SourceLocation:
        return resolve_source_location(
            workspaces.workspace(workspace_id),
            conversation_root(workspaces.root, workspace_id),
            evidence_id,
            as_of_chapter=as_of_chapter,
        )

    return router
