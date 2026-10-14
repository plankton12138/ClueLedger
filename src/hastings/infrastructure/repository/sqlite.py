"""SQLite Repository 实现：单文件、事务覆盖 Commit/Branch/Relation/Outbox（T12.3）。

Phase 12 范围为单进程：连接以 check_same_thread=False 创建，全部事务经进程内
RLock 串行化（与内存后端一致），跨进程并发与分布式锁不在本阶段目标内。
数据库路径由配置/项目注册表提供，本模块不接受来自 Agent 参数的任意路径。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any

from hastings.application.errors import EnvironmentFailure, ViolationError
from hastings.application.ports.clock import Clock
from hastings.application.ports.ids import IdGenerator
from hastings.application.services.commit_service import CommitService
from hastings.domain.repository import error_codes as ec
from hastings.domain.repository.ids import (
    Actor,
    ArtifactId,
    BranchId,
    CommitId,
    ContentHash,
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
from hastings.domain.repository.results import COMMITTED, CommitResult
from hastings.domain.repository.serialization import canonical_json
from hastings.infrastructure.repository.migrations.migrator import migrate

__all__ = ["SqliteRepository", "build_sqlite_repository"]


def _violation(code: str, where: str, why: str, fix: str, **details: Any) -> ViolationError:
    return ViolationError(code=code, where=where, why=why, fix=fix, details=details)


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _chain_parent(conn: sqlite3.Connection, commit_id: str) -> str | None:
    row = conn.execute(
        "SELECT parent_id FROM commit_parents WHERE commit_id = ? AND position = 0",
        (commit_id,),
    ).fetchone()
    return row[0] if row else None


class SqliteTxn:
    """事务原语的 SQLite 实现；连接已处于 BEGIN IMMEDIATE 事务中。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ---- 读 ----
    def get_project(self, project_id: ProjectId) -> Project | None:
        row = self._conn.execute(
            "SELECT id, name, created_at FROM projects WHERE id = ?", (project_id.value,)
        ).fetchone()
        if row is None:
            return None
        return Project(id=ProjectId(row[0]), name=row[1], created_at=_dt(row[2]))

    def get_branch(self, branch_id: BranchId) -> Branch | None:
        row = self._conn.execute(
            "SELECT id, project_id, name, head_commit_id, version FROM branches WHERE id = ?",
            (branch_id.value,),
        ).fetchone()
        if row is None:
            return None
        return Branch(
            id=BranchId(row[0]),
            project_id=ProjectId(row[1]),
            name=row[2],
            head_commit_id=CommitId(row[3]) if row[3] else None,
            version=row[4],
        )

    def branch_by_name(self, project_id: ProjectId, name: str) -> Branch | None:
        row = self._conn.execute(
            "SELECT id, project_id, name, head_commit_id, version FROM branches"
            " WHERE project_id = ? AND name = ?",
            (project_id.value, name),
        ).fetchone()
        if row is None:
            return None
        return Branch(
            id=BranchId(row[0]),
            project_id=ProjectId(row[1]),
            name=row[2],
            head_commit_id=CommitId(row[3]) if row[3] else None,
            version=row[4],
        )

    def get_artifact_record(self, artifact_id: ArtifactId) -> Artifact | None:
        row = self._conn.execute(
            "SELECT id, project_id, kind, created_at FROM artifacts WHERE id = ?",
            (artifact_id.value,),
        ).fetchone()
        if row is None:
            return None
        return Artifact(
            id=ArtifactId(row[0]), project_id=ProjectId(row[1]), kind=row[2], created_at=_dt(row[3])
        )

    def artifacts_of_project(self, project_id: ProjectId) -> tuple[Artifact, ...]:
        rows = self._conn.execute(
            "SELECT id, project_id, kind, created_at FROM artifacts WHERE project_id = ?"
            " ORDER BY id",
            (project_id.value,),
        ).fetchall()
        return tuple(
            Artifact(
                id=ArtifactId(r[0]), project_id=ProjectId(r[1]), kind=r[2], created_at=_dt(r[3])
            )
            for r in rows
        )

    def get_commit(self, commit_id: CommitId) -> Commit | None:
        row = self._conn.execute(
            "SELECT id, project_id, message, actor_kind, actor_id, created_at, sequence"
            " FROM commits WHERE id = ?",
            (commit_id.value,),
        ).fetchone()
        if row is None:
            return None
        parents = [
            r[0]
            for r in self._conn.execute(
                "SELECT parent_id FROM commit_parents WHERE commit_id = ? ORDER BY position",
                (row[0],),
            ).fetchall()
        ]
        return Commit(
            id=CommitId(row[0]),
            project_id=ProjectId(row[1]),
            parent_ids=tuple(CommitId(p) for p in parents),
            message=row[2],
            actor=Actor(kind=row[3], id=row[4]),
            created_at=_dt(row[5]),
            sequence=row[6],
        )

    def commit_chain(self, commit_id: CommitId) -> tuple[Commit, ...]:
        chain: list[Commit] = []
        current: str | None = commit_id.value
        while current is not None:
            commit = self.get_commit(CommitId(current))
            if commit is None:
                raise _violation(
                    ec.REPO_INTEGRITY_ERROR,
                    f"commit_chain({commit_id.value})",
                    f"历史链在 {current} 断裂",
                    "这是数据完整性故障：运行仓库完整性检查并从备份恢复",
                )
            chain.append(commit)
            current = _chain_parent(self._conn, current)
        chain.reverse()
        return tuple(chain)

    def entries_for_commits(
        self, commit_ids: tuple[CommitId, ...]
    ) -> dict[CommitId, tuple[CommitEntry, ...]]:
        result: dict[CommitId, list[CommitEntry]] = {cid: [] for cid in commit_ids}
        ids = [c.value for c in commit_ids]
        if not ids:
            return {cid: () for cid in commit_ids}
        placeholders = ",".join("?" * len(ids))
        rows = self._conn.execute(
            f"SELECT commit_id, artifact_id, old_revision_id, new_revision_id"
            f" FROM commit_entries WHERE commit_id IN ({placeholders})",
            ids,
        ).fetchall()
        for r in rows:
            result[CommitId(r[0])].append(
                CommitEntry(
                    commit_id=CommitId(r[0]),
                    artifact_id=ArtifactId(r[1]),
                    old_revision_id=RevisionId(r[2]) if r[2] else None,
                    new_revision_id=RevisionId(r[3]),
                )
            )
        return {cid: tuple(entries) for cid, entries in result.items()}

    def relation_rows_for_commits(
        self, commit_ids: tuple[CommitId, ...]
    ) -> tuple[RelationRevision, ...]:
        if not commit_ids:
            return ()
        order = {c.value: i for i, c in enumerate(commit_ids)}
        placeholders = ",".join("?" * len(commit_ids))
        rows = self._conn.execute(
            f"SELECT id, commit_id, action, source, relation_type, target, metadata, position"
            f" FROM relation_revisions WHERE commit_id IN ({placeholders})"
            f" ORDER BY position",
            [c.value for c in commit_ids],
        ).fetchall()
        parsed = [
            RelationRevision(
                id=r[0],
                commit_id=CommitId(r[1]),
                action=r[2],
                source=ArtifactId(r[3]),
                relation_type=r[4],
                target=ArtifactId(r[5]),
                metadata=dict(json.loads(r[6])),
                position=r[7],
            )
            for r in rows
        ]
        # 稳定排序：同 Commit 内保持 position 序，跨 Commit 按链序
        parsed.sort(key=lambda r: order[r.commit_id.value])
        return tuple(parsed)

    def get_revision(self, revision_id: RevisionId) -> Revision | None:
        row = self._conn.execute(
            "SELECT id, artifact_id, parent_id, content, content_hash, author_kind,"
            " author_id, created_at FROM revisions WHERE id = ?",
            (revision_id.value,),
        ).fetchone()
        if row is None:
            return None
        return Revision(
            id=RevisionId(row[0]),
            artifact_id=ArtifactId(row[1]),
            parent_id=RevisionId(row[2]) if row[2] else None,
            content=json.loads(row[3]),
            content_hash=ContentHash(row[4]),
            author=Actor(kind=row[5], id=row[6]),
            created_at=_dt(row[7]),
        )

    def get_idempotent_result(
        self, project_id: ProjectId, key: str
    ) -> tuple[str, CommitResult] | None:
        row = self._conn.execute(
            "SELECT commit_id, new_head, sequence, changed_artifact_ids, no_change_reason,"
            " request_fingerprint FROM idempotency_records WHERE project_id = ? AND key = ?",
            (project_id.value, key),
        ).fetchone()
        if row is None:
            return None
        if row[4] is not None:  # no_change 记录（当前不落库，防御性读取）
            from hastings.domain.repository.results import NO_CHANGE

            return (row[5] or "", CommitResult(status=NO_CHANGE, no_change_reason=row[4]))
        return (
            row[5] or "",
            CommitResult(
                status=COMMITTED,
                commit_id=CommitId(row[0]),
                new_head=CommitId(row[1]),
                sequence=row[2],
                changed_artifact_ids=tuple(ArtifactId(a) for a in json.loads(row[3])),
            ),
        )

    def get_lease(self, resource_key: str) -> Lease | None:
        row = self._conn.execute(
            "SELECT resource_key, owner_job_id, expires_at, version FROM leases"
            " WHERE resource_key = ?",
            (resource_key,),
        ).fetchone()
        if row is None:
            return None
        return Lease(
            resource_key=row[0], owner_job_id=JobId(row[1]), expires_at=_dt(row[2]), version=row[3]
        )

    def next_sequence(self, project_id: ProjectId) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM commits WHERE project_id = ?",
            (project_id.value,),
        ).fetchone()
        return int(row[0])

    def next_outbox_sequence(self, project_id: ProjectId) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM outbox_events WHERE project_id = ?",
            (project_id.value,),
        ).fetchone()
        return int(row[0])

    # ---- 写 ----
    def insert_project(self, project: Project) -> None:
        self._conn.execute(
            "INSERT INTO projects (id, name, created_at) VALUES (?, ?, ?)",
            (project.id.value, project.name, project.created_at.isoformat()),
        )

    def insert_branch(self, branch: Branch) -> None:
        self._conn.execute(
            "INSERT INTO branches (id, project_id, name, head_commit_id, version)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                branch.id.value,
                branch.project_id.value,
                branch.name,
                branch.head_commit_id.value if branch.head_commit_id else None,
                branch.version,
            ),
        )

    def insert_artifact(self, artifact: Artifact) -> None:
        self._conn.execute(
            "INSERT INTO artifacts (id, project_id, kind, created_at) VALUES (?, ?, ?, ?)",
            (
                artifact.id.value,
                artifact.project_id.value,
                artifact.kind,
                artifact.created_at.isoformat(),
            ),
        )

    def insert_revision(self, revision: Revision) -> None:
        self._conn.execute(
            "INSERT INTO revisions (id, artifact_id, parent_id, content, content_hash,"
            " author_kind, author_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                revision.id.value,
                revision.artifact_id.value,
                revision.parent_id.value if revision.parent_id else None,
                canonical_json(revision.content),
                revision.content_hash.value,
                revision.author.kind,
                revision.author.id,
                revision.created_at.isoformat(),
            ),
        )

    def insert_relation(self, row: RelationRevision) -> None:
        self._conn.execute(
            "INSERT INTO relation_revisions (id, commit_id, action, source, relation_type,"
            " target, metadata, position) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row.id,
                row.commit_id.value,
                row.action,
                row.source.value,
                row.relation_type,
                row.target.value,
                canonical_json(row.metadata),
                row.position,
            ),
        )

    def insert_commit(self, commit: Commit, entries: tuple[CommitEntry, ...]) -> None:
        self._conn.execute(
            "INSERT INTO commits (id, project_id, message, actor_kind, actor_id, created_at,"
            " sequence) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                commit.id.value,
                commit.project_id.value,
                commit.message,
                commit.actor.kind,
                commit.actor.id,
                commit.created_at.isoformat(),
                commit.sequence,
            ),
        )
        for position, parent in enumerate(commit.parent_ids):
            self._conn.execute(
                "INSERT INTO commit_parents (commit_id, position, parent_id) VALUES (?, ?, ?)",
                (commit.id.value, position, parent.value),
            )
        for entry in entries:
            self._conn.execute(
                "INSERT INTO commit_entries (commit_id, artifact_id, old_revision_id,"
                " new_revision_id) VALUES (?, ?, ?, ?)",
                (
                    entry.commit_id.value,
                    entry.artifact_id.value,
                    entry.old_revision_id.value if entry.old_revision_id else None,
                    entry.new_revision_id.value,
                ),
            )

    def set_branch_head(
        self, branch_id: BranchId, new_head: CommitId, expected_version: int
    ) -> None:
        cur = self._conn.execute(
            "UPDATE branches SET head_commit_id = ?, version = version + 1"
            " WHERE id = ? AND version = ?",
            (new_head.value, branch_id.value, expected_version),
        )
        if cur.rowcount != 1:
            raise _violation(
                ec.REPO_HEAD_CONFLICT,
                f"move_branch({branch_id.value})",
                f"分支版本期望 {expected_version}，实际版本已变化",
                "重新读取分支后以最新 version 重试",
                expected=expected_version,
            )

    def append_outbox_events(self, events: tuple[OutboxEvent, ...]) -> None:
        for event in events:
            self._conn.execute(
                "INSERT INTO outbox_events (id, project_id, commit_id, sequence, type,"
                " payload, published_at) VALUES (?, ?, ?, ?, ?, ?, NULL)",
                (
                    event.id,
                    event.project_id.value,
                    event.commit_id.value if event.commit_id else None,
                    event.sequence,
                    event.type,
                    canonical_json(event.payload),
                ),
            )

    def put_idempotent_result(
        self, project_id: ProjectId, key: str, fingerprint: str, result: CommitResult
    ) -> None:
        if not result.is_committed or result.commit_id is None or result.new_head is None:
            return  # no_change 不产生 Commit，也不记幂等键
        self._conn.execute(
            "INSERT INTO idempotency_records (project_id, key, commit_id, new_head, sequence,"
            " changed_artifact_ids, no_change_reason, request_fingerprint)"
            " VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
            (
                project_id.value,
                key,
                result.commit_id.value,
                result.new_head.value,
                result.sequence or 0,
                canonical_json([a.value for a in result.changed_artifact_ids]),
                fingerprint,
            ),
        )


