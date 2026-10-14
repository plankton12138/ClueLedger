"""SQLite 后端契约测试：同一套 ALL_CHECKS + 迁移器专项。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import make_env_sqlite_factory
from contract import ALL_CHECKS, ContractEnv


@pytest.fixture
def make_env_sqlite(tmp_path: object) -> Callable[[], ContractEnv]:
    from pathlib import Path

    return make_env_sqlite_factory(Path(str(tmp_path)) / "repo" / "repo.sqlite3")


@pytest.mark.parametrize("check", ALL_CHECKS, ids=lambda f: f.__name__)
def test_sqlite_contract(
    check: Callable[[ContractEnv], None], make_env_sqlite: Callable[[], ContractEnv]
) -> None:
    check(make_env_sqlite())


# ----------------------------------------------------------------------
# 迁移器专项（计划 T12.3：空库创建、未知较新版本拒绝）
# ----------------------------------------------------------------------


def test_empty_database_is_created_at_current_version(tmp_path: object) -> None:
    import sqlite3

    from hastings.infrastructure.repository.migrations.migrator import migrate
    from hastings.infrastructure.repository.schema import SCHEMA_VERSION

    db = Path(str(tmp_path)) / "fresh.sqlite3"  # type: ignore[name-defined]
    conn = sqlite3.connect(str(db))
    try:
        migrate(conn)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == SCHEMA_VERSION
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert {
            "projects",
            "revisions",
            "commits",
            "branches",
            "outbox_events",
            "leases",
            "idempotency_records",
        } <= tables
        migrate(conn)  # 已是当前版本：幂等
    finally:
        conn.close()


def test_older_unknown_version_is_rejected(tmp_path: object) -> None:
    import sqlite3

    from hastings.infrastructure.repository.migrations.migrator import migrate

    db = Path(str(tmp_path)) / "old.sqlite3"  # type: ignore[name-defined]
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA user_version = 99")
    try:
        with pytest.raises(Exception) as err:  # noqa: PT011
            migrate(conn)
        assert "REPO_SCHEMA_TOO_NEW" in str(err.value)
        assert "备份" in str(err.value)  # 恢复建议在三要素消息里
    finally:
        conn.close()


def test_unversioned_nonempty_database_is_rejected(tmp_path: object) -> None:
    import sqlite3

    from hastings.infrastructure.repository.migrations.migrator import migrate

    db = Path(str(tmp_path)) / "mystery.sqlite3"  # type: ignore[name-defined]
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE something_else (id INTEGER PRIMARY KEY)")
    try:
        with pytest.raises(Exception) as err:  # noqa: PT011
            migrate(conn)
        assert "REPO_SCHEMA_UNKNOWN" in str(err.value)
    finally:
        conn.close()


def test_foreign_keys_are_enforced(tmp_path: object) -> None:

    from conftest import FakeClock, FakeIds

    from hastings.infrastructure.repository.sqlite import build_sqlite_repository

    repo = build_sqlite_repository(
        Path(str(tmp_path)) / "fk.sqlite3",
        clock=FakeClock(),
        ids=FakeIds(),  # type: ignore[name-defined]
    )
    conn = repo._storage.connection
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    repo.close()


# ----------------------------------------------------------------------
# schema v1 → v2 升级路径：idempotency_records 加请求指纹列
# ----------------------------------------------------------------------


def test_v1_database_upgrades_to_v2_with_fingerprint_column(tmp_path: object) -> None:
    """v1 旧库迁移后指纹列存在、旧数据可读、旧行指纹按冲突拒绝（防御）。"""
    import sqlite3

    from hastings.infrastructure.repository.migrations.migrator import migrate
    from hastings.infrastructure.repository.schema import SCHEMA_VERSION

    db = Path(str(tmp_path)) / "legacy.sqlite3"
    conn = sqlite3.connect(str(db))
    try:
        # 最小 v1 形状库：包含各升级步会触碰的表
        conn.execute(
            "CREATE TABLE idempotency_records ("
            " project_id TEXT NOT NULL, key TEXT NOT NULL, commit_id TEXT NOT NULL,"
            " new_head TEXT NOT NULL, sequence INTEGER NOT NULL,"
            " changed_artifact_ids TEXT NOT NULL, no_change_reason TEXT,"
            " PRIMARY KEY (project_id, key))"
        )
        conn.execute(
            "CREATE TABLE relation_revisions ("
            " id TEXT PRIMARY KEY, commit_id TEXT NOT NULL, action TEXT NOT NULL,"
            " source TEXT NOT NULL, relation_type TEXT NOT NULL, target TEXT NOT NULL,"
            " metadata TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO idempotency_records VALUES ('p1', 'k1', 'cmt_1', 'cmt_1', 1, '[]', NULL)"
        )
        conn.execute("PRAGMA user_version = 1")
        conn.commit()

        migrate(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        columns = {
            r[1] for r in conn.execute("PRAGMA table_info(idempotency_records)").fetchall()
        }
        assert "request_fingerprint" in columns
        # 旧数据仍在，指纹为空（无法验证请求内容 → 重放时按冲突拒绝）
        row = conn.execute(
            "SELECT commit_id, request_fingerprint FROM idempotency_records"
            " WHERE project_id='p1' AND key='k1'"
        ).fetchone()
        assert row == ("cmt_1", "")
        migrate(conn)  # 已是当前版本：幂等
    finally:
        conn.close()


def test_v2_database_upgrades_to_v3_with_position_column(tmp_path: object) -> None:
    """v2 旧库迁移后 relation_revisions 带 position 列，旧数据可读。"""
    import sqlite3

    from hastings.infrastructure.repository.migrations.migrator import migrate
    from hastings.infrastructure.repository.schema import SCHEMA_VERSION

    db = Path(str(tmp_path)) / "legacy-v2.sqlite3"
    conn = sqlite3.connect(str(db))
    try:
        # 最小 v2 形状库：只有迁移步会触碰的表
        conn.execute(
            "CREATE TABLE relation_revisions ("
            " id TEXT PRIMARY KEY, commit_id TEXT NOT NULL, action TEXT NOT NULL,"
            " source TEXT NOT NULL, relation_type TEXT NOT NULL, target TEXT NOT NULL,"
            " metadata TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO relation_revisions"
            " VALUES ('rel_1', 'cmt_1', 'add', 'a', 'links', 'b', '{}')"
        )
        conn.execute("PRAGMA user_version = 2")
        conn.commit()

        migrate(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        columns = {r[1] for r in conn.execute("PRAGMA table_info(relation_revisions)").fetchall()}
        assert "position" in columns
        row = conn.execute("SELECT id, position FROM relation_revisions").fetchone()
        assert row == ("rel_1", 0)
    finally:
        conn.close()
