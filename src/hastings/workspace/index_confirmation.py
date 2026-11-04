"""索引候选确认入账（1.0.9 P6）。

语义索引只提出「正文新增」候选；作者确认后，服务端在同一次联合提交里创建信息线
（候选未指向已有信息线时）、信息变化与正文绑定。入账不改正文原句，也不接受客户端
给出的位置：摘要、原句与唯一性都在提交前后重新核对。任一步失败整体不提交。
"""

from __future__ import annotations

from dataclasses import dataclass

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.story_index_jobs import IndexJob, IndexJobStore
from hastings.workspace.story_models import (
    FORMAT_VERSION,
    AddInformationMove,
    AddInformationThread,
    BindProse,
    InformationMove,
    InformationThread,
    ProseBinding,
    StoryChangeSet,
    StoryDocument,
    StoryReceipt,
)
from hastings.workspace.story_store import MAIN_VARIANT_ID, StoryStore, new_id

__all__ = ["IndexConfirmResult", "confirm_index_candidate"]


@dataclass(frozen=True)
class IndexConfirmResult:
    chapter_id: str
    thread: InformationThread
    move: InformationMove
    binding: ProseBinding
    plan_revision: str
    receipt: StoryReceipt


def confirm_index_candidate(
    store: StoryStore,
    workspace: Workspace,
    index_jobs: IndexJobStore,
    *,
    chapter_id: str,
    candidate_id: str,
    expected_prose_digest: str,
    request_key: str,
) -> IndexConfirmResult:
    prior = store.receipt_for(request_key)
    if prior is not None and prior.kind == "index_confirm":
        return _result_from_receipt(store, prior)
    variant = store.require_variant(MAIN_VARIANT_ID)
    if variant.format_version != FORMAT_VERSION:
        raise _format_error(chapter_id)
    plan = variant.chapter(chapter_id)
    if plan is None or plan.prose_path is None:
        raise _chapter_missing(chapter_id)
    path = plan.prose_path
    view = workspace.read(path)
    if view.digest != expected_prose_digest:
        raise _stale(path, "正文在确认前已经变化")
    job = index_jobs.read(chapter_id)
    _validate_job(job, candidate_id=candidate_id, digest=view.digest, path=path)
    assert job is not None
    candidate = next((row for row in job.candidates if row.item_id == candidate_id), None)
    if candidate is None:
        raise WorkspaceError(
            code="INDEX_CANDIDATE_NOT_FOUND",
            where=f"章节/{chapter_id}",
            why=f"没有这条索引候选：{candidate_id}",
            fix="刷新本章信息后重新选择候选",
            details={"candidate_id": candidate_id},
        )
    if any(
        move.statement == candidate.statement
        for move in variant.information_moves_for_chapter(chapter_id)
    ):
        raise _stale(path, "这条候选已经入账")
    if not candidate.quote:
        raise _stale(path, "候选没有可核对的正文原句")
    first = view.text.find(candidate.quote)
    if first < 0:
        raise _stale(path, "候选引用的原句已不在正文中")
    if view.text.find(candidate.quote, first + 1) >= 0:
        raise _stale(path, "候选引用的原句在正文中出现多次，无法确定归属")

    thread: InformationThread
    thread_id = candidate.thread_id
    existing = variant.information_threads.get(thread_id) if thread_id else None
    if existing is not None:
        thread = existing
        thread_op = []
    else:
        thread = InformationThread(id=new_id("thread"), summary=candidate.label[:200])
        thread_op = [AddInformationThread(thread=thread)]
    move = InformationMove(
        id=new_id("move"),
        thread_id=thread.id,
        chapter_id=chapter_id,
        statement=candidate.statement,
    )
    binding = ProseBinding(
        id=new_id("binding"),
        move_id=move.id,
        document_id=chapter_id,
        path=path,
        digest=view.digest,
        start=first,
        end=first + len(candidate.quote),
        quote=candidate.quote,
        source="index_confirmed",
    )
    change = StoryChangeSet(
        id=new_id("change"),
        base_revision=variant.revision,
        operations=[*thread_op, AddInformationMove(move=move), BindProse(binding=binding)],
        requested_scope=f"index-confirm:{candidate_id}",
    )

    def prepare(document: StoryDocument | None) -> tuple[StoryChangeSet, None, dict[str, object]]:
        if document is None:
            raise _chapter_missing(chapter_id)
        current = document.variants.get(MAIN_VARIANT_ID)
        if current is None:
            raise _chapter_missing(chapter_id)
        if current.format_version != FORMAT_VERSION:
            raise _format_error(chapter_id)
        # 入账前重新核对正文摘要；提交与确认必须基于同一份正文。
        latest = workspace.read(path)
        if latest.digest != expected_prose_digest:
            raise _stale(path, "正文在确认前已经变化")
        return change, None, {
            "thread_id": thread.id,
            "move_id": move.id,
            "binding_id": binding.id,
        }

    receipt = store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        change=change,
        request_key=request_key,
        kind="index_confirm",
        prepare_joint=prepare,
    )
    updated = store.require_variant(MAIN_VARIANT_ID)
    return IndexConfirmResult(
        chapter_id=chapter_id,
        thread=thread,
        move=move,
        binding=binding,
        plan_revision=updated.revision,
        receipt=receipt,
    )


def _result_from_receipt(store: StoryStore, receipt: StoryReceipt) -> IndexConfirmResult:
    variant = store.require_variant(MAIN_VARIANT_ID)
    thread_id = str(receipt.result.get("thread_id", ""))
    move_id = str(receipt.result.get("move_id", ""))
    binding_id = str(receipt.result.get("binding_id", ""))
    thread = variant.information_threads.get(thread_id)
    move = variant.information_moves.get(move_id)
    binding = variant.bindings.get(binding_id)
    if thread is None or move is None or binding is None:
        raise _stale("story.commit", "重复的确认请求找不到原入账结果")
    return IndexConfirmResult(
        chapter_id=move.chapter_id,
        thread=thread,
        move=move,
        binding=binding,
        plan_revision=variant.revision,
        receipt=receipt,
    )


def _validate_job(job: IndexJob | None, *, candidate_id: str, digest: str, path: str) -> None:
    if job is None or job.status != "ready" or job.based_on_digest != digest:
        raise _stale(path, "索引尚未基于当前正文完成")
    if not any(row.item_id == candidate_id for row in job.candidates):
        raise WorkspaceError(
            code="INDEX_CANDIDATE_NOT_FOUND",
            where=path,
            why=f"没有这条索引候选：{candidate_id}",
            fix="刷新本章信息后重新选择候选",
            details={"candidate_id": candidate_id},
        )


def _chapter_missing(chapter_id: str) -> WorkspaceError:
    return WorkspaceError(
        code="CHAPTER_NOT_FOUND",
        where=f"章节/{chapter_id}",
        why="章节或正文不存在",
        fix="刷新工作台后重新选择章节",
        details={"chapter_id": chapter_id},
    )


def _stale(path: str, why: str) -> WorkspaceError:
    return WorkspaceError(
        code="INDEX_RESULT_STALE",
        where=path,
        why=why,
        fix="等待最新章节信息后重新确认；不要用旧候选覆盖当前正文",
        details={"path": path},
    )


def _format_error(chapter_id: str) -> WorkspaceError:
    return WorkspaceError(
        code="STORY_FORMAT_UNSUPPORTED",
        where=f"章节/{chapter_id}",
        why="这份作品仍使用旧构思格式",
        fix="新建作品后使用写作工作台；本版不迁移旧数据",
    )
