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
import threading
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, FastAPI, File, Header, Query, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hastings.workspace.activity import ActivityStore
from hastings.workspace.chapter_index import (
    MAX_INDEX_CHAPTERS,
    ChapterIndexEntry,
    ChapterIndexRecord,
    ChapterIndexStore,
)
from hastings.workspace.character_states import (
    CharacterStateStore,
    build_roster,
    roster_view,
)
from hastings.workspace.clues import ClueStore, build_clues, clues_view
from hastings.workspace.directory_dialog import DirectoryPickerError, pick_directory
from hastings.workspace.disclosure import (
    DisclosureState as DisclosureStateRecord,
)
from hastings.workspace.disclosure import (
    DisclosureStore,
    build_disclosure_state,
    state_view,
)
from hastings.workspace.errors import WorkspaceError, status_for_code
from hastings.workspace.files import FileEntry, FileKind, Workspace, is_managed_path
from hastings.workspace.git import GitRepo
from hastings.workspace.glossary import GlossaryStore, build_glossary, glossary_view
from hastings.workspace.locations import default_workspace_root
from hastings.workspace.material_tasks import (
    MaterialTaskScope,
    material_conversation_ids,
    task_list,
    task_view,
)
from hastings.workspace.materials import MAX_REFERENCES, ContextRequest
from hastings.workspace.review import ReviewReport, ReviewStore
from hastings.workspace.service import ProviderFactory, TurnService
from hastings.workspace.state import (
    Conversation,
    ConversationStore,
    ConversationUi,
    ConversationUiStore,
    DraftContext,
    RecentWorkspaces,
    WorkspaceBinding,
    conversation_root,
)
from hastings.workspace.story_materials import (
    DOMAIN_LABEL,
    author_view,
    entry_detail,
)
from hastings.workspace.timeline import TimelineStore, build_timeline, timeline_view

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


class PruneWorkspacesView(BaseModel):
    """清理结果：被移除的登记与清理后仍在册的作品。"""

    removed: list[WorkspaceView]
    workspaces: list[WorkspaceView]


class FileEntryView(BaseModel):
    path: str
    kind: FileKind
    title: str
    #: 服务端生成的显示名：目录、稿纸与模型目录共用，前端不再自行拼章序。
    display_title: str
    order: str
    chapter_number: int | None = None
    chapter_type: Literal["regular", "prologue", "epilogue", "extra"] | None = None
    roles: list[str] = Field(default_factory=list)
    exists: bool
    size: int
    metadata_warnings: list[str] = Field(default_factory=list)


class CatalogView(BaseModel):
    chapters: list[FileEntryView]
    characters: list[FileEntryView]
    settings: list[FileEntryView]


class FileViewResponse(BaseModel):
    path: str
    kind: FileKind
    title: str
    display_title: str
    text: str
    #: 阅读用正文：人物小传去掉开头元信息，其余与 text 相同。
    display_text: str
    digest: str
    size: int
    chapter_number: int | None = None
    chapter_type: Literal["regular", "prologue", "epilogue", "extra"] | None = None
    roles: list[str] = Field(default_factory=list)
    metadata_warnings: list[str] = Field(default_factory=list)


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


class ContextInput(BaseModel):
    """本轮带上来的材料：当前阅读位置、显式参考与客户端看到的版本。

    只接受作品受管路径；``observed_digests`` 是客户端看到的版本，不是服务端内容依据。
    """

    model_config = ConfigDict(extra="forbid")
    selected_path: str | None = None
    reference_paths: list[str] = Field(default_factory=list, max_length=MAX_REFERENCES)
    observed_digests: dict[str, str] = Field(default_factory=dict)
    state_chapter: int | None = Field(default=None, ge=0, strict=True)

    @field_validator("selected_path")
    @classmethod
    def _check_selected(cls, value: str | None) -> str | None:
        if value is not None and not is_managed_path(value):
            raise ValueError("selected_path 必须是作品内的受管路径")
        return value

    @field_validator("reference_paths")
    @classmethod
    def _check_references(cls, value: list[str]) -> list[str]:
        for path in value:
            if not is_managed_path(path):
                raise ValueError(f"参考路径不是作品受管路径：{path}")
        return value

    def to_request(self) -> ContextRequest:
        return ContextRequest(
            state_chapter=self.state_chapter,
            selected_path=self.selected_path,
            reference_paths=tuple(self.reference_paths),
            observed_digests=dict(self.observed_digests),
        )


