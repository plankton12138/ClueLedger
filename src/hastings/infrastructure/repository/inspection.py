"""T12.8 受限只读 inspection：列项目/分支/Commit、校验完整性。

设计边界（计划 §4 T12.8）：

- **不得提供任意 SQL**：模块只暴露固定检查集合（inspect_sqlite 的实现内部
  是白名单查询，调用方拿不到查询通道）；输出是结构化报告而非原始行。
- **只读**：inspect_repository 以 SQLite ``mode=ro`` URI 打开连接——任何写
  尝试都会被 SQLite 拒绝，检查逻辑自身也只含 SELECT 与 PRAGMA。
- 完整性检查覆盖两类：SQLite 自检（integrity_check / foreign_key_check）
  与应用层不变量（head 可达、sequence 连续、entry/revision 链一致、
  outbox 指向存在的 commit、内容哈希与规范化重算一致）。

备份说明见 SqliteStorage.backup_to：始终用 backup API 产出一致性快照，
不要直接复制运行中的数据库文件（WAL 模式下会拿到缺 -wal 的半状态）。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from hastings.domain.repository.serialization import content_hash
from hastings.infrastructure.repository.migrations.migrator import (
    SCHEMA_VERSION_UNKNOWN,
    read_schema_version,
)

__all__ = [
    "BranchSummary",
    "CommitSummary",
    "ProjectSummary",
    "RepositoryInspection",
    "inspect_repository",
    "inspect_sqlite",
]


@dataclass(frozen=True)
class ProjectSummary:
    id: str
    name: str
    branch_count: int
    commit_count: int
    artifact_count: int


@dataclass(frozen=True)
class BranchSummary:
    project_id: str
    name: str
    head_commit_id: str | None
    version: int


@dataclass(frozen=True)
class CommitSummary:
    id: str
    project_id: str
    sequence: int
    message: str
    entry_count: int


@dataclass(frozen=True)
class RepositoryInspection:
    """一次 inspection 的结构化报告；integrity_problems 非空即数据库受损。"""

    schema_version: int
    projects: tuple[ProjectSummary, ...]
    branches: tuple[BranchSummary, ...]
    commits: tuple[CommitSummary, ...]
    pending_outbox_events: int
    integrity_problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.integrity_problems


def _sqlite_self_checks(conn: sqlite3.Connection, problems: list[str]) -> None:
    result = conn.execute("PRAGMA integrity_check").fetchall()
    bad = [row[0] for row in result if row[0] != "ok"]
    if bad:
        problems.append(f"integrity_check 异常：{'; '.join(bad)}（数据库页级损坏，从备份恢复）")
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    for row in violations:
        problems.append(
            f"外键违例：表 {row[0]} 行 rowid={row[1]} 引用了不存在的对象"
            "（用该表的 FK 定义定位孤儿行）"
        )


def _check_branch_heads(conn: sqlite3.Connection, problems: list[str]) -> None:
    rows = conn.execute(
        "SELECT b.id, b.name, b.project_id, b.head_commit_id FROM branches b"
        " WHERE b.head_commit_id IS NOT NULL"
        " AND NOT EXISTS (SELECT 1 FROM commits c WHERE c.id = b.head_commit_id)"
    ).fetchall()
    for row in rows:
        problems.append(
            f"分支 {row[1]}({row[0]}) 的 head 指向不存在的 commit {row[3]}；"
            "从备份恢复或把 head 重置到最近的有效 commit"
        )
    wrong_project = conn.execute(
        "SELECT b.id, b.name FROM branches b JOIN commits c ON c.id = b.head_commit_id"
        " WHERE b.head_commit_id IS NOT NULL AND c.project_id != b.project_id"
    ).fetchall()
    for row in wrong_project:
        problems.append(f"分支 {row[1]}({row[0]}) 的 head 属于其他项目（跨项目引用）")


def _check_sequence_continuity(conn: sqlite3.Connection, table: str, problems: list[str]) -> None:
    """同一项目内 sequence 必须从 1 连续递增（提交顺序可重放的根基）。"""
    rows = conn.execute(
        f"SELECT project_id, sequence FROM {table} ORDER BY project_id, sequence"  # noqa: S608
    ).fetchall()
    by_project: dict[str, list[int]] = {}
    for project_id, sequence in rows:
        by_project.setdefault(project_id, []).append(sequence)
    for project_id, sequences in by_project.items():
        expected = list(range(1, len(sequences) + 1))
        if sequences != expected:
            problems.append(
                f"项目 {project_id} 的 {table}.sequence 不连续：{sequences}，"
                f"期望 {expected}（历史链有缺口，从备份恢复）"
            )


def _check_entries_and_revisions(conn: sqlite3.Connection, problems: list[str]) -> None:
    orphan_entries = conn.execute(
        "SELECT e.commit_id FROM commit_entries e"
        " WHERE NOT EXISTS (SELECT 1 FROM commits c WHERE c.id = e.commit_id)"
    ).fetchall()
    for (commit_id,) in orphan_entries:
        problems.append(f"entry 指向不存在的 commit {commit_id}；从备份恢复")
    missing = conn.execute(
        "SELECT e.commit_id, e.new_revision_id FROM commit_entries e"
        " WHERE NOT EXISTS (SELECT 1 FROM revisions r WHERE r.id = e.new_revision_id)"
        " UNION ALL"
        " SELECT e.commit_id, e.old_revision_id FROM commit_entries e"
        " WHERE e.old_revision_id IS NOT NULL AND NOT EXISTS"
        " (SELECT 1 FROM revisions r WHERE r.id = e.old_revision_id)"
    ).fetchall()
    for commit_id, rev in missing:
        problems.append(f"commit {commit_id} 的 entry 指向不存在的 revision {rev}；从备份恢复")
    cross = conn.execute(
        "SELECT e.commit_id, e.artifact_id, a.project_id, c.project_id"
        " FROM commit_entries e JOIN artifacts a ON a.id = e.artifact_id"
        " JOIN commits c ON c.id = e.commit_id WHERE a.project_id != c.project_id"
    ).fetchall()
    for commit_id, artifact_id, a_proj, c_proj in cross:
        problems.append(
            f"commit {commit_id}（项目 {c_proj}）引用了项目 {a_proj} 的 artifact "
            f"{artifact_id}（跨项目 entry）"
        )
    # revision 链：parent 必须存在且属于同一 artifact；内容哈希必须可复算
    orphan = conn.execute(
        "SELECT r.id, r.parent_id FROM revisions r"
        " WHERE r.parent_id IS NOT NULL AND NOT EXISTS"
        " (SELECT 1 FROM revisions p WHERE p.id = r.parent_id)"
    ).fetchall()
    for rev_id, parent_id in orphan:
        problems.append(f"revision {rev_id} 的 parent {parent_id} 不存在（版本链断裂）")
    mismatched = conn.execute(
        "SELECT r.id, r.parent_id FROM revisions r JOIN revisions p ON p.id = r.parent_id"
        " WHERE p.artifact_id != r.artifact_id"
    ).fetchall()
    for rev_id, parent_id in mismatched:
        problems.append(
            f"revision {rev_id} 的 parent {parent_id} 属于另一个 artifact（版本链串线）"
        )
    rows = conn.execute("SELECT id, content, content_hash FROM revisions").fetchall()
    for rev_id, raw_content, stored_hash in rows:
        try:
            recomputed = content_hash(json.loads(raw_content))
        except (ValueError, TypeError) as exc:
            problems.append(f"revision {rev_id} 的 content 无法规范化：{exc}")
            continue
        if recomputed != stored_hash:
            problems.append(
                f"revision {rev_id} 的 content_hash 与内容不符"
                f"（存储 {stored_hash}，重算 {recomputed}）——内容被篡改或哈希规则漂移"
            )


def _check_relations_and_outbox(conn: sqlite3.Connection, problems: list[str]) -> None:
    broken_relation = conn.execute(
        "SELECT r.id FROM relation_revisions r WHERE"
        " NOT EXISTS (SELECT 1 FROM commits c WHERE c.id = r.commit_id)"
        " OR NOT EXISTS (SELECT 1 FROM artifacts a WHERE a.id = r.source)"
        " OR NOT EXISTS (SELECT 1 FROM artifacts a WHERE a.id = r.target)"
    ).fetchall()
    for (rel_id,) in broken_relation:
        problems.append(f"关系边 {rel_id} 指向不存在的 commit 或 artifact；从备份恢复")
    orphan_outbox = conn.execute(
        "SELECT o.id, o.commit_id FROM outbox_events o"
        " WHERE o.commit_id IS NOT NULL"
        " AND NOT EXISTS (SELECT 1 FROM commits c WHERE c.id = o.commit_id)"
    ).fetchall()
    for event_id, commit_id in orphan_outbox:
        problems.append(f"outbox 事件 {event_id} 指向不存在的 commit {commit_id}")
    orphan_idem = conn.execute(
        "SELECT i.project_id, i.key FROM idempotency_records i"
        " WHERE NOT EXISTS (SELECT 1 FROM commits c WHERE c.id = i.commit_id)"
    ).fetchall()
    for project_id, key in orphan_idem:
        problems.append(
            f"项目 {project_id} 的幂等记录 {key} 指向不存在的 commit（删除该记录以恢复重试）"
        )


def inspect_sqlite(conn: sqlite3.Connection) -> RepositoryInspection:
    """对一个已迁移的 SQLite 仓库数据库执行完整检查（只读）。"""
    problems: list[str] = []
    version = read_schema_version(conn)
    if version == SCHEMA_VERSION_UNKNOWN:
        problems.append(
            "schema 版本未知（有表但 user_version=0）：数据库可能由旧工具创建，"
            "请用迁移器升级或从备份恢复"
        )
    _sqlite_self_checks(conn, problems)
    _check_branch_heads(conn, problems)
    _check_sequence_continuity(conn, "commits", problems)
    _check_sequence_continuity(conn, "outbox_events", problems)
    _check_entries_and_revisions(conn, problems)
    _check_relations_and_outbox(conn, problems)

    projects = tuple(
        ProjectSummary(
            id=row[0],
            name=row[1],
            branch_count=conn.execute(
                "SELECT COUNT(*) FROM branches WHERE project_id = ?", (row[0],)
            ).fetchone()[0],
            commit_count=conn.execute(
                "SELECT COUNT(*) FROM commits WHERE project_id = ?", (row[0],)
            ).fetchone()[0],
            artifact_count=conn.execute(
                "SELECT COUNT(*) FROM artifacts WHERE project_id = ?", (row[0],)
            ).fetchone()[0],
        )
        for row in conn.execute("SELECT id, name FROM projects ORDER BY id").fetchall()
    )
    branches = tuple(
        BranchSummary(project_id=row[0], name=row[1], head_commit_id=row[2], version=row[3])
        for row in conn.execute(
            "SELECT project_id, name, head_commit_id, version FROM branches"
            " ORDER BY project_id, name"
        ).fetchall()
    )
    commits = tuple(
        CommitSummary(
            id=row[0],
            project_id=row[1],
            sequence=row[2],
            message=row[3],
            entry_count=conn.execute(
                "SELECT COUNT(*) FROM commit_entries WHERE commit_id = ?", (row[0],)
            ).fetchone()[0],
        )
        for row in conn.execute(
            "SELECT id, project_id, sequence, message FROM commits ORDER BY project_id, sequence"
        ).fetchall()
    )
    pending = conn.execute(
        "SELECT COUNT(*) FROM outbox_events WHERE published_at IS NULL"
    ).fetchone()[0]
    return RepositoryInspection(
        schema_version=version,
        projects=projects,
        branches=branches,
        commits=commits,
        pending_outbox_events=pending,
        integrity_problems=tuple(problems),
    )


def inspect_repository(path: Path | str) -> RepositoryInspection:
    """以只读 URI 打开仓库文件执行 inspection（写操作在此连接上必然失败）。"""
    uri = f"file:{Path(path).resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        return inspect_sqlite(conn)
    finally:
        conn.close()
