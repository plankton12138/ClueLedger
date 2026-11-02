"""章节正文的统一写入策略（1.0.9 P1）。

所有应用内正文写入——人工保存、Agent 创建或修改、导入之后的再次编辑——都必须经过
同一个判断：这一章是否有已确认的最小构思，写入依据的章修订是否仍是当前修订。文件层
只负责调用，不复制规则；这样 HTTP、工具和页面不会各有一套门槛。

旧格式 2 的作品在此阶段仍按原行为放行；新作品写入格式 3，才启用门槛。没有构思文档的
作品按新作品处理，不能直接创建正文。
"""

from __future__ import annotations

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import managed_kind
from hastings.workspace.story_models import FORMAT_VERSION, StoryVariant
from hastings.workspace.story_store import MAIN_VARIANT_ID, StoryStore

__all__ = ["ChapterWritePolicy"]


class ChapterWritePolicy:
    """正文写入前的最小构思检查；只读故事状态，不写文件。"""

    def __init__(self, store: StoryStore) -> None:
        self._store = store

    def current_variant(self) -> StoryVariant | None:
        return self._store.read().variant(MAIN_VARIANT_ID)

    def require_write(self, path: str, *, expected_chapter_revision: str | None = None) -> None:
        if managed_kind(path) != "chapter":
            return
        variant = self.current_variant()
        if variant is None:
            raise _required(path, "本章还没有章节构思")
        if variant.format_version < FORMAT_VERSION:
            return
        chapter = variant.chapter_for_path(path)
        if chapter is None:
            raise _required(path, "这一章还没有已确认的章节构思")
        problem = variant.chapter_start_problem(chapter.id)
        if problem == "章节构思尚未按当前修订确认":
            raise _stale(path)
        if problem is not None:
            raise _required(path, problem)
        if expected_chapter_revision is not None and chapter.revision != expected_chapter_revision:
            raise _stale(path)

    def require_move(self, source: str, target: str) -> None:
        """已开始的章节不能直接改文件名；未挂计划的导入正文仍可整理。"""
        if managed_kind(source) != "chapter":
            return
        variant = self.current_variant()
        if variant is None or variant.format_version < FORMAT_VERSION:
            return
        chapter = variant.chapter_for_path(source)
        if chapter is None:
            return
        raise WorkspaceError(
            code="PROSE_PATH_CONFLICT",
            where=source,
            why="这一章已经按当前构思开始写作，不能直接改文件名",
            fix="在工作台里调整章节标题；正文路径由开始事务维护",
            details={"source": source, "target": target, "chapter_id": chapter.id},
        )

    def require_current_plan(
        self,
        variant: StoryVariant,
        chapter_id: str,
        *,
        expected_chapter_revision: str,
        where: str | None = None,
    ) -> None:
        """在已取得的故事快照上核对本章；供提交事务与开始事务复用。"""
        label = where or f"章节/{chapter_id}"
        chapter = variant.chapter(chapter_id)
        if chapter is None:
            raise _required(label, "章节构思不存在")
        problem = variant.chapter_start_problem(chapter_id)
        if problem == "章节构思尚未按当前修订确认":
            raise _stale(label)
        if problem is not None:
            raise _required(label, problem)
        if chapter.revision != expected_chapter_revision:
            raise _stale(label)

    def require_start(
        self,
        variant: StoryVariant,
        chapter_id: str,
        *,
        expected_story_revision: str,
        expected_chapter_revision: str,
    ) -> None:
        """开始写作事务内再次核对；调用方必须已经持有故事锁与最新章节快照。"""
        if variant.revision != expected_story_revision:
            raise _stale(f"章节/{chapter_id}")
        chapter = variant.chapter(chapter_id)
        if chapter is None:
            raise _required(f"章节/{chapter_id}", "章节构思不存在")
        problem = variant.chapter_plan_problem(chapter_id)
        if problem is not None:
            raise _required(f"章节/{chapter_id}", problem)
        if chapter.revision != expected_chapter_revision:
            raise _stale(f"章节/{chapter_id}")


def _required(path: str, why: str) -> WorkspaceError:
    return WorkspaceError(
        code="CHAPTER_PLAN_REQUIRED",
        where=path,
        why=why,
        fix="先写一句本章变化，或选择「交给模型构思」",
        details={"path": path},
    )


def _stale(path: str) -> WorkspaceError:
    return WorkspaceError(
        code="CHAPTER_PLAN_STALE",
        where=path,
        why="本章构思在上次确认后已经变化",
        fix="重新读取当前摘要并保留作者草稿，确认后再写入",
        details={"path": path},
    )
