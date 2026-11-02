"""来源定位接口（1.0.9 P5）。

线索页、线索标注与关系图已随旧页面撤除；账本证据仍可从对话和审查报告打开，
因此只保留来源定位这一条只读接口。
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from hastings.library.modules import ModuleLibrary
from hastings.workspace.source_locations import SourceLocation, resolve_source_location
from hastings.workspace.state import RecentWorkspaces, conversation_root


def create_clue_router(
    workspaces: RecentWorkspaces, library: ModuleLibrary | None = None
) -> APIRouter:
    router = APIRouter(prefix="/api/file-workspaces", tags=["clues"])

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
