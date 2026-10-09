"""REST 面（P4 T4 slug 化，P5 T2 受管写端点，计划 D3）。

红线措辞演进（D3）：写路径必须经守门管道——agent 走工具层，作者走
受管写端点 PUT chapters/{id}（归档 + 运行态检查 + 乐观并发，与工具层
共享同一套不变量）。POST /api/workspaces 只是资源创建（建目录）。
泄压阀三情形在 HTTP 层的落点：
- 参数违规（坏 slug、未知账本类型、坏章节 id、坏标题）→ 422/404 + 可读指引；
- 合法但空（空账本、无章节、无工作区、空 diff no-op）→ 200，不是错误；
- 撞名新建 / 过期提交 / 写作流冲突（409）、数据故障（YAML 坏 → 422）；
  环境故障（IO）→ 500 带归因，不与数据故障混谈。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Final, Literal

import httpx
import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError

from hastings.bible.models import Clue, Foreshadow, Method, TimelineEntry
from hastings.bible.stats import compute_story_stats
from hastings.bible.store import (
    InvalidEntityNameError,
    LedgerFormatError,
    StoryWorkspace,
    list_characters,
    load_ledger,
)
from hastings.events_store import CATEGORIES, EventStore, events_file
from hastings.manuscript.diff import InvalidChapterFormat, validate_title_heading
from hastings.manuscript.store import current_revision, save_chapter
from hastings.server.provider_config import ProviderBook, env_fallback_config, mask_key
from hastings.server.registry import WorkspaceExistsError, WorkspaceRegistry
from hastings.server.session import WritingSession
from hastings.settings import (
    DEFAULT_PROVIDER_PROFILE,
    WRITING_LANGUAGES,
    SettingsFormatError,
    StorySettings,
    load_settings,
    save_settings,
)
from hastings.tools.bible_tools import ENTITY_LABELS
from tau_agent.types import JSONValue

BibleType = Literal["character", "clue", "foreshadow", "method", "timeline"]

# 章节文件名（去 .md）即 id：只允许字母数字、连字符与下划线，杜绝路径穿越。
_CHAPTER_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}"

# 模型发现请求超时（P6 D3）：够慢端点响应，又不至于挂死页面。
_MODEL_DISCOVERY_TIMEOUT_SECONDS: Final[float] = 10.0

# 事件流分页单页上限（P6 D5）：防止一次拉满历史把浏览器拖垮。
_EVENT_LIMIT_MAX: Final[int] = 1000


class ModelDiscoveryError(Exception):
    """模型发现失败（P6 D3）：携带拟采用的 HTTP 状态码与三要素文案。"""

    def __init__(self, *, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def discover_models(
    base_url: str,
    api_key: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[str]:
    """直连 OpenAI 兼容 ``{base_url}/models`` 拉候选模型（P6 D3）。

    该端点兼任「测试连接」：通 = key/网络/base_url 全链路可用。
    泄压阀三情形：401/403 → key 无效（422，配置可修正）；网络/上游故障 → 502
    环境故障归因；成功（含空列表）→ 返回列表（空列表指引手动输入）。
    """
    url = f"{base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with httpx.AsyncClient(
            timeout=_MODEL_DISCOVERY_TIMEOUT_SECONDS, transport=transport
        ) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise ModelDiscoveryError(
            status_code=502,
            detail=(
                f"拉取模型列表失败（{url}）。"
                f"为什么错：环境故障——{exc}。"
                "怎样算对：确认网络连通且 base_url 可达后重试；"
                "也可以不拉列表，直接在设置页手动输入模型名。"
            ),
        ) from exc

    if response.status_code in (401, 403):
        raise ModelDiscoveryError(
            status_code=422,
            detail=(
                f"拉取模型列表被拒绝（HTTP {response.status_code}）。"
                "为什么错：API key 无效或无权访问该端点。"
                "怎样算对：检查 config.yaml 中该档案的 api_key / api_key_env 后重试。"
            ),
        )
    if response.status_code >= 400:
        raise ModelDiscoveryError(
            status_code=502,
            detail=(
                f"拉取模型列表失败（HTTP {response.status_code}）。"
                f"为什么错：上游端点返回了 {response.status_code}。"
                "怎样算对：确认 base_url 指向 OpenAI 兼容端点（通常以 /v1 结尾）后重试。"
            ),
        )
    try:
        payload: object = response.json()
    except ValueError as exc:
        raise ModelDiscoveryError(
            status_code=502,
            detail=(
                f"拉取模型列表失败（{url}）。"
                "为什么错：响应不是合法 JSON，端点可能不是 OpenAI 兼容接口。"
                "怎样算对：检查 base_url，或改用手动输入模型名。"
            ),
        ) from exc

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []  # 合法但空结果：不报错，指引手动输入
    return [
        item["id"] for item in data if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]


def _ledger_reader[M: (Clue, Foreshadow, Method, TimelineEntry)](
    path_getter: Callable[[StoryWorkspace], Path], model_cls: type[M]
) -> Callable[[StoryWorkspace], list[BaseModel]]:
    """在构造点绑定具体模型，绕开 load_ledger 的约束型类型参数。"""

    def read(ws: StoryWorkspace) -> list[BaseModel]:
        return list(load_ledger(path_getter(ws), model_cls))

    return read


# 单文件账本注册表：entity_type → 读取器 / 路径（422 指引用）。角色一人一
# 文件，单独走 list_characters；其余四类共用 load_ledger。
_LEDGER_READERS: Final[dict[str, Callable[[StoryWorkspace], list[BaseModel]]]] = {
    "clue": _ledger_reader(lambda ws: ws.clues_path, Clue),
    "foreshadow": _ledger_reader(lambda ws: ws.foreshadows_path, Foreshadow),
    "method": _ledger_reader(lambda ws: ws.method_path, Method),
    "timeline": _ledger_reader(lambda ws: ws.timeline_path, TimelineEntry),
}

_LEDGER_PATHS: Final[dict[str, Callable[[StoryWorkspace], Path]]] = {
    "clue": lambda ws: ws.clues_path,
    "foreshadow": lambda ws: ws.foreshadows_path,
    "method": lambda ws: ws.method_path,
    "timeline": lambda ws: ws.timeline_path,
}


class StateResponse(BaseModel):
    slug: str
    model: str
    is_running: bool
    tools: list[str]
    bible_types: list[str]


class BibleResponse(BaseModel):
    entity_type: str
    count: int
    entities: list[dict[str, JSONValue]]


class ChaptersResponse(BaseModel):
    chapters: list[str]


class ChapterResponse(BaseModel):
    id: str
    content: str
    revision: int


class ChapterUpdateRequest(BaseModel):
    # D3：请求体为 {content, baseRevision}——validation alias 收 camelCase
    content: str
    base_revision: int = Field(validation_alias="baseRevision")
    # 对账接线（D5）：T4 消费；T2 只接收（sync=false 只存不同步）
    sync: bool = True


class DiffHunkResponse(BaseModel):
    # D3 契约：hunk 字段经别名在 wire 上是 camelCase（与 WS 帧同形，
    # 前端 diff 视图一套类型消费）
    kind: str
    old_index: int | None = Field(serialization_alias="oldIndex")
    new_index: int | None = Field(serialization_alias="newIndex")
    old_text: str = Field(serialization_alias="oldText")
    new_text: str = Field(serialization_alias="newText")


class ChapterSavedResponse(BaseModel):
    id: str
    revision: int
    diff: list[DiffHunkResponse]


class EditMarkResponse(BaseModel):
    # D4：作者草稿锁加锁结果（wire 上 camelCase，与章节端点同形）
    chapter_id: str = Field(serialization_alias="chapterId")
    holder: str
    ts: float


class EditMarkReleaseResponse(BaseModel):
    chapter_id: str = Field(serialization_alias="chapterId")
    released: bool


class WorkspacesResponse(BaseModel):
    workspaces: list[str]


class WorkspaceCreateRequest(BaseModel):
    slug: str


class WorkspaceCreatedResponse(BaseModel):
    slug: str


class SettingsResponse(BaseModel):
    # REST 沿用 model_dump() 无别名口径 → wire 上是 snake_case
    writing_language: str
    provider_profile: str
    model: str


class SettingsUpdateRequest(BaseModel):
    """设置局部更新（P6 T2）：未提供的字段保持当前值。"""

    writing_language: str
    provider_profile: str | None = None
    model: str | None = None


class ProviderSummary(BaseModel):
    """档案目录条目（P6 D1）：key 只进不出，仅给尾 4 位提示。"""

    name: str
    base_url: str
    default_model: str
    key_hint: str
    available: bool


class ProvidersResponse(BaseModel):
    providers: list[ProviderSummary]


class ModelsResponse(BaseModel):
    """模型发现结果（P6 D3）；空列表 = 合法空结果（前端指引手动输入）。"""

    models: list[str]


class EventRecordResponse(BaseModel):
    """一条持久化事件（P6 D4）：ts/seq/type 为索引列，data 为事件帧原文。"""

    ts: str
    seq: int
    type: str
    data: dict[str, JSONValue]


class EventsResponse(BaseModel):
    """事件流分页结果（P6 D5）：倒序（新→旧），has_more 指示可继续向上翻页。

    ``total``/``counts`` 是整份账本的统计（不受分页/过滤影响），喂运行概览
    与分类 Tab 徽标。
    """

    events: list[EventRecordResponse]
    has_more: bool
    total: int
    counts: dict[str, int]


class ForeshadowOverdueResponse(BaseModel):
    """逾期伏笔条目（P6 D7）：名字 + 埋设/预计回收两章锚点。"""

    name: str
    plant_chapter: str
    expected_resolve_chapter: str


class StatsResponse(BaseModel):
    """全工作区统计（P6 T7，D7）：纯派生聚合，前端统计卡数据源。

    风险派生（逾期/临近/正常）由 stats.py 后端算出、前端只展示——
    T8 伏笔表格的风险列直接复用同一套纯函数，不引入第二份数据源。
    """

    chapters: int
    total_chars: int
    latest_chapter: int
    foreshadow_counts: dict[str, int]
    foreshadow_total: int
    foreshadow_resolved: int
    foreshadow_recovery_rate: float
    overdue_foreshadows: list[ForeshadowOverdueResponse]
    clue_counts: dict[str, int]
    clue_total: int
    clue_red_herring: int
    clue_red_herring_ratio: float


def _read_ledger(workspace: StoryWorkspace, entity_type: BibleType) -> list[BaseModel]:
    """复用 store 层 loader 读一类账本；数据故障 422、IO 故障 500。"""
    try:
        if entity_type == "character":
            return list(list_characters(workspace))
        return _LEDGER_READERS[entity_type](workspace)
    except (ValidationError, LedgerFormatError, yaml.YAMLError) as exc:
        target = (
            workspace.characters_dir
            if entity_type == "character"
            else _LEDGER_PATHS[entity_type](workspace)
        )
        raise HTTPException(
            status_code=422,
            detail=(
                f"Story Bible 账本读取失败（{entity_type}）。"
                f"为什么错：{exc}。"
                "怎样算对：修复或删除损坏的 YAML 文件后重试"
                f"（本账本对应 {target}）。"
            ),
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Story Bible 账本读取失败（{entity_type}）：文件系统错误 {exc}。",
        ) from exc


def create_rest_router(
    *,
    registry: WorkspaceRegistry,
    book: ProviderBook | None = None,
    model_transport: httpx.AsyncBaseTransport | None = None,
) -> APIRouter:
    """装配 REST 路由（P6 T2：``book`` 提供档案目录与跨档案重建工厂）。

    ``book`` 缺省时按 env 回落单档案新建（测试 / 纯 API 模式；与启动装配同源）。
    ``model_transport`` 仅供测试注入 MockTransport，生产为 None（走真实网络）。
    """
    router = APIRouter()
    fallback_book: ProviderBook | None = None

    def _book() -> ProviderBook:
        nonlocal fallback_book
        if book is not None:
            return book
        if fallback_book is None:
            fallback_book = ProviderBook(
                env_fallback_config(),
                boot_name=DEFAULT_PROVIDER_PROFILE,
                boot_model="",
            )
        return fallback_book

    def _workspace(slug: str) -> StoryWorkspace:
        # get_or_create 已过校验；这里只做路径解析（构造即校验，双保险）
        return StoryWorkspace(root=registry.root, slug=slug)

    async def _session(slug: str) -> WritingSession:
        try:
            return await registry.get_or_create(slug)
        except InvalidEntityNameError as exc:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"slug 不合法：{slug!r}。"
                    f"为什么错：{exc}。"
                    "怎样算对：slug 只允许字母、数字、中文、空格或连字符"
                    "（如 orient-express）；先 GET /api/workspaces 查看可用工作区。"
                ),
            ) from exc

    @router.get("/api/workspaces", response_model=WorkspacesResponse)
    async def workspaces() -> WorkspacesResponse:
        return WorkspacesResponse(workspaces=registry.list_slugs())

    @router.get("/api/providers", response_model=ProvidersResponse)
    async def providers() -> ProvidersResponse:
        """档案目录（P6 D1）：未注入 book 时按 env 回落单档案计算（与启动装配同源）。"""
        return ProvidersResponse(
            providers=[
                ProviderSummary(
                    name=profile.name,
                    base_url=profile.base_url,
                    default_model=profile.default_model,
                    key_hint=mask_key(profile.resolved_api_key()),
                    available=profile.available,
                )
                for profile in _book().config.providers
            ]
        )

    @router.get("/api/providers/{name}/models", response_model=ModelsResponse)
    async def provider_models(name: str) -> ModelsResponse:
        """拉取档案的候选模型（P6 D3）；兼作连通测试。"""
        profile = _book().profile(name)
        if profile is None:
            available = "、".join(p.name for p in _book().config.providers) or "（当前无档案）"
            raise HTTPException(
                status_code=404,
                detail=(
                    f"provider 档案不存在：{name!r}。"
                    "为什么错：档案名不在服务级 config.yaml 的 providers 列表中。"
                    f"怎样算对：从 {available} 中选择一个档案名。"
                ),
            )
        if not profile.available:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"档案 {name!r} 缺少 API key。"
                    "为什么错：api_key 与 api_key_env 都为空，或引用的环境变量未设置。"
                    "怎样算对：在 config.yaml 对应档案直填 api_key，"
                    "或用 api_key_env 引用已设置的环境变量。"
                ),
            )
        try:
            models = await discover_models(
                profile.base_url,
                profile.resolved_api_key(),
                transport=model_transport,
            )
        except ModelDiscoveryError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return ModelsResponse(models=models)

    @router.post(
        "/api/workspaces",
        response_model=WorkspaceCreatedResponse,
        status_code=201,
    )
    async def create_workspace(body: WorkspaceCreateRequest) -> WorkspaceCreatedResponse:
        try:
            registry.create_workspace(body.slug)
        except InvalidEntityNameError as exc:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"slug 不合法：{body.slug!r}。"
                    f"为什么错：{exc}。"
                    "怎样算对：slug 只允许字母、数字、中文、空格或连字符"
                    "（如 orient-express）。"
                ),
            ) from exc
        except WorkspaceExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return WorkspaceCreatedResponse(slug=body.slug)

    @router.get("/api/{slug}/state", response_model=StateResponse)
    async def state(slug: str) -> StateResponse:
        session = await _session(slug)
        return StateResponse(
            slug=session.slug,
            model=session.model,
            is_running=session.is_running,
            tools=list(session.tool_names),
            bible_types=sorted(ENTITY_LABELS),
        )

    @router.get("/api/{slug}/bible/{entity_type}", response_model=BibleResponse)
    async def bible(slug: str, entity_type: BibleType) -> BibleResponse:
        await _session(slug)
        entities = _read_ledger(_workspace(slug), entity_type)
        return BibleResponse(
            entity_type=entity_type,
            count=len(entities),
            entities=[entity.model_dump() for entity in entities],
        )

    @router.get("/api/{slug}/stats", response_model=StatsResponse)
    async def stats(slug: str) -> StatsResponse:
        """全工作区统计（纯派生）。账本损坏 422、IO 故障 500——与 bible 端点同出口。"""
        await _session(slug)
        workspace = _workspace(slug)
        try:
            data = compute_story_stats(workspace)
        except (ValidationError, LedgerFormatError, yaml.YAMLError) as exc:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"账本统计失败（{slug}）。"
                    f"为什么错：{exc}。"
                    "怎样算对：修复或删除损坏的 YAML 文件后重试。"
                ),
            ) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"账本统计失败（{slug}）：文件系统错误 {exc}。",
            ) from exc
        return StatsResponse(
            chapters=data.chapters,
            total_chars=data.total_chars,
            latest_chapter=data.latest_chapter,
            foreshadow_counts=data.foreshadow_counts,
            foreshadow_total=data.foreshadow_total,
            foreshadow_resolved=data.foreshadow_resolved,
            foreshadow_recovery_rate=data.foreshadow_recovery_rate,
            overdue_foreshadows=[
                ForeshadowOverdueResponse(
                    name=item.name,
                    plant_chapter=item.plant_chapter,
                    expected_resolve_chapter=item.expected_resolve_chapter,
                )
                for item in data.overdue_foreshadows
            ],
            clue_counts=data.clue_counts,
            clue_total=data.clue_total,
            clue_red_herring=data.clue_red_herring,
            clue_red_herring_ratio=data.clue_red_herring_ratio,
        )

    @router.get("/api/{slug}/chapters", response_model=ChaptersResponse)
    async def chapters(slug: str) -> ChaptersResponse:
        await _session(slug)
        workspace = _workspace(slug)
        if not workspace.chapters_dir.exists():
            return ChaptersResponse(chapters=[])  # 空章节是合法空结果
        ids = sorted(path.stem for path in workspace.chapters_dir.glob("*.md"))
        return ChaptersResponse(chapters=ids)

    @router.get("/api/{slug}/chapters/{chapter_id}", response_model=ChapterResponse)
    async def chapter(slug: str, chapter_id: str) -> ChapterResponse:
        await _session(slug)
        if not re.fullmatch(_CHAPTER_ID_PATTERN, chapter_id):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"章节 id 不合法：{chapter_id!r}。"
                    "为什么错：id 只允许字母、数字、连字符与下划线。"
                    f"怎样算对：先 GET /api/{slug}/chapters 查看可用章节 id。"
                ),
            )
        workspace = _workspace(slug)
        path = workspace.chapters_dir / f"{chapter_id}.md"
        try:
            content = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            available = (
                sorted(p.stem for p in workspace.chapters_dir.glob("*.md"))
                if workspace.chapters_dir.exists()
                else []
            )
            hint = (
                f"当前可用章节：{'、'.join(available)}"
                if available
                else "工作区还没有任何章节（先用 write_chapter 工具落盘）"
            )
            raise HTTPException(
                status_code=404,
                detail=(
                    f"章节 {chapter_id} 不存在。"
                    f"为什么错：{hint}。"
                    f"怎样算对：先 GET /api/{slug}/chapters 列出可用章节 id。"
                ),
            ) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"章节读取失败：文件系统错误 {exc}。",
            ) from exc
        return ChapterResponse(
            id=chapter_id,
            content=content,
            revision=current_revision(workspace, chapter_id),
        )

    @router.put("/api/{slug}/chapters/{chapter_id}/edit-mark", response_model=EditMarkResponse)
    async def acquire_edit_mark(chapter_id: str, slug: str) -> EditMarkResponse:
        """作者开始编辑：加草稿锁（P5 D4）。幂等——重复加锁覆盖旧 mark。"""
        if not re.fullmatch(_CHAPTER_ID_PATTERN, chapter_id):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"章节 id 不合法：{chapter_id!r}。"
                    "为什么错：id 只允许字母、数字、连字符与下划线。"
                    f"怎样算对：先 GET /api/{slug}/chapters 查看可用章节 id。"
                ),
            )
        session = await _session(slug)
        if not (_workspace(slug).chapters_dir / f"{chapter_id}.md").exists():
            raise HTTPException(
                status_code=404,
                detail=(
                    f"章节 {chapter_id} 不存在。"
                    "为什么错：编辑锁只对已有章节有意义。"
                    f"怎样算对：先 GET /api/{slug}/chapters 查看可用章节 id。"
                ),
            )
        mark = session.edit_marks.acquire(chapter_id)
        return EditMarkResponse(chapter_id=mark.chapter_id, holder=mark.holder, ts=mark.ts)

    @router.delete(
        "/api/{slug}/chapters/{chapter_id}/edit-mark",
        response_model=EditMarkReleaseResponse,
    )
    async def release_edit_mark(chapter_id: str, slug: str) -> EditMarkReleaseResponse:
        """作者放弃草稿：释放锁（幂等，未持锁无害）。"""
        if not re.fullmatch(_CHAPTER_ID_PATTERN, chapter_id):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"章节 id 不合法：{chapter_id!r}。"
                    "为什么错：id 只允许字母、数字、连字符与下划线。"
                    f"怎样算对：先 GET /api/{slug}/chapters 查看可用章节 id。"
                ),
            )
        session = await _session(slug)
        released = session.edit_marks.release(chapter_id)
        return EditMarkReleaseResponse(chapter_id=chapter_id, released=released)

    @router.put("/api/{slug}/chapters/{chapter_id}", response_model=ChapterSavedResponse)
    async def update_chapter(
        chapter_id: str,
        body: ChapterUpdateRequest,
        slug: str,
    ) -> ChapterSavedResponse:
        """作者受管写端点（D3 管线，顺序固定）。"""
        if not re.fullmatch(_CHAPTER_ID_PATTERN, chapter_id):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"章节 id 不合法：{chapter_id!r}。"
                    "为什么错：id 只允许字母、数字、连字符与下划线。"
                    f"怎样算对：先 GET /api/{slug}/chapters 查看可用章节 id。"
                ),
            )
        try:
            validate_title_heading(body.content)
        except InvalidChapterFormat as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        session = await _session(slug)
        if session.is_running:
            # D4 HTTP 侧守门：写作流进行中作者不得保存
            # （EditMark 是另一半——挡 agent 写作者有草稿的章，在工具层）
            raise HTTPException(
                status_code=409,
                detail=(
                    f"章节 {chapter_id} 保存被拒：agent 写作流进行中。"
                    "为什么错：并发写同一文件会产生交叠版本。"
                    "怎样算对：等待写作流结束（状态条消失）后再保存。"
                ),
            )

        workspace = _workspace(slug)
        revision = current_revision(workspace, chapter_id)
        if body.base_revision != revision:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"章节 {chapter_id} 已被其他页面更新"
                    f"（当前 revision={revision}，本次提交基于 "
                    f"revision={body.base_revision}）。"
                    "为什么错：继续保存会覆盖那份更新。"
                    "怎样算对：重新 GET 该章节，把改动合并到最新版本后再保存。"
                ),
            )

        try:
            saved = save_chapter(workspace, chapter_id, body.content)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"章节 {chapter_id} 不存在。"
                    "为什么错：受管写端点只保存已有章节，不负责创建新章。"
                    f"怎样算对：先 GET /api/{slug}/chapters 查看可用 id，"
                    "新章节请让 agent 用 write_chapter 落盘。"
                ),
            ) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=(
                    f"章节 {chapter_id} 保存失败：文件系统错误 {exc}。"
                    "旧版已归档至 .revisions/；主文件未受影响"
                    "（归档先于写入、覆盖为原子替换），修复后可直接重试保存。"
                ),
            ) from exc

        # D4 锁生命周期：保存成功即释放作者草稿锁（agent 随后可写该章）
        session.edit_marks.release(chapter_id)

        # D5：保存成功且实际有变更 → 自动入队对账（同章 debounce 合并）；
        # sync=false 只存不同步；空 diff 已在上面走 no-op，不触发对账。
        if body.sync and saved.hunks:
            await session.request_reconcile(chapter_id, saved.hunks)

        return ChapterSavedResponse(
            id=chapter_id,
            revision=saved.revision,
            diff=[
                DiffHunkResponse(
                    kind=hunk.kind,
                    old_index=hunk.old_index,
                    new_index=hunk.new_index,
                    old_text=hunk.old_text,
                    new_text=hunk.new_text,
                )
                for hunk in saved.hunks
            ],
        )

    @router.get("/api/{slug}/events", response_model=EventsResponse)
    async def events(
        slug: str,
        limit: int = 200,
        before_seq: int | None = None,
        category: str | None = None,
    ) -> EventsResponse:
        """事件流历史查询（P6 D4/D5）：倒序分页（seq 游标）+ 分类过滤。

        读的是 events.jsonl（可观测性账本，与 session.jsonl 无关）；空账本
        是合法空结果（新工作区尚无事件）→ 200 空列表。
        """
        if limit < 1 or limit > _EVENT_LIMIT_MAX:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"事件流分页参数不合法：limit={limit}。"
                    "为什么错：单页条数须在 1 与 1000 之间。"
                    "怎样算对：把 limit 调到该区间内再请求。"
                ),
            )
        if category is not None and category not in CATEGORIES:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"事件流分类不合法：{category!r}。"
                    "为什么错：分类只接受预设取值。"
                    f"怎样算对：从 {'、'.join(CATEGORIES)} 中选择，或不传 category 取全部。"
                ),
            )
        await _session(slug)
        store = EventStore(events_file(_workspace(slug)))
        page = await store.read(limit=limit, before_seq=before_seq, category=category)
        return EventsResponse(
            events=[
                EventRecordResponse(ts=r.ts, seq=r.seq, type=r.type, data=r.data)
                for r in page.records
            ],
            has_more=page.has_more,
            total=page.total,
            counts=page.counts,
        )

    @router.get("/api/{slug}/events/export")
    async def export_events(slug: str) -> FileResponse:
        """导出完整事件账本（P6 D5）：events.jsonl 原样下载（NDJSON 附件）。

        服务端流式回文件、不整载内存；尚无账本（新工作区从未运行）→ 404
        三要素，不回空文件冒充成功。
        """
        await _session(slug)
        path = events_file(_workspace(slug))
        if not path.exists():
            raise HTTPException(
                status_code=404,
                detail=(
                    f"工作区 {slug} 尚无事件历史可导出。"
                    "为什么错：events.jsonl 还未生成（新工作区或从未运行过写作流）。"
                    "怎样算对：先运行一次对话/写作流产生事件，再点导出。"
                ),
            )
        return FileResponse(
            path,
            media_type="application/x-ndjson",
            filename=f"hastings-{slug}-events.jsonl",
        )

    def _read_settings(workspace: StoryWorkspace) -> SettingsResponse:
        """读设置并返回生效值（P6 T2：档案/模型经 ProviderBook 宽容解析）。

        缺失回落默认值，数据故障 422、IO 故障 500。返回的是「会话实际会用
        的端点与模型」——与 default_session_factory 同一解析函数，至于磁盘
        上存的是哪条档案/哪个模型名不在此暴露（provider_profile 名称已给）。
        """
        try:
            current = load_settings(workspace)
        except (ValidationError, SettingsFormatError, yaml.YAMLError) as exc:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"设置文件读取失败（{workspace.settings_path.name}）。"
                    f"为什么错：{exc}。"
                    "怎样算对：修复或删除损坏的 settings.yaml 后重试，"
                    f"（本设置对应 {workspace.settings_path}）。"
                ),
            ) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"设置文件读取失败：文件系统错误 {exc}。",
            ) from exc
        resolved = _book().resolve(current.provider_profile, current.model)
        return SettingsResponse(
            writing_language=current.writing_language,
            provider_profile=resolved.profile_name,
            model=resolved.model,
        )

    @router.get("/api/{slug}/settings", response_model=SettingsResponse)
    async def get_settings(slug: str) -> SettingsResponse:
        await _session(slug)
        return _read_settings(_workspace(slug))

    @router.put("/api/{slug}/settings", response_model=SettingsResponse)
    async def update_settings(slug: str, body: SettingsUpdateRequest) -> SettingsResponse:
        """局部更新设置（P6 T2）：校验 → 落盘 → 会话生效。

        同档案换模型 = 热切（config.model 赋值，对话历史原地保留）；换档案 =
        registry.rebuild（新 provider → 新 harness，对话经 session.jsonl 恢复）。
        未提供的 provider_profile / model 保持当前值（局部更新语义）。
        """
        if body.writing_language not in WRITING_LANGUAGES:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"写作语言取值不合法：{body.writing_language!r}。"
                    "为什么错：本工作区只支持预设语言之一。"
                    f"怎样算对：从 {'、'.join(WRITING_LANGUAGES)} 中选择一个。"
                ),
            )
        session = await _session(slug)
        workspace = _workspace(slug)
        try:
            current = load_settings(workspace)
        except (ValidationError, SettingsFormatError, yaml.YAMLError) as exc:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"设置文件读取失败（{workspace.settings_path.name}）。"
                    f"为什么错：{exc}。"
                    "怎样算对：修复或删除损坏的 settings.yaml 后重试。"
                ),
            ) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"设置文件读取失败：文件系统错误 {exc}。",
            ) from exc

        target_profile = (
            body.provider_profile if body.provider_profile is not None else current.provider_profile
        )
        requested_model = body.model if body.model is not None else current.model

        book = _book()
        profile = book.profile(target_profile)
        if profile is None:
            available = "、".join(p.name for p in book.config.providers) or "（当前无档案）"
            raise HTTPException(
                status_code=422,
                detail=(
                    f"provider 档案不存在：{target_profile!r}。"
                    "为什么错：档案名不在服务级 config.yaml 的 providers 列表中。"
                    f"怎样算对：从 {available} 中选择一个档案名。"
                ),
            )
        if not profile.available:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"档案 {target_profile!r} 缺少 API key。"
                    "为什么错：api_key 与 api_key_env 都为空，或引用的环境变量未设置。"
                    "怎样算对：在 config.yaml 对应档案直填 api_key，"
                    "或用 api_key_env 引用已设置的环境变量。"
                ),
            )

        resolved = book.resolve(target_profile, requested_model)
        if not resolved.model:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"档案 {target_profile!r} 没有可用的模型名。"
                    "为什么错：请求未提供 model，档案也未配 default_model。"
                    "怎样算对：在设置页填写模型名（可先用拉取模型列表），"
                    "或为该档案补 default_model。"
                ),
            )

        if session.is_running:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"切换模型被拒：工作区 {slug} 有写作流进行中。"
                    "为什么错：切换会替换当前会话，进行中的流会被打断。"
                    "怎样算对：等待写作流结束（状态条消失）后再切换。"
                ),
            )

        # 落盘 requested_model（空串 = 「取档案 default_model」的语义，不固化生效值）
        try:
            save_settings(
                workspace,
                StorySettings(
                    writing_language=body.writing_language,
                    provider_profile=target_profile,
                    model=requested_model,
                ),
            )
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=(f"设置保存失败：文件系统错误 {exc}。怎样算对：确认工作区目录可写后重试。"),
            ) from exc

        if target_profile != session.provider_profile:
            # 跨档案：需新 provider 实例 → registry 重装配（工厂重读磁盘设置、
            # restore_and_attach 恢复对话；system prompt 亦按新设置重建）
            await registry.rebuild(slug)
        else:
            session.apply_writing_language(body.writing_language)
            session.apply_model(resolved.model)

        return SettingsResponse(
            writing_language=body.writing_language,
            provider_profile=target_profile,
            model=resolved.model,
        )

    return router


__all__ = ["BibleType", "create_rest_router"]
