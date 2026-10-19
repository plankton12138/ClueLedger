"""操作影响查询（V03-7.7）：按实际版本差异回读这次操作改了什么。

「影响高亮」只说明**版本差异**，不是修订完成度、质量分数或嫌疑概率：

- 章节：操作前/操作后文本的差异统计（hunk 数、增删字符数），以及操作后到当前
  head 是否又被改过；只列真正有文本差异的章；
- 账本：操作前/操作后条目内容里实际变化的字段名，以及之后是否被独立修改；
- 关联采用：基于这次操作采用账本建议产生的独立提交（V03-7.7 因果关联），
  只列出来，不把它们的内容混进本操作的差异；
- 继续创作：列出本次实际改过的章，并说明是否有后续修改/未结束副本。

范围来自操作记录与实际 Commit 差异（``diff``），不接受调用方自定义「影响范围」。
"""

from __future__ import annotations

import difflib
from typing import Any

from hastings.application.queries.ledger import entry_view
from hastings.application.services.commit_service import CommitService
from hastings.application.services.edit_session_service import EditSessionService
from hastings.application.services.operation_service import OperationService
from hastings.application.services.revision_links import read_acceptances
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.manuscript.operation_record import OperationRecord
from hastings.domain.repository.ids import ArtifactId, CommitId, ProjectId
from hastings.domain.story import kinds as story_kinds
from hastings.domain.story.catalog import StoryCatalog

__all__ = ["ImpactQueries"]

#: 高亮的唯一依据；写进响应，避免前端把它读成质量评分。
IMPACT_BASIS = "version_diff"
IMPACT_NOTE = (
    "高亮只表示这次操作前后的实际版本差异；不代表修订完成、质量分数或嫌疑概率。"
)


