"""REST 只读面（P4 T4 slug 化，计划 D4）：workspaces + state / bible / chapters。

零写入口——唯一例外 POST /api/workspaces 是资源创建（建目录），不碰业务
数据（P2 守门人原则照旧：写路径在工具层，不容 HTTP 绕过）。
泄压阀三情形在 HTTP 层的落点：
- 参数违规（坏 slug、未知账本类型、坏章节 id）→ 422/404 + 可读指引；
- 合法但空（空账本、无章节、无工作区）→ 200 空数组，不是错误；
- 撞名新建（409）、数据故障（YAML 坏）→ 422 + 路径指引；
  环境故障（IO）→ 500 带归因，不与数据故障混谈。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Final, Literal

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from hastings.bible.models import Clue, Foreshadow, Method, TimelineEntry
from hastings.bible.store import (
    InvalidEntityNameError,
    LedgerFormatError,
    StoryWorkspace,
    list_characters,
    load_ledger,
)
from hastings.server.registry import WorkspaceExistsError, WorkspaceRegistry
from hastings.server.session import WritingSession
from hastings.tools.bible_tools import ENTITY_LABELS
from tau_agent.types import JSONValue

BibleType = Literal["character", "clue", "foreshadow", "method", "timeline"]

# 章节文件名（去 .md）即 id：只允许字母数字、连字符与下划线，杜绝路径穿越。
_CHAPTER_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}"


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


class WorkspacesResponse(BaseModel):
    workspaces: list[str]


class WorkspaceCreateRequest(BaseModel):
    slug: str


class WorkspaceCreatedResponse(BaseModel):
    slug: str


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


def create_rest_router(*, registry: WorkspaceRegistry) -> APIRouter:
    router = APIRouter()

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
        return ChapterResponse(id=chapter_id, content=content)

    return router


__all__ = ["BibleType", "create_rest_router"]
