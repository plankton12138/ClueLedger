"""章节文件的确定性写入策略（1.0.9 P1；1.2 移除语义门槛）。

正文写入不依赖额外摘要或确认状态。这里只保留能从事实层确定的文件规则：已登记章节
的正文路径由章节事务维护，不能绕过事务直接改名。
"""

from __future__ import annotations

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import managed_kind
from hastings.workspace.story_models import FORMAT_VERSION, StoryVariant
from hastings.workspace.story_store import MAIN_VARIANT_ID, StoryStore

__all__ = ["ChapterWritePolicy"]


class ChapterWritePolicy:
    """章节路径保护；只读故事状态，不写文件。"""

    def __init__(self, store: StoryStore) -> None:
        self._store = store

    def current_variant(self) -> StoryVariant | None:
        return self._store.read().variant(MAIN_VARIANT_ID)

    def require_write(self, path: str) -> None:
        """正文内容没有全局语义门槛；保留方法以兼容文件层调用。"""
        del path

    def require_move(self, source: str, target: str) -> None:
        """已登记章节的正文路径由建章事务维护，不能直接改文件名。"""
        if managed_kind(source) != "chapter":
            return
        variant = self.current_variant()
        if variant is None or variant.format_version != FORMAT_VERSION:
            return
        chapter = variant.chapter_for_path(source)
        if chapter is None:
            return
        raise WorkspaceError(
            code="PROSE_PATH_CONFLICT",
            where=source,
            why="这一章的正文路径由章节维护，不能直接改文件名",
            fix="在写作页新建或整理章节；正文路径不能手工改名",
            details={"source": source, "target": target, "chapter_id": chapter.id},
        )
