"""Manuscript 写命令的应用层入口（Phase 14 T14.2）。

领域命令是纯函数（``fn(catalog, new_id, **intent) -> ManuscriptChange``），不触达
Repository；本 handler 是它们唯一的写路径封装，与 ``StoryCommandHandler`` 同构，
固定六步：

1. 幂等重放——同按键重试先返回首次结果，不重跑命令（重放必须早于命令重执行：
   命令每次都生成新 Artifact ID，晚重放会带上新 head、新 ID 被判成同键不同请求）；
2. 解析目标分支（默认 ``main``）并取 head；head 为空表示空目录（首次提交）；
3. 取 head 的 Snapshot，投影为 ``ManuscriptCatalog``；
4. 执行领域命令，ID 由 ``IdGenerator`` 适配成 ``art_*``；
5. 领域 ``ManuscriptViolation`` 在边界转成应用错误信封 ``ViolationError``
   （四要素原样透传，不做「人话翻译」——四透镜①）；
6. 领域 no-op（``ManuscriptChange.is_empty()``）→ 合法空结果，不提交、不抛异常
   （四透镜③）；否则组装 ``CommitRequest`` 交 Commit Service（Outbox 事件由
   Commit Service 与 Commit 同事务写入，ADR-007）。

保存意图的**范围**由领域命令签名保证：``chapter_id`` 是必填关键字，命令只在该章
的活动段落上操作（计划 §T14.2「保存意图只允许当前章/场景范围」）。人工保存用例
（``workflows/manual_edit.py``）与 Agent Patch（T14.4）复用同一 handler，区别只在
调用者与 Patch 接受流程。

已知留白：重放表在 handler 实例内（进程内），跨进程持久化的幂等重放依赖 Commit
Service 的内容级幂等（与 StoryCommandHandler 相同）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial

from hastings.application.errors import ViolationError
from hastings.application.ports.ids import IdGenerator
from hastings.application.ports.repository import CommitRepository, RepositoryReader
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.manuscript.commands import NewId
from hastings.domain.manuscript.errors import ManuscriptViolation
from hastings.domain.manuscript.models import ManuscriptChange
from hastings.domain.repository import error_codes as ec
from hastings.domain.repository.ids import Actor, CommitId, ProjectId
from hastings.domain.repository.models import Project
from hastings.domain.repository.requests import CommitRequest
from hastings.domain.repository.results import COMMITTED, NO_CHANGE, CommitResult
from hastings.domain.repository.serialization import content_hash

__all__ = [
    "ManuscriptCommand",
    "ManuscriptCommandHandler",
    "ManuscriptCommandResult",
    "manuscript_command",
]

_NO_CHANGE_SUMMARY = "正文未产生任何变化（合法空结果）"


@dataclass(frozen=True, slots=True)
class ManuscriptCommand:
    """领域命令的应用层封装：稳定身份 + 作者意图 + 纯函数本体。

    ``name`` 与 ``intent`` 构成「同一逻辑操作」的稳定指纹——Artifact ID 由每次
    执行新生成，不能承担意图标识；只有命令身份与规范化意图在重试间不变。
    """

    name: str
    intent: Mapping[str, object]
    apply_fn: Callable[[ManuscriptCatalog, NewId], ManuscriptChange]

    def __call__(self, catalog: ManuscriptCatalog, new_id: NewId) -> ManuscriptChange:
        return self.apply_fn(catalog, new_id)


def manuscript_command(
    func: Callable[..., ManuscriptChange], /, **intent: object
) -> ManuscriptCommand:
    """把领域命令 ``fn(catalog, new_id, **intent)`` 绑成 ``ManuscriptCommand``。

    ``name`` 取函数的限定名（如 ``manuscript.commands.apply_edits``），重试间恒定。
    """
    return ManuscriptCommand(
        name=func.__qualname__,
        intent=dict(intent),
        apply_fn=partial(func, **intent),
    )


def _intent_fingerprint(command: ManuscriptCommand) -> str:
    """命令意图指纹：只取命令身份与规范化意图，与执行时生成的 ID 无关。

    参数值用 ``repr`` 归一（EditOperation 等冻结 dataclass 的 repr 稳定），
    键排序保证字典顺序不影响指纹。
    """
    intent = {key: repr(value) for key, value in sorted(command.intent.items())}
    return content_hash({"name": command.name, "intent": intent})


@dataclass(frozen=True, slots=True)
class ManuscriptCommandResult:
    """命令执行结果：状态 + 摘要 + 领域变更 + Commit（未提交时为 None）。

    ``status`` 与 CommitResult 同源（``committed`` / ``no_change``）：
    领域 no-op 或 Commit Service 的 no-op 均为 no_change，不产生 Commit。
    """

    status: str
    summary: str
    change: ManuscriptChange
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


class ManuscriptCommandHandler:
    """把正文领域命令接到 Commit Service 的应用层写入口。"""

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
        # 幂等重放表：(project_id, idempotency_key) -> (意图指纹, 首次结果)
        self._replays: dict[tuple[str, str], tuple[str, ManuscriptCommandResult]] = {}

    def create_project(self, name: str) -> Project:
        """创建项目与 main 分支；首次提交前的 head 为空。"""
        return self._committer.create_project(name)

    def execute(
        self,
        command: ManuscriptCommand,
        *,
        project_id: ProjectId,
        idempotency_key: str,
        summary: str,
    ) -> ManuscriptCommandResult:
        """在项目当前 head 上执行一条正文命令，必要时提交。

        同 ``(project_id, idempotency_key)`` 且意图指纹一致直接返回首次结果，不重跑
        命令、不生成新 Artifact ID；同键但意图不同则显式冲突（不静默返回旧结果）。
        """
        fingerprint = _intent_fingerprint(command)
        replay_key = (project_id.value, idempotency_key)
        recorded = self._replays.get(replay_key)
        if recorded is not None:
            stored_fingerprint, stored_result = recorded
            if stored_fingerprint != fingerprint:
                raise ViolationError(
                    code=ec.REPO_IDEMPOTENCY_CONFLICT,
                    where=f"manuscript.execute({project_id.value})",
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
        command: ManuscriptCommand,
        *,
        project_id: ProjectId,
        idempotency_key: str,
        summary: str,
    ) -> ManuscriptCommandResult:
        branch = self._reader.get_branch(project_id, self._branch_name)
        if branch is None:
            raise ViolationError(
                code=ec.REPO_BRANCH_NOT_FOUND,
                where=f"manuscript.execute({project_id.value})",
                why=f"项目 {project_id.value} 下没有名为 {self._branch_name!r} 的分支",
                fix="先用 create_project 建立项目与 main 分支，再提交正文命令",
                details={"project_id": project_id.value, "branch_name": self._branch_name},
            )
        head = branch.head_commit_id
        catalog = self._catalog_at(project_id, head)
        change = self._run(command, catalog)
        if change.is_empty():
            return ManuscriptCommandResult(
                status=NO_CHANGE,
                summary=change.summary or _NO_CHANGE_SUMMARY,
                change=change,
            )
        result = self._committer.commit(
            CommitRequest(
                project_id=project_id,
                branch_id=branch.id,
                expected_head=head,
                actor=self._actor,
                idempotency_key=idempotency_key,
                summary=summary,
                changes=change.artifact_changes,
                events=change.events,  # 业务事件与 Commit 同事务落盘（ADR-007）
            )
        )
        return ManuscriptCommandResult(
            status=result.status,
            summary=result.no_change_reason or summary,
            change=change,
            commit=result,
        )

    def _new_id(self, kind: str) -> str:
        """领域 NewId 适配：kind 仅作语义提示，ArtifactId 统一用 ``art`` 前缀。"""
        del kind
        return self._ids.new_id("art")

    def _catalog_at(self, project_id: ProjectId, head: CommitId | None) -> ManuscriptCatalog:
        if head is None:
            return ManuscriptCatalog(commit_id=None, entries=())
        return ManuscriptCatalog.from_snapshot(self._reader.get_snapshot(project_id, head))

    def _run(self, command: ManuscriptCommand, catalog: ManuscriptCatalog) -> ManuscriptChange:
        try:
            return command(catalog, self._new_id)
        except ManuscriptViolation as exc:
            # 边界转换：领域不认应用错误信封，应用层不认领域错误类型（四透镜①）
            raise ViolationError(
                code=exc.code,
                where=exc.where,
                why=exc.why,
                fix=exc.fix,
                details=exc.details,
            ) from exc
