"""Story 写命令的应用层入口（Phase 13 T13.10）。

领域命令是纯函数（``fn(catalog, new_id, ...) -> DomainChange``），不触达
Repository；本 handler 是它们唯一的写路径封装，固定五步：

1. 幂等重放（见下）——同按键重试先返回首次结果，不重跑命令；
2. 解析目标分支（默认 ``main``）并取 head；head 为空表示空目录（首次提交）；
3. 取 head 的 Snapshot，投影为 ``StoryCatalog``；
4. 执行领域命令，ID 由 ``IdGenerator`` 适配成 ``art_*``；
5. 领域 ``StoryViolation`` 在边界转成应用错误信封 ``ViolationError``
   （``category=VIOLATION``，四要素原样透传，不做「人话翻译」——四透镜①）；
6. 领域 no-op（``DomainChange.is_empty()``）→ 合法空结果，不提交、不抛异常
   （四透镜③）；否则组装 ``CommitRequest`` 交 Commit Service。

幂等重放（评审 P1）：Commit Service 的幂等键比对的是「写什么」的内容指纹，
而命令每次执行都会生成新的 Artifact ID，因此**重放必须早于命令重执行**——否则
同一键的第二次请求会带上新 head、新 ID，被判成「同键不同请求」
（REPO_IDEMPOTENCY_CONFLICT），网络超时后的正常重试反而失败。重放按
``(project_id, idempotency_key)`` 索引，并用「命令身份 + 规范化意图」的稳定指纹
校验确实是同一逻辑操作。

已知留白：重放表在 handler 实例内（进程内）。跨进程持久化的幂等重放依赖
Commit Service 的内容级幂等；重放表的持久化留待 Phase 14「最小 API」真正落地
（当前阶段只交付可编程 handler，尚无长驻服务进程）。

查询走 ``application/queries/story_queries.py``，与本模块共用同一只读端口。
「最小 API」到这一层为止：HTTP/CLI 外壳留待 Phase 14，本阶段只交付
handler + queries 两个可编程入口。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial

from hastings.application.errors import ViolationError
from hastings.application.ports.ids import IdGenerator
from hastings.application.ports.repository import CommitRepository, RepositoryReader
from hastings.domain.repository import error_codes as ec
from hastings.domain.repository.ids import Actor, CommitId, ProjectId
from hastings.domain.repository.models import Project
from hastings.domain.repository.requests import CommitRequest
from hastings.domain.repository.results import COMMITTED, NO_CHANGE, CommitResult
from hastings.domain.repository.serialization import content_hash
from hastings.domain.story.catalog import StoryCatalog
from hastings.domain.story.changes import DomainChange
from hastings.domain.story.errors import StoryViolation
from hastings.domain.story.refs import NewId

__all__ = ["StoryCommand", "StoryCommandHandler", "StoryCommandResult", "story_command"]

_NO_CHANGE_SUMMARY = "命令未产生任何变化（合法空结果）"


@dataclass(frozen=True, slots=True)
class StoryCommand:
    """领域命令的应用层封装：稳定身份 + 作者意图 + 纯函数本体。

    ``name`` 与 ``intent`` 构成「同一逻辑操作」的稳定指纹——Artifact ID 由每次
    执行新生成，不能承担意图标识；只有命令身份与规范化意图在重试间不变，幂等
    重放才可校验（见 ``StoryCommandHandler.execute``）。
    """

    name: str
    intent: Mapping[str, object]
    apply_fn: Callable[[StoryCatalog, NewId], DomainChange]

    def __call__(self, catalog: StoryCatalog, new_id: NewId) -> DomainChange:
        return self.apply_fn(catalog, new_id)


def story_command(func: Callable[..., DomainChange], /, **intent: object) -> StoryCommand:
    """把领域命令 ``fn(catalog, new_id, **intent)`` 绑成 ``StoryCommand``。

    ``name`` 取函数的限定名（如 ``create_character``），同一领域命令在重试间恒定。
    """
    return StoryCommand(
        name=func.__qualname__,
        intent=dict(intent),
        apply_fn=partial(func, **intent),
    )


def _intent_fingerprint(command: StoryCommand) -> str:
    """命令意图指纹：只取命令身份与规范化意图，与执行时生成的 ID 无关。

    参数值用 ``repr`` 归一，使不可 JSON 化的领域对象（如 ``DisclosureSpec``）也
    获得稳定表示；键排序保证字典顺序不影响指纹。
    """
    intent = {key: repr(value) for key, value in sorted(command.intent.items())}
    return content_hash({"name": command.name, "intent": intent})


@dataclass(frozen=True, slots=True)
class StoryCommandResult:
    """命令执行结果：状态 + 摘要 + 领域变更 + Commit（未提交时为 None）。

    ``status`` 与 CommitResult 同源（``committed`` / ``no_change``）：
    - domain no-op（无内容/关系变化）→ no_change，未触达 Commit Service；
    - Commit Service 的 no-op（与 head 有效版本内容一致）→ no_change；
    - 否则 committed，携带 ``commit_id`` / ``new_head``。
    """

    status: str
    summary: str
    domain_change: DomainChange
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


class StoryCommandHandler:
    """把领域命令接到 Commit Service 的应用层写入口。"""

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
        self._replays: dict[tuple[str, str], tuple[str, StoryCommandResult]] = {}

    def create_project(self, name: str) -> Project:
        """创建项目与 main 分支；首次提交前的 head 为空。"""
        return self._committer.create_project(name)

    def execute(
        self,
        command: StoryCommand,
        *,
        project_id: ProjectId,
        idempotency_key: str,
        summary: str,
    ) -> StoryCommandResult:
        """在项目当前 head 上执行一条领域命令，必要时提交。

        先做幂等重放（评审 P1）：同 ``(project_id, idempotency_key)`` 且意图指纹
        一致的调用直接返回首次结果，不重跑命令、不生成新 Artifact ID。同键但意图
        不同则显式冲突（与 Commit Service 同哲学：不静默返回旧结果）。
        """
        fingerprint = _intent_fingerprint(command)
        replay_key = (project_id.value, idempotency_key)
        recorded = self._replays.get(replay_key)
        if recorded is not None:
            stored_fingerprint, stored_result = recorded
            if stored_fingerprint != fingerprint:
                raise ViolationError(
                    code=ec.REPO_IDEMPOTENCY_CONFLICT,
                    where=f"story.execute({project_id.value})",
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

    def _execute_and_commit(
        self,
        command: StoryCommand,
        *,
        project_id: ProjectId,
        idempotency_key: str,
        summary: str,
    ) -> StoryCommandResult:
        """读 head → 执行命令 → 提交；不含幂等重放（由 ``execute`` 前置）。"""
        branch = self._reader.get_branch(project_id, self._branch_name)
        if branch is None:
            raise ViolationError(
                code=ec.REPO_BRANCH_NOT_FOUND,
                where=f"story.execute({project_id.value})",
                why=f"项目 {project_id.value} 下没有名为 {self._branch_name!r} 的分支",
                fix="先用 create_project 建立项目与 main 分支，再提交故事命令",
                details={"project_id": project_id.value, "branch_name": self._branch_name},
            )
        head = branch.head_commit_id
        catalog = self._catalog_at(project_id, head)
        domain_change = self._run(command, catalog)
        if domain_change.is_empty():
            return StoryCommandResult(
                status=NO_CHANGE,
                summary=domain_change.summary or _NO_CHANGE_SUMMARY,
                domain_change=domain_change,
            )
        result = self._committer.commit(
            CommitRequest(
                project_id=project_id,
                branch_id=branch.id,
                expected_head=head,
                actor=self._actor,
                idempotency_key=idempotency_key,
                summary=summary,
                changes=domain_change.artifact_changes,
                relations=domain_change.relation_changes,
            )
        )
        return StoryCommandResult(
            status=result.status,
            summary=result.no_change_reason or summary,
            domain_change=domain_change,
            commit=result,
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _new_id(self, kind: str) -> str:
        """领域 NewId 适配：kind 仅作语义提示，ArtifactId 统一用 ``art`` 前缀。"""
        del kind
        return self._ids.new_id("art")

    def _catalog_at(self, project_id: ProjectId, head: CommitId | None) -> StoryCatalog:
        if head is None:
            return StoryCatalog(commit_id=None, entries=())
        return StoryCatalog.from_snapshot(self._reader.get_snapshot(project_id, head))

    def _run(self, command: StoryCommand, catalog: StoryCatalog) -> DomainChange:
        try:
            return command(catalog, self._new_id)
        except StoryViolation as exc:
            # 边界转换：领域不认应用错误信封，应用层不认领域错误类型（四透镜①）
            raise ViolationError(
                code=exc.code,
                where=exc.where,
                why=exc.why,
                fix=exc.fix,
                details=exc.details,
            ) from exc
