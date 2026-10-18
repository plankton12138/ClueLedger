"""基础关联撤销（V03-4.6）：先回答「能不能撤、为什么不能」，再谈写入。

撤销只能沿着**操作记录**走：它记了 ``before_commit_id`` 与本操作影响的章节，
after 版本就是携带记录的那个提交。没有操作记录的写入（例如账本单存、候选采用）
不在本模块范围内——它们没有「一次操作」的因果边界。

本刀只做**只读预览与冲突判定**，不写任何版本：

- 该操作之后同一章又被改过 ⇒ ``conflict``，只展示事实，绝不覆盖新内容；
- 操作同时改了账本 ⇒ 明确告知「本版撤销只覆盖正文」，不提供半边恢复；
- 作品存在未结束的手工修改副本 ⇒ 阻断，先让作者结束或放弃副本。

写入（一次提交同时恢复正文与关联账本）需要统一发布器支持多章合并，放在 4.6 后半。
"""

from __future__ import annotations

from typing import Any

from hastings.application.errors import ViolationError
from hastings.application.services.commit_service import CommitService
from hastings.application.services.edit_session_service import EditSessionService
from hastings.application.services.operation_service import OperationService
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.manuscript.operation_record import OperationRecord
from hastings.domain.repository.ids import ArtifactId, CommitId, ProjectId
from hastings.domain.story import kinds as story_kinds
from hastings.domain.story.catalog import StoryCatalog

__all__ = ["UndoService"]


def _violation(code: str, where: str, why: str, fix: str, **details: Any) -> ViolationError:
    return ViolationError(code=code, where=where, why=why, fix=fix, details=details)


