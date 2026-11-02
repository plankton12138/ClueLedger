"""章节开始事务与整章正文保存（1.0.9 P1）。

开始写作不是「先确认构思，再调用文件写入」两个顺序动作。这里把确认修订、分配正文路径、
创建初始文件放进同一次 ``StoryStore`` 联合提交：故事状态与正文一起成功或一起失败。
普通保存复用同一提交通道，只按当前摘要整章替换，不另建正文状态文件。
"""

from __future__ import annotations

from dataclasses import dataclass

from hastings.workspace.chapter_write_policy import ChapterWritePolicy
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace, digest_bytes, managed_kind, normalize_newlines
from hastings.workspace.story_bindings import anchor_state
from hastings.workspace.story_index_jobs import changed_text
from hastings.workspace.story_models import (
    BindProse,
    ConfirmChapterPlan,
    ProsePatch,
    SetChapterProsePath,
    StoryChangeSet,
    StoryDocument,
    StoryOperation,
    UnbindProse,
)
from hastings.workspace.story_store import MAIN_VARIANT_ID, StoryStore, new_id
from hastings.workspace.writing_view import ChapterInformationIndex, build_chapter_index

__all__ = [
    "ChapterStartResult",
    "ProseSaveResult",
    "save_chapter_prose",
    "start_chapter",
]


@dataclass(frozen=True)
class ChapterStartResult:
    chapter_id: str
    path: str
    prose_digest: str
    plan_revision: str
    chapter_revision: str
    committed_change_id: str


@dataclass(frozen=True)
class ProseSaveResult:
    path: str
    prose_digest: str
    plan_revision: str
    chapter_revision: str
    context_revision: str
    index_revision: str
    index: ChapterInformationIndex
    analysis_job_id: str | None
    committed_change_id: str
    changed_text: str = ""


def start_chapter(
    store: StoryStore,
    workspace: Workspace,
    *,
    chapter_id: str,
    expected_story_revision: str,
    expected_chapter_revision: str,
    request_key: str,
    attach_path: str | None = None,
    attach_digest: str | None = None,
) -> ChapterStartResult:
    """确认章节构思并创建或附加首个正文文件；重复请求键返回同一结果。"""
    policy = ChapterWritePolicy(store)
    # 仅用于请求指纹与重复键查重；真正提交的内容由 prepare 在锁内生成。
    initial = StoryChangeSet.model_construct(
        id=new_id("change"),
        base_revision=expected_story_revision,
        operations=[],
        prose_patches=[],
        read_dependencies=[],
        requested_scope=f"chapter-start:{chapter_id}",
        operation_id="",
        generation=0,
        sync=None,
    )

    def prepare(document: StoryDocument | None) -> tuple[StoryChangeSet, None, dict[str, object]]:
        if document is None:
            raise _required(chapter_id, "章节构思不存在")
        variant = document.variants.get(MAIN_VARIANT_ID)
        if variant is None:
            raise _required(chapter_id, "章节构思不存在")
        policy.require_start(
            variant,
            chapter_id,
            expected_story_revision=expected_story_revision,
            expected_chapter_revision=expected_chapter_revision,
        )
        plan = variant.chapter(chapter_id)
        assert plan is not None
        if attach_path is not None:
            path = attach_path
            if managed_kind(path) != "chapter":
                raise _path_conflict(path, "只能附加正文目录下的 Markdown 文件")
            if attach_digest is None:
                raise WorkspaceError(
                    code="PROSE_DIGEST_MISMATCH",
                    where=path,
                    why="附加正文必须同时给出读取时的摘要",
                    fix="重新读取这一章，再用返回的摘要附加",
                    details={"path": path},
                )
            owner = variant.chapter_for_path(path)
            if owner is not None and owner.id != chapter_id:
                raise _path_conflict(path, f"该正文已属于章节 {owner.id}")
            view = workspace.read(path)
            if attach_digest is not None and view.digest != attach_digest:
                raise _digest_mismatch(path, view.digest, view.text)
            patches: list[ProsePatch] = []
            title = plan.title or f"第{plan.order + 1}章"
        else:
            title = plan.title or f"第{plan.order + 1}章"
            path = workspace.reserve_chapter_path(title)
            owner = variant.chapter_for_path(path)
            if owner is not None and owner.id != chapter_id:
                raise _path_conflict(path, f"该正文已属于章节 {owner.id}")
            patches = [
                ProsePatch(
                    path=path,
                    base_digest="absent",
                    start=0,
                    end=0,
                    expected="",
                    replacement=f"# {title}\n",
                )
            ]
        operations = [
            ConfirmChapterPlan(chapter_id=chapter_id, revision=expected_chapter_revision),
            SetChapterProsePath(chapter_id=chapter_id, path=path),
        ]
        prepared = StoryChangeSet(
            id=new_id("change"),
            base_revision=variant.revision,
            operations=operations,
            prose_patches=patches,
            requested_scope=f"chapter-start:{chapter_id}",
        )
        return prepared, None, {"path": path}

    receipt = store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        change=initial,
        request_key=request_key,
        kind="chapter_start",
        prepare_joint=prepare,
    )
    path = str(receipt.result.get("path", ""))
    if not path:
        raise _state_error("开始写作的回执没有正文路径")
    view = workspace.read(path)
    variant = store.require_variant(MAIN_VARIANT_ID)
    plan = variant.chapter(chapter_id)
    if plan is None:
        raise _state_error("开始写作后章节构思不存在")
    return ChapterStartResult(
        chapter_id=chapter_id,
        path=path,
        prose_digest=view.digest,
        plan_revision=variant.revision,
        chapter_revision=plan.revision,
        committed_change_id=receipt.change_id,
    )