class TurnPurposeMixin(BaseModel):
    """首次与续聊共用的用途字段：索引任务必须选章节，普通对话不能带。"""

    purpose: Literal[
        "chat", "update_chapter_index", "review", "update_disclosure", "update_state"
    ] = "chat"
    index_paths: list[str] = Field(default_factory=list, max_length=MAX_INDEX_CHAPTERS)
    review_paths: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _check_purpose_targets(self) -> TurnPurposeMixin:
        if self.purpose == "update_chapter_index":
            if not self.index_paths:
                raise ValueError("索引用途需要选择至少一章")
            if self.review_paths:
                raise ValueError("索引用途不能带检查目标")
        elif self.purpose in ("update_disclosure", "update_state"):
            if not self.index_paths:
                raise ValueError("核对状态需要选择至少一份角色小传")
            if self.review_paths:
                raise ValueError("核对身份不能带检查目标")
        elif self.purpose == "review":
            if not self.review_paths:
                raise ValueError("检查用途需要选择至少一个文件")
            if self.index_paths:
                raise ValueError("检查用途不能带索引目标")
        elif self.index_paths or self.review_paths:
            raise ValueError("普通对话不能带索引或检查目标")
        return self


class CreateConversationRequest(TurnPurposeMixin):
    """首次发送即建会话：空提示不再创建空记录。"""

    text: str = Field(min_length=1)
    request_key: str = Field(min_length=1)
    context: ContextInput = Field(default_factory=ContextInput)


class RenameConversationRequest(BaseModel):
    title: str = Field(min_length=1)


class TurnRequest(TurnPurposeMixin):
    text: str = Field(min_length=1)
    request_key: str = Field(min_length=1)
    context: ContextInput = Field(default_factory=ContextInput)


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


class DraftContextView(BaseModel):
    """草稿携带的参考选择：仅选文件、还没写文字时也保留。"""

    selected_path: str | None = None
    reference_paths: list[str] = Field(default_factory=list)


class ConversationUiView(BaseModel):
    recent_id: str | None
    drafts: dict[str, str]
    draft_contexts: dict[str, DraftContextView] = Field(default_factory=dict)
    revision: str


class ConversationUiPatch(BaseModel):
    """只改指定的那一项：不整份覆盖其他会话的草稿。"""

    set_recent: bool = False
    recent_id: str | None = None
    draft_target: str | None = None
    draft_text: str | None = None
    set_draft: bool = False
    set_draft_context: bool = False
    draft_selected_path: str | None = None
    draft_reference_paths: list[str] | None = None


class StopView(BaseModel):
    stopped: bool


class ChapterIndexEvidenceView(BaseModel):
    index: int
    start: int
    end: int
    quote: str


class ChapterIndexEntryView(BaseModel):
    kind: Literal["event", "character", "place", "clue"]
    text: str
    evidence: list[int] = Field(default_factory=list)


class ChapterIndexItemView(BaseModel):
    """一章的索引：状态由当前磁盘算出，内容来自上次成功发布。"""

    source_path: str
    source_digest: str
    recipe_version: int
    generated_at: str
    summary: str
    status: Literal["missing", "valid", "stale", "source_missing", "unavailable"]
    evidence: list[ChapterIndexEvidenceView] = Field(default_factory=list)
    events: list[ChapterIndexEntryView] = Field(default_factory=list)
    characters: list[ChapterIndexEntryView] = Field(default_factory=list)
    places: list[ChapterIndexEntryView] = Field(default_factory=list)
    clues: list[ChapterIndexEntryView] = Field(default_factory=list)


class ChapterIndexView(BaseModel):
    items: list[ChapterIndexItemView]


class DisclosureFactView(BaseModel):
    fact: str
    chapter: int
    source: Literal["text", "declared"] = "declared"


class DisclosureEntityView(BaseModel):
    path: str
    name: str
    surface: str
    aliases: list[str]
    roles: list[str]
    revealed: list[DisclosureFactView]
    upcoming: list[DisclosureFactView]
    planned: list[DisclosureFactView]
    overdue: bool
    record_status: Literal["valid", "stale", "source_missing"] | None = None
    verified: int = 0
    warnings: list[str]


class DisclosureConflictView(BaseModel):
    path: str
    name: str
    kind: str
    label: str
    detail: str


class StateView(BaseModel):
    domain: Literal["disclosure"]
    current_chapter: int
    entities: list[DisclosureEntityView]
    conflicts: list[DisclosureConflictView]


class RosterCharacterView(BaseModel):
    path: str
    name: str
    location: str = ""
    alive: bool = True
    stance: str = ""
    #: 这条状态出自第几章；从没核对过时为 null。
    chapter: int | None = None
    #: 距离当前章差几章；0 或 null 表示不落后。
    lag: int | None = None
    stale: bool = False
    warnings: list[str] = Field(default_factory=list)


