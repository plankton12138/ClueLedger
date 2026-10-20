"""本书约定的应用服务（V03-4.1）：无模型维护，写入即一个原子 Commit。

与账本直接保存（V03-3.2）同一手法：

- 领域命令只在 ``domain/story/agreements.py`` 判定冲突与字段，服务只负责取目录、
  组装 ``CommitRequest``、回读真实版本；
- 不注入任何模型端口——「无模型可维护」不是靠运行时判断，而是这条路径上不存在
  模型依赖，因此也不会有隐含任务（I06）；
- 幂等：同一 ``request_id`` 的重试直接回读首次提交结果，不产生第二个版本；
- 冲突（同主题已有生效约定 / 基础 Revision 过期）返回 409 类错误码，由作者先选择，
  自动重试没有意义。

改约定不追改正文；已经开始的任务继续使用自己的 ``AgreementSnapshot``。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from hastings.application.errors import ViolationError
from hastings.application.ports.ids import IdGenerator
from hastings.application.services.commit_service import CommitService
from hastings.domain.repository.ids import Actor, CommitId, ProjectId
from hastings.domain.repository.requests import CommitRequest
from hastings.domain.repository.results import NO_CHANGE
from hastings.domain.story import agreements as domain
from hastings.domain.story import error_codes as story_ec
from hastings.domain.story.catalog import StoryCatalog, StoryEntry
from hastings.domain.story.errors import StoryViolation

__all__ = ["AgreementService"]

_AUTHOR = Actor(kind="human", id="author")


def _violation(code: str, where: str, why: str, fix: str, **details: Any) -> ViolationError:
    return ViolationError(code=code, where=where, why=why, fix=fix, details=details)


def _vocabulary(labels: Mapping[str, str]) -> list[dict[str, str]]:
    """把领域词表转成前端下拉数据：取值与中文标签同一份来源。"""
    return [{"value": key, "label": label} for key, label in labels.items()]


def _as_violation(exc: StoryViolation) -> ViolationError:
    """领域违规 → 应用信封；Revision 过期单独映射为 409 类错误码。"""
    code = (
        story_ec.AGREEMENT_REVISION_CONFLICT
        if exc.code == story_ec.STORY_STALE_REVISION
        else exc.code
    )
    return ViolationError(code=code, where=exc.where, why=exc.why, fix=exc.fix, details=exc.details)


class AgreementService:
    def __init__(self, repo: CommitService, ids: IdGenerator) -> None:
        self._repo = repo
        self._ids = ids

    # -- 读 -----------------------------------------------------------------
    def list(self, project_id: str, *, include_retired: bool = False) -> dict[str, Any]:
        """当前生效与待选择的约定；默认不含已停用项（历史仍可按版本读）。"""
        pid = ProjectId(project_id)
        catalog = self._catalog(pid, self._head(pid))
        rows: list[dict[str, Any]] = []
        retired = 0
        for entry in catalog.by_kind(domain.KIND):
            view = self._view(entry)
            if view["status"] == domain.STATUS_RETIRED:
                retired += 1
                if not include_retired:
                    continue
            rows.append(view)
        rows.sort(key=lambda r: (r["topic"], r["statement"], r["id"]))
        return {
            "agreements": rows,
            "topics": _vocabulary(domain.AGREEMENT_TOPICS),
            "scopes": _vocabulary(domain.AGREEMENT_SCOPES),
            "content_hash": domain.snapshot(catalog).content_hash,
            "retired_count": retired,
        }

    def get(self, project_id: str, agreement_id: str) -> dict[str, Any]:
        pid = ProjectId(project_id)
        entry = self._entry_at(pid, self._head(pid), agreement_id)
        return self._view(entry)

    def snapshot(self, project_id: str, commit_id: str | None = None) -> dict[str, Any]:
        """生效约定快照；给 ``commit_id`` 时按该版本取（已启动任务的固定输入）。"""
        pid = ProjectId(project_id)
        head = CommitId(commit_id) if commit_id else self._head(pid)
        return domain.snapshot(self._catalog(pid, head)).to_wire()

    # -- 写 -----------------------------------------------------------------
    def create(
        self,
        project_id: str,
        *,
        statement: str,
        topic: str,
        scope: str,
        status: str,
        source_kind: str,
        source_message_id: str,
        supersedes: Sequence[str],
        request_id: str,
    ) -> dict[str, Any]:
        fields = {
            "statement": statement,
            "topic": topic,
            "scope": scope,
            "status": status,
            "source_kind": source_kind,
            "source_message_id": source_message_id,
        }
        return self._save(
            project_id,
            fields=fields,
            agreement_id=None,
            expected_revision=None,
            supersedes=supersedes,
            request_id=request_id,
        )

    def update(
        self,
        project_id: str,
        agreement_id: str,
        *,
        fields: Mapping[str, Any],
        expected_revision: str | None,
        supersedes: Sequence[str],
        request_id: str,
    ) -> dict[str, Any]:
        return self._save(
            project_id,
            fields=dict(fields),
            agreement_id=agreement_id,
            expected_revision=expected_revision,
            supersedes=supersedes,
            request_id=request_id,
        )

    # -- 内部 ---------------------------------------------------------------
    def _save(
        self,
        project_id: str,
        *,
        fields: Mapping[str, Any],
        agreement_id: str | None,
        expected_revision: str | None,
        supersedes: Sequence[str],
        request_id: str,
    ) -> dict[str, Any]:
        pid = ProjectId(project_id)
        key = f"agreement-save:{pid.value}:{request_id}"
        prior = self._repo.find_commit_result(pid, key)
        if prior is not None and prior.commit_id is not None:
            changed = prior.changed_artifact_ids
            if changed:
                entry = self._entry_at(pid, prior.commit_id, changed[0].value)
                return self._result(
                    prior.status, prior.commit_id.value, entry, supersedes=(), replayed=True
                )

        branch = self._repo.get_branch(pid, "main")
        if branch is None:
            raise _violation(
                "REPO_BRANCH_NOT_FOUND",
                f"agreement.save({pid.value})",
                f"作品 {pid.value} 没有 main 分支",
                "先创建作品，再保存本书约定",
                project_id=pid.value,
            )
        head = branch.head_commit_id
        catalog = self._catalog(pid, head)
        try:
            if agreement_id is None:
                change = domain.create_agreement(
                    catalog,
                    lambda _kind: self._ids.new_id("art"),
                    fields=fields,
                    supersedes=supersedes,
                )
            else:
                change = domain.revise_agreement(
                    catalog,
                    lambda _kind: self._ids.new_id("art"),
                    agreement_id=agreement_id,
                    fields=fields,
                    expected_revision=expected_revision,
                    supersedes=supersedes,
                )
        except StoryViolation as exc:
            raise _as_violation(exc) from exc

        if change.is_empty():
            entry = self._entry_at(pid, head, agreement_id or "")
            return self._result(NO_CHANGE, None, entry, supersedes=(), replayed=False)

        saved_id = change.artifact_changes[0].artifact_id.value
        result = self._repo.commit(
            CommitRequest(
                project_id=pid,
                branch_id=branch.id,
                expected_head=head,
                actor=_AUTHOR,
                idempotency_key=key,
                summary=change.summary or "保存本书约定",
                changes=change.artifact_changes,
                relations=change.relation_changes,
                events=(
                    (
                        "agreement.saved",
                        {"agreement_id": saved_id, "supersedes": list(supersedes)},
                    ),
                ),
            )
        )
        if result.commit_id is None:
            entry = self._entry_at(pid, head, saved_id)
            return self._result(NO_CHANGE, None, entry, supersedes=(), replayed=False)
        entry = self._entry_at(pid, result.commit_id, saved_id)
        return self._result(
            result.status,
            result.commit_id.value,
            entry,
            supersedes=tuple(supersedes),
            replayed=False,
        )

    def _head(self, pid: ProjectId) -> CommitId | None:
        branch = self._repo.get_branch(pid, "main")
        return branch.head_commit_id if branch is not None else None

    def _catalog(self, pid: ProjectId, head: CommitId | None) -> StoryCatalog:
        if head is None:
            return StoryCatalog(commit_id=None, entries=())
        return StoryCatalog.from_snapshot(self._repo.get_snapshot(pid, head))

    def _entry_at(self, pid: ProjectId, head: CommitId | None, agreement_id: str) -> StoryEntry:
        catalog = self._catalog(pid, head)
        entry = catalog.get(agreement_id) if agreement_id else None
        if entry is None or entry.kind != domain.KIND:
            raise _violation(
                "AGREEMENT_NOT_FOUND",
                f"agreement.get({pid.value})",
                f"作品 {pid.value} 没有本书约定 {agreement_id!r}",
                "刷新约定列表后重新选择；已停用的约定仍可用 include_retired 读到",
                project_id=pid.value,
                agreement_id=agreement_id,
            )
        return entry

    def _view(self, entry: StoryEntry) -> dict[str, Any]:
        agreement = domain.BookAgreement.from_content(entry.content)
        return {
            "id": entry.artifact_id,
            "revision_id": entry.revision_id,
            "statement": agreement.statement,
            "topic": agreement.topic,
            "topic_label": domain.AGREEMENT_TOPICS.get(agreement.topic, agreement.topic),
            "scope": agreement.scope,
            "scope_label": domain.AGREEMENT_SCOPES.get(agreement.scope, agreement.scope),
            "status": agreement.status,
            "status_label": domain.AGREEMENT_STATUSES.get(agreement.status, agreement.status),
            "source_kind": agreement.source_kind,
            "source_label": domain.SOURCE_KINDS.get(agreement.source_kind, agreement.source_kind),
            "source_message_id": agreement.source_message_id,
            "retired_reason": agreement.retired_reason,
            "retired_reason_label": domain.RETIRE_REASONS.get(agreement.retired_reason, ""),
            "superseded_by": agreement.superseded_by,
        }

    def _result(
        self,
        status: str,
        commit_id: str | None,
        entry: StoryEntry,
        *,
        supersedes: Sequence[str],
        replayed: bool,
    ) -> dict[str, Any]:
        return {
            "status": status,
            "commit_id": commit_id,
            "agreement": self._view(entry),
            "replaced": list(supersedes),
            "replayed": replayed,
        }
