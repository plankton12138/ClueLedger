"""整章正文保存（1.0.9 P1；1.2 移除全局语义门槛）。

正文保存只依赖章节登记与正文摘要，章节没有确认状态，也没有章节修订。并发由
正文摘要控制：``expected_digest`` 与磁盘不一致时保留作者草稿并返回当前版本，不自动
选择任一方。保存与绑定重定位在同一次联合提交内完成，索引随后按新正文重建。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from hastings.workspace.chapter_clues import ClueEntry, ClueFinding
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace, digest_bytes, normalize_newlines
from hastings.workspace.revision_store import RevisionStore, record_change_revision
from hastings.workspace.story_bindings import anchor_state
from hastings.workspace.story_index_jobs import changed_text
from hastings.workspace.story_models import (
    FORMAT_VERSION,
    BindProse,
    ProsePatch,
    StoryChangeSet,
    StoryDocument,
    StoryOperation,
    StoryVariant,
    UnbindProse,
)
from hastings.workspace.story_store import MAIN_VARIANT_ID, StoryStore, new_id
from hastings.workspace.writing_view import ChapterInformationIndex, build_chapter_index

__all__ = ["ProseSaveResult", "save_chapter_prose"]


@dataclass(frozen=True)
class ProseSaveResult:
    path: str
    prose_digest: str
    plan_revision: str
    context_revision: str
    index_revision: str
    index: ChapterInformationIndex
    analysis_job_id: str | None
    committed_change_id: str
    changed_text: str = ""
    revision_id: str = ""
    clue_findings: tuple[ClueFinding, ...] = ()


def save_chapter_prose(
    store: StoryStore,
    workspace: Workspace,
    *,
    chapter_id: str,
    text: str,
    expected_digest: str,
    request_key: str,
    draft_revision: str = "",
    source: str = "author",
    candidate_ref: str = "",
    clue_entries: list[ClueEntry] | None = None,
) -> ProseSaveResult:
    """整章保存；只校验章节登记与正文摘要。

    ``source`` 区分作者亲手保存与采用模型候选：修订记录据此显示来源，学习范围
    仍由作者后续的「让模型分析」决定。``clue_entries`` 随同一笔提交登记本章线索
    （v1.3）：必须锚定本章正文段落，空响与非法状态在提交门确定性拦截。
    """
    variant = store.require_variant(MAIN_VARIANT_ID)
    if variant.format_version != FORMAT_VERSION:
        raise _format_error(chapter_id)
    plan = variant.chapter(chapter_id)
    if plan is None:
        raise _chapter_missing(chapter_id)
    path = plan.prose_path
    if path is None:
        raise _prose_missing(chapter_id)
    prior = store.receipt_for(request_key)
    if prior is not None and prior.kind == "prose_save":
        paths = cast(list[object], prior.result.get("prose_paths") or [])
        if paths:
            revision = record_change_revision(
                store,
                change_id=prior.change_id,
                chapter_id=chapter_id,
                path=str(paths[0]),
                revisions=RevisionStore(store.root),
            )
            return _prose_result(
                store,
                workspace,
                chapter_id,
                str(paths[0]),
                prior.change_id,
                revision_id=revision.id if revision is not None else "",
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
            raise _chapter_missing(chapter_id)
        current = document.variants.get(MAIN_VARIANT_ID)
        if current is None:
            raise _chapter_missing(chapter_id)
        if current.format_version != FORMAT_VERSION:
            raise _format_error(chapter_id)
        if current.chapter(chapter_id) is None:
            raise _chapter_missing(chapter_id)
        return change, None, {}

    revision_extra: dict[str, object] = {"revision_source": source}
    if candidate_ref:
        revision_extra["revision_candidate_ref"] = candidate_ref
    receipt = store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        change=change,
        request_key=request_key,
        kind="prose_save",
        result_extra=revision_extra,
        prepare_joint=prepare,
        clue_entries=clue_entries,
    )
    revision = record_change_revision(
        store,
        change_id=receipt.change_id,
        chapter_id=chapter_id,
        path=path,
        revisions=RevisionStore(store.root),
    )
    return _prose_result(
        store,
        workspace,
        chapter_id,
        path,
        receipt.change_id,
        change_text,
        revision_id=revision.id if revision is not None else "",
    )


def _refresh_binding_operations(
    variant: StoryVariant, path: str, text: str, digest: str
) -> list[StoryOperation]:
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
    *,
    revision_id: str = "",
) -> ProseSaveResult:
    variant = store.require_variant(MAIN_VARIANT_ID)
    plan = variant.chapter(chapter_id)
    if plan is None:
        raise _state_error("保存正文后章节不存在")
    view = workspace.read(path)
    index = build_chapter_index(variant, plan, path=path, text=view.text, digest=view.digest)
    return ProseSaveResult(
        path=path,
        prose_digest=view.digest,
        plan_revision=variant.revision,
        context_revision=index.context_revision,
        index_revision=index.index_revision,
        index=index,
        analysis_job_id=None,
        committed_change_id=change_id,
        changed_text=change_text,
        revision_id=revision_id,
        clue_findings=_clue_findings(workspace.root, path, view.text),
    )


def _clue_findings(root: Path, path: str, text: str) -> tuple[ClueFinding, ...]:
    """本次保存后该章线索的锚失联报告（只报告，不重建）。

    修订改动了被锚定的段落时，按内容哈希派生的锚就对不上了；这里把失联事实
    随保存结果返回，由界面/下一回合提示重锚，不静默重建、也不拦截保存。
    """
    from hastings.workspace.chapter_clues import anchor_findings, read_chapter_clues
    from hastings.workspace.structure_assembly import chapter_number_of

    number = chapter_number_of(path)
    if number is None:
        return ()
    try:
        document = read_chapter_clues(root, number)
    except WorkspaceError:
        return ()
    if document is None:
        return ()
    return tuple(anchor_findings(number, document.entries, text))


def _chapter_missing(chapter_id: str) -> WorkspaceError:
    return WorkspaceError(
        code="CHAPTER_NOT_FOUND",
        where=f"章节/{chapter_id}",
        why="章节不存在",
        fix="刷新工作台后重新选择章节",
        details={"chapter_id": chapter_id},
    )


def _prose_missing(chapter_id: str) -> WorkspaceError:
    return WorkspaceError(
        code="CHAPTER_PROSE_NOT_FOUND",
        where=f"章节/{chapter_id}",
        why="这一章还没有正文文件",
        fix="重新新建章节；正文文件由建章事务一起创建",
        details={"chapter_id": chapter_id},
    )


def _format_error(chapter_id: str) -> WorkspaceError:
    return WorkspaceError(
        code="STORY_FORMAT_UNSUPPORTED",
        where=f"章节/{chapter_id}",
        why="这份作品仍使用旧构思格式",
        fix="新建作品后使用写作工作台；本版不迁移旧数据",
        details={"chapter_id": chapter_id},
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