class RosterStateView(BaseModel):
    domain: Literal["roster"]
    current_chapter: int
    characters: list[RosterCharacterView]
    warnings: list[str] = Field(default_factory=list)


class TimelineEntryView(BaseModel):
    chapter: int
    when: str
    span: str = ""
    location: str = ""


class TimelineStateView(BaseModel):
    domain: Literal["timeline"]
    current_chapter: int
    entries: list[TimelineEntryView]
    missing: list[int] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class GlossaryTermView(BaseModel):
    term: str
    kind: str
    label: str
    aliases: list[str] = Field(default_factory=list)
    first_chapter: int = 0
    source: Literal["declared", "text"] = "text"


class GlossaryConflictView(BaseModel):
    term: str
    kind: str
    detail: str


class GlossaryStateView(BaseModel):
    domain: Literal["glossary"]
    current_chapter: int
    terms: list[GlossaryTermView]
    conflicts: list[GlossaryConflictView]
    warnings: list[str] = Field(default_factory=list)


class ClueEntryView(BaseModel):
    clue: str
    planted_chapter: int
    due_chapter: int | None = None
    status: Literal["open", "resolved", "dropped"]
    label: str
    #: 计划回收章已过、仍未回收。
    overdue: bool = False
    supports: str = ""
    resolution_chapter: int | None = None
    quote: str = ""


class ClueStateView(BaseModel):
    domain: Literal["clues"]
    current_chapter: int
    entries: list[ClueEntryView]
    #: 超期未回收的线索内容，按台账顺序。
    overdue: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


#: 状态层的几种视图靠 ``domain`` 区分；用一个判别联合而不是一个宽松模型，
#: 免得某个域的字段悄悄串到另一个域。
StateUnion = Annotated[
    StateView | RosterStateView | TimelineStateView | ClueStateView | GlossaryStateView,
    Field(discriminator="domain"),
]


class ReviewEvidenceView(BaseModel):
    path: str
    digest: str
    start: int
    end: int
    quote: str


class ReviewFindingView(BaseModel):
    kind: Literal["continuity", "disclosure", "task_mismatch", "other"]
    certainty: Literal["supported", "suspected"]
    description: str
    suggestion: str
    evidence: list[ReviewEvidenceView] = Field(default_factory=list)


class ReviewReportView(BaseModel):
    """一份检查报告：内容来自发布时，freshness 按当前磁盘算出。"""

    report_id: str
    turn_id: str
    target_paths: list[str]
    target_digests: list[tuple[str, str]]
    requirement: str
    status: Literal["findings", "no_findings", "incomplete"]
    freshness: Literal["valid", "stale", "source_missing"]
    findings: list[ReviewFindingView] = Field(default_factory=list)
    uncovered: list[str] = Field(default_factory=list)
    created_at: str


class ReviewListView(BaseModel):
    items: list[ReviewReportView]


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


class ActivityFilter(BaseModel):
    conversation_id: str = ""
    turn_id: str = ""
    category: Literal["", "task", "model", "tool", "file", "context", "state", "version"] = ""
    status: str = Field(default="", max_length=40)
    q: str = Field(default="", max_length=200)
    since: str = Field(default="", max_length=40)
    until: str = Field(default="", max_length=40)
    verbose: bool = False

    @field_validator("since", "until")
    @classmethod
    def activity_time(cls, value: str) -> str:
        if value and datetime.fromisoformat(value).tzinfo is None:
            raise ValueError("时间必须包含时区")
        return value


class ActivityQuery(ActivityFilter):
    after: str = Field(default="", pattern=r"^([a-f0-9]{32}:\d+)?$")
    before: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=500)


class ActivitySettings(BaseModel):
    capture_inputs: bool = Field(default=False, strict=True)
    retention_days: int = Field(default=30, ge=0, le=3650, strict=True)


class ActivityExport(ActivityFilter):
    include_details: bool = False


MaterialDomainName = Literal[
    "chapter_index", "disclosure", "roster", "timeline", "clues", "glossary"
]


class SourceEvidenceView(BaseModel):
    """一处原文依据：路径、版本、章节、区间、引文与当前有效性。"""

    path: str
    digest: str
    chapter: int | None = None
    start: int
    end: int
    quote: str
    freshness: Literal["valid", "stale", "source_missing", "unavailable", "none"] = "valid"


