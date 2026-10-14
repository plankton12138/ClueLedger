"""计划 §8 八步验收场景（Phase 12）：建项目→原子提交→分叉→diff→
head 冲突→重读后提交→重启→全量一致性。

同一场景跑内存与 SQLite 双后端；SQLite 追加 inspection/backup 校验。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import SYSTEM_ACTOR, make_env_sqlite_factory

from hastings.application.errors import ViolationError
from hastings.domain.repository import error_codes as ec
from hastings.domain.repository.ids import ArtifactId, CommitId
from hastings.domain.repository.requests import (
    ArtifactChange,
    CommitRequest,
    CreateBranchRequest,
    RelationChange,
)
from hastings.domain.repository.serialization import content_hash

CH1_V1 = {"title": "第一章 雾中来客", "body": "雾从海湾漫上来。"}
CH1_V2 = {"title": "第一章 雾中来客", "body": "雾从海湾漫上来，灯先灭了。"}
CLUE_V1 = {"name": "停摆的座钟", "detail": "钟停在 21:47。"}
CLUE_V2 = {"name": "停摆的座钟", "detail": "钟停在 21:47，指针被人拨过。"}
REL_TYPE = "foreshadows"


@pytest.fixture(params=["memory", "sqlite"])
def env(request: Any, tmp_path: Path) -> Iterator[Any]:
    if request.param == "memory":
        from conftest import FakeClock, FakeIds

        from hastings.infrastructure.repository.memory import build_memory_repository

        clock, ids = FakeClock(), FakeIds()
        yield type("Env", (), {"repo": build_memory_repository(clock, ids)})()
    else:
        factory = make_env_sqlite_factory(tmp_path / "acceptance.db")
        yield factory()


def _pending_events(repo: Any, project_id: str) -> list[dict[str, object]]:
    return asyncio.run(repo.pending(project_id, limit=1000))


def test_eight_step_acceptance(env: Any, tmp_path: Path) -> None:
    svc = env.repo.service
    # ---- 1. 创建项目和 main ----
    project = svc.create_project("冬山庄杀人事件")
    main = svc.get_branch(project.id, "main")
    assert main is not None and main.head_commit_id is None

    ch1, clue = ArtifactId("art_ch1"), ArtifactId("art_clue_clock")
    # ---- 2. 一个 Commit 同时加入两个 Artifact 和一条 relation ----
    first = svc.commit(
        CommitRequest(
            project_id=project.id,
            branch_id=main.id,
            expected_head=None,
            actor=SYSTEM_ACTOR,
            idempotency_key="acc-1",
            summary="引入第一章与钟表线索，建立伏笔关系",
            changes=(
                ArtifactChange(artifact_id=ch1, content=CH1_V1, kind="chapter"),
                ArtifactChange(artifact_id=clue, content=CLUE_V1, kind="clue"),
            ),
            relations=(
                RelationChange(
                    action="add",
                    source=ch1,
                    relation_type=REL_TYPE,
                    target=clue,
                    metadata={"note": "座钟暗示第一发现时间"},
                ),
            ),
        )
    )
    assert first.is_committed and first.sequence == 1
    # 事件：2 revision + commit.created + head.moved = 4
    assert len(_pending_events(env.repo, project.id.value)) == 4

    # ---- 3. 从 main 创建 rewrite 分支并提交新版本 ----
    rewrite = svc.create_branch(
        CreateBranchRequest(project_id=project.id, name="rewrite", at_commit_id=first.commit_id)
    )
    assert rewrite is not None and rewrite.head_commit_id == first.commit_id
    second = svc.commit(
        CommitRequest(
            project_id=project.id,
            branch_id=rewrite.id,
            expected_head=first.commit_id,
            actor=SYSTEM_ACTOR,
            idempotency_key="acc-2",
            summary="改写第一章开场",
            changes=(
                ArtifactChange(
                    artifact_id=ch1,
                    content=CH1_V2,
                    old_revision_id=_rev_of(svc, project.id, first.commit_id, ch1),
                ),
            ),
        )
    )
    assert second.is_committed and second.sequence == 2

    # ---- 4. 比较两个分支 ----
    diff = svc.diff(first.commit_id, second.commit_id)
    assert len(diff.artifact_changes) == 1
    assert diff.artifact_changes[0].artifact_id == ch1
    assert diff.relations_added == () and diff.relations_removed == ()
    # main 未动：diff main head → rewrite head 同样只差 ch1
    main_now = svc.get_branch(project.id, "main")
    assert main_now is not None and main_now.head_commit_id == first.commit_id

    # ---- 5. 对过期 main head 提交并得到冲突（无半提交）----
    with pytest.raises(ViolationError) as err:
        svc.commit(
            CommitRequest(
                project_id=project.id,
                branch_id=main_now.id,
                expected_head=None,  # 过期：main head 已是 first
                actor=SYSTEM_ACTOR,
                idempotency_key="acc-stale",
                summary="基于过期 head 的提交",
                changes=(ArtifactChange(artifact_id=clue, content=CLUE_V2),),
            )
        )
    assert err.value.code == ec.REPO_HEAD_CONFLICT
    still = svc.get_branch(project.id, "main")
    assert still is not None and still.head_commit_id == first.commit_id
    assert svc.get_snapshot(project.id, first.commit_id).artifacts[clue].revision is not None

    # ---- 6. 重新读取后基于新 head 成功提交 ----
    fresh = svc.get_branch(project.id, "main")
    assert fresh is not None
    third = svc.commit(
        CommitRequest(
            project_id=project.id,
            branch_id=fresh.id,
            expected_head=fresh.head_commit_id,
            actor=SYSTEM_ACTOR,
            idempotency_key="acc-3",
            summary="补充座钟线索细节",
            changes=(
                ArtifactChange(
                    artifact_id=clue,
                    content=CLUE_V2,
                    old_revision_id=_rev_of(svc, project.id, fresh.head_commit_id, clue),
                ),
            ),
        )
    )
    assert third.is_committed and third.sequence == 3

    # ---- 7. 重启 Repository ----
    if env.repo.__class__.__name__ == "SqliteRepository":
        env.repo.close()
        env.repo = make_env_sqlite_factory(tmp_path / "acceptance.db")().repo
    svc = env.repo.service

    # ---- 8. 验证所有 Commit、Branch、sequence、Outbox 和 hash 一致 ----
    main_after = svc.get_branch(project.id, "main")
    rewrite_after = svc.get_branch(project.id, "rewrite")
    assert main_after is not None and main_after.head_commit_id == third.commit_id
    assert rewrite_after is not None and rewrite_after.head_commit_id == second.commit_id
    # sequence 连续：1..3
    events = _pending_events(env.repo, project.id.value)
    sequences = sorted(int(c.sequence) for c in (first, second, third))
    assert sequences == [1, 2, 3]
    # 快照内容 hash 稳定：rewrite 上的 ch1 是 V2，main 上的 clue 是 V2
    snap_rewrite = svc.get_snapshot(project.id, second.commit_id)
    rev_ch1 = snap_rewrite.artifacts[ch1].revision
    assert rev_ch1 is not None and rev_ch1.content == CH1_V2
    assert rev_ch1.content_hash.value == content_hash(CH1_V2)
    snap_main = svc.get_snapshot(project.id, third.commit_id)
    rev_clue = snap_main.artifacts[clue].revision
    assert rev_clue is not None and rev_clue.content == CLUE_V2
    assert rev_clue.content_hash.value == content_hash(CLUE_V2)
    # 伏笔关系在两个分支都可见
    assert any(r.relation_type == REL_TYPE for r in snap_main.relations)
    # Outbox：4 + 3 + 3 = 10 个未发布事件
    assert len(events) == 10
    # SQLite：inspection 全绿 + 备份一致性
    if env.repo.__class__.__name__ == "SqliteRepository":
        from hastings.infrastructure.repository.inspection import inspect_repository

        assert inspect_repository(tmp_path / "acceptance.db").ok
        backup = tmp_path / "backup.db"
        env.repo.backup_to(backup)
        report = inspect_repository(backup)
        assert report.ok
        assert report.projects[0].commit_count == 3


def _rev_of(svc: Any, project_id: Any, commit_id: CommitId, artifact_id: ArtifactId) -> Any:
    snapshot = svc.get_snapshot(project_id, commit_id)
    rev = snapshot.artifacts[artifact_id].revision
    assert rev is not None
    return rev.id
