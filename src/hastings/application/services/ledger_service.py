"""账本直接保存（V03-3.2）：作者显式创建/修订，保存后回读真实版本。

写入只经 Story 领域命令与统一 Commit Service：

- 创建/修订由 ``domain/story/canon/entries.py`` 校验字段、引用与 revision CAS；
- 一次保存 = 一个原子 Commit（kind/relations 同事务），失败不产生半套条目；
- 响应携带真实 ``commit_id`` 与新 ``revision_id``，不伪造「已保存」；
- 幂等：同一 ``request_id`` 的重试先查已提交结果，直接回读同一版本。

假设不进正式账本（未知 kind 被领域拒绝）；证词/认知以 ``story.knowledge``
原类型保存，不转成 ``story.truth_fact``。账本单独保存不重写正文，也不启动
摘要或建议任务。
"""

from __future__ import annotations

from typing import Any

from hastings.application.errors import ViolationError
from hastings.application.ports.ids import IdGenerator
from hastings.application.queries.ledger import KIND_LABELS, entry_view
from hastings.application.services.commit_service import CommitService
from hastings.domain.repository.ids import Actor, CommitId, ProjectId
from hastings.domain.repository.requests import CommitRequest
from hastings.domain.repository.results import NO_CHANGE
from hastings.domain.story import error_codes as story_ec
from hastings.domain.story.canon import entries as ledger_entries
from hastings.domain.story.catalog import StoryCatalog, StoryEntry, display_name
from hastings.domain.story.errors import StoryViolation

__all__ = ["LedgerService"]

_AUTHOR = Actor(kind="human", id="author")


def _violation(code: str, where: str, why: str, fix: str, **details: Any) -> ViolationError:
    return ViolationError(code=code, where=where, why=why, fix=fix, details=details)


def _as_violation(exc: StoryViolation) -> ViolationError:
    """领域违规 → 应用信封；修订冲突单独归为 409 类错误码。"""
    code = "LEDGER_ENTRY_CONFLICT" if exc.code == story_ec.STORY_STALE_REVISION else exc.code
    return ViolationError(code=code, where=exc.where, why=exc.why, fix=exc.fix, details=exc.details)