class MaterialEntryView(BaseModel):
    """作者视图的一条资料；``data`` 按域区分，前端用域类型收窄。"""

    id: str
    domain: MaterialDomainName
    title: str
    subtitle: str = ""
    chapter: int | None = None
    entity_path: str | None = None
    #: 整理覆盖：有结果、无发现、信息不足或还没整理。
    coverage: Literal["found", "empty", "insufficient", "unrecorded"] = "unrecorded"
    #: 版本有效性：与整理覆盖分开表达。
    freshness: Literal["valid", "stale", "source_missing", "unavailable", "none"] = "none"
    task_id: str = ""
    updated_at: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: list[SourceEvidenceView] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class MaterialChapterView(BaseModel):
    path: str
    chapter: int
    title: str
    digest: str


class MaterialEntityView(BaseModel):
    path: str
    name: str


class MaterialCoverageView(BaseModel):
    total: int = 0
    found: int = 0
    empty: int = 0
    insufficient: int = 0
    unrecorded: int = 0
    stale: int = 0


class StoryMaterialsView(BaseModel):
    domain: MaterialDomainName
    label: str
    as_of_chapter: int
    current_chapter: int
    chapters: list[MaterialChapterView]
    entities: list[MaterialEntityView]
    entries: list[MaterialEntryView]
    total: int
    next_cursor: str = ""
    coverage: MaterialCoverageView
    warnings: list[str] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)


class MaterialEntryDetailView(BaseModel):
    domain: MaterialDomainName
    as_of_chapter: int
    entry: MaterialEntryView
    coverage: MaterialCoverageView
    warnings: list[str] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)


class MaterialScopeInput(BaseModel):
    """资料页提交的范围：结构校验在模型里，真实路径与指纹在受理时复核。"""

    model_config = ConfigDict(extra="forbid")
    domain: MaterialDomainName
    chapter_paths: list[str] = Field(min_length=1, max_length=10)
    entity_paths: list[str] = Field(default_factory=list, max_length=10)
    as_of_chapter: int = Field(ge=0)
    observed_digests: dict[str, str] = Field(default_factory=dict)

    def to_model(self) -> MaterialTaskScope:
        return MaterialTaskScope(**self.model_dump())


class MaterialTaskRequest(BaseModel):
    request_key: str = Field(min_length=1)
    scope: MaterialScopeInput


class MaterialTaskAcceptedView(BaseModel):
    """受理结果：HTTP 202 只表示任务已开始，不代表整理完成。"""

    turn_id: str
    conversation_id: str
    origin: Literal["materials"] = "materials"
    domain: MaterialDomainName
    label: str
    title: str
    model: str = ""
    request_key: str
    scope: dict[str, Any]


class MaterialTargetView(BaseModel):
    path: str
    chapter: int
    entity_path: str = ""
    outcome: Literal["found", "empty", "insufficient", "unrecorded"] = "unrecorded"
    freshness: str = "none"


class MaterialTaskView(BaseModel):
    turn_id: str
    conversation_id: str
    domain: MaterialDomainName
    label: str
    scope: dict[str, Any]
    status: str
    model: str = ""
    started_at: str = ""
    published: int = 0
    done: int = 0
    total: int = 0
    targets: list[MaterialTargetView] = Field(default_factory=list)
    unfinished: list[MaterialTargetView] = Field(default_factory=list)


class MaterialTaskListView(BaseModel):
    items: list[MaterialTaskView]


def _material_prompt(scope: MaterialTaskScope) -> str:
    """任务提示由冻结范围生成：作者在活动记录里看到的就是这次整理的对象。"""
    label = DOMAIN_LABEL.get(scope.domain, "资料")
    numbers = sorted(int(path[:4]) if path[:4].isdigit() else 0 for path in scope.chapter_paths)
    if numbers:
        span = f"第 {numbers[0]} 章" if len(numbers) == 1 else f"第 {numbers[0]}—{numbers[-1]} 章"
    else:
        span = "所选章节"
    names = "、".join(path.rsplit("/", 1)[-1].removesuffix(".md") for path in scope.entity_paths)
    return f"整理{label}：{span}" + (f"（{names}）" if names else "")


