"""历史恢复用例（Phase 14 验收 9；V03-2.5 接入统一发布）。

恢复 = 读源 Commit 的章文本，转成当前 head 上的**一次新发布**，不重写历史：

- 经 ``PublishManuscriptChange`` 走与保存/导入相同的六步管线——证据核对、
  摘要失效、操作记录、回执与正文在同一个提交里（计划 §3.3）；
- 源 Commit 校验前置：章在源 Commit 不存在时归因到「源」而不是 head；
- 副本保护：该章正在手工修改副本里时拒绝恢复，不自动放弃副本；
- 幂等：同一 ``idempotency_key`` 派生同一 ``operation_id``，重试返回原结果。

段落谱系留白：经 ``save_full_text`` 的 diff 规范化恢复，未匹配段落会以新
TextUnit 身份重生（与整章文本保存同一语义）。
"""

from __future__ import annotations

from dataclasses import dataclass

from hastings.application.errors import ViolationError
from hastings.application.handlers.manuscript_commands import manuscript_command
from hastings.application.ports.repository import RepositoryReader
from hastings.application.services.edit_session_service import EditSessionService
from hastings.application.services.publish_manuscript_change import (
    PublishManuscriptChange,
    PublishResult,
)
from hastings.domain.manuscript import error_codes as ms_ec
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.manuscript.commands import save_full_text
from hastings.domain.repository.ids import CommitId, ProjectId
from hastings.domain.repository.serialization import content_hash

__all__ = ["RestoreChapterVersion", "RestoreIntent"]


@dataclass(frozen=True, slots=True)
class RestoreIntent:
    """一次恢复意图：把 ``chapter_id`` 的内容回退到 ``source_commit_id`` 的版本。"""

    project_id: ProjectId
    chapter_id: str
    source_commit_id: CommitId
    idempotency_key: str
    expected_chapter_base: str | None = None


class RestoreChapterVersion:
    """把历史章文本转成当前 head 上的一次统一发布。"""

    def __init__(
        self,
        reader: RepositoryReader,
        publisher: PublishManuscriptChange,
        edit_sessions: EditSessionService | None = None,
    ) -> None:
        self._reader = reader
        self._publisher = publisher
        self._edit_sessions = edit_sessions

    def restore(
        self, intent: RestoreIntent, *, operation_id: str | None = None
    ) -> PublishResult:
        where = f"manuscript.restore({intent.project_id.value}.{intent.chapter_id})"
        source_text = self._source_text(intent, where=where)
        self._reject_edit_session_overlap(intent)
        command = manuscript_command(
            save_full_text,
            chapter_id=intent.chapter_id,
            text=source_text,
            expected_chapter_base=intent.expected_chapter_base,
        )
        return self._publisher.publish_command(
            project_id=intent.project_id,
            command=command,
            operation_id=operation_id or _operation_id(intent),
            kind="chapter.restore",
            summary=f"恢复「{intent.chapter_id}」到 Commit {intent.source_commit_id.value}",
            refs={
                "chapter_id": intent.chapter_id,
                "source_commit_id": intent.source_commit_id.value,
            },
        )

    def _reject_edit_session_overlap(self, intent: RestoreIntent) -> None:
        """副本保护：正在修改的章不能从历史覆盖，避免作者丢掉本轮改动。"""
        if self._edit_sessions is None:
            return
        current = self._edit_sessions.current(intent.project_id.value)
        if current is None:
            return
        drafts = {str(draft["chapter_id"]) for draft in current.get("drafts", [])}
        if intent.chapter_id not in drafts:
            return
        raise ViolationError(
            code=ms_ec.EDIT_SESSION_CONFLICT,
            where=f"manuscript.restore({intent.chapter_id})",
            why="该章正在手工修改副本里，恢复会与未完成的修改冲突",
            fix="先完成或放弃这一轮修改，再恢复历史版本；系统不会自动放弃副本",
            details={"chapter_id": intent.chapter_id},
        )

    def _source_text(self, intent: RestoreIntent, *, where: str) -> str:
        """源 Commit 上该章的整章文本；章不存在时归因到「源」而非 head。"""
        source_where = f"{where}.source"
        snapshot = self._reader.get_snapshot(intent.project_id, intent.source_commit_id)
        catalog = ManuscriptCatalog.from_snapshot(snapshot)
        if catalog.chapter_entry(intent.chapter_id) is None:
            raise ViolationError(
                code=ms_ec.MANUSCRIPT_UNKNOWN_CHAPTER,
                where=source_where,
                why=f"章 {intent.chapter_id!r} 在源 Commit "
                f"{intent.source_commit_id.value} 中不存在",
                fix="改用该章已存在的 Commit（先查版本历史），或确认章 ID 无误",
                details={
                    "chapter_id": intent.chapter_id,
                    "source_commit_id": intent.source_commit_id.value,
                },
            )
        return catalog.manuscript().text_of(intent.chapter_id)


def _operation_id(intent: RestoreIntent) -> str:
    return (
        "op_"
        + content_hash(
            [
                "chapter.restore",
                intent.project_id.value,
                intent.chapter_id,
                intent.source_commit_id.value,
                intent.idempotency_key,
            ]
        )[:16]
    )
