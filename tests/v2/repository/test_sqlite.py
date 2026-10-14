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
