"""内存 Repository 实现：与 SQLite 共享同一契约测试的快速后端（Phase 12 T12.2）。

关键约束：内存实现不为图省事跳过 SQLite 必须遵守的不变量——事务语义靠
「进入时克隆快照、异常时整体还原」模拟回滚；head/version 检查与 SQLite
完全一致，否则契约测试失去意义（计划 T12.2）。
"""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from threading import RLock
from typing import TYPE_CHECKING, Any

from hastings.application.errors import ViolationError
from hastings.application.ports.clock import Clock
from hastings.application.ports.ids import IdGenerator
from hastings.application.services.commit_service import CommitService
from hastings.domain.repository import error_codes as ec
from hastings.domain.repository.ids import (
    ArtifactId,
    BranchId,
    CommitId,
    JobId,
    ProjectId,
    RevisionId,
)
from hastings.domain.repository.models import (
    Artifact,
    Branch,
    Commit,
    CommitEntry,
    Lease,
    OutboxEvent,
    Project,
    RelationRevision,
    Revision,
)
from hastings.domain.repository.results import CommitResult

if TYPE_CHECKING:
    pass

__all__ = ["MemoryRepository", "MemoryStorage", "build_memory_repository"]


def _deep[T](value: T) -> T:
    """深拷贝 JSON 值树：内存后端不保存/不交出与调用者共享的引用。

    SQLite 每次写入经序列化、读取经反序列化，天然隔离；内存实现必须
    显式做同一件事，否则 Revision 历史会被调用者改写（违反不变量 1），
    且双后端契约测试失去同构性。
    """
    return copy.deepcopy(value)


def _violation(code: str, where: str, why: str, fix: str, **details: Any) -> ViolationError:
    return ViolationError(code=code, where=where, why=why, fix=fix, details=details)


@dataclass
class _MemoryData:
    """全部状态；frozen 模型共享安全，回滚只需还原各容器。"""

    projects: dict[str, Project] = field(default_factory=dict)
    artifacts: dict[str, Artifact] = field(default_factory=dict)
    revisions: dict[str, Revision] = field(default_factory=dict)
    commits: dict[str, Commit] = field(default_factory=dict)
    entries: dict[str, list[CommitEntry]] = field(default_factory=dict)
    branches: dict[str, Branch] = field(default_factory=dict)
    relations: dict[str, RelationRevision] = field(default_factory=dict)
    outbox: dict[str, OutboxEvent] = field(default_factory=dict)
    idempotency: dict[tuple[str, str], CommitResult] = field(default_factory=dict)
    leases: dict[str, Lease] = field(default_factory=dict)

    def clone(self) -> _MemoryData:
        return _MemoryData(
            projects=dict(self.projects),
            artifacts=dict(self.artifacts),
            revisions=dict(self.revisions),
            commits=dict(self.commits),
            entries={k: list(v) for k, v in self.entries.items()},
            branches=dict(self.branches),
            relations=dict(self.relations),
            outbox=dict(self.outbox),
            idempotency=dict(self.idempotency),
            leases=dict(self.leases),
        )


