"""ChangeSet 写命令的应用层入口（Phase 15 T15.1）。

与 ``ManuscriptCommandHandler`` / ``StoryCommandHandler`` 同构，固定六步：
幂等重放 → 解析分支取 head → Snapshot 投影 ChangesCatalog → 执行纯函数命令 →
领域 ``ChangesViolation`` 转应用信封 → 组装 CommitRequest 交 Commit Service
（事件与 Commit 同事务落盘，ADR-007）。

Phase 15：命令指纹与领域结果随业务变化同 Commit 保存为 command_result，
跨实例重试读取原结果，不重新执行命令或分配 ID；实例缓存仅用于合法空结果。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from functools import partial

from hastings.application.errors import ViolationError
from hastings.application.ports.ids import IdGenerator
from hastings.application.ports.repository import CommitRepository, RepositoryReader
from hastings.domain.changes import error_codes as ec
from hastings.domain.changes.catalog import ChangesCatalog
from hastings.domain.changes.commands import NewId
from hastings.domain.changes.errors import ChangesViolation
from hastings.domain.changes.models import ChangeSetChange
from hastings.domain.repository import error_codes as repo_ec
from hastings.domain.repository.ids import Actor, ArtifactId, CommitId, ProjectId, RevisionId
from hastings.domain.repository.models import Project
from hastings.domain.repository.requests import ArtifactChange, CommitRequest
from hastings.domain.repository.results import COMMITTED, NO_CHANGE, CommitResult
from hastings.domain.repository.serialization import content_hash

__all__ = [
    "ChangeSetCommand",
    "ChangeSetCommandHandler",
    "ChangeSetCommandResult",
    "change_set_command",
]


@dataclass(frozen=True, slots=True)
class ChangeSetCommand:
    """领域命令的应用层封装：稳定身份 + 意图 + 纯函数本体。"""

    name: str
    intent: Mapping[str, object]
    apply_fn: Callable[[ChangesCatalog, NewId], ChangeSetChange]

    def __call__(self, catalog: ChangesCatalog, new_id: NewId) -> ChangeSetChange:
        return self.apply_fn(catalog, new_id)


def change_set_command(
    func: Callable[..., ChangeSetChange], /, **intent: object
) -> ChangeSetCommand:
    """把领域命令 ``fn(catalog, new_id, **intent)`` 绑成 ``ChangeSetCommand``。"""
    return ChangeSetCommand(
        name=func.__qualname__,
        intent=dict(intent),
        apply_fn=partial(func, **intent),
    )


def _intent_fingerprint(command: ChangeSetCommand) -> str:
    """命令意图指纹：命令身份 + 规范化意图（repr 归一、键排序）。"""
    intent = {key: repr(value) for key, value in sorted(command.intent.items()) if key != "at"}
    return content_hash({"name": command.name, "intent": intent})


@dataclass(frozen=True, slots=True)
class ChangeSetCommandResult:
    """命令执行结果：状态 + 摘要 + 领域变更 + Commit（未提交时为 None）。"""

    status: str
    summary: str
    change: ChangeSetChange
    commit: CommitResult | None = None

    @property
    def is_committed(self) -> bool:
        return self.status == COMMITTED

    @property
    def is_no_change(self) -> bool:
        return self.status == NO_CHANGE

    @property
    def commit_id(self) -> str | None:
        return self.commit.commit_id.value if self.commit and self.commit.commit_id else None

    @property
    def new_head(self) -> str | None:
        return self.commit.new_head.value if self.commit and self.commit.new_head else None


class ChangeSetCommandHandler:
    """把变更领域命令接到 Commit Service 的应用层写入口。"""

    def __init__(
        self,
        reader: RepositoryReader,
        committer: CommitRepository,
        ids: IdGenerator,
        actor: Actor,
        *,
        branch_name: str = "main",
    ) -> None:
        self._reader = reader
        self._committer = committer
        self._ids = ids
        self._actor = actor
        self._branch_name = branch_name
        self._replays: dict[tuple[str, str], tuple[str, ChangeSetCommandResult]] = {}

    def create_project(self, name: str) -> Project:
        """创建项目与 main 分支；首次提交前的 head 为空。"""
        return self._committer.create_project(name)

    def execute(
        self,
        command: ChangeSetCommand,
        *,
        project_id: ProjectId,
        idempotency_key: str,
        summary: str,
    ) -> ChangeSetCommandResult:
        """在项目当前 head 上执行一条变更命令并提交。

        同 ``(project_id, idempotency_key)`` 且意图指纹一致直接返回首次结果；
        同键不同意图显式冲突（不静默返回旧结果）。
        """
        fingerprint = _intent_fingerprint(command)
        replay_key = (project_id.value, idempotency_key)
        result_id = (
            f"command:{content_hash([project_id.value, self._branch_name, idempotency_key])}"
        )
        persisted = self._reader.find_commit_result(project_id, idempotency_key)
        if persisted is not None and persisted.commit_id is not None:
            view = self._reader.get_artifact(ArtifactId(result_id), persisted.commit_id)
            if (
                view is None
                or view.revision is None
                or view.revision.content["fingerprint"] != fingerprint
            ):
                raise ViolationError(
                    code=ec.CHANGES_INVALID_COMMAND,
                    where="changes.replay",
                    why="该请求键已用于其他命令或不同意图",
                    fix="重试保留原意图；新操作使用新请求键",
                )
            saved = view.revision.content
            body = saved["change"]
            change = ChangeSetChange(
                artifact_changes=tuple(
                    ArtifactChange(
                        ArtifactId(c["artifact_id"]["value"]),
                        c["content"],
                        RevisionId(c["old_revision_id"]["value"]) if c["old_revision_id"] else None,
                        c["kind"],
                    )
                    for c in body["artifact_changes"]
                ),
                events=tuple((e[0], e[1]) for e in body["events"]),
                summary=body["summary"],
            )
            return ChangeSetCommandResult(persisted.status, saved["summary"], change, persisted)
        recorded = self._replays.get(replay_key)
        if recorded is not None:
            stored_fingerprint, stored_result = recorded
            if stored_fingerprint != fingerprint:
                raise ViolationError(
                    code=ec.CHANGES_INVALID_COMMAND,
                    where=f"changes.execute({project_id.value})",
                    why=f"幂等键 {idempotency_key!r} 已被另一条命令意图使用",
                    fix=(
                        "为本次新操作生成新的 idempotency_key；同一个键只用于同一逻辑操作的全部重试"
                    ),
                    details={"key": idempotency_key, "project_id": project_id.value},
                )
            return stored_result
        result = self._execute_and_commit(
            command, project_id=project_id, idempotency_key=idempotency_key, summary=summary
        )
        self._replays[replay_key] = (fingerprint, result)
        return result

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _execute_and_commit(
        self,
        command: ChangeSetCommand,
        *,
        project_id: ProjectId,
        idempotency_key: str,
        summary: str,
    ) -> ChangeSetCommandResult:
        branch = self._reader.get_branch(project_id, self._branch_name)
        if branch is None:
            raise ViolationError(
                code=repo_ec.REPO_BRANCH_NOT_FOUND,
                where=f"changes.execute({project_id.value})",
                why=f"项目 {project_id.value} 下没有名为 {self._branch_name!r} 的分支",
                fix="先用 create_project 建立项目与 main 分支，再执行变更命令",
                details={"project_id": project_id.value, "branch_name": self._branch_name},
            )
        head = branch.head_commit_id
        catalog = self._catalog_at(project_id, head)
        change = self._run(command, catalog)
        result_digest = content_hash([project_id.value, self._branch_name, idempotency_key])
        result_artifact_id = f"command:{result_digest}"
        if command.name == "issue_approval_receipt" or (
            command.name == "advance_change_set" and command.intent.get("target") == "approved"
        ):
            cs_id = str(command.intent["change_set_id"])
            has_work = any(
                e.content.get("change_set_id") == cs_id and e.content.get("items")
                for e in catalog.by_kind("changes.impact_report")
            )
            if has_work and head is not None:
                base = catalog.require_change_set(cs_id, where="changes.approval").content[
                    "base_commit_id"
                ]
                delta = self._reader.diff(CommitId(base), head)
                if (
                    any(
                        d.kind.startswith(("story.", "narrative.", "manuscript."))
                        for d in delta.artifact_changes
                    )
                    or delta.relations_added
                    or delta.relations_removed
                ):
                    raise ViolationError(
                        code=ec.CHANGES_BASE_STALE,
                        where="changes.approval",
                        why="分析后故事内容或依赖关系已变化，旧报告不能授权执行",
                        fix="在最新 Commit 上建立变更集并重新分析、批准",
                    )
        if change.is_empty():
            return ChangeSetCommandResult(status=NO_CHANGE, summary=summary, change=change)
        result = self._committer.commit(
            CommitRequest(
                project_id=project_id,
                branch_id=branch.id,
                expected_head=head,
                actor=self._actor,
                idempotency_key=idempotency_key,
                summary=summary,
                changes=(
                    *change.artifact_changes,
                    ArtifactChange(
                        ArtifactId(result_artifact_id),
                        {
                            "fingerprint": _intent_fingerprint(command),
                            "summary": summary,
                            "change": asdict(change),
                        },
                        kind="changes.command_result",
                    ),
                ),
                events=change.events,
            )
        )
        return ChangeSetCommandResult(
            status=result.status,
            summary=result.no_change_reason or summary,
            change=change,
            commit=result,
        )

    def _new_id(self, kind: str) -> str:
        del kind
        return self._ids.new_id("art")

    def _catalog_at(self, project_id: ProjectId, head: CommitId | None) -> ChangesCatalog:
        if head is None:
            return ChangesCatalog(commit_id=None, entries=())
        return ChangesCatalog.from_snapshot(self._reader.get_snapshot(project_id, head))

    def _run(self, command: ChangeSetCommand, catalog: ChangesCatalog) -> ChangeSetChange:
        try:
            return command(catalog, self._new_id)
        except ChangesViolation as exc:
            # 边界转换：四要素原样透传，不做「人话翻译」（四透镜①）
            raise ViolationError(
                code=exc.code,
                where=exc.where,
                why=exc.why,
                fix=exc.fix,
                details=exc.details,
            ) from exc
