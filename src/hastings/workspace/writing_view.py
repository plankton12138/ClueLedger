"""写作工作台只读投影（1.0.9 P2；P6 改为正文为主）。

一次读取返回全书、当前章节、正文与确定性索引，页面不需要再拼多个旧接口。章节只有顺序、
标题和正文路径：正文能证明的出处归入 ``written``，未确认的语义结果只作为候选，冲突作为
``alignment``。章节没有信息变化时返回空列表，由前端给出空章反馈，服务端不造占位项。

投影不是新的可编辑账本：它不保存任何内容，也不修改正式构思或正文。
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, Field

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.story_bindings import anchor_state
from hastings.workspace.story_index_jobs import IndexJob, IndexJobStore
from hastings.workspace.story_models import (
    FORMAT_VERSION,
    ChapterPlan,
    InformationMove,
    InformationThread,
    StoryVariant,
)
from hastings.workspace.structure_io import read_structure_state

__all__ = [
    "ChapterInformationIndex",
    "ChapterPlanView",
    "ChapterView",
    "WritingBookView",
    "WritingWorkbenchView",
    "build_chapter_index",
    "build_workbench",
    "context_revision_for",
]

AnchorState = Literal["exact", "relocated", "missing", "ambiguous", "unavailable"]
Placement = Literal["written", "observed_candidate", "alignment"]
AnalysisState = Literal[
    "not_needed", "queued", "running", "ready", "unavailable", "failed", "stale"
]


class InformationOccurrence(BaseModel):
    path: str
    digest: str
    quote: str
    start: int | None = None
    end: int | None = None
    state: AnchorState


class InformationIndexItem(BaseModel):
    item_id: str
    thread_id: str | None
    move_id: str | None
    label: str
    placement: Placement
    occurrences: list[InformationOccurrence] = Field(default_factory=list)
    candidate_id: str | None = None
    explanation: str | None = None


class IndexAnalysis(BaseModel):
    state: AnalysisState
    based_on_digest: str | None = None
    operation_id: str | None = None


class ChapterInformationIndex(BaseModel):
    index_revision: str
    plan_revision: str
    context_revision: str
    prose_digest: str | None
    analysis: IndexAnalysis
    items: list[InformationIndexItem] = Field(default_factory=list)


class ChapterMoveView(BaseModel):
    id: str
    thread_id: str
    thread_summary: str
    statement: str
    effect: str | None = None


class ChapterPlanView(BaseModel):
    id: str
    order: int
    title: str | None
    prose_path: str | None
    moves: list[ChapterMoveView]


class ChapterSummary(BaseModel):
    id: str
    order: int
    title: str | None
    summary: str
    prose_path: str | None


class InformationThreadSummary(BaseModel):
    id: str
    summary: str


class UnplannedProse(BaseModel):
    path: str
    title: str
    digest: str
    chapter_number: int | None


class WritingBookView(BaseModel):
    structure_exists: bool
    chapters: list[ChapterSummary]
    information_threads: list[InformationThreadSummary]
    unplanned_prose: list[UnplannedProse]


class WritingProseView(BaseModel):
    path: str
    digest: str
    text: str
    editable: Literal[False] = False


class ChapterView(BaseModel):
    plan: ChapterPlanView | None
    prose: WritingProseView | None
    index: ChapterInformationIndex | None


class ModelSupportView(BaseModel):
    available: bool = False
    disabled_reason: str | None = "模型辅助尚未接入"
    supported_assists: list[str] = Field(default_factory=list)


class WritingWorkbenchView(BaseModel):
    workspace_id: str
    selection: dict[str, str]
    plan_revision: str | None
    book: WritingBookView
    chapter: ChapterView | None
    model_support: ModelSupportView
    view_revision: str


def build_workbench(
    workspace: Workspace,
    variant: StoryVariant | None,
    *,
    workspace_id: str,
    chapter_id: str | None = None,
    prose_path: str | None = None,
    model_support: ModelSupportView | None = None,
    index_jobs: IndexJobStore | None = None,
) -> WritingWorkbenchView:
    """生成全书或章节投影；选择不改变任何正式内容。"""
    if variant is not None and variant.format_version != FORMAT_VERSION:
        raise WorkspaceError(
            code="STORY_FORMAT_UNSUPPORTED",
            where="story.workbench",
            why="这份作品使用已退出的旧格式",
            fix="新建作品后使用当前写作流程；本版不迁移旧数据",
        )
    if chapter_id is not None and prose_path is not None:
        raise WorkspaceError(
            code="STORY_QUERY_CONFLICT",
            where="story.workbench",
            why="一次只能选择章节或正文路径",
            fix="去掉其中一个查询参数后重试",
        )
    book = _book_view(workspace, variant)
    chapter: ChapterView | None = None
    selection: dict[str, str] = {"kind": "book"}
    prose_digest: str | None = None
    index_revision: str | None = None
    if prose_path is not None:
        owner = variant.chapter_for_path(prose_path) if variant is not None else None
        if owner is not None:
            selection = {"kind": "chapter", "chapter_id": owner.id}
            chapter = _chapter_view(workspace, variant, owner.id, index_jobs=index_jobs)
            prose_digest = chapter.prose.digest if chapter.prose is not None else None
            index_revision = chapter.index.index_revision if chapter.index is not None else None
        else:
            selection = {"kind": "prose", "prose_path": prose_path}
            chapter = _unplanned_chapter(workspace, prose_path, variant)
            if chapter.prose is not None:
                prose_digest = chapter.prose.digest
    elif chapter_id is not None:
        selection = {"kind": "chapter", "chapter_id": chapter_id}
        chapter = _chapter_view(workspace, variant, chapter_id, index_jobs=index_jobs)
        prose_digest = chapter.prose.digest if chapter.prose is not None else None
        index_revision = chapter.index.index_revision if chapter.index is not None else None
    return WritingWorkbenchView(
        workspace_id=workspace_id,
        selection=selection,
        plan_revision=variant.revision if variant is not None else None,
        book=book,
        chapter=chapter,
        model_support=model_support or ModelSupportView(),
        view_revision=_digest(
            {
                "selection": selection,
                "plan_revision": variant.revision if variant is not None else None,
                "prose_digest": prose_digest,
                "index_revision": index_revision,
            }
        ),
    )


def build_chapter_index(
    variant: StoryVariant,
    plan: ChapterPlan,
    *,
    path: str | None,
    text: str,
    digest: str | None,
    job: IndexJob | None = None,
) -> ChapterInformationIndex:
    """按当前正文与最新语义任务生成索引；语义结果只作为候选。"""
    items: list[InformationIndexItem] = []
    moves = variant.information_moves_for_chapter(plan.id)
    for move in moves:
        thread = variant.information_threads.get(move.thread_id)
        occurrences: list[InformationOccurrence] = []
        for binding in variant.bindings.values():
            if binding.move_id != move.id or path is None or binding.path != path:
                continue
            anchor = anchor_state(binding, text, digest or "")
            span = anchor.span
            occurrences.append(
                InformationOccurrence(
                    path=binding.path,
                    digest=digest or binding.digest,
                    quote=binding.quote,
                    start=span.start if span is not None else None,
                    end=span.end if span is not None else None,
                    state=anchor.state,
                )
            )
        if not occurrences:
            continue
        items.append(
            InformationIndexItem(
                item_id=move.id,
                thread_id=move.thread_id,
                move_id=move.id,
                label=_label(thread, move),
                placement="written",
                occurrences=occurrences,
            )
        )
    analysis_state: AnalysisState = "not_needed" if digest is None else "unavailable"
    based_on: str | None = None
    job_id: str | None = None
    if digest is not None and job is not None:
        based_on = job.based_on_digest
        job_id = job.job_id
        analysis_state = "stale" if job.based_on_digest != digest else _analysis_state(job.status)
        if job.status == "ready" and job.based_on_digest == digest:
            confirmed = {move.statement for move in moves}
            for candidate in job.candidates:
                if candidate.statement in confirmed:
                    continue
                occurrences = []
                if candidate.quote and path is not None:
                    first = text.find(candidate.quote)
                    state: AnchorState
                    start: int | None = None
                    end: int | None = None
                    if first < 0:
                        state = "missing"
                    elif text.find(candidate.quote, first + 1) >= 0:
                        state = "ambiguous"
                    else:
                        state, start, end = "exact", first, first + len(candidate.quote)
                    occurrences.append(
                        InformationOccurrence(
                            path=path,
                            digest=digest,
                            quote=candidate.quote,
                            start=start,
                            end=end,
                            state=state,
                        )
                    )
                items.append(
                    InformationIndexItem(
                        item_id=f"candidate:{candidate.item_id}",
                        thread_id=candidate.thread_id,
                        move_id=candidate.move_id,
                        label=candidate.label,
                        placement=(
                            "alignment" if candidate.kind == "alignment" else "observed_candidate"
                        ),
                        occurrences=occurrences,
                        candidate_id=candidate.item_id,
                        explanation=candidate.explanation,
                    )
                )
    analysis = IndexAnalysis(
        state=analysis_state,
        based_on_digest=based_on,
        operation_id=job_id,
    )
    context_revision = context_revision_for(variant, plan)
    index_revision = _digest(
        {
            "chapter_id": plan.id,
            "prose_digest": digest,
            "items": [item.model_dump(mode="json") for item in items],
            "analysis": analysis.model_dump(mode="json"),
        }
    )
    return ChapterInformationIndex(
        index_revision=f"idx_{index_revision[:32]}",
        plan_revision=variant.revision,
        context_revision=context_revision,
        prose_digest=digest,
        analysis=analysis,
        items=items,
    )


def _analysis_state(status: str) -> AnalysisState:
    if status in ("queued", "running", "ready", "failed", "unavailable", "stale"):
        return status  # type: ignore[return-value]
    return "unavailable"


def context_revision_for(variant: StoryVariant, plan: ChapterPlan) -> str:
    """当前章与直接相关信息线的组合修订；无关章节变化不影响它。"""
    threads = {
        (thread.id, thread.summary)
        for move in variant.information_moves_for_chapter(plan.id)
        if (thread := variant.information_threads.get(move.thread_id)) is not None
    }
    payload = {
        "chapter_id": plan.id,
        "moves": [
            {
                "id": move.id,
                "thread_id": move.thread_id,
                "statement": move.statement,
            }
            for move in variant.information_moves_for_chapter(plan.id)
        ],
        "threads": [
            {"id": thread_id, "summary": summary} for thread_id, summary in sorted(threads)
        ],
    }
    return f"ctx_{_digest(payload)[:32]}"


def _book_view(workspace: Workspace, variant: StoryVariant | None) -> WritingBookView:
    plans = list(variant.chapter_plans.values()) if variant is not None else []
    chapters = sorted(plans, key=lambda plan: (plan.order, plan.id))
    owned_paths = {plan.prose_path for plan in chapters if plan.prose_path is not None}
    unplanned: list[UnplannedProse] = []
    for entry in workspace.catalog().chapters:
        if not entry.exists or entry.path in owned_paths:
            continue
        try:
            view = workspace.read(entry.path)
        except WorkspaceError:
            continue
        unplanned.append(
            UnplannedProse(
                path=entry.path,
                title=entry.display_title or entry.title,
                digest=view.digest,
                chapter_number=entry.chapter_number,
            )
        )
    return WritingBookView(
        structure_exists=read_structure_state(workspace).exists,
        chapters=[
            ChapterSummary(
                id=plan.id,
                order=plan.order,
                title=plan.title,
                summary=_plan_summary(variant, plan) if variant is not None else "",
                prose_path=plan.prose_path,
            )
            for plan in chapters
        ],
        information_threads=[
            InformationThreadSummary(id=thread.id, summary=thread.summary)
            for thread in sorted(
                variant.information_threads.values() if variant is not None else [],
                key=lambda row: row.id,
            )
        ],
        unplanned_prose=unplanned,
    )


def _chapter_view(
    workspace: Workspace,
    variant: StoryVariant | None,
    chapter_id: str,
    *,
    index_jobs: IndexJobStore | None = None,
) -> ChapterView:
    plan = variant.chapter(chapter_id) if variant is not None else None
    if plan is None:
        raise WorkspaceError(
            code="CHAPTER_NOT_FOUND",
            where=f"章节/{chapter_id}",
            why="章节不存在",
            fix="刷新工作台后重新选择章节",
            details={"chapter_id": chapter_id},
        )
    prose: WritingProseView | None = None
    text = ""
    digest: str | None = None
    if plan.prose_path is not None:
        try:
            view = workspace.read(plan.prose_path)
        except WorkspaceError:
            view = None
        if view is not None:
            text = view.text
            digest = view.digest
            prose = WritingProseView(
                path=view.path,
                digest=view.digest,
                text=view.text,
            )
    job = index_jobs.read(plan.id) if index_jobs is not None else None
    index = (
        build_chapter_index(variant, plan, path=plan.prose_path, text=text, digest=digest, job=job)
        if variant is not None
        else None
    )
    return ChapterView(
        plan=_plan_view(variant, plan) if variant is not None else None,
        prose=prose,
        index=index,
    )


def _unplanned_chapter(
    workspace: Workspace, prose_path: str, variant: StoryVariant | None
) -> ChapterView:
    try:
        view = workspace.read(prose_path)
    except WorkspaceError as exc:
        raise WorkspaceError(
            code="FILE_NOT_FOUND" if exc.code == "FILE_NOT_FOUND" else exc.code,
            where=prose_path,
            why="未挂章节的正文不可读",
            fix="刷新目录后重新选择正文",
        ) from exc
    return ChapterView(
        plan=None,
        prose=WritingProseView(
            path=view.path,
            digest=view.digest,
            text=view.text,
        ),
        index=None,
    )


def _plan_view(variant: StoryVariant, plan: ChapterPlan) -> ChapterPlanView:
    moves: list[ChapterMoveView] = []
    for move in variant.information_moves_for_chapter(plan.id):
        thread = variant.information_threads.get(move.thread_id)
        moves.append(
            ChapterMoveView(
                id=move.id,
                thread_id=move.thread_id,
                thread_summary=thread.summary if thread is not None else "",
                statement=move.statement,
                effect=move.effect,
            )
        )
    return ChapterPlanView(
        id=plan.id,
        order=plan.order,
        title=plan.title,
        prose_path=plan.prose_path,
        moves=moves,
    )


def _plan_summary(variant: StoryVariant, plan: ChapterPlan) -> str:
    for move in variant.information_moves_for_chapter(plan.id):
        return _short(move.statement)
    return ""


def _label(thread: InformationThread | None, move: InformationMove) -> str:
    if thread is not None:
        return _short(thread.summary)
    return _short(move.statement)


def _short(value: str, limit: int = 200) -> str:
    clean = " ".join(value.split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
