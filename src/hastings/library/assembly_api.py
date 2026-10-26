"""作品装配接口（0.8 D）。

``GET/PUT /assembly`` 读写当前作品的绑定清单；``POST /module-updates`` 对应界面上的
「保存并用于本作品」，分别返回发布与绑定两步的结果。

接口只改作品装配，不复制模块原文；库接口仍然负责发布内容。两者挂同一个应用，
但权限边界不同：作品装配接口不接受任意库路径，也只写当前作品。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import APIRouter
from pydantic import BaseModel, Field

from hastings.library.api import EntryPayload
from hastings.library.bindings import AssemblyService
from hastings.library.changes import ChangeEdit, ChangeOperation, ModuleChangeService
from hastings.library.derive import derive_workspace
from hastings.library.ids import new_id
from hastings.library.imports import import_legacy_materials, read_import_info
from hastings.library.modules import ModuleLibrary
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.git import GitRepo
from hastings.workspace.module_assembly import Binding, ModuleRef
from hastings.workspace.state import ConversationStore, RecentWorkspaces, conversation_root

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
    #: 省略表示保留当前关系；显式空列表才是「清空关系」。装卸通常不改关系，
    #: 客户端漏传时不能把作者已经建立的关联一起删掉。
    links: list[list[str]] | None = None
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


class ImportLegacyRequest(BaseModel):
    expected_digest: str | None = None
    request_key: str


class SaveContentRequest(BaseModel):
    module_id: str
    base_revision: str
    name: str
    entries: list[EntryPayload]
    expected_digest: str | None = None
    request_key: str


class ChangeEditPayload(BaseModel):
    entry_id: str
    base_digest: str
    find: str = ""
    replace: str = ""
    body: str | None = None


class ChangeOperationPayload(BaseModel):
    operation_id: str
    action: Literal["create", "update"]
    name: str = ""
    body: str = ""
    binding_id: str = ""
    module_id: str = ""
    base_revision: str = ""
    edits: list[ChangeEditPayload] = Field(default_factory=list)


class PrepareChangesRequest(BaseModel):
    operations: list[ChangeOperationPayload]
    base_digest: str | None = None
    request_key: str
    #: 可选：把准备结果写进这条对话，刷新后仍能回看实际进度。
    conversation_id: str | None = None


class ApplyChangesRequest(BaseModel):
    expected_digest: str | None = None
    request_key: str
    conversation_id: str | None = None


class UndoChangesRequest(BaseModel):
    expected_digest: str | None = None
    request_key: str
    conversation_id: str | None = None


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
        service = service_for(workspace_id)
        if payload.links is None:
            links = tuple(service.store.read().links)
        else:
            links = tuple((str(link[0]), str(link[1])) for link in payload.links if len(link) == 2)
        return service.replace(
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

    @router.get("/{workspace_id}/library-imports")
    def read_imports(workspace_id: str) -> dict[str, object]:
        return read_import_info(workspaces.workspace(workspace_id))

    @router.put("/{workspace_id}/assembly/{binding_id}/content")
    def save_content(
        workspace_id: str, binding_id: str, payload: SaveContentRequest
    ) -> dict[str, object]:
        require_idle(workspace_id)
        return service_for(workspace_id).save_content(
            binding_id=binding_id,
            module_id=payload.module_id,
            base_revision=payload.base_revision,
            name=payload.name,
            entries=[entry.model_dump() for entry in payload.entries],
            expected_digest=payload.expected_digest,
            request_key=payload.request_key,
        )

    @router.post("/{workspace_id}/library-imports")
    def import_legacy(workspace_id: str, payload: ImportLegacyRequest) -> dict[str, object]:
        require_idle(workspace_id)
        return import_legacy_materials(
            library,
            workspaces.workspace(workspace_id),
            expected_digest=payload.expected_digest,
            request_key=payload.request_key,
        )

    def changes_for(workspace_id: str) -> ModuleChangeService:
        return ModuleChangeService(library, workspaces.workspace(workspace_id))

    @router.post("/{workspace_id}/module-change-sets")
    def prepare_changes(workspace_id: str, payload: PrepareChangesRequest) -> dict[str, object]:
        require_idle(workspace_id)
        _checked_request_key(payload.request_key, where="changes.prepare")
        service = changes_for(workspace_id)
        base_digest = payload.base_digest or service.assembly.store.digest()
        view = service.prepare(
            turn_id=f"http:{payload.request_key}",
            operations=_change_operations(payload.operations),
            base_digest=base_digest,
            request_key=payload.request_key,
        )
        _append_change_event(
            workspaces,
            workspace_id,
            payload.conversation_id,
            "module_changes_prepared",
            {"turn_id": f"http:{payload.request_key}", **view},
        )
        return view

    @router.get("/{workspace_id}/module-change-sets/{change_set_id}")
    def read_change_set(workspace_id: str, change_set_id: str) -> dict[str, object]:
        return changes_for(workspace_id).view(change_set_id=change_set_id)

    @router.post("/{workspace_id}/module-change-sets/{change_set_id}/apply")
    def apply_changes(
        workspace_id: str, change_set_id: str, payload: ApplyChangesRequest
    ) -> dict[str, object]:
        require_idle(workspace_id)
        _checked_request_key(payload.request_key, where="changes.apply")
        service = changes_for(workspace_id)
        prepared = service.view(change_set_id=change_set_id)
        turn_id = str(prepared.get("turn_id") or "")
        result = service.apply(
            change_set_id=change_set_id,
            turn_id=turn_id,
            expected_digest=payload.expected_digest,
        )
        if payload.conversation_id:
            saved_events = cast(list[object], result.get("saved_events") or [])
            for saved in saved_events:
                if isinstance(saved, dict):
                    _append_change_event(
                        workspaces,
                        workspace_id,
                        payload.conversation_id,
                        "module_content_saved",
                        {"turn_id": turn_id, **saved},
                    )
            if result.get("applied"):
                _append_change_event(
                    workspaces,
                    workspace_id,
                    payload.conversation_id,
                    "module_changes_applied",
                    {
                        "turn_id": turn_id,
                        "change_set_id": change_set_id,
                        "assembly": result.get("assembly"),
                        "operations": result.get("operations"),
                        "saved": result.get("saved"),
                        "undo": result.get("undo"),
                    },
                )
            else:
                _append_change_event(
                    workspaces,
                    workspace_id,
                    payload.conversation_id,
                    "module_changes_failed",
                    {
                        "turn_id": turn_id,
                        "change_set_id": change_set_id,
                        "stage": result.get("stage"),
                        "error": result.get("error"),
                        "operations": result.get("operations"),
                        "saved": result.get("saved"),
                    },
                )
        return result

    @router.post("/{workspace_id}/module-change-sets/{change_set_id}/undo")
    def undo_changes(
        workspace_id: str, change_set_id: str, payload: UndoChangesRequest
    ) -> dict[str, object]:
        require_idle(workspace_id)
        _checked_request_key(payload.request_key, where="changes.undo")
        service = changes_for(workspace_id)
        prepared = service.view(change_set_id=change_set_id)
        result = service.undo(
            change_set_id=change_set_id,
            expected_digest=payload.expected_digest,
        )
        _append_change_event(
            workspaces,
            workspace_id,
            payload.conversation_id,
            "module_changes_reverted",
            {
                "turn_id": str(prepared.get("turn_id") or ""),
                "change_set_id": change_set_id,
                "assembly": result.get("assembly"),
                "operations": result.get("operations"),
                "note": result.get("note"),
            },
        )
        return result

    return router


def _append_change_event(
    workspaces: RecentWorkspaces,
    workspace_id: str,
    conversation_id: str | None,
    event_type: str,
    payload: dict[str, object],
) -> None:
    """HTTP 路径的继续应用与撤销也要留下同一条会话事件；没有对话就只返回结果。"""
    if not conversation_id:
        return
    store = ConversationStore(conversation_root(workspaces.root, workspace_id))
    store.append(conversation_id, {"type": event_type, **payload})


def _change_operations(
    payloads: list[ChangeOperationPayload],
) -> tuple[ChangeOperation, ...]:
    return tuple(
        ChangeOperation(
            operation_id=item.operation_id,
            action=item.action,
            name=item.name,
            body=item.body,
            binding_id=item.binding_id,
            module_id=item.module_id,
            base_revision=item.base_revision,
            edits=tuple(
                ChangeEdit(
                    entry_id=edit.entry_id,
                    base_digest=edit.base_digest,
                    find=edit.find,
                    replace=edit.replace,
                    body=edit.body,
                )
                for edit in item.edits
            ),
        )
        for item in payloads
    )


def _checked_request_key(key: str, *, where: str) -> str:
    text = str(key or "").strip()
    if not 8 <= len(text) <= 120:
        raise WorkspaceError(
            code="REQUEST_KEY_INVALID",
            where=where,
            why="请求键长度需要在 8–120 字符之间",
            fix="为这次操作生成稳定且不重复的请求键",
        )
    return text


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