class SqliteStorage:
    """事务边界：BEGIN IMMEDIATE + 进程内 RLock；异常一律 ROLLBACK。"""

    def __init__(self, conn: sqlite3.Connection, clock: Clock) -> None:
        self._conn = conn
        self._clock = clock
        self._lock = RLock()

    def transaction(self) -> Any:
        return self._transaction()

    @contextmanager
    def _transaction(self) -> Iterator[SqliteTxn]:
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                yield SqliteTxn(self._conn)
            except sqlite3.IntegrityError as exc:
                self._conn.execute("ROLLBACK")
                raise _violation(
                    ec.REPO_INTEGRITY_ERROR,
                    "sqlite transaction",
                    f"存储约束拒绝写入：{exc}",
                    "检查是否重复创建对象或引用了不存在的 ID；约束细节见日志",
                ) from exc
            except sqlite3.Error as exc:
                self._conn.execute("ROLLBACK")
                raise EnvironmentFailure(
                    code=ec.REPO_UNAVAILABLE,
                    where="sqlite transaction",
                    why=f"数据库操作失败：{exc}",
                    fix="事务已回滚，无半提交；恢复数据库可用性后重试",
                ) from exc
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def now(self) -> datetime:
        return self._clock.now()

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def lock(self) -> RLock:
        return self._lock

    def backup_to(self, dest_path: Path) -> None:
        """一致性备份：用 SQLite backup API，避免复制到半提交状态。

        在持锁状态下执行，保证备份与最新已提交状态一致。目标目录必须存在。
        """
        with self._lock:
            dest = sqlite3.connect(str(dest_path))
            try:
                self._conn.backup(dest)
                dest.commit()
            finally:
                dest.close()


