"""Repository 端口：只读查询、原子提交、Lease 与事务存储接缝。

Phase 11 冻结的方法名全部保留；Phase 12（T12.1/T12.2）把签名从
``Mapping[str, object]`` 收窄为领域值对象——收窄只变具体，不改名、
不删版本条件（``expected_head`` / ``old_revision_id`` / ``expected_version``）。

``RepositoryStorage`` 是 CommitService 驱动固定提交顺序的低层接缝：
内存与 SQLite 各自实现同一套事务原语，提交算法只有一份
（application/services/commit_service.py），故障注入与契约测试因此对两个
后端完全同构。
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
from typing import Protocol, runtime_checkable

from hastings.domain.repository.ids import (
    ArtifactId,
    BranchId,
    CommitId,
    JobId,
    ProjectId,
    RevisionId,
    Scope,
)
from hastings.domain.repository.models import (
    Artifact,
    ArtifactView,
    Branch,
    Commit,
    CommitEntry,
    Lease,
    OutboxEvent,
    Project,
    RelationRevision,
    RepositoryDiff,
    Revision,
    Snapshot,
)
from hastings.domain.repository.requests import (
    ArtifactQuery,
    CommitRequest,
    CreateBranchRequest,
    MoveBranchRequest,
)
from hastings.domain.repository.results import CommitResult

__all__ = [
    "CommitRepository",
    "LeaseRepository",
    "RepositoryReader",
    "RepositoryStorage",
    "RepositoryTxn",
]

# diff 的缺省作用域（frozen dataclass，共享实例安全；避免 B008 函数调用默认值）
DEFAULT_SCOPE = Scope()


@runtime_checkable
class RepositoryReader(Protocol):
    """只读端口：Snapshot、Artifact 与 Diff 查询（ADR-002）。"""

    def get_project(self, project_id: ProjectId) -> Project | None:
        """读取项目；不存在返回 None（合法空结果，非异常）。"""

    def find_commit_result(self, project_id: ProjectId, key: str) -> CommitResult | None:
        """读取已原子提交的操作结果，供工作流在重启后跳过已提交步骤。"""

    def commit_history(self, project_id: ProjectId, at_commit_id: CommitId) -> tuple[Commit, ...]:
        """读取所选版本的 first-parent 历史，用于精确关联修复证据。"""

    def get_branch(self, project_id: ProjectId, name: str) -> Branch | None:
        """按项目与分支名读取分支；不存在返回 None。"""

    def get_snapshot(self, project_id: ProjectId, commit_id: CommitId) -> Snapshot:
        """读取某 Commit 下的项目快照；commit 不存在或跨项目即违规。"""

    def get_artifact(self, artifact_id: ArtifactId, at_commit_id: CommitId) -> ArtifactView | None:
        """读取某 Artifact 在指定 Commit 的版本视图；该提交尚未引入时返回 None。"""

    def list_artifacts(self, query: ArtifactQuery, at_commit_id: CommitId) -> list[ArtifactView]:
        """列出某 Commit 下的 Artifact 版本视图，可按 kind 过滤。"""

    def diff(
        self,
        before_commit_id: CommitId,
        after_commit_id: CommitId,
        scope: Scope = DEFAULT_SCOPE,
    ) -> RepositoryDiff:
        """比较两个 Commit 间的 Artifact 与关系差异（两提交必须同项目）。"""


@runtime_checkable
class CommitRepository(Protocol):
    """写端口：原子提交、分支创建与移动（乐观版本检查）。

    agents 层禁止 import 本端口（依赖规则：工作者只返回 Proposal，ADR-003）。
    """

    def create_project(self, name: str) -> Project:
        """创建项目及其 main 分支（head 为空，等待首个提交）。"""

    def commit(self, request: CommitRequest) -> CommitResult:
        """按 CommitRequest 原子提交；``expected_head`` 不匹配时整体失败（ADR-002/007）。"""

    def create_branch(self, request: CreateBranchRequest) -> Branch:
        """从指定 Commit 创建分支；分支名项目内唯一。"""

    def move_branch(self, request: MoveBranchRequest) -> Branch:
        """移动分支 head；``expected_version`` 用于乐观并发检查，只允许前移。"""


@runtime_checkable
class LeaseRepository(Protocol):
    """跨会话短期互斥（不变量 9）；到期判定使用注入 Clock，无 sleep。"""

    def acquire(self, resource_key: str, owner_job_id: JobId, ttl_seconds: float) -> Lease:
        """获取租约；资源被其他存活 owner 持有时抛 REPO_LEASE_CONFLICT。"""

    def renew(self, lease: Lease, ttl_seconds: float) -> Lease:
        """续租；owner 或 version 不匹配、已过期时抛 REPO_LEASE_CONFLICT。"""

    def release(self, lease: Lease) -> None:
        """释放租约；owner 或 version 不匹配时抛 REPO_LEASE_CONFLICT。"""


class RepositoryTxn(Protocol):
    """单事务存储原语。CommitService 按固定顺序调用；实现保证原子性。

    读方法返回 None/空是合法空结果；任何写点抛出的异常都必须使整个事务回滚，
    不留半提交（计划 T12.4 故障注入验收）。
    """

    # ---- 读 ----
    def get_project(self, project_id: ProjectId) -> Project | None: ...
    def get_branch(self, branch_id: BranchId) -> Branch | None: ...
    def branch_by_name(self, project_id: ProjectId, name: str) -> Branch | None: ...
    def get_artifact_record(self, artifact_id: ArtifactId) -> Artifact | None: ...
    def artifacts_of_project(self, project_id: ProjectId) -> tuple[Artifact, ...]: ...
    def get_commit(self, commit_id: CommitId) -> Commit | None: ...
    def commit_chain(self, commit_id: CommitId) -> tuple[Commit, ...]:
        """返回 root→commit 的链（沿 parent_ids[0] 回溯）；commit 必须存在。"""

    def entries_for_commits(
        self, commit_ids: tuple[CommitId, ...]
    ) -> dict[CommitId, tuple[CommitEntry, ...]]: ...
    def relation_rows_for_commits(
        self, commit_ids: tuple[CommitId, ...]
    ) -> tuple[RelationRevision, ...]: ...
    def get_revision(self, revision_id: RevisionId) -> Revision | None: ...

    def get_idempotent_result(
        self, project_id: ProjectId, key: str
    ) -> tuple[str, CommitResult] | None:
        """返回 ``(请求指纹, 结果)``；键不存在返回 None。

        指纹比对由 CommitService 负责：同键不同指纹必须显式冲突，
        不得静默返回旧结果。
        """

    def get_lease(self, resource_key: str) -> Lease | None: ...
    def next_sequence(self, project_id: ProjectId) -> int: ...
    def next_outbox_sequence(self, project_id: ProjectId) -> int: ...

    # ---- 写 ----
    def insert_project(self, project: Project) -> None: ...
    def insert_branch(self, branch: Branch) -> None: ...
    def insert_artifact(self, artifact: Artifact) -> None: ...
    def insert_revision(self, revision: Revision) -> None: ...
    def insert_relation(self, row: RelationRevision) -> None: ...
    def insert_commit(self, commit: Commit, entries: tuple[CommitEntry, ...]) -> None: ...
    def set_branch_head(
        self, branch_id: BranchId, new_head: CommitId, expected_version: int
    ) -> None: ...
    def append_outbox_events(self, events: tuple[OutboxEvent, ...]) -> None: ...

    def put_idempotent_result(
        self, project_id: ProjectId, key: str, fingerprint: str, result: CommitResult
    ) -> None: ...


@runtime_checkable
class RepositoryStorage(Protocol):
    """事务存储接缝：CommitService 唯一依赖的低层端口。"""

    def transaction(self) -> AbstractContextManager[RepositoryTxn]:
        """开启事务；正常退出提交，异常退出回滚。"""

    def now(self) -> datetime:
        """存储层持有的 Clock 注入（与 Lease/时间戳共用同一时间源）。"""
