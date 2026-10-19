"""关联撤销（V03-4.6 基础版 / V03-5.4 冲突选择）。

撤销只能沿着**操作记录**走：它记了 ``before_commit_id`` 与本操作影响的章节，
after 版本就是携带记录的那个提交。没有操作记录的写入（例如账本单存、候选采用）
不在本模块范围内——它们没有「一次操作」的因果边界。

V03-5.4 起，预览与写入按**三方逆向**处理：

- 当前章仍等于操作后版本 ⇒ 直接恢复操作前文本；
- 之后有非重叠修改 ⇒ 用 ``merge_texts`` 反向叠回，保留后续内容；
- 重叠修改 ⇒ 逐处冲突，作者选择「恢复旧内容」或「保留当前内容」；
- 关联账本同理：当前 revision 仍等于操作后 ⇒ 默认恢复；后来独立改过 ⇒
  逐条选择，未选择不发布；
- 新增引用只报告（证据会由统一发布器重新核对），不删除、不覆盖。

冲突选择与 ``preview_token`` 绑定：预览之后 head 又变了，旧选择带新 token
会被拒绝并要求重新预览。选择未覆盖全部冲突时零写入，绝不发布半套恢复。
"""

from __future__ import annotations

from typing import Any

from hastings.application.errors import ViolationError
from hastings.application.handlers.manuscript_commands import manuscript_command
from hastings.application.services.commit_service import CommitService
from hastings.application.services.edit_session_service import EditSessionService
from hastings.application.services.operation_service import OperationService
from hastings.application.services.publish_manuscript_change import PublishManuscriptChange
from hastings.application.services.revision_links import read_acceptances
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.manuscript.commands import save_full_text
from hastings.domain.manuscript.evidence import KIND_EVIDENCE_LINK
from hastings.domain.manuscript.merge import MergeConflict, merge_texts
from hastings.domain.manuscript.operation_record import OperationRecord
from hastings.domain.repository.ids import ArtifactId, CommitId, ProjectId, RevisionId
from hastings.domain.repository.requests import ArtifactChange
from hastings.domain.repository.serialization import content_hash
from hastings.domain.story import kinds as story_kinds
from hastings.domain.story.catalog import StoryCatalog

__all__ = ["UndoService"]

_CHOICES = {"keep", "restore"}


def _violation(code: str, where: str, why: str, fix: str, **details: Any) -> ViolationError:
    return ViolationError(code=code, where=where, why=why, fix=fix, details=details)


