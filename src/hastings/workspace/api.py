"""文件工作区接口（0.4 M06 的首批端点）。

契约按「界面直接反映目录」设计：目录列表、两块内容目录、单文件原文与指纹、搜索、
导入。这里**没有**写文件端点——正文修改只能来自对话里的 Agent 或应用之外的编辑器，
界面因此不会出现「先保存再发布」的第二条写入路径。

错误信封与 0.3 保持一致（``{"error": {code, where, why, fix, details}}``），
状态码由错误码尾缀决定，前端按 code 分支而不是解析文本。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, FastAPI, File, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from hastings.workspace.errors import WorkspaceError, status_for_code
from hastings.workspace.files import SETTING_PATHS, FileEntry, Workspace, chapter_title
from hastings.workspace.state import RecentWorkspaces, WorkspaceBinding

__all__ = [
    "CatalogView",
    "FileEntryView",
    "FileViewResponse",
    "OpenWorkspaceRequest",
    "SearchResponse",
    "WorkspaceView",
    "create_file_workspace_router",
    "register_workspace_error_handlers",
]

PREFIX = "/api/file-workspaces"


class OpenWorkspaceRequest(BaseModel):
    """按服务器本地路径打开或新建；``create`` 为真时补齐两块内容目录。"""

    path: str = Field(min_length=1)
    create: bool = False


class WorkspaceView(BaseModel):
    id: str
    name: str
    path: str
    exists: bool


class FileEntryView(BaseModel):
    path: str
    kind: Literal["chapter", "setting"]
    title: str
    order: str
    exists: bool
    size: int


class CatalogView(BaseModel):
    chapters: list[FileEntryView]
    settings: list[FileEntryView]


class FileViewResponse(BaseModel):
    path: str
    kind: Literal["chapter", "setting"]
    title: str
    text: str
    digest: str
    size: int


class SearchHitView(BaseModel):
    path: str
    line: int
    text: str


class SearchResponse(BaseModel):
    hits: list[SearchHitView]
    unreadable: list[str]
    truncated: bool


def create_file_workspace_router(book: RecentWorkspaces) -> APIRouter:
    """绑定一个最近目录登记实例；路由本身不持有全局状态。"""
    router = APIRouter(prefix=PREFIX, tags=["file-workspaces"])

    def view(binding: WorkspaceBinding) -> WorkspaceView:
        return WorkspaceView(
            id=binding.id, name=binding.name, path=binding.path, exists=binding.exists
        )

    @router.get("", response_model=list[WorkspaceView])
    def list_workspaces() -> list[WorkspaceView]:
        return [view(binding) for binding in book.recent()]

    @router.post("", response_model=WorkspaceView)
    def open_workspace(body: OpenWorkspaceRequest) -> WorkspaceView:
        target = Path(body.path).expanduser()
        if body.create:
            Workspace.create(target)
        else:
            Workspace.open(target)
        return view(book.bind(target))

    @router.get("/{workspace_id}/files", response_model=CatalogView)
    def list_files(workspace_id: str) -> CatalogView:
        catalog = book.workspace(workspace_id).catalog()
        return CatalogView(
            chapters=[_entry(entry) for entry in catalog.chapters],
            settings=[_entry(entry) for entry in catalog.settings],
        )

    @router.get("/{workspace_id}/file", response_model=FileViewResponse)
    def read_file(workspace_id: str, path: str = Query(min_length=1)) -> FileViewResponse:
        workspace = book.workspace(workspace_id)
        view_file = workspace.read(path)
        kind: Literal["chapter", "setting"] = "setting" if path in SETTING_PATHS else "chapter"
        title = (
            _setting_title(path)
            if kind == "setting"
            else chapter_title(view_file.text, Path(path).name)
        )
        return FileViewResponse(
            path=view_file.path,
            kind=kind,
            title=title,
            text=view_file.text,
            digest=view_file.digest,
            size=view_file.size,
        )

    @router.get("/{workspace_id}/search", response_model=SearchResponse)
    def search(
        workspace_id: str,
        q: str = Query(min_length=1),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> SearchResponse:
        result = book.workspace(workspace_id).search(q, limit=limit)
        return SearchResponse(
            hits=[
                SearchHitView(path=hit.path, line=hit.line, text=hit.text) for hit in result.hits
            ],
            unreadable=list(result.unreadable),
            truncated=result.truncated,
        )

    @router.post("/{workspace_id}/imports", response_model=CatalogView)
    async def import_manuscript(
        workspace_id: str,
        file: Annotated[UploadFile, File(description="待导入的 .md 或 .txt 正文")],
    ) -> CatalogView:
        workspace = book.workspace(workspace_id)
        data = await file.read()
        workspace.import_text(file.filename or "导入正文.md", data)
        catalog = workspace.catalog()
        return CatalogView(
            chapters=[_entry(entry) for entry in catalog.chapters],
            settings=[_entry(entry) for entry in catalog.settings],
        )

    return router


def register_workspace_error_handlers(app: FastAPI) -> None:
    """把文件工作区错误映射为统一信封；只用于新应用，不影响旧 /api/v2。"""

    async def handler(request: Request, exc: Exception) -> JSONResponse:
        del request
        # 只注册给 WorkspaceError；这里显式收窄，避免把其他异常也伪装成文件错误。
        assert isinstance(exc, WorkspaceError)
        return JSONResponse(status_code=status_for_code(exc.code), content=_error_body(exc))

    app.add_exception_handler(WorkspaceError, handler)


def _error_body(exc: WorkspaceError) -> dict[str, object]:
    return {
        "error": {
            "code": exc.code,
            "where": exc.where,
            "why": exc.why,
            "fix": exc.fix,
            "details": dict(exc.details),
        }
    }


def _entry(entry: FileEntry) -> FileEntryView:
    return FileEntryView(
        path=entry.path,
        kind=entry.kind,
        title=entry.title,
        order=entry.order,
        exists=entry.exists,
        size=entry.size,
    )


def _setting_title(path: str) -> str:
    return Path(path).stem