class MemoryTxn:
    """事务原语的内存实现；所有方法直接操作共享 _MemoryData。"""

    def __init__(self, data: _MemoryData) -> None:
        self._data = data

    # ---- 读 ----
    def get_project(self, project_id: ProjectId) -> Project | None:
        return self._data.projects.get(project_id.value)

    def get_branch(self, branch_id: BranchId) -> Branch | None:
        return self._data.branches.get(branch_id.value)

    def branch_by_name(self, project_id: ProjectId, name: str) -> Branch | None:
        for branch in self._data.branches.values():
            if branch.project_id == project_id and branch.name == name:
                return branch
        return None

    def get_artifact_record(self, artifact_id: ArtifactId) -> Artifact | None:
        return self._data.artifacts.get(artifact_id.value)

    def artifacts_of_project(self, project_id: ProjectId) -> tuple[Artifact, ...]:
        return tuple(a for a in self._data.artifacts.values() if a.project_id == project_id)

    def get_commit(self, commit_id: CommitId) -> Commit | None:
        return self._data.commits.get(commit_id.value)

    def commit_chain(self, commit_id: CommitId) -> tuple[Commit, ...]:
        chain: list[Commit] = []
        current: CommitId | None = commit_id
        while current is not None:
            commit = self._data.commits.get(current.value)
            if commit is None:
                raise _violation(
                    ec.REPO_INTEGRITY_ERROR,
                    f"commit_chain({commit_id.value})",
                    f"历史链在 {current.value} 断裂",
                    "这是数据完整性故障：运行仓库完整性检查并从备份恢复",
                )
            chain.append(commit)
            current = commit.parent_ids[0] if commit.parent_ids else None
        chain.reverse()
        return tuple(chain)

    def entries_for_commits(
        self, commit_ids: tuple[CommitId, ...]
    ) -> dict[CommitId, tuple[CommitEntry, ...]]:
        return {cid: tuple(self._data.entries.get(cid.value, ())) for cid in commit_ids}

    def relation_rows_for_commits(
        self, commit_ids: tuple[CommitId, ...]
    ) -> tuple[RelationRevision, ...]:
        order = {cid.value: i for i, cid in enumerate(commit_ids)}
        rows = [r for r in self._data.relations.values() if r.commit_id.value in order]
        rows.sort(key=lambda r: order[r.commit_id.value])
        return tuple(replace(r, metadata=_deep(r.metadata)) for r in rows)

    def get_revision(self, revision_id: RevisionId) -> Revision | None:
        rev = self._data.revisions.get(revision_id.value)
        if rev is None:
            return None
        return replace(rev, content=_deep(rev.content))

    def get_idempotent_result(self, project_id: ProjectId, key: str) -> CommitResult | None:
        return self._data.idempotency.get((project_id.value, key))

    def get_lease(self, resource_key: str) -> Lease | None:
        return self._data.leases.get(resource_key)

    def next_sequence(self, project_id: ProjectId) -> int:
        seqs = [c.sequence for c in self._data.commits.values() if c.project_id == project_id]
        return max(seqs, default=0) + 1

    def next_outbox_sequence(self, project_id: ProjectId) -> int:
        seqs = [e.sequence for e in self._data.outbox.values() if e.project_id == project_id]
        return max(seqs, default=0) + 1

    # ---- 写 ----
    def insert_project(self, project: Project) -> None:
        self._data.projects[project.id.value] = project

    def insert_branch(self, branch: Branch) -> None:
        self._data.branches[branch.id.value] = branch

    def insert_artifact(self, artifact: Artifact) -> None:
        self._data.artifacts[artifact.id.value] = artifact

    def insert_revision(self, revision: Revision) -> None:
        self._data.revisions[revision.id.value] = replace(
            revision, content=_deep(revision.content)
        )

    def insert_relation(self, row: RelationRevision) -> None:
        self._data.relations[row.id] = replace(row, metadata=_deep(row.metadata))

    def insert_commit(self, commit: Commit, entries: tuple[CommitEntry, ...]) -> None:
        self._data.commits[commit.id.value] = commit
        self._data.entries[commit.id.value] = list(entries)

    def set_branch_head(
        self, branch_id: BranchId, new_head: CommitId, expected_version: int
    ) -> None:
        branch = self._data.branches.get(branch_id.value)
        if branch is None or branch.version != expected_version:
            actual = branch.version if branch else None
            raise _violation(
                ec.REPO_HEAD_CONFLICT,
                f"move_branch({branch_id.value})",
                f"分支版本期望 {expected_version}，实际 {actual}",
                "重新读取分支后以最新 version 重试",
                expected=expected_version,
                actual=actual,
            )
        self._data.branches[branch_id.value] = replace(
            branch, head_commit_id=new_head, version=expected_version + 1
        )

    def append_outbox_events(self, events: tuple[OutboxEvent, ...]) -> None:
        for event in events:
            self._data.outbox[event.id] = replace(event, payload=_deep(event.payload))

    def put_idempotent_result(self, project_id: ProjectId, key: str, result: CommitResult) -> None:
        self._data.idempotency[(project_id.value, key)] = result


class MemoryStorage:
    """事务边界：RLock 串行化 + 克隆快照实现异常回滚。"""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._lock = RLock()
        self._data = _MemoryData()

    def transaction(self) -> Any:
        return self._transaction()

    @contextmanager
    def _transaction(self) -> Iterator[MemoryTxn]:
        with self._lock:
            snapshot = self._data.clone()
            try:
                yield MemoryTxn(self._data)
            except BaseException:
                self._data = snapshot  # 整体回滚，不留半提交
                raise

    def now(self) -> datetime:
        return self._clock.now()

    @property
    def data(self) -> _MemoryData:
        """只读访问（inspection 工具用）；不得在事务外修改。"""
        return self._data

    def lock(self) -> RLock:
        return self._lock