def create_file_workspace_router(
    workspaces: RecentWorkspaces, provider_factory: ProviderFactory
) -> APIRouter:
    """绑定最近目录登记与模型来源；每个作品一个轮次服务，按需创建。"""
    router = APIRouter(prefix=PREFIX, tags=["file-workspaces"])
    services: dict[str, TurnService] = {}
    ui_stores: dict[str, ConversationUiStore] = {}
    activity_stores: dict[str, ActivityStore] = {}
    activity_lock = threading.RLock()

    def activity_for(workspace_id: str) -> ActivityStore:
        workspaces.get(workspace_id)
        with activity_lock:
            if workspace_id not in activity_stores:
                store = ActivityStore(conversation_root(workspaces.root, workspace_id))
                store.recover()
                activity_stores[workspace_id] = store
            return activity_stores[workspace_id]

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
        service.activity = activity_for(workspace_id)
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

    @router.delete("/stale", response_model=PruneWorkspacesView)
    def prune_stale_workspaces() -> PruneWorkspacesView:
        """清掉目录已不存在的登记；作品目录本身不受影响。"""
        removed = workspaces.prune_missing()
        return PruneWorkspacesView(
            removed=[view(binding) for binding in removed],
            workspaces=[view(binding) for binding in workspaces.recent()],
        )

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
        entry = workspace.describe(path)
        return FileViewResponse(
            path=view_file.path,
            kind=entry.kind,
            title=entry.title,
            display_title=entry.display_title,
            text=view_file.text,
            display_text=view_file.display_text,
            digest=view_file.digest,
            size=view_file.size,
            chapter_number=entry.chapter_number,
            chapter_type=entry.chapter_type,
            roles=list(entry.roles),
            metadata_warnings=list(entry.metadata_warnings),
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
        with activity_for(workspace_id).operation("导入正文", filename=file.filename) as operation:
            imported = workspace.import_text(file.filename or "导入正文.md", data)
            operation.result = {
                "files": [
                    {"path": item.path, "digest": item.digest, "size": item.size}
                    for item in imported
                ]
            }
        catalog = workspace.catalog()
        return CatalogView(
            chapters=[_entry(entry) for entry in catalog.chapters],
            characters=[_entry(entry) for entry in catalog.characters],
            settings=[_entry(entry) for entry in catalog.settings],
        )

    @router.get("/{workspace_id}/chapter-index", response_model=ChapterIndexView)
    def chapter_index(
        workspace_id: str, path: str | None = Query(default=None, min_length=1)
    ) -> ChapterIndexView:
        """已发布的索引与当前状态；没有记录的章节由界面按目录标为「未生成」。"""
        workspace = workspaces.workspace(workspace_id)
        store = ChapterIndexStore(conversation_root(workspaces.root, workspace_id))
        records = store.records(workspace)
        if path is not None:
            records = tuple(record for record in records if record.source_path == path)
        return ChapterIndexView(items=[_index_item(record) for record in records])

    @router.get("/{workspace_id}/state", response_model=StateUnion)
    def workspace_state(
        workspace_id: str,
        domain: Literal["disclosure", "roster", "timeline", "clues", "glossary"] = Query(
            default="disclosure"
        ),
    ) -> StateUnion:
        """状态层按域返回：身份披露、人物状态、故事时间、线索台账、专有名词。

        每个域分开返回，界面上也分栏显示：作者要能看出「这条是声明」还是「这条是原文核对
        到的」，混成一份会让计划和事实互相冒充。破坏性冲突单列，不替作者裁决。
        """
        workspace = workspaces.workspace(workspace_id)
        root = conversation_root(workspaces.root, workspace_id)
        if domain == "roster":
            roster = roster_view(build_roster(workspace, CharacterStateStore(root)))
            return RosterStateView(**roster)  # type: ignore[arg-type]
        if domain == "timeline":
            timeline = timeline_view(build_timeline(workspace, TimelineStore(root)))
            return TimelineStateView(**timeline)  # type: ignore[arg-type]
        if domain == "clues":
            clues = clues_view(build_clues(workspace, ClueStore(root)))
            return ClueStateView(**clues)  # type: ignore[arg-type]
        if domain == "glossary":
            terms = glossary_view(build_glossary(workspace, GlossaryStore(root)))
            return GlossaryStateView(**terms)  # type: ignore[arg-type]
        state: DisclosureStateRecord = build_disclosure_state(workspace, DisclosureStore(root))
        return StateView(**state_view(state))  # type: ignore[arg-type]

    @router.get("/{workspace_id}/reviews", response_model=ReviewListView)
    def list_reviews(
        workspace_id: str, path: str | None = Query(default=None, min_length=1)
    ) -> ReviewListView:
        """历史检查报告；过期或源文件已删除的报告只标记状态，不重跑模型。"""
        workspace = workspaces.workspace(workspace_id)
        store = ReviewStore(conversation_root(workspaces.root, workspace_id))
        return ReviewListView(
            items=[_review_item(report) for report in store.reports(workspace, path=path)]
        )

    # -- 故事资料：作者视图与整理任务 ---------------------------------------

    @router.get("/{workspace_id}/story-materials/{domain}", response_model=StoryMaterialsView)
    def story_materials(
        workspace_id: str,
        domain: MaterialDomainName,
        as_of_chapter: int | None = Query(default=None, ge=0),
        q: str = Query(default="", max_length=200),
        limit: int = Query(default=100, ge=1, le=200),
        cursor: str = Query(default="", max_length=32),
    ) -> StoryMaterialsView:
        """作者视图：条目、证据、覆盖统计与实际采用的时点。

        截止章只影响浏览；服务端按真实文件重算章号，不接受浏览器传来的任意路径。
        """
        workspace = workspaces.workspace(workspace_id)
        root = conversation_root(workspaces.root, workspace_id)
        view = author_view(
            workspace,
            root,
            domain,
            as_of_chapter=as_of_chapter,
            query=q,
            limit=limit,
            cursor=cursor,
        )
        return StoryMaterialsView(**view)  # type: ignore[arg-type]

    @router.get(
        "/{workspace_id}/story-materials/{domain}/entries/{entry_id}",
        response_model=MaterialEntryDetailView,
    )
    def story_material_entry(
        workspace_id: str,
        domain: MaterialDomainName,
        entry_id: str,
        as_of_chapter: int | None = Query(default=None, ge=0),
    ) -> MaterialEntryDetailView:
        """按稳定 ID 取完整条目；条目不存在时 404，不返回空对象冒充。"""
        workspace = workspaces.workspace(workspace_id)
        root = conversation_root(workspaces.root, workspace_id)
        view = entry_detail(workspace, root, domain, entry_id, as_of_chapter=as_of_chapter)
        return MaterialEntryDetailView(**view)  # type: ignore[arg-type]

    @router.post(
        "/{workspace_id}/material-tasks",
        response_model=MaterialTaskAcceptedView,
        status_code=202,
    )
    async def create_material_task(
        workspace_id: str, body: MaterialTaskRequest
    ) -> MaterialTaskAcceptedView:
        """受理一次整理：一个任务只更新一个域，返回的是受理结果而不是完成结果。"""
        service = service_for(workspace_id)
        scope = body.scope.to_model()
        info = await service.accept(
            _material_prompt(scope),
            body.request_key,
            purpose="organize_materials",
            material_scope=scope,
        )
        frozen = info.material_scope or {}
        active = service.active_turn()
        return MaterialTaskAcceptedView(
            turn_id=info.turn_id,
            conversation_id=info.conversation_id,
            domain=scope.domain,
            label=DOMAIN_LABEL[scope.domain],
            title=info.title,
            model=active.model if active is not None else "",
            request_key=body.request_key,
            scope={
                "domain": frozen.get("domain"),
                "chapter_paths": frozen.get("chapter_paths", []),
                "entity_paths": frozen.get("entity_paths", []),
                "as_of_chapter": frozen.get("as_of_chapter"),
            },
        )

    @router.get("/{workspace_id}/material-tasks", response_model=MaterialTaskListView)
    def list_material_tasks(
        workspace_id: str,
        domain: Annotated[MaterialDomainName | None, Query()] = None,
        limit: int = Query(default=20, ge=1, le=100),
    ) -> MaterialTaskListView:
        """本域最近的资料任务；仍在运行的任务也列出来，刷新后能接上。"""
        service = service_for(workspace_id)
        active = service.active_turn()
        items = task_list(
            service.store,
            conversation_root(workspaces.root, workspace_id),
            domain=domain or "",
            limit=limit,
            active_turn_id=active.turn_id if active is not None else "",
        )
        return MaterialTaskListView(items=items)  # type: ignore[arg-type]

    @router.get("/{workspace_id}/material-tasks/{turn_id}", response_model=MaterialTaskView)
    def material_task_detail(workspace_id: str, turn_id: str) -> MaterialTaskView:
        """单个任务的规范化结果：目标、已发布、未完成项与覆盖结果。"""
        service = service_for(workspace_id)
        active = service.active_turn()
        view = task_view(
            service.store,
            conversation_root(workspaces.root, workspace_id),
            service.workspace,
            turn_id,
            active_turn_id=active.turn_id if active is not None else "",
        )
        return MaterialTaskView(**view)  # type: ignore[arg-type]

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
        with activity_for(workspace_id).operation("启用版本记录"):
            repo.init()
        return _git_status(repo)

    @router.post("/{workspace_id}/git/record", response_model=RecordedView)
    def git_record(workspace_id: str, body: RecordRequest | None = None) -> RecordedView:
        """单独补记未记录的改动：只执行 Git，不调用模型。"""
        message = (body.message.strip() if body else "") or "补记：应用之外的改动"
        with activity_for(workspace_id).operation("补记版本", summary=message) as operation:
            commit = GitRepo(workspaces.workspace(workspace_id).root).record(message)
            operation.result = {"commit": commit}
        return RecordedView(commit=commit)

    @router.post("/{workspace_id}/git/revert", response_model=RecordedView)
    def git_revert(workspace_id: str, body: RevertRequest) -> RecordedView:
        """撤销指定提交（必须是 HEAD）；生成反向提交，不改写历史。"""
        with activity_for(workspace_id).operation(
            "撤销版本", target_commit=body.commit
        ) as operation:
            commit = GitRepo(workspaces.workspace(workspace_id).root).revert_latest(body.commit)
            operation.result = {"commit": commit}
        return RecordedView(commit=commit or None)

    # -- 作品活动：与聊天 SSE 分开，持续订阅跨越任务边界 ------------------------

    @router.get("/{workspace_id}/activity")
    def list_activity(
        workspace_id: str, query: Annotated[ActivityQuery, Query()]
    ) -> dict[str, Any]:
        return activity_for(workspace_id).query(**query.model_dump())

    @router.get("/{workspace_id}/activity/settings")
    def activity_settings(workspace_id: str) -> dict[str, Any]:
        return activity_for(workspace_id).settings()

    @router.put("/{workspace_id}/activity/settings")
    def set_activity_settings(workspace_id: str, body: ActivitySettings) -> dict[str, Any]:
        store = activity_for(workspace_id)
        with store.operation("设置活动详情保留", **body.model_dump()):
            return store.configure(**body.model_dump())

    @router.delete("/{workspace_id}/activity/details")
    def clear_activity_details(workspace_id: str) -> dict[str, Any]:
        store = activity_for(workspace_id)
        with store.operation("清理活动详情") as operation:
            removed = store.clear_details()
            operation.result = {"removed": removed}
        return {"removed": removed}

    @router.post("/{workspace_id}/activity/rebuild")
    def rebuild_activity(workspace_id: str) -> dict[str, bool]:
        store = activity_for(workspace_id)
        with store.operation("重建活动索引"):
            store.rebuild()
        return {"rebuilt": True}

    @router.get("/{workspace_id}/activity/attachments/{key}")
    def activity_attachment(workspace_id: str, key: str) -> object:
        return activity_for(workspace_id).detail_attachment(key)

    @router.get("/{workspace_id}/activity/export")
    def export_activity(
        workspace_id: str, query: Annotated[ActivityExport, Query()]
    ) -> StreamingResponse:
        return StreamingResponse(
            activity_for(workspace_id).export(**query.model_dump()),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": 'attachment; filename="activity.jsonl"'},
        )

    @router.get("/{workspace_id}/activity/stream")
    async def stream_activity(
        workspace_id: str, request: Request, query: Annotated[ActivityQuery, Query()]
    ) -> StreamingResponse:
        store = activity_for(workspace_id)

        async def stream() -> AsyncIterator[str]:
            filters = query.model_dump()
            # 首次连接同时送出快照，之后按作品索引游标续传。
            while not await request.is_disconnected():
                page = await asyncio.to_thread(store.query, **filters)
                yield f"id: {page['cursor']}\ndata: {json.dumps(page, ensure_ascii=False)}\n\n"
                filters["after"] = page["cursor"]
                filters["before"] = 0
                if not page["has_more"]:
                    await asyncio.sleep(1)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/{workspace_id}/activity/{event_id}")
    def activity_detail(workspace_id: str, event_id: str) -> dict[str, Any]:
        return activity_for(workspace_id).detail(event_id)

    # -- 对话与轮次 ---------------------------------------------------------

    @router.get("/{workspace_id}/conversations", response_model=ConversationListView)
    def list_conversations(workspace_id: str) -> ConversationListView:
        service = service_for(workspace_id)
        # 资料任务的专用会话不在写作会话列表里出现；它们从资料页与活动记录回看。
        hidden = material_conversation_ids(service.store)
        items = sorted(
            (item for item in service.store.conversations() if item.id not in hidden),
            key=lambda item: item.updated_at,
            reverse=True,
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
        info = await service.accept(
            body.text,
            body.request_key,
            context=body.context.to_request(),
            purpose=body.purpose,
            index_paths=tuple(body.index_paths),
            review_paths=tuple(body.review_paths),
        )
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
        info = await service.accept(
            body.text,
            body.request_key,
            conversation_id=conversation_id,
            context=body.context.to_request(),
            purpose=body.purpose,
            index_paths=tuple(body.index_paths),
            review_paths=tuple(body.review_paths),
        )
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
        return _ui_view(ui_for(workspace_id).read())

    @router.patch("/{workspace_id}/conversation-ui", response_model=ConversationUiView)
    def update_conversation_ui(
        workspace_id: str,
        body: ConversationUiPatch,
        if_match: Annotated[str, Header(alias="If-Match")] = "",
    ) -> ConversationUiView:
        """按目标合并界面状态：只动指定的那一条草稿或最近会话。"""
        store = ui_for(workspace_id)
        if (body.set_draft or body.set_draft_context) and not (body.draft_target or "").strip():
            raise WorkspaceError(
                code="CONVERSATION_UI_TARGET_INVALID",
                where="conversation-ui.update",
                why="没有指定草稿属于哪个会话",
                fix="带上会话 ID，或对新对话使用固定槽位 new",
            )
        if body.set_recent and body.recent_id:
            service_for(workspace_id).store.get(body.recent_id)
        draft_context: DraftContext | None = None
        if body.set_draft_context:
            for path in body.draft_reference_paths or []:
                if not is_managed_path(path):
                    raise WorkspaceError(
                        code="CONVERSATION_UI_TARGET_INVALID",
                        where="conversation-ui.update",
                        why=f"草稿参考不是作品受管路径：{path}",
                        fix="只保存作品内的章节、小传或固定设定路径",
                        details={"path": path},
                    )
            draft_context = DraftContext(
                selected_path=body.draft_selected_path,
                reference_paths=tuple(body.draft_reference_paths or ()),
            )
        ui = store.update(
            expected_revision=if_match,
            set_recent=body.set_recent,
            recent_id=body.recent_id,
            set_draft=body.set_draft,
            draft_target=body.draft_target,
            draft_text=body.draft_text,
            set_draft_context=body.set_draft_context,
            draft_context=draft_context,
        )
        return _ui_view(ui)

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
            # 追尾读取：只有这一次按游标定位，之后每次只读新增的完整行。
            # 流式增量的条数比整轮事件多一个量级，每次从头扫整份日志会把长回复拖成平方级。
            tail = service.store.tail(conversation_id, after=after)
            while True:
                batch = tail.read()
                for record in batch:
                    payload = json.dumps(record, ensure_ascii=False)
                    yield f"id: {record['seq']}\ndata: {payload}\n\n"
                    if record.get("type") == "run_finished":
                        return
                if not batch and not follow:
                    return
                await asyncio.sleep(0.1)

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


def _index_item(record: ChapterIndexRecord) -> ChapterIndexItemView:
    return ChapterIndexItemView(
        source_path=record.source_path,
        source_digest=record.source_digest,
        recipe_version=record.recipe_version,
        generated_at=record.generated_at,
        summary=record.summary,
        status=record.status,
        evidence=[
            ChapterIndexEvidenceView(
                index=item.index, start=item.start, end=item.end, quote=item.quote
            )
            for item in record.evidence
        ],
        events=[_index_entry(item) for item in record.events],
        characters=[_index_entry(item) for item in record.characters],
        places=[_index_entry(item) for item in record.places],
        clues=[_index_entry(item) for item in record.clues],
    )


def _index_entry(entry: ChapterIndexEntry) -> ChapterIndexEntryView:
    return ChapterIndexEntryView(kind=entry.kind, text=entry.text, evidence=list(entry.evidence))


def _review_item(report: ReviewReport) -> ReviewReportView:
    return ReviewReportView(
        report_id=report.report_id,
        turn_id=report.turn_id,
        target_paths=list(report.target_paths),
        target_digests=list(report.target_digests),
        requirement=report.requirement,
        status=report.status,
        freshness=report.freshness,
        findings=[
            ReviewFindingView(
                kind=finding.kind,
                certainty=finding.certainty,
                description=finding.description,
                suggestion=finding.suggestion,
                evidence=[
                    ReviewEvidenceView(
                        path=item.path,
                        digest=item.digest,
                        start=item.start,
                        end=item.end,
                        quote=item.quote,
                    )
                    for item in finding.evidence
                ],
            )
            for finding in report.findings
        ],
        uncovered=list(report.uncovered),
        created_at=report.created_at,
    )


def _ui_view(ui: ConversationUi) -> ConversationUiView:
    return ConversationUiView(
        recent_id=ui.recent_id,
        drafts=dict(ui.drafts),
        draft_contexts={
            key: DraftContextView(
                selected_path=value.selected_path,
                reference_paths=list(value.reference_paths),
            )
            for key, value in ui.draft_contexts.items()
        },
        revision=ui.revision,
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
        display_title=entry.display_title,
        order=entry.order,
        chapter_number=entry.chapter_number,
        chapter_type=entry.chapter_type,
        roles=list(entry.roles),
        exists=entry.exists,
        size=entry.size,
        metadata_warnings=list(entry.metadata_warnings),
    )
