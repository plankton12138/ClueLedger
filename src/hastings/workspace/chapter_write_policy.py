"""正文写入的统一策略（1.0.9 P1；P6 改为全书构思门禁）。

所有应用内正文写入——人工保存、Agent 创建或修改、导入之后的再次编辑——都经过同一个
判断：作品是否已经有一段可继续发展的全书构思。章节没有构思前置，也没有确认状态；
文件层只负责调用，不复制规则，这样 HTTP、工具和页面不会各有一套门槛。

旧格式 2 的作品没有全书构思概念，按原行为放行。新作品写入格式 4，才启用门禁；
没有任何故事状态的作品按新作品处理，第一次写正文前必须先写全书构思。
"""

from __future__ import annotations

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import managed_kind
from hastings.workspace.story_models import FORMAT_VERSION, StoryVariant
from hastings.workspace.story_store import MAIN_VARIANT_ID, StoryStore

__all__ = ["ChapterWritePolicy"]


class ChapterWritePolicy:
    """正文写入前的全书构思检查；只读故事状态，不写文件。"""

    def __init__(self, store: StoryStore) -> None:
        self._store = store

    def current_variant(self) -> StoryVariant | None:
        return self._store.read().variant(MAIN_VARIANT_ID)

    def require_write(self, path: str) -> None:
        if managed_kind(path) != "chapter":
            return
        variant = self.current_variant()
        if variant is None:
            # 还没有任何故事状态：按新作品处理，必须先有全书构思。
            raise _book_intent_required(path)
        if variant.format_version < FORMAT_VERSION:
            return
        self.require_book_intent(variant, where=path)

    def require_move(self, source: str, target: str) -> None:
        """已登记章节的正文路径由建章事务维护，不能直接改文件名。"""
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
            why="这一章的正文路径由章节维护，不能直接改文件名",
            fix="在写作页新建或整理章节；正文路径不能手工改名",
            details={"source": source, "target": target, "chapter_id": chapter.id},
        )

    def require_book_intent(self, variant: StoryVariant, *, where: str) -> None:
        """作品已有可继续发展的全书构思才允许写正文；这是唯一门禁。"""
        if variant.format_version < FORMAT_VERSION:
            return
        intent = variant.book_intent
        if intent is not None and intent.text.strip():
            return
        raise _book_intent_required(where)


def _book_intent_required(where: str) -> WorkspaceError:
    return WorkspaceError(
        code="BOOK_INTENT_REQUIRED",
        where=where,
        why="作品尚无全书构思",
        fix="先写一句作品意图，或选择「交给模型构思」",
        details={"path": where},
    )
