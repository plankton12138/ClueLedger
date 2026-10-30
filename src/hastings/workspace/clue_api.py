"""线索专用接口：只读视图与新建线索身份；不调用模型。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from hastings.library.modules import ModuleLibrary
from hastings.workspace.clue_api_models import CreateClueRequest
from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.clue_views import (
    ClueDetail,
    ClueList,
    clue_detail,
    clue_list,
)
from hastings.workspace.clues import create_clue_identity
from hastings.workspace.source_locations import SourceLocation, resolve_source_location
from hastings.workspace.state import RecentWorkspaces, conversation_root


def create_clue_router(
    workspaces: RecentWorkspaces, library: ModuleLibrary | None = None
) -> APIRouter:
    router = APIRouter(prefix="/api/file-workspaces", tags=["clues"])

    @router.get("/{workspace_id}/clues", response_model=ClueList)
    def list_clues(
        workspace_id: str,
        chapter_path: str | None = Query(default=None, max_length=512),
        as_of_chapter: int | None = Query(default=None, ge=0),
        q: str = Query(default="", max_length=200),
        limit: int = Query(default=20, ge=1, le=50),
        cursor: str = Query(default="", max_length=4096),
    ) -> ClueList:
        return clue_list(
            workspaces.workspace(workspace_id),
            conversation_root(workspaces.root, workspace_id),
            chapter_path=chapter_path,
            as_of_chapter=as_of_chapter,
            query=q,
            limit=limit,
            cursor=cursor,
        )

    @router.get("/{workspace_id}/clues/{clue_id}", response_model=ClueDetail)
    def get_clue(
        workspace_id: str, clue_id: str, as_of_chapter: int | None = Query(default=None, ge=0)
    ) -> ClueDetail:
        return clue_detail(
            workspaces.workspace(workspace_id),
            conversation_root(workspaces.root, workspace_id),
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
