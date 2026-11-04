"""章节创建与改名的写入动作（1.0.9 P2；1.2 移除全局摘要门槛）。

页面只发送自然语言与插入位置；服务端补齐标识、顺序与正文路径。所有动作都走现有
``StoryStore`` 提交，不新建状态文件。章节没有构思输入：建章事务在同一个联合提交里
登记章节、分配路径并创建只含标题的正文文件，失败时两样一起回滚。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace, managed_kind
from hastings.workspace.story_models import (
    FORMAT_VERSION,
    AddChapterPlan,
    ChapterPlan,
    ProsePatch,
    StoryChangeSet,
    StoryDocument,
    StoryOperation,
    StoryReceipt,
    UpdateChapterPlan,
)
from hastings.workspace.story_store import EMPTY_REVISION, MAIN_VARIANT_ID, StoryStore, new_id

__all__ = [
    "ChapterCreateResult",
    "ChapterRenameResult",
    "ProseRenameResult",
    "adopt_prose",
    "create_chapter",
    "rename_chapter",
    "rename_unplanned_prose",
]


@dataclass(frozen=True)
class ChapterCreateResult:
    chapter_id: str
    order: int
    path: str
    prose_digest: str
    plan_revision: str
    receipt: StoryReceipt


@dataclass(frozen=True)
class ChapterRenameResult:
    chapter_id: str
    title: str | None
    plan_revision: str
    receipt: StoryReceipt


@dataclass(frozen=True)
class ProseRenameResult:
    path: str
    title: str
    digest: str


def create_chapter(
    store: StoryStore,
    workspace: Workspace,
    *,
    title: str | None,
    after_chapter_id: str | None,
    expected_plan_revision: str,
    request_key: str,
) -> ChapterCreateResult:
    """在指定章节之后插入一章；同一事务分配正文路径并创建初始文件。"""
    prior = store.receipt_for(request_key)
    if prior is not None and prior.kind == "chapter_create":
        return _chapter_result_from_receipt(store, workspace, prior)
    variant = store.read().variant(MAIN_VARIANT_ID)
    if variant is not None and variant.format_version != FORMAT_VERSION:
        raise _format_error()
    current_revision = variant.revision if variant is not None else EMPTY_REVISION
    if current_revision != expected_plan_revision:
        raise _stale("作品构思")

    plans = list(variant.chapter_plans.values()) if variant is not None else []
    if after_chapter_id is not None:
        if variant is None:
            raise WorkspaceError(
                code="CHAPTER_NOT_FOUND",
                where=f"章节/{after_chapter_id}",
                why="用于定位的章节不存在",
                fix="刷新目录后重新选择插入位置",
                details={"chapter_id": after_chapter_id},
            )
        anchor = variant.chapter(after_chapter_id)
        if anchor is None:
            raise WorkspaceError(
                code="CHAPTER_NOT_FOUND",
                where=f"章节/{after_chapter_id}",
                why="用于定位的章节不存在",
                fix="刷新目录后重新选择插入位置",
                details={"chapter_id": after_chapter_id},
            )
        order = anchor.order + 1
        shifted = [plan for plan in plans if plan.order >= order]
    else:
        order = max((plan.order for plan in plans), default=-1) + 1
        shifted = []
    chapter_id = new_id("ch")
    clean_title = (title or "").strip() or None
    display_title = clean_title or f"第{order + 1}章"
    path = workspace.reserve_chapter_path(display_title)
    plan = ChapterPlan(id=chapter_id, order=order, title=clean_title, prose_path=path)
    operations: list[StoryOperation] = [AddChapterPlan(plan=plan)]
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
        prose_patches=[
            ProsePatch(
                path=path,
                base_digest="absent",
                start=0,
                end=0,
                expected="",
                replacement=f"# {display_title}\n",
            )
        ],
        requested_scope=f"chapter-create:{chapter_id}",
    )

    def prepare(document: StoryDocument | None) -> tuple[StoryChangeSet, None, dict[str, object]]:
        if document is None:
            if expected_plan_revision != EMPTY_REVISION:
                raise _stale("作品构思")
            return change, None, {"path": path, "chapter_id": chapter_id, "order": order}
        current = document.variants.get(MAIN_VARIANT_ID)
        if current is None:
            raise _state_error("作品状态存在但缺少主方案")
        if current.format_version != FORMAT_VERSION:
            raise _format_error()
        if current.revision != expected_plan_revision:
            raise _stale("作品构思")
        return change, None, {"path": path, "chapter_id": chapter_id, "order": order}

    receipt = store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        change=change,
        request_key=request_key,
        kind="chapter_create",
        prepare_joint=prepare,
    )
    updated = store.require_variant(MAIN_VARIANT_ID)
    created = updated.chapter(chapter_id)
    if created is None or created.prose_path is None:
        raise _state_error("创建章节后找不到章节或正文路径")
    view = workspace.read(created.prose_path)
    return ChapterCreateResult(
        chapter_id=chapter_id,
        order=created.order,
        path=created.prose_path,
        prose_digest=view.digest,
        plan_revision=updated.revision,
        receipt=receipt,
    )


def adopt_prose(
    store: StoryStore,
    workspace: Workspace,
    *,
    path: str,
    expected_plan_revision: str,
    request_key: str,
) -> ChapterCreateResult:
    """把未登记的正文并入章节计划；正文原样保留，只补一条章节条目。

    模型直接写进 ``正文/`` 的文件、或作者从别处拷入的稿件，不会自动出现在计划里，
    因此不能修订、建索引或参与分析。登记用文件名里的章序定位；该位置已被占用时
    排到末尾，不改动已有章节的先后关系。
    """
    prior = store.receipt_for(request_key)
    if prior is not None and prior.kind == "chapter_adopt":
        return _chapter_result_from_receipt(store, workspace, prior)
    variant = store.read().variant(MAIN_VARIANT_ID)
    if variant is not None and variant.format_version != FORMAT_VERSION:
        raise _format_error()
    current_revision = variant.revision if variant is not None else EMPTY_REVISION
    if current_revision != expected_plan_revision:
        raise _stale("作品构思")
    if variant is not None and variant.chapter_for_path(path) is not None:
        raise _already_adopted(path)
    entry = next((item for item in workspace.catalog().chapters if item.path == path), None)
    if entry is None or not entry.exists:
        raise WorkspaceError(
            code="PROSE_NOT_FOUND",
            where=path,
            why="这份正文不在作品目录里",
            fix="刷新目录后重新选择正文",
            details={"path": path},
        )

    plans = list(variant.chapter_plans.values()) if variant is not None else []
    taken = {plan.order for plan in plans}
    preferred = (entry.chapter_number or 0) - 1
    order = preferred if preferred >= 0 and preferred not in taken else max(taken, default=-1) + 1
    chapter_id = new_id("ch")
    plan = ChapterPlan(
        id=chapter_id,
        order=order,
        title=_clean_chapter_title(entry.title),
        prose_path=path,
    )
    change = StoryChangeSet(
        id=new_id("change"),
        base_revision=expected_plan_revision,
        operations=[AddChapterPlan(plan=plan)],
        requested_scope=f"chapter-adopt:{path}",
    )
    meta = {"path": path, "chapter_id": chapter_id, "order": order}

    def prepare(document: StoryDocument | None) -> tuple[StoryChangeSet, None, dict[str, object]]:
        if document is None:
            if expected_plan_revision != EMPTY_REVISION:
                raise _stale("作品构思")
            return change, None, meta
        current = document.variants.get(MAIN_VARIANT_ID)
        if current is None:
            raise _state_error("作品状态存在但缺少主方案")
        if current.format_version != FORMAT_VERSION:
            raise _format_error()
        if current.revision != expected_plan_revision:
            raise _stale("作品构思")
        if current.chapter_for_path(path) is not None:
            raise _already_adopted(path)
        return change, None, meta

    receipt = store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        change=change,
        request_key=request_key,
        kind="chapter_adopt",
        prepare_joint=prepare,
    )
    updated = store.require_variant(MAIN_VARIANT_ID)
    created = updated.chapter(chapter_id)
    if created is None:
        raise _state_error("登记正文后找不到章节")
    view = workspace.read(path)
    return ChapterCreateResult(
        chapter_id=chapter_id,
        order=created.order,
        path=path,
        prose_digest=view.digest,
        plan_revision=updated.revision,
        receipt=receipt,
    )


def rename_chapter(
    store: StoryStore,
    workspace: Workspace,
    *,
    chapter_id: str,
    title: str | None,
    expected_plan_revision: str,
    request_key: str,
) -> ChapterRenameResult:
    """修改章节标题；正文首行仍是旧标题时一并替换，其余正文不动。"""
    prior = store.receipt_for(request_key)
    if prior is not None and prior.kind == "chapter_rename":
        return _rename_result_from_receipt(store, prior, chapter_id)
    variant = store.read().variant(MAIN_VARIANT_ID)
    if variant is not None and variant.format_version != FORMAT_VERSION:
        raise _format_error()
    if variant is None:
        raise _chapter_missing(chapter_id)
    if variant.revision != expected_plan_revision:
        raise _stale("作品构思")
    plan = variant.chapter(chapter_id)
    if plan is None:
        raise _chapter_missing(chapter_id)

    clean_title = (title or "").strip() or None
    display_title = clean_title or f"第{plan.order + 1}章"
    operations: list[StoryOperation] = [
        UpdateChapterPlan(
            chapter_id=chapter_id,
            plan=ChapterPlan(
                id=chapter_id,
                order=plan.order,
                title=clean_title,
                prose_path=plan.prose_path,
            ),
        )
    ]
    patches = _heading_patch(workspace, plan, display_title)
    change = StoryChangeSet(
        id=new_id("change"),
        base_revision=expected_plan_revision,
        operations=operations,
        prose_patches=patches,
        requested_scope=f"chapter-rename:{chapter_id}",
    )
    receipt = store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        change=change,
        request_key=request_key,
        kind="chapter_rename",
    )
    updated = store.require_variant(MAIN_VARIANT_ID)
    renamed = updated.chapter(chapter_id)
    if renamed is None:
        raise _state_error("修改章节名后找不到章节")
    return ChapterRenameResult(
        chapter_id=chapter_id,
        title=renamed.title,
        plan_revision=updated.revision,
        receipt=receipt,
    )


def _heading_patch(workspace: Workspace, plan: ChapterPlan, display_title: str) -> list[ProsePatch]:
    """只有正文确实以旧章节标题开头时才替换首行；作者改过的开头保持原样。"""
    if plan.prose_path is None:
        return []
    try:
        view = workspace.read(plan.prose_path)
    except WorkspaceError:
        return []
    old_heading = f"# {plan.title or f'第{plan.order + 1}章'}"
    if not view.text.startswith(old_heading):
        return []
    following = view.text[len(old_heading) : len(old_heading) + 1]
    if following and following not in ("\n", "\r"):
        return []
    return [
        ProsePatch(
            path=view.path,
            base_digest=view.digest,
            start=0,
            end=len(old_heading),
            expected=old_heading,
            replacement=f"# {display_title}",
        )
    ]


def _rename_result_from_receipt(
    store: StoryStore, receipt: StoryReceipt, chapter_id: str
) -> ChapterRenameResult:
    variant = store.require_variant(MAIN_VARIANT_ID)
    plan = variant.chapter(chapter_id)
    if plan is None:
        raise _state_error("重复的改名请求找不到原章节")
    return ChapterRenameResult(
        chapter_id=chapter_id,
        title=plan.title,
        plan_revision=variant.revision,
        receipt=receipt,
    )


def _chapter_missing(chapter_id: str) -> WorkspaceError:
    return WorkspaceError(
        code="CHAPTER_NOT_FOUND",
        where=f"章节/{chapter_id}",
        why="章节不存在",
        fix="刷新目录后重新选择章节",
        details={"chapter_id": chapter_id},
    )


def rename_unplanned_prose(
    workspace: Workspace, *, path: str, title: str, expected_digest: str
) -> ProseRenameResult:
    """只改未登记章节的正文文件首行标题；文件名与其他内容保持原样。"""
    clean = title.strip()
    if not clean:
        raise WorkspaceError(
            code="PROSE_TITLE_REQUIRED",
            where=path,
            why="新的章节名不能为空",
            fix="填写一个非空的章节名后重试",
        )
    if managed_kind(path) != "chapter":
        raise WorkspaceError(
            code="FILE_KIND_MISMATCH",
            where=path,
            why="只有正文文件可以直接改名",
            fix="在写作页选择正文后再改名",
        )
    view = workspace.read(path)
    lines = view.text.splitlines(keepends=True)
    index = next((i for i, line in enumerate(lines) if line.strip()), None)
    if index is not None and lines[index].lstrip().startswith("# "):
        ending = ""
        if lines[index].endswith("\r\n"):
            ending = "\r\n"
        elif lines[index].endswith("\n"):
            ending = "\n"
        replacement = f"# {clean}{ending}"
        if lines[index] == replacement:
            return ProseRenameResult(path=view.path, title=clean, digest=view.digest)
        lines[index] = replacement
        text = "".join(lines)
    else:
        text = f"# {clean}\n\n{view.text}" if view.text else f"# {clean}\n"
    updated = workspace.write(path, text, expected_digest=expected_digest)
    return ProseRenameResult(path=updated.path, title=clean, digest=updated.digest)


def _chapter_result_from_receipt(
    store: StoryStore, workspace: Workspace, receipt: StoryReceipt
) -> ChapterCreateResult:
    chapter_id = str(receipt.result.get("chapter_id", ""))
    path = str(receipt.result.get("path", ""))
    variant = store.require_variant(MAIN_VARIANT_ID)
    plan = variant.chapter(chapter_id)
    if plan is None or not path:
        raise _state_error("重复的建章请求找不到原章节")
    view = workspace.read(path)
    return ChapterCreateResult(
        chapter_id=chapter_id,
        order=plan.order,
        path=path,
        prose_digest=view.digest,
        plan_revision=variant.revision,
        receipt=receipt,
    )


def _format_error() -> WorkspaceError:
    return WorkspaceError(
        code="STORY_FORMAT_UNSUPPORTED",
        where="写作工作台",
        why="这份作品使用已退出的旧格式",
        fix="新建作品后使用当前写作流程；本版不迁移旧数据",
    )


def _stale(where: str) -> WorkspaceError:
    return WorkspaceError(
        code="STORY_CHANGE_CONFLICT",
        where=where,
        why="作品构思在保存前已经变化",
        fix="重新读取当前作品后再保存",
    )


def _already_adopted(path: str) -> WorkspaceError:
    return WorkspaceError(
        code="PROSE_ALREADY_ADOPTED",
        where=path,
        why="这份正文已经登记为章节",
        fix="刷新目录后直接从章节进入修订",
        details={"path": path},
    )


#: 首行里的章序前缀；目录已经按顺序显示，标题里不必再重复一次。
_CHAPTER_NUMBER_PREFIX = re.compile(
    r"^第\s*(?:[0-9０-９]+|[一二三四五六七八九十百千零〇两]+)\s*[章回节篇]\s*"
)


def _clean_chapter_title(value: str) -> str | None:
    """去掉「第X章」前缀，避免目录显示成「第1章 · 第一章 拔牙人进镇」。"""
    cleaned = _CHAPTER_NUMBER_PREFIX.sub("", value or "").strip()
    return cleaned or None


def _state_error(why: str) -> WorkspaceError:
    return WorkspaceError(
        code="STORY_STATE_FAILED",
        where="story.commit",
        why=why,
        fix="刷新作品状态后重试；若仍失败，检查状态目录是否完整",
    )