class LedgerService:
    def __init__(self, repo: CommitService, ids: IdGenerator) -> None:
        self._repo = repo
        self._ids = ids

    # -- 读 -----------------------------------------------------------------
    def get_entry(self, project_id: str, entry_id: str) -> dict[str, Any]:
        pid = ProjectId(project_id)
        entry = self._entry_at(pid, self._head(pid), entry_id)
        return self._view(entry)

    # -- 写 -----------------------------------------------------------------
    def create_entry(
        self,
        project_id: str,
        *,
        kind: str,
        fields: dict[str, Any],
        request_id: str,
    ) -> dict[str, Any]:
        return self._save(
            project_id,
            kind=kind,
            fields=fields,
            entry_id=None,
            expected_revision=None,
            request_id=request_id,
        )

    def update_entry(
        self,
        project_id: str,
        entry_id: str,
        *,
        fields: dict[str, Any],
        expected_revision: str | None,
        request_id: str,
    ) -> dict[str, Any]:
        return self._save(
            project_id,
            kind="",
            fields=fields,
            entry_id=entry_id,
            expected_revision=expected_revision,
            request_id=request_id,
        )

    # -- 内部 ---------------------------------------------------------------
    def _save(
        self,
        project_id: str,
        *,
        kind: str,
        fields: dict[str, Any],
        entry_id: str | None,
        expected_revision: str | None,
        request_id: str,
    ) -> dict[str, Any]:
        pid = ProjectId(project_id)
        key = f"ledger-save:{pid.value}:{request_id}"
        # 幂等：同一请求已提交时不再执行命令，直接回读首次结果。
        prior = self._repo.find_commit_result(pid, key)
        if prior is not None and prior.commit_id is not None:
            changed = prior.changed_artifact_ids
            if changed:
                entry = self._entry_at(pid, prior.commit_id, changed[0].value)
                return self._result(prior.status, prior.commit_id.value, entry, replayed=True)

        branch = self._repo.get_branch(pid, "main")
        if branch is None:
            raise _violation(
                "REPO_BRANCH_NOT_FOUND",
                f"ledger.save({pid.value})",
                f"作品 {pid.value} 没有 main 分支",
                "先创建作品，再保存账本条目",
                project_id=pid.value,
            )
        head = branch.head_commit_id
        catalog = self._catalog(pid, head)
        try:
            if entry_id is None:
                change = ledger_entries.create_entry(
                    catalog,
                    lambda _kind: self._ids.new_id("art"),
                    kind=kind,
                    fields=fields,
                )
            else:
                change = ledger_entries.revise_entry(
                    catalog,
                    lambda _kind: self._ids.new_id("art"),
                    entry_id=entry_id,
                    fields=fields,
                    expected_revision=expected_revision,
                )
        except StoryViolation as exc:
            raise _as_violation(exc) from exc

        if change.is_empty():
            target = entry_id or ""
            entry = self._entry_at(pid, head, target)
            return self._result(NO_CHANGE, None, entry, replayed=False)

        artifact_change = change.artifact_changes[0]
        saved_id = artifact_change.artifact_id.value
        existing = catalog.get(saved_id)
        saved_kind = artifact_change.kind or (existing.kind if existing is not None else "")
        title = display_name(saved_kind, dict(artifact_change.content))
        label = KIND_LABELS.get(saved_kind, saved_kind)
        result = self._repo.commit(
            CommitRequest(
                project_id=pid,
                branch_id=branch.id,
                expected_head=head,
                actor=_AUTHOR,
                idempotency_key=key,
                summary=f"保存账本：{label}「{title or saved_id}」",
                changes=change.artifact_changes,
                relations=change.relation_changes,
                events=(
                    (
                        "ledger.entry.saved",
                        {"entry_id": saved_id, "kind": saved_kind},
                    ),
                ),
            )
        )
        if result.commit_id is None:
            entry = self._entry_at(pid, head, saved_id)
            return self._result(NO_CHANGE, None, entry, replayed=False)
        entry = self._entry_at(pid, result.commit_id, saved_id)
        return self._result(result.status, result.commit_id.value, entry, replayed=False)

    def _head(self, pid: ProjectId) -> CommitId | None:
        branch = self._repo.get_branch(pid, "main")
        return branch.head_commit_id if branch is not None else None

    def _catalog(self, pid: ProjectId, head: CommitId | None) -> StoryCatalog:
        if head is None:
            return StoryCatalog(commit_id=None, entries=())
        return StoryCatalog.from_snapshot(self._repo.get_snapshot(pid, head))

    def _entry_at(self, pid: ProjectId, head: CommitId | None, entry_id: str) -> StoryEntry:
        catalog = self._catalog(pid, head)
        entry = catalog.get(entry_id) if entry_id else None
        if entry is None:
            raise _violation(
                "LEDGER_ENTRY_NOT_FOUND",
                f"ledger.get({pid.value})",
                f"作品 {pid.value} 没有账本条目 {entry_id!r}",
                "刷新账本后重新选择条目；删除不是本版提供的操作",
                project_id=pid.value,
                entry_id=entry_id,
            )
        return entry

    def _view(self, entry: StoryEntry) -> dict[str, Any]:
        return {
            **entry_view(entry),
            "revision_id": entry.revision_id,
            "content": dict(entry.content),
        }

    def _result(
        self,
        status: str,
        commit_id: str | None,
        entry: StoryEntry,
        *,
        replayed: bool,
    ) -> dict[str, Any]:
        return {
            "status": status,
            "commit_id": commit_id,
            "entry": self._view(entry),
            "replayed": replayed,
        }
