"""作品意图、章节壳与章节构思的写入动作（1.0.9 P2）。

页面只发送自然语言、章节归属和可选的信息变化；服务端补齐标识、修订与无模型回退。
所有动作都走现有 ``StoryStore`` 提交，不新建状态文件，也不接受客户端伪造的确认状态。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.story_models import (
    AddChapterPlan,
    AddInformationMove,
    AddInformationThread,
    BookIntent,
    ChapterPlan,
    InformationMove,
    InformationThread,
    RemoveInformationMove,
    SetBookIntent,
    StoryChangeSet,
    StoryOperation,
    StoryReceipt,
    UpdateChapterPlan,
    UpdateInformationMove,
    chapter_content_revision,
)
from hastings.workspace.story_store import EMPTY_REVISION, MAIN_VARIANT_ID, StoryStore, new_id

__all__ = [
    "ChapterPlanResult",
    "ChapterShellResult",
    "InformationMoveInput",
    "create_chapter_plan",
    "save_book_intent",
    "save_chapter_plan",
]

Effect = Literal["introduce", "reinforce", "reframe", "refute", "resolve", "knowledge"]


class InformationMoveInput(BaseModel):
    """作者或模型提供的单条信息变化；标识可不填，由服务端生成。"""

    model_config = {"extra": "forbid"}

    id: str | None = Field(default=None, max_length=64)
    thread_id: str | None = Field(default=None, max_length=64)
    statement: str = Field(min_length=1, max_length=4000)
    effect: Effect | None = None

    @model_validator(mode="after")
    def check_text(self) -> InformationMoveInput:
        if not self.statement.strip():
            raise ValueError("信息变化不能为空")
        return self


@dataclass(frozen=True)
class ChapterShellResult:
    chapter_id: str
    order: int
    plan_revision: str
    receipt: StoryReceipt


@dataclass(frozen=True)
class ChapterPlanResult:
    chapter_id: str
    chapter_revision: str
    plan_revision: str
    moves: tuple[InformationMove, ...]
    threads: tuple[InformationThread, ...]
    receipt: StoryReceipt


def save_book_intent(
    store: StoryStore,
    *,
    text: str,
    expected_plan_revision: str,
    request_key: str,
) -> StoryReceipt:
    """写入全书起点；首次提交允许 `expected_plan_revision="empty"`。"""
    if not text.strip():
        raise WorkspaceError(
            code="BOOK_INTENT_REQUIRED",
            where="作品意图",
            why="作品意图不能为空",
            fix="写一句核心处境或起点，或选择「交给模型构思」",
        )
    variant = store.read().variant(MAIN_VARIANT_ID)
    if variant is not None and variant.format_version < 3:
        raise _format_error()
    base = variant.revision if variant is not None else EMPTY_REVISION
    if expected_plan_revision != base:
        raise WorkspaceError(
            code="STORY_CHANGE_CONFLICT",
            where="作品意图",
            why="作品构思在保存前已经变化",
            fix="重新读取当前作品意图后再保存",
            details={"expected": expected_plan_revision, "current": base},
        )
    change = StoryChangeSet(
        id=new_id("change"),
        base_revision=expected_plan_revision,
        operations=[SetBookIntent(intent=BookIntent(text=text.strip()))],
        requested_scope="book-intent",
    )
    return store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        change=change,
        request_key=request_key,
        kind="book_intent",
    )


def create_chapter_plan(
    store: StoryStore,
    *,
    title: str | None,
    after_chapter_id: str | None,
    expected_plan_revision: str,
    request_key: str,
) -> ChapterShellResult:
    """在指定章节之后插入一个计划壳；不分配正文路径。"""
    variant = store.read().variant(MAIN_VARIANT_ID)
    if variant is None or variant.book_intent is None:
        raise WorkspaceError(
            code="BOOK_INTENT_REQUIRED",
            where="作品意图",
            why="还没有全书起点，不能先建章节",
            fix="先写一句作品意图，或选择「交给模型构思」",
        )
    if variant.format_version < 3:
        raise _format_error()
    if variant.revision != expected_plan_revision:
        raise _stale("作品构思")
    plans = list(variant.chapter_plans.values())
    if after_chapter_id is not None:
        anchor = variant.chapter(after_chapter_id)
        if anchor is None:
            raise WorkspaceError(
                code="CHAPTER_PLAN_REQUIRED",
                where=f"章节/{after_chapter_id}",
                why="用于定位的章节不存在",
                fix="刷新目录后重新选择插入位置",
            )
        order = anchor.order + 1
        shifted = [plan for plan in plans if plan.order >= order]
    else:
        order = max((plan.order for plan in plans), default=-1) + 1
        shifted = []
    chapter_id = new_id("ch")
    plan = ChapterPlan(
        id=chapter_id,
        order=order,
        title=title or None,
        intent="",
        information_move_ids=[],
        revision=chapter_content_revision("", []),
    )
    operations = [AddChapterPlan(plan=plan)]
    operations.extend(
        UpdateChapterPlan(
            chapter_id=item.id,
            plan=item.model_copy(update={"order": item.order + 1}),
        )
        for item in shifted
    )
    change = StoryChangeSet(
        id=new_id("change"),
        base_revision=expected_plan_revision,
        operations=operations,
        requested_scope=f"chapter-create:{chapter_id}",
    )
    receipt = store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        change=change,
        request_key=request_key,
        kind="chapter_create",
    )
    updated = store.require_variant(MAIN_VARIANT_ID)
    created = updated.chapter(chapter_id)
    if created is None:
        raise _state_error("创建章节后找不到章节构思")
    return ChapterShellResult(
        chapter_id=chapter_id,
        order=created.order,
        plan_revision=updated.revision,
        receipt=receipt,
    )


def save_chapter_plan(
    store: StoryStore,
    *,
    chapter_id: str,
    intent: str,
    title: str | None,
    information_moves: list[InformationMoveInput] | None,
    expected_chapter_revision: str,
    expected_plan_revision: str,
    request_key: str,
) -> ChapterPlanResult:
    """保存本章意图与信息变化；未提供变化时保留旧的，完全没有时生成一条最小变化。"""
    variant = store.require_variant(MAIN_VARIANT_ID)
    if variant.format_version < 3:
        raise _format_error()
    plan = variant.chapter(chapter_id)
    if plan is None:
        raise WorkspaceError(
            code="CHAPTER_PLAN_REQUIRED",
            where=f"章节/{chapter_id}",
            why="章节构思不存在",
            fix="刷新工作台后重新选择章节",
        )
    if variant.revision != expected_plan_revision:
        raise _stale("作品构思")
    if plan.revision != expected_chapter_revision:
        raise _stale(f"章节/{chapter_id}")
    if not intent.strip():
        raise WorkspaceError(
            code="CHAPTER_PLAN_REQUIRED",
            where=f"章节/{chapter_id}",
            why="还没有说明本章要改变什么",
            fix="写一句本章变化，或选择「交给模型构思」",
        )
    if information_moves is not None and not information_moves:
        raise WorkspaceError(
            code="CHAPTER_PLAN_INCOMPLETE",
            where=f"章节/{chapter_id}",
            why="本章至少需要一项信息变化",
            fix="写一项读者或角色理解到的变化，或让服务端从一句话构思整理",
        )

    new_threads: dict[str, InformationThread] = {}
    desired: list[InformationMove] = []
    if information_moves is None:
        existing = list(variant.information_moves_for_chapter(chapter_id))
        if existing:
            desired = existing
        else:
            move_id = new_id("move")
            thread = _fallback_thread(intent)
            new_threads[thread.id] = thread
            desired = [
                InformationMove(
                    id=move_id,
                    thread_id=thread.id,
                    chapter_id=chapter_id,
                    statement=intent.strip(),
                )
            ]
    else:
        for item in information_moves:
            move_id = item.id or new_id("move")
            if item.id is not None and item.id in variant.information_moves:
                owner = variant.information_moves[item.id].chapter_id
                if owner != chapter_id:
                    raise WorkspaceError(
                        code="CHAPTER_PLAN_INCOMPLETE",
                        where=f"章节/{chapter_id}",
                        why="这条信息变化已经属于另一章",
                        fix="为本章使用新的信息变化标识，或先移除原章的变化",
                        details={"move_id": item.id, "chapter_id": owner},
                    )
            thread_id = item.thread_id or new_id("thread")
            if thread_id not in variant.information_threads and thread_id not in new_threads:
                new_threads[thread_id] = InformationThread(
                    id=thread_id, summary=_short(item.statement)
                )
            desired.append(
                InformationMove(
                    id=move_id,
                    thread_id=thread_id,
                    chapter_id=chapter_id,
                    statement=item.statement.strip(),
                    effect=item.effect,
                )
            )

    desired_ids = {move.id for move in desired}
    existing_moves = list(variant.information_moves_for_chapter(chapter_id))
    existing_ids = {move.id for move in existing_moves}
    removed = [move for move in existing_moves if move.id not in desired_ids]
    for move in removed:
        if any(binding.move_id == move.id for binding in variant.bindings.values()):
            raise WorkspaceError(
                code="STORY_CHANGE_CONFLICT",
                where=f"章节/{chapter_id}",
                why=f"信息变化已有正文出处，不能直接删除：{move.statement[:30]}",
                fix="先在正文里解除这条出处，再修改构思",
                details={"move_id": move.id},
            )
    new_revision = chapter_content_revision(intent, desired)
    content_changed = new_revision != plan.revision
    updated_plan = plan.model_copy(
        update={
            "title": (title or None) if title is not None else plan.title,
            "intent": intent.strip(),
            "information_move_ids": [move.id for move in desired],
            "revision": new_revision,
            "confirmed_revision": None if content_changed else plan.confirmed_revision,
        }
    )
    operations: list[StoryOperation] = [
        AddInformationThread(thread=thread) for thread in new_threads.values()
    ]
    for move in desired:
        if move.id in existing_ids:
            operations.append(UpdateInformationMove(move_id=move.id, move=move))
        else:
            operations.append(AddInformationMove(move=move))
    operations.extend(RemoveInformationMove(move_id=move.id) for move in removed)
    operations.append(UpdateChapterPlan(chapter_id=chapter_id, plan=updated_plan))
    change = StoryChangeSet(
        id=new_id("change"),
        base_revision=expected_plan_revision,
        operations=operations,
        requested_scope=f"chapter-plan:{chapter_id}",
    )
    receipt = store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        change=change,
        request_key=request_key,
        kind="chapter_plan",
    )
    updated = store.require_variant(MAIN_VARIANT_ID)
    saved = updated.chapter(chapter_id)
    if saved is None:
        raise _state_error("保存章节构思后找不到章节")
    moves = updated.information_moves_for_chapter(chapter_id)
    thread_ids = {move.thread_id for move in moves}
    threads = tuple(
        updated.information_threads[thread_id]
        for thread_id in sorted(thread_ids)
        if thread_id in updated.information_threads
    )
    return ChapterPlanResult(
        chapter_id=chapter_id,
        chapter_revision=saved.revision,
        plan_revision=updated.revision,
        moves=moves,
        threads=threads,
        receipt=receipt,
    )


def _fallback_thread(intent: str) -> InformationThread:
    """无模型时给一句构思配一条最小信息线；作者之后可改摘要。"""
    return InformationThread(id=new_id("thread"), summary=_short(intent))


def _short(value: str, limit: int = 200) -> str:
    clean = " ".join(value.split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def _format_error() -> WorkspaceError:
    return WorkspaceError(
        code="STORY_FORMAT_UNSUPPORTED",
        where="写作工作台",
        why="这份作品仍使用旧构思格式",
        fix="新建作品后使用写作工作台；本版不迁移旧数据",
    )


def _stale(where: str) -> WorkspaceError:
    return WorkspaceError(
        code="CHAPTER_PLAN_STALE",
        where=where,
        why="构思在保存前已经变化",
        fix="重新读取当前摘要，确认后再保存",
    )


def _state_error(why: str) -> WorkspaceError:
    return WorkspaceError(
        code="STORY_STATE_FAILED",
        where="story.commit",
        why=why,
        fix="刷新作品状态后重试；若仍失败，检查状态目录是否完整",
    )
