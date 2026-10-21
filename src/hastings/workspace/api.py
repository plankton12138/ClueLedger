"""文件工作区接口（0.4 M06）。

契约按「界面直接反映目录」设计：目录列表、两块内容目录、单文件原文与指纹、搜索、
导入，以及对话、轮次与事件流。这里**没有**写文件端点——正文修改只能来自对话里的
Agent 或应用之外的编辑器，界面因此不会出现「先保存再发布」的第二条写入路径。

错误信封与 0.3 保持一致（``{"error": {code, where, why, fix, details}}``），
状态码由错误码尾缀决定，前端按 code 分支而不是解析文本。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, FastAPI, File, Header, Query, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from hastings.workspace.directory_dialog import DirectoryPickerError, pick_directory
from hastings.workspace.errors import WorkspaceError, status_for_code
from hastings.workspace.files import (
    FileEntry,
    FileKind,
    Workspace,
    chapter_title,
    character_title,
    managed_kind,
)
from hastings.workspace.git import GitRepo
from hastings.workspace.locations import default_workspace_root
from hastings.workspace.service import ProviderFactory, TurnService
from hastings.workspace.state import (
    Conversation,
    ConversationStore,
    ConversationUiStore,
    RecentWorkspaces,
    WorkspaceBinding,
    conversation_root,
)

__all__ = [
    "AcceptedTurnView",
    "ActiveTurnView",
    "CatalogView",
    "ConversationListView",
    "ConversationUiView",
    "ConversationView",
    "DefaultLocationView",
    "FileEntryView",
    "FileViewResponse",
    "GitChangeView",
    "GitCommitView",
    "GitDiffView",
    "GitLogView",
    "GitStatusView",
    "OpenWorkspaceRequest",
    "PickDirectoryRequest",
    "PickedDirectoryView",
    "SearchResponse",
    "TurnRequest",
    "TurnView",
    "WorkspaceView",
    "create_file_workspace_router",
    "register_workspace_error_handlers",
]

PREFIX = "/api/file-workspaces"


class OpenWorkspaceRequest(BaseModel):
    """按服务器本地路径打开或新建；``create`` 为真时补齐两块内容目录。"""

    path: str = Field(min_length=1)
    create: bool = False


class DefaultLocationView(BaseModel):
    """新作品的默认父目录。只给目录，不替作者起作品名。"""

    root: str


class PickDirectoryRequest(BaseModel):
    """打开文件夹选择框时的起始目录；不是最终结果。"""

    initial: str = ""


class PickedDirectoryView(BaseModel):
    """作者在系统对话框里选的目录；取消时 ``path`` 为 null。"""

    path: str | None


class WorkspaceView(BaseModel):
    id: str
    name: str
    path: str
    exists: bool


class FileEntryView(BaseModel):
    path: str
    kind: FileKind
    title: str
    order: str
    exists: bool
    size: int


class CatalogView(BaseModel):
    chapters: list[FileEntryView]
    characters: list[FileEntryView]
    settings: list[FileEntryView]


class FileViewResponse(BaseModel):
    path: str
    kind: FileKind
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


class ConversationView(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str


class ActiveTurnView(BaseModel):
    """本作品当前占用的那一轮；只含公开信息。"""

    conversation_id: str
    turn_id: str
    state: Literal["accepted", "running", "stop_requested"]
    model: str
    connection: str
    started_at: str


class ConversationListView(BaseModel):
    """会话列表带上「上次看的会话」与作品当前活动轮次，一次读齐。"""

    items: list[ConversationView]
    recent_id: str | None
    active_turn: ActiveTurnView | None
    revision: str


class AcceptedTurnView(BaseModel):
    conversation_id: str
    turn_id: str
    started_at: str
    title: str
    request_key: str


class CreateConversationRequest(BaseModel):
    """首次发送即建会话：空提示不再创建空记录。"""

    text: str = Field(min_length=1)
    request_key: str = Field(min_length=1)


class RenameConversationRequest(BaseModel):
    title: str = Field(min_length=1)


class TurnRequest(BaseModel):
    text: str = Field(min_length=1)
    request_key: str = Field(min_length=1)


class TurnView(BaseModel):
    turn_id: str
    conversation_id: str
    started_at: str
    title: str = ""


class EventsView(BaseModel):
    events: list[dict[str, object]]
    last_seq: int
    active_turn: ActiveTurnView | None = None
    revision: str = ""


class ConversationUiView(BaseModel):
    recent_id: str | None
    drafts: dict[str, str]
    revision: str


class ConversationUiPatch(BaseModel):
    """只改指定的那一项：不整份覆盖其他会话的草稿。"""

    set_recent: bool = False
    recent_id: str | None = None
    draft_target: str | None = None
    draft_text: str | None = None
    set_draft: bool = False


class StopView(BaseModel):
    stopped: bool


class GitChangeView(BaseModel):
    path: str
    code: str
    orig_path: str | None = None


class GitStatusView(BaseModel):
    initialized: bool
    branch: str | None = None
    head: str | None = None
    staged: list[str] = Field(default_factory=list)
    changes: list[GitChangeView] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    clean: bool = True


class GitDiffView(BaseModel):
    diff: str


class GitCommitView(BaseModel):
    commit: str
    at: str
    subject: str


class GitLogView(BaseModel):
    commits: list[GitCommitView]


class RevertRequest(BaseModel):
    commit: str = Field(min_length=1)


class RecordRequest(BaseModel):
    message: str = ""


class RecordedView(BaseModel):
    commit: str | None


def create_file_workspace_router(
    workspaces: RecentWorkspaces, provider_factory: ProviderFactory
) -> APIRouter:
    """绑定最近目录登记与模型来源；每个作品一个轮次服务，按需创建。"""
    router = APIRouter(prefix=PREFIX, tags=["file-workspaces"])
    services: dict[str, TurnService] = {}
    ui_stores: dict[str, ConversationUiStore] = {}

    def view(binding: WorkspaceBinding) -> WorkspaceView:
        return WorkspaceView(
            id=binding.id, name=binding.name, path=binding.path, exists=binding.exists
        )

    def service_for(workspace_id: str) -> TurnService:
        binding = workspaces.get(workspace_id)
        cached = services.get(workspace_id)
        if cached is not None and str(cached.workspace.root) == binding.path:
            return cached
        workspace = workspaces.workspace(workspace_id)
        store = ConversationStore(conversation_root(workspaces.root, workspace_id))
        service = TurnService(workspace, store, provider_factory)
        # 进程重启后，没有正常结束的轮次在这里补一条中断记录；不自动继续。
        service.mark_interrupted()
        services[workspace_id] = service
        return service

    def ui_for(workspace_id: str) -> ConversationUiStore:
        workspaces.get(workspace_id)
        cached = ui_stores.get(workspace_id)
        if cached is not None:
            return cached
        store = ConversationUiStore(conversation_root(workspaces.root, workspace_id))
        ui_stores[workspace_id] = store
        return store

    def active_view(
        service: TurnService, conversation_id: str | None = None
    ) -> ActiveTurnView | None:
        active = service.active_turn()
        if active is None:
            return None
        if conversation_id is not None and active.conversation_id != conversation_id:
            return None
        return ActiveTurnView(
            conversation_id=active.conversation_id,
            turn_id=active.turn_id,
            state=active.state,
            model=active.model,
            connection=active.connection,
            started_at=active.started_at,
        )

    @router.get("", response_model=list[WorkspaceView])
    def list_workspaces() -> list[WorkspaceView]:
        return [view(binding) for binding in workspaces.recent()]

    @router.get("/default-location", response_model=DefaultLocationView)
    def default_location() -> DefaultLocationView:
        """新作品的默认父目录（作者的文档目录）；界面用它作为起始位置。"""
        return DefaultLocationView(root=str(default_workspace_root()))

    @router.post("/pick-directory", response_model=PickedDirectoryView)
    async def choose_directory(body: PickDirectoryRequest | None = None) -> PickedDirectoryView:
        """在运行应用的这台机器上弹出系统文件夹选择框；取消返回 null。

        界面不再接受手输路径，作者只能从系统对话框里选；这条接口是唯一入口。
        对话框阻塞到作者操作完为止，因此放在线程里等，不挡住其他请求。
        """
        initial: Path | None = None
        if body is not None and body.initial.strip():
            candidate = Path(body.initial).expanduser()
            initial = candidate if candidate.is_dir() else None
        try:
            chosen = await asyncio.to_thread(pick_directory, initial=initial)
        except DirectoryPickerError as exc:
            raise WorkspaceError(
                code=exc.code,
                where="workspace.pick_directory",
                why=exc.why,
                fix=exc.fix,
            ) from exc
        return PickedDirectoryView(path=str(chosen) if chosen is not None else None)

    @router.post("", response_model=WorkspaceView)
    def open_workspace(body: OpenWorkspaceRequest) -> WorkspaceView:
        target = Path(body.path).expanduser()
        if body.create:
            # 新作品同时建立版本记录：历史从第一份内容开始，而不是从某次修改补起。
            Workspace.create(target)
            GitRepo(target).init()
        else:
            # 打开已有目录只读检查，不擅自初始化或改写内容。
            Workspace.open(target)
        return view(workspaces.bind(target))

    @router.get("/{workspace_id}/files", response_model=CatalogView)
    def list_files(workspace_id: str) -> CatalogView:
        catalog = workspaces.workspace(workspace_id).catalog()
        return CatalogView(
            chapters=[_entry(entry) for entry in catalog.chapters],
            characters=[_entry(entry) for entry in catalog.characters],
            settings=[_entry(entry) for entry in catalog.settings],
        )

    @router.get("/{workspace_id}/file", response_model=FileViewResponse)
    def read_file(workspace_id: str, path: str = Query(min_length=1)) -> FileViewResponse:
        workspace = workspaces.workspace(workspace_id)
        view_file = workspace.read(path)
        kind = managed_kind(path)
        assert kind is not None  # workspace.read 已校验受管路径。
        if kind == "setting":
            title = _setting_title(path)
        elif kind == "character":
            title = character_title(view_file.text, Path(path).name)
        else:
            title = chapter_title(view_file.text, Path(path).name)
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
        result = workspaces.workspace(workspace_id).search(q, limit=limit)
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
        workspace = workspaces.workspace(workspace_id)
        data = await file.read()
        workspace.import_text(file.filename or "导入正文.md", data)
        catalog = workspace.catalog()
        return CatalogView(
            chapters=[_entry(entry) for entry in catalog.chapters],
            characters=[_entry(entry) for entry in catalog.characters],
            settings=[_entry(entry) for entry in catalog.settings],
        )

    # -- Git ---------------------------------------------------------------

    @router.get("/{workspace_id}/git/status", response_model=GitStatusView)
    def git_status(workspace_id: str) -> GitStatusView:
        return _git_status(GitRepo(workspaces.workspace(workspace_id).root))

    @router.get("/{workspace_id}/git/diff", response_model=GitDiffView)
    def git_diff(workspace_id: str) -> GitDiffView:
        """工作区相对 HEAD 的差异；未跟踪的新章节不在其中（见模块说明）。"""
        repo = GitRepo(workspaces.workspace(workspace_id).root)
        return GitDiffView(diff=repo.diff())

    @router.get("/{workspace_id}/git/log", response_model=GitLogView)
    def git_log(workspace_id: str, limit: int = Query(default=50, ge=1, le=200)) -> GitLogView:
        repo = GitRepo(workspaces.workspace(workspace_id).root)
        return GitLogView(
            commits=[
                GitCommitView(commit=commit.sha, at=commit.committed_at, subject=commit.subject)
                for commit in repo.log(limit)
            ]
        )

    @router.get("/{workspace_id}/git/commits/{commit}/diff", response_model=GitDiffView)
    def git_commit_diff(workspace_id: str, commit: str) -> GitDiffView:
        repo = GitRepo(workspaces.workspace(workspace_id).root)
        return GitDiffView(diff=repo.commit_diff(commit))

    @router.post("/{workspace_id}/git/init", response_model=GitStatusView)
    def git_init(workspace_id: str) -> GitStatusView:
        """对已有目录启用版本记录：初始化并记录当前内容作为基线。"""
        repo = GitRepo(workspaces.workspace(workspace_id).root)
        repo.init()
        return _git_status(repo)

    @router.post("/{workspace_id}/git/record", response_model=RecordedView)
    def git_record(workspace_id: str, body: RecordRequest | None = None) -> RecordedView:
        """单独补记未记录的改动：只执行 Git，不调用模型。"""
        message = (body.message.strip() if body else "") or "补记：应用之外的改动"
        commit = GitRepo(workspaces.workspace(workspace_id).root).record(message)
        return RecordedView(commit=commit)

    @router.post("/{workspace_id}/git/revert", response_model=RecordedView)
    def git_revert(workspace_id: str, body: RevertRequest) -> RecordedView:
        """撤销指定提交（必须是 HEAD）；生成反向提交，不改写历史。"""
        commit = GitRepo(workspaces.workspace(workspace_id).root).revert_latest(body.commit)
        return RecordedView(commit=commit or None)

    # -- 对话与轮次 ---------------------------------------------------------

    @router.get("/{workspace_id}/conversations", response_model=ConversationListView)
    def list_conversations(workspace_id: str) -> ConversationListView:
        service = service_for(workspace_id)
        items = sorted(
            service.store.conversations(), key=lambda item: item.updated_at, reverse=True
        )
        ui = ui_for(workspace_id).read()
        known = {item.id for item in items}
        return ConversationListView(
            items=[_conversation(item) for item in items],
            # 记住的会话已被删除时不给悬空 ID：界面按最新活动自己选一个。
            recent_id=ui.recent_id if ui.recent_id in known else None,
            active_turn=active_view(service),
            revision=service.store.revision(),
        )

    @router.post("/{workspace_id}/conversations", response_model=AcceptedTurnView, status_code=202)
    async def create_conversation(
        workspace_id: str, body: CreateConversationRequest
    ) -> AcceptedTurnView:
        """首次发送：一次请求同时建会话与受理首轮，不再有「空会话」这一步。"""
        service = service_for(workspace_id)
        info = await service.accept(body.text, body.request_key)
        return AcceptedTurnView(
            conversation_id=info.conversation_id,
            turn_id=info.turn_id,
            started_at=info.started_at,
            title=info.title,
            request_key=body.request_key,
        )

    @router.patch(
        "/{workspace_id}/conversations/{conversation_id}", response_model=ConversationView
    )
    def rename_conversation(
        workspace_id: str, conversation_id: str, body: RenameConversationRequest
    ) -> ConversationView:
        service = service_for(workspace_id)
        return _conversation(service.store.rename(conversation_id, body.title))

    @router.get(
        "/{workspace_id}/conversations/{conversation_id}/messages", response_model=EventsView
    )
    def conversation_messages(
        workspace_id: str,
        conversation_id: str,
        after: int = Query(default=0, ge=0),
    ) -> EventsView:
        service = service_for(workspace_id)
        service.store.get(conversation_id)
        events = service.store.events(conversation_id, after=after)
        last_seq = int(events[-1]["seq"]) if events else after
        return EventsView(
            events=[dict(event) for event in events],
            last_seq=last_seq,
            active_turn=active_view(service, conversation_id),
            revision=service.store.revision(),
        )

    @router.post(
        "/{workspace_id}/conversations/{conversation_id}/turns",
        response_model=TurnView,
        status_code=202,
    )
    async def start_turn(workspace_id: str, conversation_id: str, body: TurnRequest) -> TurnView:
        service = service_for(workspace_id)
        service.store.get(conversation_id)
        info = await service.accept(body.text, body.request_key, conversation_id=conversation_id)
        return TurnView(
            turn_id=info.turn_id,
            conversation_id=info.conversation_id,
            started_at=info.started_at,
            title=info.title,
        )

    @router.post("/{workspace_id}/turns/{turn_id}/stop", response_model=StopView)
    async def stop_turn(workspace_id: str, turn_id: str) -> StopView:
        service = service_for(workspace_id)
        if not await service.stop(turn_id):
            raise WorkspaceError(
                code="TURN_NOT_FOUND",
                where="turns.stop",
                why=f"这一轮已经结束或不存在：{turn_id}",
                fix="刷新对话后确认当前是否还有正在进行的轮次",
                details={"turn_id": turn_id},
            )
        return StopView(stopped=True)

    @router.get("/{workspace_id}/conversation-ui", response_model=ConversationUiView)
    def read_conversation_ui(workspace_id: str) -> ConversationUiView:
        ui = ui_for(workspace_id).read()
        return ConversationUiView(
            recent_id=ui.recent_id, drafts=dict(ui.drafts), revision=ui.revision
        )

    @router.patch("/{workspace_id}/conversation-ui", response_model=ConversationUiView)
    def update_conversation_ui(
        workspace_id: str,
        body: ConversationUiPatch,
        if_match: Annotated[str, Header(alias="If-Match")] = "",
    ) -> ConversationUiView:
        """按目标合并界面状态：只动指定的那一条草稿或最近会话。"""
        store = ui_for(workspace_id)
        if body.set_draft and not (body.draft_target or "").strip():
            raise WorkspaceError(
                code="CONVERSATION_UI_TARGET_INVALID",
                where="conversation-ui.update",
                why="没有指定草稿属于哪个会话",
                fix="带上会话 ID，或对新对话使用固定槽位 new",
            )
        if body.set_recent and body.recent_id:
            service_for(workspace_id).store.get(body.recent_id)
        ui = store.update(
            expected_revision=if_match,
            set_recent=body.set_recent,
            recent_id=body.recent_id,
            set_draft=body.set_draft,
            draft_target=body.draft_target,
            draft_text=body.draft_text,
        )
        return ConversationUiView(
            recent_id=ui.recent_id, drafts=dict(ui.drafts), revision=ui.revision
        )

    @router.get("/{workspace_id}/events")
    async def event_stream(
        workspace_id: str,
        conversation_id: str = Query(min_length=1),
        after: int = Query(default=0, ge=0),
        follow: bool = Query(default=True, description="false 时只回放已有事件"),
    ) -> StreamingResponse:
        """按游标推送事件；每轮以 ``run_finished`` 结束，客户端带新的 ``after`` 再连。"""
        service = service_for(workspace_id)
        service.store.get(conversation_id)

        async def stream() -> AsyncIterator[str]:
            cursor = after
            while True:
                batch = service.store.events(conversation_id, after=cursor)
                for record in batch:
                    cursor = int(record["seq"])
                    payload = json.dumps(record, ensure_ascii=False)
                    yield f"id: {cursor}\ndata: {payload}\n\n"
                    if record.get("type") == "run_finished":
                        return
                if not batch and not follow:
                    return
                await asyncio.sleep(0.2)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router


def _git_status(repo: GitRepo) -> GitStatusView:
    if not repo.is_repository():
        return GitStatusView(initialized=False)
    status = repo.status()
    return GitStatusView(
        initialized=True,
        branch=status.branch,
        head=status.head,
        staged=list(status.staged),
        changes=[
            GitChangeView(path=change.path, code=change.code, orig_path=change.orig_path)
            for change in status.changes
        ],
        conflicts=list(status.conflicts),
        clean=status.clean,
    )


def _conversation(item: Conversation) -> ConversationView:
    return ConversationView(
        id=item.id,
        title=item.title,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


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
