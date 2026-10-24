"""作品装配接口（0.8 D）。

``GET/PUT /assembly`` 读写当前作品的绑定清单；``POST /module-updates`` 对应界面上的
「保存并用于本作品」，分别返回发布与绑定两步的结果。

接口只改作品装配，不复制模块原文；库接口仍然负责发布内容。两者挂同一个应用，
但权限边界不同：作品装配接口不接受任意库路径，也只写当前作品。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from hastings.library.api import EntryPayload
from hastings.library.bindings import AssemblyService
from hastings.library.derive import derive_workspace
from hastings.library.ids import new_id
from hastings.library.modules import ModuleLibrary
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.git import GitRepo
from hastings.workspace.module_assembly import Binding, ModuleRef
from hastings.workspace.state import RecentWorkspaces

__all__ = ["create_assembly_router"]


class ModuleRefPayload(BaseModel):
    library_id: str = ""
    module_id: str
    revision_id: str


class BindingPayload(BaseModel):
    id: str = ""
    module: ModuleRefPayload
    label: str = ""


class PutAssemblyRequest(BaseModel):
    bindings: list[BindingPayload] = Field(default_factory=list)
    links: list[list[str]] = Field(default_factory=list)
    expected_digest: str | None = None
    request_key: str


class DeriveRequest(BaseModel):
    name: str
    directory: str | None = None
    replacements: dict[str, str] = Field(default_factory=dict)
    expected_digest: str | None = None
    request_key: str


class ModuleUpdateRequest(BaseModel):
    module_id: str
    base_revision: str | None = None
    expected_head: str | None = None
    entries: list[EntryPayload]
    expected_digest: str | None = None
    request_key: str


def create_assembly_router(
    workspaces: RecentWorkspaces,
    library: ModuleLibrary,
    *,
    busy: Callable[[Path], bool] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/file-workspaces", tags=["assembly"])

    def service_for(workspace_id: str) -> AssemblyService:
        return AssemblyService(library, workspaces.workspace(workspace_id))

    def require_idle(workspace_id: str) -> None:
        """写作轮次运行中不改装配：这一轮已按受理时的版本冻结，不接受中途换输入。"""
        if busy is None:
            return
        workspace = workspaces.workspace(workspace_id)
        if busy(workspace.root):
            raise WorkspaceError(
                code="ASSEMBLY_BUSY_CONFLICT",
                where="assembly.write",
                why="这个作品还有一轮正在写入，装配已经按受理时的版本冻结",
                fix="等这一轮结束或停止后再装卸；库的发布不受影响",
                details={"workspace_id": workspace_id},
            )

    @router.get("/{workspace_id}/assembly")
    def read_assembly(workspace_id: str) -> dict[str, object]:
        return service_for(workspace_id).view()

    @router.put("/{workspace_id}/assembly")
    def put_assembly(workspace_id: str, payload: PutAssemblyRequest) -> dict[str, object]:
        require_idle(workspace_id)
        bindings = [
            Binding(
                id=item.id or new_id("binding"),
                module=ModuleRef(
                    library_id=item.module.library_id,
                    module_id=item.module.module_id,
                    revision_id=item.module.revision_id,
                ),
                label=item.label,
            )
            for item in payload.bindings
        ]
        links = tuple((str(link[0]), str(link[1])) for link in payload.links if len(link) == 2)
        return service_for(workspace_id).replace(
            bindings=bindings,
            links=links,
            expected_digest=payload.expected_digest,
            request_key=payload.request_key,
        )

    @router.post("/{workspace_id}/derive")
    def derive(workspace_id: str, payload: DeriveRequest) -> dict[str, object]:
        source = workspaces.workspace(workspace_id)
        name = _checked_name(payload.name)
        target = (
            Path(payload.directory).expanduser()
            if payload.directory
            else Path(source.root).parent / name
        )
        result = derive_workspace(
            library,
            source=source,
            source_workspace_id=workspace_id,
            target=target,
            replacements=payload.replacements,
            expected_digest=payload.expected_digest,
            request_key=payload.request_key,
        )
        # 新作品与「新建」走同一条登记与版本记录路径，界面里能看到同一个名字。
        GitRepo(result.root).init()
        binding = workspaces.bind(result.root)
        return {
            "workspace": {
                "id": binding.id,
                "name": binding.name,
                "path": binding.path,
                "exists": binding.exists,
            },
            "assembly": result.assembly,
            "recovered": result.recovered,
        }

    @router.post("/{workspace_id}/module-updates")
    def module_updates(workspace_id: str, payload: ModuleUpdateRequest) -> dict[str, object]:
        require_idle(workspace_id)
        entries: list[dict[str, Any]] = [entry.model_dump() for entry in payload.entries]
        return service_for(workspace_id).publish_and_bind(
            module_id=payload.module_id,
            base_revision=payload.base_revision,
            expected_head=payload.expected_head,
            entries=entries,
            expected_digest=payload.expected_digest,
            request_key=payload.request_key,
        )

    return router


def _checked_name(name: str) -> str:
    text = name.strip()
    if not text:
        raise WorkspaceError(
            code="DERIVE_NAME_INVALID",
            where="derive.target",
            why="新作品需要一个名称",
            fix="写一个目录名，例如「雪山旅馆（另一条线）」",
        )
    if "/" in text or "\\" in text or text in {".", ".."} or len(text) > 80:
        raise WorkspaceError(
            code="DERIVE_NAME_INVALID",
            where="derive.target",
            why=f"名称不能包含路径分隔符或过长：{text}",
            fix="只写目录名；需要别的位置时单独指定目录",
            details={"name": text},
        )
    return text