class ImpactQueries:
    """只读：操作影响、关联采用与继续创作上下文。"""

    def __init__(
        self,
        repo: CommitService,
        operations: OperationService,
        edit_sessions: EditSessionService | None = None,
    ) -> None:
        self._repo = repo
        self._operations = operations
        self._edit_sessions = edit_sessions

    def impact(self, project_id: str, operation_id: str) -> dict[str, Any]:
        pid = ProjectId(project_id)
        envelope = self._operations.get_operation(pid, operation_id)
        if envelope.commit_id is None:
            return self._empty(project_id, operation_id)
        after = CommitId(envelope.commit_id)
        record_view = self._repo.get_artifact(ArtifactId(f"art_{operation_id}"), after)
        if record_view is None or record_view.revision is None:
            return self._empty(project_id, operation_id)
        record = OperationRecord.from_wire(record_view.revision.content)
        if record.before_commit_id is None:
            return self._empty(project_id, operation_id)
        before = CommitId(record.before_commit_id)
        head = self._head(pid)

        chapters = self._chapters(pid, record, before, after, head)
        ledger = self._ledger(pid, before, after, head)
        related = read_acceptances(self._repo, project_id, operation_id)
        highlights = [
            {
                "target_id": row["chapter_id"],
                "kind": "chapter",
                "basis": IMPACT_BASIS,
                "hunks": row["hunks"],
            }
            for row in chapters
        ] + [
            {
                "target_id": row["artifact_id"],
                "kind": "ledger",
                "basis": IMPACT_BASIS,
                "fields": list(row["changed_fields"]),
            }
            for row in ledger
        ]
        has_later = any(row["has_later_changes"] for row in chapters) or any(
            row["has_later_changes"] for row in ledger
        )
        return {
            "operation_id": operation_id,
            "kind": record.kind,
            "commit_id": after.value,
            "before_commit_id": before.value,
            "head_commit_id": head.value if head is not None else None,
            "basis": IMPACT_BASIS,
            "note": IMPACT_NOTE,
            "chapters": chapters,
            "ledger": ledger,
            "highlights": highlights,
            "related_acceptances": [
                {
                    "commit_id": str(item.get("commit_id", "")),
                    "request_id": str(item.get("request_id", "")),
                    "item_ids": list(item.get("item_ids") or []),
                    "at": str(item.get("at", "")),
                }
                for item in related
            ],
            "continuation": {
                "chapter_ids": [row["chapter_id"] for row in chapters],
                "has_later_changes": has_later,
                "blocked_by_edit_session": bool(
                    self._edit_sessions is not None
                    and self._edit_sessions.current(project_id) is not None
                ),
                "note": (
                    "从这些章继续创作；有后续修改时先看差异，不覆盖作者后来写下的内容。"
                ),
            },
            "refs": self._operations.record_refs(pid, operation_id),
        }

    # -- 内部 ----------------------------------------------------------------
    def _chapters(
        self,
        pid: ProjectId,
        record: OperationRecord,
        before: CommitId,
        after: CommitId,
        head: CommitId | None,
    ) -> list[dict[str, Any]]:
        before_catalog = self._manuscript(pid, before)
        after_catalog = self._manuscript(pid, after)
        head_catalog = self._manuscript(pid, head)
        rows: list[dict[str, Any]] = []
        for chapter_id in sorted(record.changed_chapter_ids):
            before_text = _text_of(before_catalog, chapter_id)
            after_text = _text_of(after_catalog, chapter_id)
            if before_text == after_text:
                continue  # 没有实际差异就不算影响
            current_text = _text_of(head_catalog, chapter_id)
            stats = _text_diff(before_text, after_text)
            entry = after_catalog.chapter_entry(chapter_id)
            rows.append(
                {
                    "chapter_id": chapter_id,
                    "title": str(entry.model().title) if entry is not None else "",
                    "chars_before": len(before_text),
                    "chars_after": len(after_text),
                    "chars_current": len(current_text),
                    "hunks": stats["hunks"],
                    "inserted_chars": stats["inserted_chars"],
                    "deleted_chars": stats["deleted_chars"],
                    "has_later_changes": current_text != after_text,
                    "basis": IMPACT_BASIS,
                }
            )
        return rows

    def _ledger(
        self,
        pid: ProjectId,
        before: CommitId,
        after: CommitId,
        head: CommitId | None,
    ) -> list[dict[str, Any]]:
        diff = self._repo.diff(before, after)
        before_map = self._story_entries(pid, before)
        after_map = self._story_entries(pid, after)
        head_map = self._story_entries(pid, head)
        rows: list[dict[str, Any]] = []
        for change in diff.artifact_changes:
            artifact_id = change.artifact_id.value
            after_entry = after_map.get(artifact_id)
            if after_entry is None or after_entry["kind"] not in story_kinds.CANON_KINDS:
                continue
            before_entry = before_map.get(artifact_id)
            before_content = (before_entry or {}).get("content", {})
            after_content = after_entry["content"]
            changed_fields = sorted(
                key
                for key in set(before_content) | set(after_content)
                if before_content.get(key) != after_content.get(key)
            )
            if not changed_fields:
                continue
            head_entry = head_map.get(artifact_id)
            rows.append(
                {
                    "artifact_id": artifact_id,
                    "kind": after_entry["kind"],
                    "title": after_entry["title"],
                    "changed_fields": changed_fields,
                    "before_revision": (before_entry or {}).get("revision_id"),
                    "after_revision": after_entry["revision_id"],
                    "current_revision": (head_entry or {}).get("revision_id"),
                    "has_later_changes": (head_entry or {}).get("revision_id")
                    != after_entry["revision_id"],
                    "basis": IMPACT_BASIS,
                }
            )
        return rows

    def _manuscript(self, pid: ProjectId, commit: CommitId | None) -> ManuscriptCatalog:
        if commit is None:
            return ManuscriptCatalog(commit_id=None, entries=())
        return ManuscriptCatalog.from_snapshot(self._repo.get_snapshot(pid, commit))

    def _story_entries(
        self, pid: ProjectId, commit: CommitId | None
    ) -> dict[str, dict[str, Any]]:
        if commit is None:
            return {}
        catalog = StoryCatalog.from_snapshot(self._repo.get_snapshot(pid, commit))
        return {
            entry.artifact_id: {
                "kind": entry.kind,
                "title": entry_view(entry).get("title", entry.artifact_id),
                "content": dict(entry.content),
                "revision_id": entry.revision_id,
            }
            for entry in catalog.entries
            if entry.kind in story_kinds.CANON_KINDS
        }

    def _head(self, pid: ProjectId) -> CommitId | None:
        branch = self._repo.get_branch(pid, "main")
        return branch.head_commit_id if branch is not None else None

    def _empty(self, project_id: str, operation_id: str) -> dict[str, Any]:
        return {
            "operation_id": operation_id,
            "kind": "",
            "commit_id": None,
            "before_commit_id": None,
            "head_commit_id": None,
            "basis": IMPACT_BASIS,
            "note": IMPACT_NOTE,
            "chapters": [],
            "ledger": [],
            "highlights": [],
            "related_acceptances": [],
            "continuation": {
                "chapter_ids": [],
                "has_later_changes": False,
                "blocked_by_edit_session": False,
                "note": "这次操作没有产生版本变化，没有可继续的正文范围。",
            },
            "refs": {},
        }


def _text_of(catalog: ManuscriptCatalog, chapter_id: str) -> str:
    if catalog.chapter_entry(chapter_id) is None:
        return ""
    return "\n\n".join(unit.model().text for unit in catalog.units_of(chapter_id))


def _text_diff(before: str, after: str) -> dict[str, int]:
    """字符级差异统计：只数 hunk 与增删字符，不产出比例/评分。"""
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    hunks = 0
    inserted = 0
    deleted = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        hunks += 1
        deleted += i2 - i1
        inserted += j2 - j1
    return {"hunks": hunks, "inserted_chars": inserted, "deleted_chars": deleted}