class MemoryRepository:
    """内存后端门面：service（读+写）+ lease + outbox 共用同一存储与 Clock。"""

    def __init__(self, clock: Clock, ids: IdGenerator) -> None:
        self._storage = MemoryStorage(clock)
        self._clock = clock
        self.service = CommitService(self._storage, ids)

    # LeaseRepository ----
    def acquire(self, resource_key: str, owner_job_id: JobId, ttl_seconds: float) -> Lease:
        if ttl_seconds <= 0:
            raise _violation(
                ec.REPO_LEASE_CONFLICT,
                f"acquire({resource_key})",
                f"ttl={ttl_seconds} 必须为正数",
                "使用正的租约时长（秒）",
            )
        now = self._clock.now()
        with self._storage.lock():
            existing = self._storage.data.leases.get(resource_key)
            if (
                existing is not None
                and not existing.is_expired(now)
                and existing.owner_job_id != owner_job_id
            ):
                raise _violation(
                    ec.REPO_LEASE_CONFLICT,
                    f"acquire({resource_key})",
                    f"资源正由 {existing.owner_job_id.value} 持有，"
                    f"到期时间 {existing.expires_at.isoformat()}",
                    "等待租约到期或由持有者释放后再 acquire",
                )
            if (
                existing is not None
                and not existing.is_expired(now)
                and existing.owner_job_id == owner_job_id
            ):
                return existing  # 同 owner 重复 acquire：幂等返回现有租约
            lease = Lease(
                resource_key=resource_key,
                owner_job_id=owner_job_id,
                expires_at=now + timedelta(seconds=ttl_seconds),
                version=(existing.version if existing else 0) + 1,
            )
            self._storage.data.leases[resource_key] = lease
        return lease

    def renew(self, lease: Lease, ttl_seconds: float) -> Lease:
        if ttl_seconds <= 0:
            raise _violation(
                ec.REPO_LEASE_CONFLICT,
                f"renew({lease.resource_key})",
                f"ttl={ttl_seconds} 必须为正数",
                "使用正的租约时长（秒）",
            )
        now = self._clock.now()
        with self._storage.lock():
            current = self._storage.data.leases.get(lease.resource_key)
            if (
                current is None
                or current.owner_job_id != lease.owner_job_id
                or current.version != lease.version
                or current.is_expired(now)
            ):
                raise _violation(
                    ec.REPO_LEASE_CONFLICT,
                    f"renew({lease.resource_key})",
                    self._lease_state_reason(current, lease, now),
                    "重新 acquire 获取新租约后继续工作",
                )
            renewed = replace(
                current,
                expires_at=now + timedelta(seconds=ttl_seconds),
                version=current.version + 1,
            )
            self._storage.data.leases[lease.resource_key] = renewed
        return renewed

    def release(self, lease: Lease) -> None:
        now = self._clock.now()
        with self._storage.lock():
            current = self._storage.data.leases.get(lease.resource_key)
            if (
                current is None
                or current.owner_job_id != lease.owner_job_id
                or current.version != lease.version
            ):
                raise _violation(
                    ec.REPO_LEASE_CONFLICT,
                    f"release({lease.resource_key})",
                    self._lease_state_reason(current, lease, now),
                    "租约已被接管或释放；无需重复释放",
                )
            # 不删除记录：保留版本号单调递增，防止 release→re-acquire 的 ABA 复用
            released = replace(
                current,
                owner_job_id=JobId("released"),
                expires_at=now,
                version=current.version + 1,
            )
            self._storage.data.leases[lease.resource_key] = released

    @staticmethod
    def _lease_state_reason(current: Lease | None, lease: Lease, now: datetime) -> str:
        if current is None:
            return f"资源 {lease.resource_key} 上已没有租约"
        if current.owner_job_id != lease.owner_job_id:
            return f"租约现由 {current.owner_job_id.value} 持有，不是 {lease.owner_job_id.value}"
        if current.version != lease.version:
            return f"租约版本已前进（当前 {current.version}，请求 {lease.version}）"
        if current.is_expired(now):
            return f"租约已于 {current.expires_at.isoformat()} 过期"
        return "租约状态不匹配"

    # EventOutbox（消费侧）----
    async def pending(self, project_id: str, limit: int) -> list[dict[str, object]]:
        await asyncio.sleep(0)
        with self._storage.lock():
            events = sorted(
                (
                    e
                    for e in self._storage.data.outbox.values()
                    if e.project_id.value == project_id and e.published_at is None
                ),
                key=lambda e: e.sequence,
            )[:limit]
            return [self._event_dict(e) for e in events]

    async def mark_published(self, event_id: str) -> None:
        await asyncio.sleep(0)
        with self._storage.lock():
            event = self._storage.data.outbox.get(event_id)
            if event is not None and event.published_at is None:
                self._storage.data.outbox[event_id] = replace(event, published_at=self._clock.now())

    @staticmethod
    def _event_dict(event: OutboxEvent) -> dict[str, object]:
        return {
            "id": event.id,
            "commit_id": event.commit_id.value if event.commit_id else None,
            "project_id": event.project_id.value,
            "sequence": event.sequence,
            "type": event.type,
            "payload": _deep(event.payload),
        }


def build_memory_repository(clock: Clock, ids: IdGenerator) -> MemoryRepository:
    """组装内存后端：service 持有 storage，lease/outbox 共用 Clock。"""
    return MemoryRepository(clock, ids)