def save_chapter_prose(
    store: StoryStore,
    workspace: Workspace,
    *,
    chapter_id: str,
    text: str,
    expected_digest: str,
    expected_chapter_revision: str,
    request_key: str,
    draft_revision: str = "",
) -> ProseSaveResult:
    """整章保存；摘要冲突时保留服务端版本，不自动覆盖。"""
    policy = ChapterWritePolicy(store)
    variant = store.require_variant(MAIN_VARIANT_ID)
    if variant.format_version < 3:
        raise _required(chapter_id, "旧格式章节不使用这条保存路径")
    plan = variant.chapter(chapter_id)
    if plan is None:
        raise _required(chapter_id, "章节构思不存在")
    path = plan.prose_path
    if path is None:
        raise _required(chapter_id, "这一章还没有开始写作")
    prior = store.receipt_for(request_key)
    if prior is not None and prior.kind == "prose_save":
        paths = prior.result.get("prose_paths") or []
        if paths:
            return _prose_result(store, workspace, chapter_id, str(paths[0]), prior.change_id)
    policy.require_current_plan(
        variant, chapter_id, expected_chapter_revision=expected_chapter_revision
    )
    view = workspace.read(path)
    if view.digest != expected_digest:
        raise _digest_mismatch(path, view.digest, view.text)
    if view.text == text:
        return _prose_result(store, workspace, chapter_id, path, "")
    normalized = normalize_newlines(text)
    new_digest = digest_bytes(normalized.encode("utf-8"))
    change_text = changed_text(view.text, normalized)
    patch = ProsePatch(
        path=path,
        base_digest=view.digest,
        start=0,
        end=len(view.text),
        expected=view.text,
        replacement=normalized,
    )
    change = StoryChangeSet(
        id=new_id("change"),
        base_revision=variant.revision,
        operations=_refresh_binding_operations(variant, path, normalized, new_digest),
        prose_patches=[patch],
        requested_scope=f"chapter-save:{chapter_id}:{draft_revision}".rstrip(":"),
    )

    def prepare(document: StoryDocument | None) -> tuple[StoryChangeSet, None, dict[str, object]]:
        if document is None:
            raise _required(chapter_id, "章节构思不存在")
        current = document.variants.get(MAIN_VARIANT_ID)
        if current is None:
            raise _required(chapter_id, "章节构思不存在")
        policy.require_current_plan(
            current, chapter_id, expected_chapter_revision=expected_chapter_revision
        )
        return change, None, {}

    receipt = store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        change=change,
        request_key=request_key,
        kind="prose_save",
        prepare_joint=prepare,
    )
    return _prose_result(store, workspace, chapter_id, path, receipt.change_id, change_text)


def _refresh_binding_operations(variant, path: str, text: str, digest: str) -> list[StoryOperation]:
    """正文保存时按唯一引文重定位已有绑定；缺失或歧义保留原引用，由索引显示状态。"""
    operations: list[StoryOperation] = []
    for binding in variant.bindings.values():
        if binding.path != path or binding.move_id is None or binding.digest == digest:
            continue
        anchor = anchor_state(binding, text, digest)
        span = anchor.span
        if span is None:
            continue
        updated = binding.model_copy(
            update={"digest": digest, "start": span.start, "end": span.end}
        )
        operations.append(UnbindProse(binding_id=binding.id))
        operations.append(BindProse(binding=updated))
    return operations


def _prose_result(
    store: StoryStore,
    workspace: Workspace,
    chapter_id: str,
    path: str,
    change_id: str,
    change_text: str = "",
) -> ProseSaveResult:
    variant = store.require_variant(MAIN_VARIANT_ID)
    plan = variant.chapter(chapter_id)
    if plan is None:
        raise _state_error("保存正文后章节构思不存在")
    view = workspace.read(path)
    index = build_chapter_index(variant, plan, path=path, text=view.text, digest=view.digest)
    return ProseSaveResult(
        path=path,
        prose_digest=view.digest,
        plan_revision=variant.revision,
        chapter_revision=plan.revision,
        context_revision=index.context_revision,
        index_revision=index.index_revision,
        index=index,
        analysis_job_id=None,
        committed_change_id=change_id,
        changed_text=change_text,
    )


def _required(chapter_id: str, why: str) -> WorkspaceError:
    return WorkspaceError(
        code="CHAPTER_PLAN_REQUIRED",
        where=f"章节/{chapter_id}",
        why=why,
        fix="先写一句本章变化，或选择「交给模型构思」",
        details={"chapter_id": chapter_id},
    )


def _path_conflict(path: str, why: str) -> WorkspaceError:
    return WorkspaceError(
        code="PROSE_PATH_CONFLICT",
        where=path,
        why=why,
        fix="选择空闲的正文路径，或先解除原章节的正文关联",
        details={"path": path},
    )


def _digest_mismatch(path: str, current_digest: str, current_text: str) -> WorkspaceError:
    return WorkspaceError(
        code="PROSE_DIGEST_MISMATCH",
        where=path,
        why="正文在本次写入之前已被修改",
        fix="比较当前版本与本地草稿后再保存，不要用旧内容覆盖",
        details={
            "path": path,
            "current_digest": current_digest,
            "current_text": current_text,
        },
    )


def _state_error(why: str) -> WorkspaceError:
    return WorkspaceError(
        code="STORY_STATE_FAILED",
        where="story.commit",
        why=why,
        fix="刷新作品状态后重试；若仍失败，检查状态目录是否完整",
    )
