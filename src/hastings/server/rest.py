"""REST 只读面（P3 T4，计划 D6）：state / bible / chapters 三组端点。

零写入口——写路径唯一在工具层（P2 守门人原则，不容 HTTP 绕过）。
泄压阀三情形在 HTTP 层的落点：
- 参数违规（未知账本类型、坏章节 id）→ 422/404 + 可读指引；
- 合法但空（空账本、无章节）→ 200 空数组，不是错误；
- 数据故障（YAML 语法坏/根节点结构坏/字段不合法）→ 422 + 路径指引；
  环境故障（IO）→ 500 带归因（R3 清单），不与数据故障混谈。
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
    LedgerFormatError,
    StoryWorkspace,
    list_characters,
    load_ledger,
)
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


def create_rest_router(*, workspace: StoryWorkspace, session: WritingSession) -> APIRouter:
    router = APIRouter()

    @router.get("/api/state", response_model=StateResponse)
    async def state() -> StateResponse:
        return StateResponse(
            slug=session.slug,
            model=session.model,
            is_running=session.is_running,
            tools=list(session.tool_names),
            bible_types=sorted(ENTITY_LABELS),
        )

    @router.get("/api/bible/{entity_type}", response_model=BibleResponse)
    async def bible(entity_type: BibleType) -> BibleResponse:
        entities = _read_ledger(workspace, entity_type)
        return BibleResponse(
            entity_type=entity_type,
            count=len(entities),
            entities=[entity.model_dump() for entity in entities],
        )

    @router.get("/api/chapters", response_model=ChaptersResponse)
    async def chapters() -> ChaptersResponse:
        if not workspace.chapters_dir.exists():
            return ChaptersResponse(chapters=[])  # 空章节是合法空结果
        ids = sorted(path.stem for path in workspace.chapters_dir.glob("*.md"))
        return ChaptersResponse(chapters=ids)

    @router.get("/api/chapters/{chapter_id}", response_model=ChapterResponse)
    async def chapter(chapter_id: str) -> ChapterResponse:
        if not re.fullmatch(_CHAPTER_ID_PATTERN, chapter_id):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"章节 id 不合法：{chapter_id!r}。"
                    "为什么错：id 只允许字母、数字、连字符与下划线。"
                    "怎样算对：先 GET /api/chapters 查看可用章节 id。"
                ),
            )
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
                    "怎样算对：先 GET /api/chapters 列出可用章节 id。"
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