class UndoService:
    def __init__(
        self,
        repo: CommitService,
        operations: OperationService,
        edit_sessions: EditSessionService | None = None,
    ) -> None:
        self._repo = repo
        self._operations = operations
        self._edit_sessions = edit_sessions

    def preview(self, project_id: str, operation_id: str) -> dict[str, Any]:
        """撤销预览：受影响章节、账本变化、冲突与阻断原因。"""
        pid = ProjectId(project_id)
        envelope = self._operations.get_operation(pid, operation_id)
        if envelope.commit_id is None:
            return self._blocked(pid, operation_id, "该操作没有产生版本变化，没有可撤销的内容")
        after = CommitId(envelope.commit_id)
        record_view = self._repo.get_artifact(ArtifactId(f"art_{operation_id}"), after)
        if record_view is None or record_view.revision is None:
            return self._blocked(pid, operation_id, "找不到这次操作的记录，无法判定撤销范围")
        record = OperationRecord.from_wire(record_view.revision.content)
        if record.before_commit_id is None:
            return self._blocked(pid, operation_id, "这是作品的首次提交，之前没有可恢复的版本")
        before = CommitId(record.before_commit_id)

        before_catalog = self._manuscript(pid, before)
        after_catalog = self._manuscript(pid, after)
        head = self._head(pid)
        head_catalog = self._manuscript(pid, head)

        chapters = [
            self._chapter_row(cid, before_catalog, after_catalog, head_catalog)
            for cid in sorted(record.changed_chapter_ids)
        ]
        ledger = self._ledger_rows(pid, before, after, head)

        conflicts = [
            f"{row['title'] or row['chapter_id']}：之后又被修改过"
            for row in chapters
            if row["status"] == "conflict"
        ]
        unsupported = [row for row in chapters if row["status"] == "created"]
        ledger_conflicts = [row for row in ledger if row["status"] == "conflict"]

        blocked_reason = ""
        if self._edit_sessions is not None and self._edit_sessions.current(project_id):
            blocked_reason = "作品有未结束的手工修改副本；先完成或放弃副本，再撤销"
        elif conflicts or ledger_conflicts:
            blocked_reason = "该操作之后有新的修改；撤销会覆盖这些内容，本版只展示事实不做写入"
        elif unsupported:
            blocked_reason = "这次操作新建了章节；删除章节不是本版提供的操作"
        elif ledger:
            blocked_reason = "该操作同时改了账本；本版撤销只覆盖正文，不做半边恢复"
        elif not chapters:
            blocked_reason = "这次操作没有可识别的正文变化"

        return {
            "operation_id": operation_id,
            "before_commit_id": before.value,
            "after_commit_id": after.value,
            "chapters": chapters,
            "ledger": ledger,
            "conflicts": [*conflicts, *[row["why"] for row in ledger_conflicts]],
            "can_undo": not blocked_reason,
            "blocked_reason": blocked_reason,
            "note": "撤销写入随 4.6 后半交付；本接口只报告事实",
        }

    # -- 内部 ---------------------------------------------------------------
    def _blocked(self, pid: ProjectId, operation_id: str, reason: str) -> dict[str, Any]:
        del pid
        return {
            "operation_id": operation_id,
            "before_commit_id": None,
            "after_commit_id": None,
            "chapters": [],
            "ledger": [],
            "conflicts": [],
            "can_undo": False,
            "blocked_reason": reason,
            "note": "撤销写入随 4.6 后半交付；本接口只报告事实",
        }

    def _head(self, pid: ProjectId) -> CommitId | None:
        branch = self._repo.get_branch(pid, "main")
        return branch.head_commit_id if branch is not None else None

    def _manuscript(self, pid: ProjectId, commit: CommitId | None) -> ManuscriptCatalog:
        if commit is None:
            return ManuscriptCatalog(commit_id=None, entries=())
        return ManuscriptCatalog.from_snapshot(self._repo.get_snapshot(pid, commit))

    def _chapter_row(
        self,
        chapter_id: str,
        before: ManuscriptCatalog,
        after: ManuscriptCatalog,
        head: ManuscriptCatalog,
    ) -> dict[str, Any]:
        entry = after.chapter_entry(chapter_id)
        title = str(entry.model().title) if entry is not None else ""
        before_entry = before.chapter_entry(chapter_id)
        after_base = after.chapter_base(chapter_id) if entry is not None else ""
        before_base = before.chapter_base(chapter_id) if before_entry is not None else ""
        head_entry = head.chapter_entry(chapter_id)
        current_base = head.chapter_base(chapter_id) if head_entry is not None else ""
        if before_entry is None:
            status = "created"
        elif current_base != after_base:
            status = "conflict"
        else:
            status = "ok"
        return {
            "chapter_id": chapter_id,
            "title": title,
            "before_base": before_base,
            "after_base": after_base,
            "current_base": current_base,
            "chars_before": self._chars(before, chapter_id),
            "chars_after": self._chars(after, chapter_id),
            "status": status,
        }

    @staticmethod
    def _chars(catalog: ManuscriptCatalog, chapter_id: str) -> int:
        if catalog.chapter_entry(chapter_id) is None:
            return 0
        return sum(len(unit.model().text) for unit in catalog.units_of(chapter_id))

    def _ledger_rows(
        self, pid: ProjectId, before: CommitId, after: CommitId, head: CommitId | None
    ) -> list[dict[str, Any]]:
        """对比两次快照里的故事对象：只报告，不纳入本版撤销。"""
        before_map = self._story_revisions(pid, before)
        after_map = self._story_revisions(pid, after)
        head_map = self._story_revisions(pid, head)
        rows: list[dict[str, Any]] = []
        for artifact_id in sorted(set(before_map) | set(after_map)):
            if before_map.get(artifact_id) == after_map.get(artifact_id):
                continue
            entry = after_map.get(artifact_id)
            rows.append(
                {
                    "artifact_id": artifact_id,
                    "kind": entry[0] if entry else "",
                    "title": entry[1] if entry else artifact_id,
                    "status": "conflict"
                    if head_map.get(artifact_id) != after_map.get(artifact_id)
                    else "ok",
                    "why": "账本条目在该操作之后又被修改过"
                    if head_map.get(artifact_id) != after_map.get(artifact_id)
                    else "",
                }
            )
        return rows

    def _story_revisions(
        self, pid: ProjectId, commit: CommitId | None
    ) -> dict[str, tuple[str, str]]:
        if commit is None:
            return {}
        catalog = StoryCatalog.from_snapshot(self._repo.get_snapshot(pid, commit))
        return {
            entry.artifact_id: (
                entry.kind,
                str(
                    entry.content.get("name") or entry.content.get("statement") or entry.artifact_id
                ),
            )
            for entry in catalog.entries
            if entry.kind in story_kinds.CANON_KINDS
        }