class SqliteRepository:
    """SQLite 后端门面：service（读+写）+ lease + outbox。"""

    def __init__(self, path: Path | str, clock: Clock, ids: IdGenerator) -> None:
        self._path = Path(path)
        existed = self._path.exists()
        if not existed:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), isolation_level=None, check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        migrate(self._conn)
        self._clock = clock
        self._storage = SqliteStorage(self._conn, clock)
        self.service = CommitService(self._storage, ids)

    def close(self) -> None:
        self._conn.close()

    def backup_to(self, dest_path: Path) -> None:
        """一致性备份（T12.8）：SQLite backup API，持锁执行，见 SqliteStorage。"""
        self._storage.backup_to(Path(dest_path))

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
        with self._storage.lock(), self._storage.transaction() as txn:
            existing: Lease | None = txn.get_lease(resource_key)
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
                return existing  # 同 owner 重复 acquire：幂等返回
            lease = Lease(
                resource_key=resource_key,
                owner_job_id=owner_job_id,
                expires_at=now + timedelta(seconds=ttl_seconds),
                version=(existing.version if existing else 0) + 1,
            )
            self._conn.execute(
                "INSERT INTO leases (resource_key, owner_job_id, expires_at, version)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(resource_key) DO UPDATE SET owner_job_id = excluded.owner_job_id,"
                " expires_at = excluded.expires_at, version = excluded.version",
                (
                    lease.resource_key,
                    lease.owner_job_id.value,
                    lease.expires_at.isoformat(),
                    lease.version,
                ),
            )
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
            cur = self._conn.execute(
                "UPDATE leases SET expires_at = ?, version = version + 1"
                " WHERE resource_key = ? AND owner_job_id = ? AND version = ?"
                " AND expires_at > ?",
                (
                    (now + timedelta(seconds=ttl_seconds)).isoformat(),
                    lease.resource_key,
                    lease.owner_job_id.value,
                    lease.version,
                    now.isoformat(),
                ),
            )
            if cur.rowcount != 1:
                current = self._conn.execute(
                    "SELECT owner_job_id, version, expires_at FROM leases WHERE resource_key = ?",
                    (lease.resource_key,),
                ).fetchone()
                raise _violation(
                    ec.REPO_LEASE_CONFLICT,
                    f"renew({lease.resource_key})",
                    self._reason(current, lease, now),
                    "重新 acquire 获取新租约后继续工作",
                )
            row = self._conn.execute(
                "SELECT resource_key, owner_job_id, expires_at, version FROM leases"
                " WHERE resource_key = ?",
                (lease.resource_key,),
            ).fetchone()
        return Lease(
            resource_key=row[0], owner_job_id=JobId(row[1]), expires_at=_dt(row[2]), version=row[3]
        )

    def release(self, lease: Lease) -> None:
        now = self._clock.now()
        with self._storage.lock():
            cur = self._conn.execute(
                "UPDATE leases SET owner_job_id = 'released', expires_at = ?,"
                " version = version + 1"
                " WHERE resource_key = ? AND owner_job_id = ? AND version = ?",
                (now.isoformat(), lease.resource_key, lease.owner_job_id.value, lease.version),
            )
            if cur.rowcount != 1:
                current = self._conn.execute(
                    "SELECT owner_job_id, version, expires_at FROM leases WHERE resource_key = ?",
                    (lease.resource_key,),
                ).fetchone()
                raise _violation(
                    ec.REPO_LEASE_CONFLICT,
                    f"release({lease.resource_key})",
                    self._reason(current, lease, now),
                    "租约已被接管或释放；无需重复释放",
                )

    @staticmethod
    def _reason(current: tuple[str, int, str] | None, lease: Lease, now: datetime) -> str:
        if current is None:
            return f"资源 {lease.resource_key} 上已没有租约"
        owner, version, expires = current
        if _dt(expires) <= now:
            return f"租约已于 {expires} 过期"
        if owner != lease.owner_job_id.value:
            return f"租约现由 {owner} 持有，不是 {lease.owner_job_id.value}"
        return f"租约版本已前进（当前 {version}，请求 {lease.version}）"

    # EventOutbox（消费侧）----
    async def pending(self, project_id: str, limit: int) -> list[dict[str, object]]:
        return await asyncio.to_thread(self._pending_sync, project_id, limit)

    def _pending_sync(self, project_id: str, limit: int) -> list[dict[str, object]]:
        with self._storage.lock():
            rows = self._conn.execute(
                "SELECT id, commit_id, project_id, sequence, type, payload FROM outbox_events"
                " WHERE project_id = ? AND published_at IS NULL ORDER BY sequence LIMIT ?",
                (project_id, limit),
            ).fetchall()
            return [
                {
                    "id": r[0],
                    "commit_id": r[1],
                    "project_id": r[2],
                    "sequence": r[3],
                    "type": r[4],
                    "payload": dict(json.loads(r[5])),
                }
                for r in rows
            ]

    async def mark_published(self, event_id: str) -> None:
        await asyncio.to_thread(self._mark_published_sync, event_id)

    def _mark_published_sync(self, event_id: str) -> None:
        with self._storage.lock():
            self._conn.execute(
                "UPDATE outbox_events SET published_at = ? WHERE id = ? AND published_at IS NULL",
                (self._clock.now().isoformat(), event_id),
            )


def build_sqlite_repository(path: Path | str, clock: Clock, ids: IdGenerator) -> SqliteRepository:
    """组装 SQLite 后端：migrate → storage → CommitService。"""
    return SqliteRepository(path, clock=clock, ids=ids)
