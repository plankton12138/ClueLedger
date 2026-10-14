"""T12.8 只读 inspection 测试：列项目/分支/Commit、完整性校验、只读保证。

inspection 只针对 SQLite 数据库文件（内存后端即进程内对象，无需 inspection）。
「不得提供任意 SQL」的边界体现为：模块只暴露固定检查集合，连接以 mode=ro 打开。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from conftest import make_env_sqlite_factory

from hastings.domain.repository.ids import ArtifactId
from hastings.infrastructure.repository.inspection import (
    inspect_repository,
    inspect_sqlite,
)


def _make_populated_db(tmp_path: Path) -> Path:
    """建一个含两项目之一、3 次提交（建→改→新建）的数据库，返回路径。

    第 2 次提交更新同一 artifact（产生 old_revision 与 parent 链），
    第 3 次提交新建对象——让各完整性检查都有可破坏的中间态。
    """
    from conftest import SYSTEM_ACTOR

    from hastings.domain.repository.requests import ArtifactChange, CommitRequest

    db_path = tmp_path / "repo.db"
    env = make_env_sqlite_factory(db_path)()
    svc = env.repo.service
    project = svc.create_project("winter-manor")
    aid = ArtifactId(f"art_{project.id.value}_ch1")

    def _commit(key: str, summary: str, *changes: ArtifactChange) -> None:
        branch = svc.get_branch(project.id, "main")
        svc.commit(
            CommitRequest(
                project_id=project.id,
                branch_id=branch.id,
                expected_head=branch.head_commit_id,
                actor=SYSTEM_ACTOR,
                idempotency_key=key,
                summary=summary,
                changes=changes,
            )
        )

    _commit(
        "init-1",
        "第一次提交",
        ArtifactChange(artifact_id=aid, content={"v": 1}, kind="chapter"),
    )
    snapshot = svc.get_snapshot(project.id, svc.get_branch(project.id, "main").head_commit_id)
    old_rev = snapshot.artifacts[aid].revision
    assert old_rev is not None
    _commit(
        "init-2",
        "第二次提交",
        ArtifactChange(artifact_id=aid, content={"v": 2}, old_revision_id=old_rev.id),
    )
    _commit(
        "init-3",
        "第三次提交",
        ArtifactChange(
            artifact_id=ArtifactId(f"art_{project.id.value}_ch2"),
            content={"v": 1},
            kind="note",
        ),
    )
    env.repo.close()
    return db_path


def test_inspect_empty_db(tmp_path: Path) -> None:
    db_path = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db_path))
    from hastings.infrastructure.repository.migrations.migrator import migrate

    migrate(conn)
    report = inspect_sqlite(conn)
    assert report.ok
    from hastings.infrastructure.repository.schema import SCHEMA_VERSION

    assert report.schema_version == SCHEMA_VERSION
    assert report.projects == ()
    assert report.integrity_problems == ()
    conn.close()


def test_inspect_populated_db(tmp_path: Path) -> None:
    db_path = _make_populated_db(tmp_path)
    report = inspect_repository(db_path)
    assert report.ok, report.integrity_problems
    from hastings.infrastructure.repository.schema import SCHEMA_VERSION as _SV

    assert report.schema_version == _SV
    assert len(report.projects) == 1
    project = report.projects[0]
    assert project.name == "winter-manor"
    assert project.commit_count == 3
    assert project.artifact_count == 2
    assert len(report.branches) == 1
    assert report.branches[0].name == "main"
    assert report.branches[0].head_commit_id is not None
    assert [c.sequence for c in report.commits] == [1, 2, 3]
    # 每次提交产生 3 个 outbox 事件，均未发布
    assert report.pending_outbox_events == 9


def test_inspect_detects_dangling_branch_head(tmp_path: Path) -> None:
    db_path = _make_populated_db(tmp_path)
    raw = sqlite3.connect(str(db_path))  # 直连绕过 FK，模拟外部损坏
    raw.execute("UPDATE branches SET head_commit_id = 'cmt_ghost'")
    raw.commit()
    raw.close()
    report = inspect_repository(db_path)
    assert not report.ok
    assert any("cmt_ghost" in p for p in report.integrity_problems)


def test_inspect_detects_commit_sequence_gap(tmp_path: Path) -> None:
    db_path = _make_populated_db(tmp_path)
    raw = sqlite3.connect(str(db_path))
    raw.execute("PRAGMA foreign_keys = OFF")
    raw.execute("DELETE FROM commits WHERE sequence = 2")
    raw.commit()
    raw.close()
    report = inspect_repository(db_path)
    assert any("sequence" in p for p in report.integrity_problems)


def test_inspect_detects_content_hash_mismatch(tmp_path: Path) -> None:
    db_path = _make_populated_db(tmp_path)
    raw = sqlite3.connect(str(db_path))
    raw.execute("UPDATE revisions SET content = '{\"tampered\":true}'")
    raw.commit()
    raw.close()
    report = inspect_repository(db_path)
    assert any("content_hash" in p for p in report.integrity_problems)


def test_inspect_detects_missing_outbox_commit(tmp_path: Path) -> None:
    db_path = _make_populated_db(tmp_path)
    raw = sqlite3.connect(str(db_path))
    raw.execute("PRAGMA foreign_keys = OFF")
    raw.execute("DELETE FROM commits WHERE sequence = 1")
    raw.commit()
    raw.close()
    report = inspect_repository(db_path)
    assert any("outbox" in p for p in report.integrity_problems)


def test_inspect_sqlite_is_read_only(tmp_path: Path) -> None:
    """以 mode=ro 打开连接执行 inspection：任何写尝试都会抛错，成功即证只读。"""
    db_path = _make_populated_db(tmp_path)
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        report = inspect_sqlite(conn)
        assert report.ok
    finally:
        conn.close()


def test_inspect_lists_multiple_projects(tmp_path: Path) -> None:
    db_path = tmp_path / "multi.db"
    env = make_env_sqlite_factory(db_path)()
    env.repo.service.create_project("alpha")
    env.repo.service.create_project("beta")
    env.repo.close()
    report = inspect_repository(db_path)
    assert [p.name for p in report.projects] == ["alpha", "beta"]
    assert all(p.commit_count == 0 for p in report.projects)


@pytest.mark.parametrize("missing", ["new_revision_id", "old_revision_id"])
def test_inspect_detects_broken_entry_revision(tmp_path: Path, missing: str) -> None:
    """commit_entries 指向不存在的 revision（绕过 FK 模拟损坏）。"""
    db_path = _make_populated_db(tmp_path)
    raw = sqlite3.connect(str(db_path))
    raw.execute("PRAGMA foreign_keys = OFF")
    if missing == "new_revision_id":
        raw.execute("UPDATE commit_entries SET new_revision_id = 'rev_ghost'")
    else:
        raw.execute(
            "UPDATE commit_entries SET old_revision_id = 'rev_ghost'"
            " WHERE old_revision_id IS NOT NULL"
        )
    raw.commit()
    raw.close()
    report = inspect_repository(db_path)
    assert any("rev_ghost" in p for p in report.integrity_problems)


def test_inspect_detects_broken_revision_parent(tmp_path: Path) -> None:
    """revision.parent_id 指向不存在或其他 artifact 的 revision。"""
    db_path = _make_populated_db(tmp_path)
    raw = sqlite3.connect(str(db_path))
    raw.execute("PRAGMA foreign_keys = OFF")
    raw.execute("UPDATE revisions SET parent_id = 'rev_ghost' WHERE parent_id IS NOT NULL")
    raw.commit()
    raw.close()
    report = inspect_repository(db_path)
    assert any("parent" in p for p in report.integrity_problems)