class UndoService:
    def __init__(
        self,
        repo: CommitService,
        operations: OperationService,
        publisher: PublishManuscriptChange,
        edit_sessions: EditSessionService | None = None,
    ) -> None:
        self._repo = repo
        self._operations = operations
        self._publisher = publisher
        self._edit_sessions = edit_sessions

    def preview(self, project_id: str, operation_id: str) -> dict[str, Any]:
        """撤销预览：受影响章节、账本、冲突、引用与可选择的处理方式。"""
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

        chapters: list[dict[str, Any]] = []
        chapter_conflicts: dict[str, tuple[MergeConflict, ...]] = {}
        for cid in sorted(record.changed_chapter_ids):
            row, conflicts = self._chapter_row(cid, before_catalog, after_catalog, head_catalog)
            chapters.append(row)
            if conflicts:
                chapter_conflicts[cid] = conflicts
        ledger = self._ledger_rows(pid, before, after, head)
        related = self._related_acceptance_rows(
            pid, operation_id, head, {str(row["artifact_id"]) for row in ledger}
        )
        ledger.extend(related["rows"])
        ledger_ids = [str(row["artifact_id"]) for row in ledger if row["status"] != "created"]
        references = self._references(pid, after, head, set(record.changed_chapter_ids), ledger_ids)

        decisions: list[dict[str, Any]] = []
        for cid, conflicts in chapter_conflicts.items():
            for index, conflict in enumerate(conflicts):
                decisions.append(
                    {
                        "id": f"text:{cid}:{index}",
                        "kind": "text",
                        "target_id": cid,
                        "choices": ["restore", "keep"],
                        "restore": list(conflict.ours),
                        "current": list(conflict.theirs),
                        "base": list(conflict.base),
                        "why": "这一处正文在撤销之后又被改过，恢复旧内容会覆盖后来的写法",
                    }
                )
        for row in ledger:
            if row["status"] != "conflict":
                continue
            decisions.append(
                {
                    "id": f"ledger:{row['artifact_id']}",
                    "kind": "ledger",
                    "target_id": row["artifact_id"],
                    "choices": ["restore", "keep"],
                    "kind_label": row["kind"],
                    "title": row["title"],
                    "restore": row["before_content"],
                    "current": row["current_content"],
                    "why": "这条账本在撤销之后又被独立修改过，恢复会覆盖后来的修改",
                }
            )

        hard_reason = ""
        created = [row for row in chapters if row["status"] == "created"]
        created_ledger = [row for row in ledger if row["status"] == "created"]
        missing = [row for row in chapters if row["status"] == "missing"]
        missing_ledger = [row for row in ledger if row["status"] == "missing"]
        if self._edit_sessions is not None and self._edit_sessions.current(project_id):
            hard_reason = "作品有未结束的手工修改副本；先完成或放弃副本，再撤销"
        elif created:
            hard_reason = "这次操作新建了章节；删除章节不是本版提供的操作"
        elif created_ledger:
            hard_reason = "这次操作新建了账本条目；删除账本不是本版提供的操作"
        elif missing:
            hard_reason = "有章节在操作后已不存在，无法安全恢复"
        elif missing_ledger:
            hard_reason = "有账本条目在操作后已不存在，无法安全恢复"
        elif not chapters and not ledger:
            hard_reason = "这次操作没有可识别的正文或账本变化"

        blocked_reason = hard_reason
        if not hard_reason and decisions:
            blocked_reason = (
                f"有 {len(decisions)} 处后续修改需要选择「恢复旧内容」或「保留当前内容」；"
                "选择覆盖前不会写入任何内容"
            )

        preview = {
            "operation_id": operation_id,
            "before_commit_id": before.value,
            "after_commit_id": after.value,
            "head_commit_id": head.value if head is not None else None,
            "chapters": chapters,
            "ledger": ledger,
            "references": references,
            "related_acceptances": related["acceptances"],
            "decisions": decisions,
            "conflicts": [item["why"] for item in decisions],
            "requires_choices": bool(decisions),
            "hard_blocked": bool(hard_reason),
            "can_undo": not hard_reason and not decisions,
            "blocked_reason": blocked_reason,
            "note": "选择随撤销提交；未覆盖全部冲突时零写入",
        }
        preview["preview_token"] = self._preview_token(preview)
        return preview

    # -- 写入 ---------------------------------------------------------------
    def apply(
        self,
        project_id: str,
        operation_id: str,
        *,
        choices: dict[str, str] | None = None,
        preview_token: str | None = None,
    ) -> dict[str, Any]:
        """按预览与选择执行撤销：正文与关联账本一次提交，失败不留半套版本。

        每个待选择项都必须显式给 ``restore``（恢复旧内容）或 ``keep``（保留
        当前内容）；任一项缺失即 409，不猜作者想怎么办。撤销 ID 由原操作与
        选择共同派生：同一组选择重试返回原提交，换一组选择是新意图。
        """
        pid = ProjectId(project_id)
        picked = {str(key): str(value) for key, value in (choices or {}).items()}
        for decision_id, value in picked.items():
            if value not in _CHOICES:
                raise _violation(
                    "UNDO_DEPENDENCY_CONFLICT",
                    f"undo.apply({pid.value}.{operation_id})",
                    f"选择 {decision_id!r} 的值 {value!r} 不是 restore/keep",
                    "每项选择使用 restore（恢复旧内容）或 keep（保留当前内容）",
                )
        undo_operation_id = self._undo_operation_id(pid, operation_id, picked)
        # 先看这次撤销是否已经执行过：连点两次应回读同一提交，而不是被自己的结果
        # 判成「之后又被修改」。
        prior = self._find_undo(pid, undo_operation_id)
        if prior is not None:
            prior_refs = self._operations.record_refs(pid, undo_operation_id)
            return {
                "status": prior.status,
                "operation_id": undo_operation_id,
                "undo_of": operation_id,
                "commit_id": prior.commit_id,
                "restored_chapter_ids": list(prior.changed_chapter_ids),
                "restored_ledger_ids": list(prior_refs.get("restored_ledger_ids") or []),
                "kept": list(prior_refs.get("kept") or []),
                "replayed": True,
            }
        preview = self.preview(project_id, operation_id)
        if preview_token is not None and preview_token != preview["preview_token"]:
            raise _violation(
                "UNDO_DEPENDENCY_CONFLICT",
                f"undo.apply({pid.value}.{operation_id})",
                "撤销预览之后作品又发生了变化",
                "重新读取撤销预览后按最新事实重新选择",
                current_preview_token=preview["preview_token"],
            )
        if preview["hard_blocked"]:
            raise _violation(
                "UNDO_DEPENDENCY_CONFLICT",
                f"undo.apply({pid.value}.{operation_id})",
                preview["blocked_reason"] or "这次操作当前不能撤销",
                "按提示先处理冲突或结束副本；本版不会覆盖已有内容",
                operation_id=operation_id,
                conflicts=list(preview["conflicts"]),
            )
        required = [item["id"] for item in preview["decisions"]]
        missing = [item for item in required if item not in picked]
        unknown = sorted(set(picked) - set(required))
        if unknown:
            raise _violation(
                "UNDO_DEPENDENCY_CONFLICT",
                f"undo.apply({pid.value}.{operation_id})",
                f"有 {len(unknown)} 个选择对应的事实已经变化",
                "重新读取撤销预览后按当前冲突重新选择",
                operation_id=operation_id,
                unknown_choices=unknown,
                decisions=preview["decisions"],
            )
        if missing:
            raise _violation(
                "UNDO_DEPENDENCY_CONFLICT",
                f"undo.apply({pid.value}.{operation_id})",
                f"还有 {len(missing)} 处后续修改没有选择",
                "为每个待选择项给出 restore 或 keep；未覆盖前不会写入任何内容",
                operation_id=operation_id,
                required_choices=missing,
                decisions=preview["decisions"],
            )

        head = CommitId(str(preview["head_commit_id"])) if preview["head_commit_id"] else None
        before = CommitId(str(preview["before_commit_id"]))
        before_catalog = self._manuscript(pid, before)
        after_catalog = self._manuscript(pid, CommitId(str(preview["after_commit_id"])))
        head_catalog = self._manuscript(pid, head)
        commands = []
        kept: list[dict[str, Any]] = []
        for row in preview["chapters"]:
            cid = str(row["chapter_id"])
            status = str(row["status"])
            if status == "created":
                raise _violation(
                    "UNDO_DEPENDENCY_CONFLICT",
                    f"undo.apply({pid.value}.{operation_id})",
                    "撤销范围包含新建章节；本版不删除章节",
                    "只撤销正文修改，或从版本历史单独处理新建章",
                )
            if status == "ok":
                text = before_catalog.manuscript().text_of(cid)
            elif status == "mergeable":
                text = merge_texts(
                    base=after_catalog.manuscript().text_of(cid),
                    ours=before_catalog.manuscript().text_of(cid),
                    theirs=head_catalog.manuscript().text_of(cid),
                ).text
            else:  # conflict：每个冲突都必须有选择
                wanted = {
                    index: "ours" if picked.get(f"text:{cid}:{index}") == "restore" else "theirs"
                    for index in range(len(row.get("conflicts") or []))
                }
                merged = merge_texts(
                    base=after_catalog.manuscript().text_of(cid),
                    ours=before_catalog.manuscript().text_of(cid),
                    theirs=head_catalog.manuscript().text_of(cid),
                    choices=wanted,
                )
                text = merged.text
                for index in range(len(row.get("conflicts") or [])):
                    if picked.get(f"text:{cid}:{index}") == "keep":
                        kept.append({"id": f"text:{cid}:{index}", "kind": "text", "target_id": cid})
            commands.append(
                manuscript_command(
                    save_full_text,
                    chapter_id=cid,
                    text=text,
                    # 以**当前**章基础做 CAS：准备之后又有人改过同一章就整体拒绝。
                    expected_chapter_base=str(row["current_base"]),
                )
            )

        extra: list[ArtifactChange] = []
        restored_ledger: list[str] = []
        for row in preview["ledger"]:
            if row["status"] == "created":
                raise _violation(
                    "UNDO_DEPENDENCY_CONFLICT",
                    f"undo.apply({pid.value}.{operation_id})",
                    "撤销范围包含新建账本条目；本版不删除账本",
                    "只恢复被修改的账本条目",
                )
            if row["status"] == "missing":
                raise _violation(
                    "UNDO_DEPENDENCY_CONFLICT",
                    f"undo.apply({pid.value}.{operation_id})",
                    "撤销范围内的账本条目已不存在",
                    "重新读取撤销预览；本版不会重建已删除的账本条目",
                )
            choice = picked.get(f"ledger:{row['artifact_id']}")
            if row["status"] == "conflict" and choice == "keep":
                kept.append(
                    {
                        "id": f"ledger:{row['artifact_id']}",
                        "kind": "ledger",
                        "target_id": row["artifact_id"],
                    }
                )
                continue
            extra.append(
                ArtifactChange(
                    artifact_id=ArtifactId(str(row["artifact_id"])),
                    content=dict(row["before_content"]),
                    kind=str(row["kind"]),
                    old_revision_id=RevisionId(str(row["current_revision"])),
                )
            )
            restored_ledger.append(str(row["artifact_id"]))

        # 撤销记录的归属跟着原操作走（V03-4.5）：作者在哪条讨论里看到这次修改，
        # 就在同一条讨论里看到它的撤销，历史不会断链。
        original = self._operations.get_operation(pid, operation_id)
        refs: dict[str, Any] = {
            "undo_of": operation_id,
            "chapters": [str(row["chapter_id"]) for row in preview["chapters"]],
            "restored_ledger_ids": restored_ledger,
            "kept": kept,
            "choices": dict(sorted(picked.items())),
            "preview_token": preview["preview_token"],
        }
        if not commands and not extra:
            envelope = self._operations.submit(
                project_id=pid.value,
                operation_id=undo_operation_id,
                kind="chapter.undo",
                changes=(),
                summary=f"撤销操作 {operation_id}（保留全部后续修改）",
                refs=refs,
                origin_conversation_id=original.conversation_id,
            )
            return {
                "status": envelope.status,
                "operation_id": undo_operation_id,
                "undo_of": operation_id,
                "commit_id": envelope.commit_id,
                "restored_chapter_ids": [],
                "restored_ledger_ids": list(refs["restored_ledger_ids"]),
                "kept": kept,
            }
        result = self._publisher.publish_commands(
            project_id=pid,
            commands=tuple(commands),
            operation_id=undo_operation_id,
            kind="chapter.undo",
            summary=f"撤销操作 {operation_id}",
            refs=refs,
            origin_conversation_id=original.conversation_id,
            # 以当前 head 为准备基础：当前章基础就是各命令的 CAS 条件。
            base_commit_id=head,
            extra_changes=tuple(extra),
        )
        return {
            "status": result.envelope.status,
            "operation_id": undo_operation_id,
            "undo_of": operation_id,
            "commit_id": result.envelope.commit_id,
            "restored_chapter_ids": list(result.envelope.changed_chapter_ids),
            "restored_ledger_ids": list(refs["restored_ledger_ids"]),
            "kept": kept,
        }

    # -- 内部 ---------------------------------------------------------------
    def _undo_operation_id(self, pid: ProjectId, operation_id: str, choices: dict[str, str]) -> str:
        payload: list[Any] = ["manuscript.undo", pid.value, operation_id]
        if choices:
            payload.append(sorted(choices.items()))
        return "op_" + content_hash(payload)[:16]

    def _preview_token(self, preview: dict[str, Any]) -> str:
        return content_hash(
            {
                "before": preview["before_commit_id"],
                "after": preview["after_commit_id"],
                "head": preview["head_commit_id"],
                "chapters": [
                    [row["chapter_id"], row["status"], row["current_base"]]
                    for row in preview["chapters"]
                ],
                "ledger": [
                    [
                        row["artifact_id"],
                        row["status"],
                        row.get("current_revision"),
                    ]
                    for row in preview["ledger"]
                ],
                "decisions": [item["id"] for item in preview["decisions"]],
                "references": [[item["kind"], item["id"]] for item in preview["references"]],
            }
        )

    def _find_undo(self, pid: ProjectId, undo_operation_id: str) -> Any | None:
        """按回执回读已执行的撤销；没有回执 = 还没撤过。"""
        try:
            return self._operations.get_operation(pid, undo_operation_id)
        except ViolationError as exc:
            if exc.code == "OPERATION_NOT_FOUND":
                return None
            raise

    def _blocked(self, pid: ProjectId, operation_id: str, reason: str) -> dict[str, Any]:
        del pid
        return {
            "operation_id": operation_id,
            "before_commit_id": None,
            "after_commit_id": None,
            "head_commit_id": None,
            "chapters": [],
            "ledger": [],
            "references": [],
            "decisions": [],
            "conflicts": [],
            "requires_choices": False,
            "hard_blocked": True,
            "can_undo": False,
            "blocked_reason": reason,
            "preview_token": "",
            "note": "本接口只报告事实",
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
    ) -> tuple[dict[str, Any], tuple[MergeConflict, ...]]:
        entry = after.chapter_entry(chapter_id)
        title = str(entry.model().title) if entry is not None else ""
        before_entry = before.chapter_entry(chapter_id)
        head_entry = head.chapter_entry(chapter_id)
        after_base = after.chapter_base(chapter_id) if entry is not None else ""
        before_base = before.chapter_base(chapter_id) if before_entry is not None else ""
        current_base = head.chapter_base(chapter_id) if head_entry is not None else ""
        conflicts: tuple[MergeConflict, ...] = ()
        planned = ""
        if before_entry is None:
            status = "created"
        elif head_entry is None:
            status = "missing"
        elif current_base == after_base:
            status = "ok"
            planned = before.manuscript().text_of(chapter_id)
        else:
            merged = merge_texts(
                base=after.manuscript().text_of(chapter_id),
                ours=before.manuscript().text_of(chapter_id),
                theirs=head.manuscript().text_of(chapter_id),
            )
            conflicts = merged.conflicts
            status = "conflict" if conflicts else "mergeable"
            planned = merged.text
        return (
            {
                "chapter_id": chapter_id,
                "title": title,
                "before_base": before_base,
                "after_base": after_base,
                "current_base": current_base,
                "chars_before": self._chars(before, chapter_id),
                "chars_after": self._chars(after, chapter_id),
                "chars_current": self._chars(head, chapter_id),
                "status": status,
                "planned_chars": len(planned),
                "conflicts": [
                    {
                        "index": index,
                        "restore": list(conflict.ours),
                        "current": list(conflict.theirs),
                        "base": list(conflict.base),
                    }
                    for index, conflict in enumerate(conflicts)
                ],
            },
            conflicts,
        )

    @staticmethod
    def _chars(catalog: ManuscriptCatalog, chapter_id: str) -> int:
        if catalog.chapter_entry(chapter_id) is None:
            return 0
        return sum(len(unit.model().text) for unit in catalog.units_of(chapter_id))

    def _ledger_rows(
        self, pid: ProjectId, before: CommitId, after: CommitId, head: CommitId | None
    ) -> list[dict[str, Any]]:
        """对比三个版本的故事对象：报告恢复值、当前值与冲突。"""
        before_map = self._story_entries(pid, before)
        after_map = self._story_entries(pid, after)
        head_map = self._story_entries(pid, head)
        rows: list[dict[str, Any]] = []
        for artifact_id in sorted(set(before_map) | set(after_map)):
            before_entry = before_map.get(artifact_id)
            after_entry = after_map.get(artifact_id)
            before_revision = before_entry["revision_id"] if before_entry else None
            after_revision = after_entry["revision_id"] if after_entry else None
            if before_revision == after_revision:
                continue
            head_entry = head_map.get(artifact_id)
            if before_entry is None:
                status = "created"
            elif head_entry is None:
                status = "missing"
            elif after_entry is not None and head_entry["revision_id"] == after_revision:
                status = "ok"
            else:
                status = "conflict"
            current = head_entry or after_entry or before_entry or {}
            rows.append(
                {
                    "artifact_id": artifact_id,
                    "kind": (after_entry or before_entry or {}).get("kind", ""),
                    "title": (after_entry or before_entry or {}).get("title", artifact_id),
                    "status": status,
                    "before_content": (before_entry or {}).get("content", {}),
                    "after_content": (after_entry or {}).get("content", {}),
                    "current_content": current.get("content", {}),
                    "current_revision": current.get("revision_id", ""),
                    "why": "账本条目在该操作之后又被修改过" if status == "conflict" else "",
                }
            )
        return rows

    def _related_acceptance_rows(
        self,
        pid: ProjectId,
        operation_id: str,
        head: CommitId | None,
        existing_ids: set[str],
    ) -> dict[str, Any]:
        """把基于本操作采用建议的独立提交纳入同一份撤销检查（V03-7.7）。

        只处理**修改**已有条目的情况：关联采用新建的条目不能在撤销里删除，跳过并
        报告数量，不阻塞原操作本身的撤销。每条的恢复值仍按三方判定：当前版本等于
        采用后版本才默认恢复，之后被独立改过就进入冲突选择。
        """
        acceptances = read_acceptances(self._repo, pid.value, operation_id)
        rows: list[dict[str, Any]] = []
        report: list[dict[str, Any]] = []
        for item in acceptances:
            commit_value = str(item.get("commit_id") or "")
            if not commit_value:
                continue
            try:
                chain = self._repo.commit_history(pid, CommitId(commit_value))
            except (ViolationError, ValueError):
                continue
            commit = next((row for row in chain if row.id.value == commit_value), None)
            if commit is None or not commit.parent_ids:
                continue
            parent = commit.parent_ids[0]
            diff = self._repo.diff(parent, CommitId(commit_value))
            before_map = self._story_entries(pid, parent)
            after_map = self._story_entries(pid, CommitId(commit_value))
            head_map = self._story_entries(pid, head)
            included = 0
            skipped_created = 0
            skipped_missing = 0
            for change in diff.artifact_changes:
                artifact_id = change.artifact_id.value
                if artifact_id in existing_ids:
                    continue
                before_entry = before_map.get(artifact_id)
                after_entry = after_map.get(artifact_id)
                head_entry = head_map.get(artifact_id)
                if after_entry is None or head_entry is None:
                    skipped_missing += 1
                    continue
                if before_entry is None:
                    skipped_created += 1
                    continue
                status = (
                    "ok"
                    if head_entry["revision_id"] == after_entry["revision_id"]
                    else "conflict"
                )
                rows.append(
                    {
                        "artifact_id": artifact_id,
                        "kind": after_entry["kind"],
                        "title": after_entry["title"],
                        "status": status,
                        "before_content": before_entry["content"],
                        "after_content": after_entry["content"],
                        "current_content": head_entry["content"],
                        "current_revision": head_entry["revision_id"],
                        "source": "related_acceptance",
                        "acceptance_commit_id": commit_value,
                        "why": (
                            "这条账本在关联采用之后又被独立修改过，恢复会覆盖后来的修改"
                            if status == "conflict"
                            else ""
                        ),
                    }
                )
                existing_ids.add(artifact_id)
                included += 1
            report.append(
                {
                    "commit_id": commit_value,
                    "request_id": str(item.get("request_id", "")),
                    "included": included,
                    "skipped_created": skipped_created,
                    "skipped_missing": skipped_missing,
                }
            )
        return {"rows": rows, "acceptances": report}

    def _story_entries(self, pid: ProjectId, commit: CommitId | None) -> dict[str, dict[str, Any]]:
        if commit is None:
            return {}
        catalog = StoryCatalog.from_snapshot(self._repo.get_snapshot(pid, commit))
        return {
            entry.artifact_id: {
                "kind": entry.kind,
                "title": str(
                    entry.content.get("name") or entry.content.get("statement") or entry.artifact_id
                ),
                "content": dict(entry.content),
                "revision_id": entry.revision_id,
            }
            for entry in catalog.entries
            if entry.kind in story_kinds.CANON_KINDS
        }

    def _references(
        self,
        pid: ProjectId,
        after: CommitId,
        head: CommitId | None,
        changed_chapters: set[str],
        ledger_ids: list[str],
    ) -> list[dict[str, Any]]:
        """操作之后新增的引用：只报告，不删除；正文证据会被发布器重新核对。"""
        if head is None or (not changed_chapters and not ledger_ids):
            return []
        after_ids = {
            view.artifact.id.value
            for view in self._repo.get_snapshot(pid, after).artifacts.values()
        }
        head_snapshot = self._repo.get_snapshot(pid, head)
        references: list[dict[str, Any]] = []
        for view in head_snapshot.artifacts.values():
            artifact_id = view.artifact.id.value
            if artifact_id in after_ids or view.revision is None:
                continue
            if view.artifact.kind == KIND_EVIDENCE_LINK:
                content = view.revision.content
                chapter_id = str(content.get("chapter_id", ""))
                if chapter_id in changed_chapters:
                    references.append(
                        {
                            "kind": "evidence_link",
                            "id": artifact_id,
                            "chapter_id": chapter_id,
                            "status": "will_recheck",
                        }
                    )
        if ledger_ids:
            after_story = self._story_entries(pid, after)
            head_story = self._story_entries(pid, head)
            wanted = set(ledger_ids)
            for artifact_id, entry in sorted(head_story.items()):
                if artifact_id in after_story:
                    continue
                hit = sorted(wanted & _referenced_ids(entry["content"]))
                if hit:
                    references.append(
                        {
                            "kind": "ledger_entry",
                            "id": artifact_id,
                            "refers_to": hit,
                            "status": "kept",
                        }
                    )
        return references


def _referenced_ids(content: Any) -> set[str]:
    """递归收集 JSON 内容里出现的字符串 ID；只用于「新引用」提示。"""
    found: set[str] = set()
    if isinstance(content, str):
        found.add(content)
    elif isinstance(content, dict):
        for value in content.values():
            found |= _referenced_ids(value)
    elif isinstance(content, list | tuple):
        for value in content:
            found |= _referenced_ids(value)
    return found
