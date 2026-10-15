"""历史恢复用例（Phase 14 验收 9）：恢复 = 一次新的正文提交，不重写历史。

「版本历史可查看和恢复为新 Commit」（计划 §T14.8）的写路径。设计取舍：

- **不移动分支、不重写 Commit 链**：恢复读源 Commit 的章文本，转成本次 head 上的
  一次 ``save_full_text`` 提交——与人工保存走同一条命令、同一套乐观并发检查与
  幂等重放（``expected_chapter_base`` 冲突照样拒绝，恢复不能绕过并发保护）。
  旧版本永远在历史里，恢复本身也产生新历史，双向可追溯；
- **源 Commit 校验前置**：章在源 Commit 不存在时给出指向「源」的违规（哪里错 =
  restore.source），而不是让 ``save_full_text`` 在 head 上误报「章不存在」——
  同一个错误码，两种错因必须可区分（四透镜①）。

段落谱系留白：经 ``save_full_text`` 的 diff 规范化恢复，未匹配段落会以新 TextUnit
身份重生（与整章文本保存同一语义）；按 Revision 逐一恢复属后续增强，不在本阶段。
"""

from __future__ import annotations

from dataclasses import dataclass

from hastings.application.errors import ViolationError
from hastings.application.handlers.manuscript_commands import (
    ManuscriptCommandHandler,
    ManuscriptCommandResult,
    manuscript_command,
)
from hastings.application.ports.repository import RepositoryReader
from hastings.domain.manuscript import error_codes as ms_ec
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.manuscript.commands import save_full_text
from hastings.domain.repository.ids import CommitId, ProjectId

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
    """把历史章文本转成当前 head 上的一次保存提交。"""

    def __init__(self, handler: ManuscriptCommandHandler, reader: RepositoryReader) -> None:
        self._handler = handler
        self._reader = reader

    def restore(self, intent: RestoreIntent) -> ManuscriptCommandResult:
        where = f"manuscript.restore({intent.project_id.value}.{intent.chapter_id})"
        source_text = self._source_text(intent, where=where)
        return self._handler.execute(
            manuscript_command(
                save_full_text,
                chapter_id=intent.chapter_id,
                text=source_text,
                expected_chapter_base=intent.expected_chapter_base,
            ),
            project_id=intent.project_id,
            idempotency_key=intent.idempotency_key,
            summary=f"恢复「{intent.chapter_id}」到 Commit {intent.source_commit_id.value}",
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
