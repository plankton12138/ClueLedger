"""Repository 契约测试（Phase 12）：同一套检查运行于内存与 SQLite 后端。

检查清单对应计划 §6 与 modules/repository-kernel.md §8：
初始提交、head 冲突、多对象原子性、no-op、幂等、分支/diff/restore、
relation、lease、Commit+Outbox 同事务、重开不变、并发单胜者。

env 由各后端测试文件提供（conftest.make_env_memory / sqlite 工厂）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from hastings.application.errors import HastingsError, ViolationError
from hastings.domain.repository import error_codes as ec
from hastings.domain.repository.ids import ArtifactId, CommitId
from hastings.domain.repository.models import (
    RELATION_ACTION_ADD,
    RELATION_ACTION_REMOVE,
)
from hastings.domain.repository.requests import (
    ArtifactChange,
    ArtifactQuery,
    CommitRequest,
    CreateBranchRequest,
    RelationChange,
)
from conftest import AGENT_ACTOR, SYSTEM_ACTOR, ContractEnv

CHAPTER_ONE = {"title": "第一章 雾中来客", "body": "雾从海湾漫上来。"}
CHAPTER_ONE_REVISED = {"title": "第一章 雾中来客", "body": "雾从海湾漫上来，灯先灭了。"}


def _svc(env: ContractEnv) -> Any:
    return env.repo.service


def _new_project(env: ContractEnv, name: str = "winter-manor") -> Any:
    return _svc(env).create_project(name)


def _first_commit(env: ContractEnv, project: Any, content: dict[str, Any] | None = None) -> Any:
    """创建 main 上的首个提交，返回 CommitResult。"""
    branch = _svc(env).get_branch(project.id, "main")
    request = CommitRequest(
        project_id=project.id,
        branch_id=branch.id,
        expected_head=None,
        actor=SYSTEM_ACTOR,
        idempotency_key=f"init-{project.id.value}",
        summary="初始提交",
        changes=(
            ArtifactChange(
                artifact_id=ArtifactId(f"art_{project.id.value}_ch1"),
                content=content or CHAPTER_ONE,
                kind="chapter",
            ),
        ),
    )
    return _svc(env).commit(request)


# ----------------------------------------------------------------------
# 1/2. 初始 Commit 与 Snapshot
# ----------------------------------------------------------------------


def check_initial_commit_and_snapshot(env: ContractEnv) -> None:
    project = _new_project(env)
    assert _svc(env).get_project(project.id) is not None
    branch = _svc(env).get_branch(project.id, "main")
    assert branch is not None and branch.head_commit_id is None

    result = _first_commit(env, project)
    assert result.is_committed and result.sequence == 1
    assert result.commit_id == result.new_head

    after = _svc(env).get_branch(project.id, "main")
    assert after.head_commit_id == result.commit_id
    assert after.version == 2

    snapshot = _svc(env).get_snapshot(project.id, result.commit_id)
    aid = ArtifactId(f"art_{project.id.value}_ch1")
    view = snapshot.artifacts[aid]
    assert view.revision is not None
    assert view.revision.content == CHAPTER_ONE
    assert view.revision.parent_id is None

    # 未知提交读取快照 → 稳定违规码
    with pytest.raises(ViolationError) as err:
        _svc(env).get_snapshot(project.id, CommitId("cmt_nope"))
    assert err.value.code == ec.REPO_COMMIT_NOT_FOUND


# ----------------------------------------------------------------------
# 3. expected head 冲突（且无半提交）
# ----------------------------------------------------------------------


def check_head_conflict(env: ContractEnv) -> None:
    project = _new_project(env)
    first = _first_commit(env, project)
    branch = _svc(env).get_branch(project.id, "main")
    aid = ArtifactId(f"art_{project.id.value}_ch1")

    stale = CommitRequest(
        project_id=project.id,
        branch_id=branch.id,
        expected_head=None,  # 过期：head 已是 first
        actor=AGENT_ACTOR,
        idempotency_key="stale-1",
        summary="基于过期 head 的提交",
        changes=(ArtifactChange(artifact_id=aid, content=CHAPTER_ONE_REVISED,
                                old_revision_id=None),),
    )
    with pytest.raises(HastingsError) as err:
        _svc(env).commit(stale)
    assert err.value.code == ec.REPO_HEAD_CONFLICT
    assert err.value.details["actual"] == first.commit_id.value

    # 无半提交：head 不变、无新 commit/revision/事件
    assert _svc(env).get_branch(project.id, "main").head_commit_id == first.commit_id
    outbox = asyncio.run(env.repo.pending(project.id.value, limit=100))
    assert len(outbox) == 3  # 首次提交的 3 个事件，无新增


# ----------------------------------------------------------------------
# 4. 多 Artifact 原子提交（含关系）
# ----------------------------------------------------------------------


def check_multi_artifact_atomic(env: ContractEnv) -> None:
    project = _new_project(env)
    branch = _svc(env).get_branch(project.id, "main")
    ch1, ch2 = ArtifactId(f"art_{project.id.value}_ch1"), ArtifactId(f"art_{project.id.value}_ch2")
    foreshadow = ArtifactId(f"art_{project.id.value}_fs1")

    request = CommitRequest(
        project_id=project.id,
        branch_id=branch.id,
        expected_head=None,
        actor=SYSTEM_ACTOR,
        idempotency_key="multi-1",
        summary="两个章节加一条伏笔关系",
        changes=(
            ArtifactChange(artifact_id=ch1, content=CHAPTER_ONE, kind="chapter"),
            ArtifactChange(artifact_id=ch2, content={"title": "第二章", "body": "信在抽屉里。"},
                           kind="chapter"),
        ),
        relations=(
            RelationChange(action=RELATION_ACTION_ADD, source=ch1,
                           relation_type="foreshadows", target=foreshadow),
            RelationChange(action=RELATION_ACTION_ADD, source=foreshadow,
                           relation_type="hidden_in", target=ch2),
        ),
    )
    result = _svc(env).commit(request)
    assert result.is_committed

    snapshot = _svc(env).get_snapshot(project.id, result.commit_id)
    assert set(snapshot.artifacts) == {ch1, ch2}
    assert len(snapshot.relations) == 2
    # 关系可以在同一提交里指向新建对象
    assert any(r.source == foreshadow for r in snapshot.relations)


def check_multi_artifact_failure_rolls_back_all(env: ContractEnv) -> None:
    """一条非法变化 → 全部回滚（契约 §8：多对象提交遇非法变化全回滚）。"""
    project = _new_project(env)
    branch = _svc(env).get_branch(project.id, "main")
    ch1, bad = ArtifactId(f"art_{project.id.value}_ch1"), ArtifactId(f"art_{project.id.value}_bad")

    request = CommitRequest(
        project_id=project.id,
        branch_id=branch.id,
        expected_head=None,
        actor=SYSTEM_ACTOR,
        idempotency_key="atomic-1",
        summary="合法对象 + 非法对象（缺 kind）",
        changes=(
            ArtifactChange(artifact_id=ch1, content=CHAPTER_ONE, kind="chapter"),
            ArtifactChange(artifact_id=bad, content={"body": "x"}),
        ),
    )
    with pytest.raises(ViolationError) as err:
        _svc(env).commit(request)
    assert err.value.code == ec.REPO_INVALID_CHANGE
    # 回滚后分支仍无 head，事件一条不发
    assert _svc(env).get_branch(project.id, "main").head_commit_id is None
    assert asyncio.run(env.repo.pending(project.id.value, limit=10)) == []


# ----------------------------------------------------------------------
# 5. no-op 不创建 Commit
# ----------------------------------------------------------------------


def check_no_op(env: ContractEnv) -> None:
    project = _new_project(env)
    first = _first_commit(env, project)
    branch = _svc(env).get_branch(project.id, "main")
    aid = ArtifactId(f"art_{project.id.value}_ch1")
    revision = _svc(env).get_artifact(aid, first.commit_id).revision

    same = CommitRequest(
        project_id=project.id,
        branch_id=branch.id,
        expected_head=first.commit_id,
        actor=AGENT_ACTOR,
        idempotency_key="noop-1",
        summary="重复写入相同内容",
        changes=(ArtifactChange(artifact_id=aid, content=CHAPTER_ONE,
                                old_revision_id=revision.id),),
    )
    result = _svc(env).commit(same)
    assert result.is_no_change
    assert result.commit_id is None
    assert "REPO_NO_CHANGE" in (result.no_change_reason or "")
    # head 与提交数不变
    assert _svc(env).get_branch(project.id, "main").head_commit_id == first.commit_id
    outbox = asyncio.run(env.repo.pending(project.id.value, limit=100))
    assert len(outbox) == 3


# ----------------------------------------------------------------------
# 6. 幂等重试
# ----------------------------------------------------------------------


def check_idempotent_retry(env: ContractEnv) -> None:
    project = _new_project(env)
    branch = _svc(env).get_branch(project.id, "main")
    aid = ArtifactId(f"art_{project.id.value}_ch1")

    def make(key: str) -> CommitRequest:
        return CommitRequest(
            project_id=project.id,
            branch_id=branch.id,
            expected_head=None,
            actor=SYSTEM_ACTOR,
            idempotency_key=key,
            summary="初始提交（可能重试）",
            changes=(ArtifactChange(artifact_id=aid, content=CHAPTER_ONE, kind="chapter"),),
        )

    first = _svc(env).commit(make("retry-key"))
    assert first.is_committed
    retry = _svc(env).commit(make("retry-key"))
    assert retry == first
    # 无重复 Revision / 事件
    outbox = asyncio.run(env.repo.pending(project.id.value, limit=100))
    assert len(outbox) == 3
    snapshot = _svc(env).get_snapshot(project.id, first.commit_id)
    assert len(snapshot.artifacts) == 1


# ----------------------------------------------------------------------
# 7. Branch 分叉、diff
# ----------------------------------------------------------------------


def check_branch_fork_and_diff(env: ContractEnv) -> None:
    project = _new_project(env)
    first = _first_commit(env, project)
    aid = ArtifactId(f"art_{project.id.value}_ch1")

    rewrite = _svc(env).create_branch(
        CreateBranchRequest(project_id=project.id, name="rewrite", at_commit_id=first.commit_id)
    )
    assert rewrite.head_commit_id == first.commit_id

    old_rev = _svc(env).get_artifact(aid, first.commit_id).revision
    request = CommitRequest(
        project_id=project.id,
        branch_id=rewrite.id,
        expected_head=first.commit_id,
        actor=AGENT_ACTOR,
        idempotency_key="rewrite-1",
        summary="重写第一章",
        changes=(ArtifactChange(artifact_id=aid, content=CHAPTER_ONE_REVISED,
                                old_revision_id=old_rev.id),),
    )
    second = _svc(env).commit(request)
    assert second.is_committed

    diff = _svc(env).diff(first.commit_id, second.commit_id)
    assert len(diff.artifact_changes) == 1
    assert diff.artifact_changes[0].artifact_id == aid
    assert diff.artifact_changes[0].old_revision_id == old_rev.id
    assert diff.artifact_changes[0].new_revision_id is not None

    # main 仍在 first，不受 rewrite 影响
    assert _svc(env).get_branch(project.id, "main").head_commit_id == first.commit_id

    # 重名分支被拒
    from hastings.domain.repository import error_codes as err_codes

    with pytest.raises(ViolationError) as err:
        _svc(env).create_branch(
            CreateBranchRequest(project_id=project.id, name="rewrite",
                                at_commit_id=first.commit_id)
        )
    assert err.value.code == err_codes.REPO_BRANCH_EXISTS

    # list_artifacts 按 kind 过滤
    views = _svc(env).list_artifacts(ArtifactQuery(kind="chapter"), second.commit_id)
    assert len(views) == 1 and views[0].artifact.id == aid
    assert _svc(env).list_artifacts(ArtifactQuery(kind="clue"), second.commit_id) == []


# ----------------------------------------------------------------------
# 8. restore 产生新 Commit（不允许移动指针撤销）
# ----------------------------------------------------------------------


def check_restore_as_new_commit(env: ContractEnv) -> None:
    project = _new_project(env)
    first = _first_commit(env, project)
    aid = ArtifactId(f"art_{project.id.value}_ch1")
    branch = _svc(env).get_branch(project.id, "main")
    old_rev = _svc(env).get_artifact(aid, first.commit_id).revision

    revise = CommitRequest(
        project_id=project.id,
        branch_id=branch.id,
        expected_head=first.commit_id,
        actor=AGENT_ACTOR,
        idempotency_key="rev-1",
        summary="修改第一章",
        changes=(ArtifactChange(artifact_id=aid, content=CHAPTER_ONE_REVISED,
                                old_revision_id=old_rev.id),),
    )
    second = _svc(env).commit(revise)
    assert second.is_committed

    restored = _svc(env).restore(
        project_id=project.id,
        branch_id=branch.id,
        expected_head=second.commit_id,
        artifact_id=aid,
        at_commit_id=first.commit_id,
        actor=SYSTEM_ACTOR,
        idempotency_key="restore-1",
        summary="恢复第一章到初版",
    )
    assert restored.is_committed
    view = _svc(env).get_artifact(aid, restored.commit_id)
    assert view.revision is not None
    assert view.revision.content == CHAPTER_ONE
    assert view.revision.id != old_rev.id  # 新 Revision，不是历史复用
    assert view.revision.parent_id is not None

    # 历史 Commit 未被改写：first 快照仍是修改前内容
    assert _svc(env).get_artifact(aid, first.commit_id).revision.content == CHAPTER_ONE

    # 分支指针不允许移回旧历史伪装撤销
    from hastings.domain.repository import error_codes as err_codes

    with pytest.raises(ViolationError) as err:
        _svc(env).move_branch(
            _move_request(branch.id, first.commit_id, version=4)
        )
    assert err.value.code == err_codes.REPO_BRANCH_MOVE_BACKWARD


def _move_request(branch_id: Any, new_head: Any, version: int) -> Any:
    from hastings.domain.repository.requests import MoveBranchRequest

    return MoveBranchRequest(
        branch_id=branch_id, new_head_commit_id=new_head, expected_version=version
    )


# ----------------------------------------------------------------------
# 9. Relation revision：add/remove 折叠
# ----------------------------------------------------------------------


def check_relation_revision(env: ContractEnv) -> None:
    project = _new_project(env)
    branch = _svc(env).get_branch(project.id, "main")
    ch1, clue = ArtifactId(f"art_{project.id.value}_ch1"), ArtifactId(f"art_{project.id.value}_clue")
    result = _svc(env).commit(
        CommitRequest(
            project_id=project.id,
            branch_id=branch.id,
            expected_head=None,
            actor=SYSTEM_ACTOR,
            idempotency_key="rel-1",
            summary="章节与线索",
            changes=(
                ArtifactChange(artifact_id=ch1, content=CHAPTER_ONE, kind="chapter"),
                ArtifactChange(artifact_id=clue, content={"name": "烧毁的信"}, kind="clue"),
            ),
            relations=(
                RelationChange(action=RELATION_ACTION_ADD, source=ch1,
                               relation_type="introduces", target=clue,
                               metadata={"note": "首次出场"}),
            ),
        )
    )
    assert result.is_committed
    snapshot = _svc(env).get_snapshot(project.id, result.commit_id)
    assert len(snapshot.relations) == 1
    assert snapshot.relations[0].metadata == {"note": "首次出场"}

    # 同一提交里 add 又 remove → 净效果为无边
    branch2 = _svc(env).get_branch(project.id, "main")
    result2 = _svc(env).commit(
        CommitRequest(
            project_id=project.id,
            branch_id=branch2.id,
            expected_head=result.commit_id,
            actor=SYSTEM_ACTOR,
            idempotency_key="rel-2",
            summary="补一条又撤销",
            changes=(),
            relations=(
                RelationChange(action=RELATION_ACTION_ADD, source=clue,
                               relation_type="points_to", target=ch1),
                RelationChange(action=RELATION_ACTION_REMOVE, source=clue,
                               relation_type="points_to", target=ch1),
            ),
        )
    )
    assert result2.is_committed
    snapshot2 = _svc(env).get_snapshot(project.id, result2.commit_id)
    keys = {(r.source.value, r.relation_type, r.target.value) for r in snapshot2.relations}
    assert ("art_%s_clue" % project.id.value, "points_to", "art_%s_ch1" % project.id.value) not in keys
    # 原有边仍在
    assert any(r.relation_type == "introduces" for r in snapshot2.relations)


# ----------------------------------------------------------------------
# 10. Lease（契约级基础语义；时钟到期细化在 test_lease.py）
# ----------------------------------------------------------------------


def check_lease_basic(env: ContractEnv) -> None:
    from hastings.domain.repository.ids import JobId
    from hastings.domain.repository.models import lease_resource_key

    project = _new_project(env)
    aid = ArtifactId("art_lease_target")
    key = lease_resource_key(project.id, aid)
    job_a, job_b = JobId("job_a"), JobId("job_b")

    lease = env.repo.acquire(key, job_a, ttl_seconds=60)
    assert lease.version == 1
    with pytest.raises(ViolationError) as err:
        env.repo.acquire(key, job_b, ttl_seconds=60)
    assert err.value.code == ec.REPO_LEASE_CONFLICT

    renewed = env.repo.renew(lease, ttl_seconds=60)
    assert renewed.version == 2
    with pytest.raises(ViolationError):
        env.repo.renew(lease, ttl_seconds=60)  # 旧 version 不能续租
    env.repo.release(renewed)
    lease_b = env.repo.acquire(key, job_b, ttl_seconds=60)
    assert lease_b.version > renewed.version  # 版本号跨 release 单调，防 ABA 复用
    with pytest.raises(ViolationError):
        env.repo.release(renewed)  # 旧 owner 不能释放


# ----------------------------------------------------------------------
# 11. Commit + Outbox 同事务；事件 payload 无正文
# ----------------------------------------------------------------------


def check_commit_and_outbox(env: ContractEnv) -> None:
    project = _new_project(env)
    _first_commit(env, project, content={"title": "标题", "body": "正文" * 500})
    events = asyncio.run(env.repo.pending(project.id.value, limit=100))
    types = [e["type"] for e in events]
    assert types == [
        "repository.commit.created",
        "artifact.revision.created",
        "branch.head.moved",
    ]
    sequences = [e["sequence"] for e in events]
    assert sequences == sorted(sequences) and len(set(sequences)) == len(sequences)
    # payload 不含完整正文
    raw = str(events)
    assert "正文" * 100 not in raw
    assert all("content_hash" in e["payload"] for e in events if e["type"] == "artifact.revision.created")
    # 标记发布后不再出现在 pending
    asyncio.run(env.repo.mark_published(str(events[0]["id"])))
    remaining = asyncio.run(env.repo.pending(project.id.value, limit=100))
    assert len(remaining) == 2


# ----------------------------------------------------------------------
# 13. 重开后状态不变（memory 是同一实例；sqlite 走真实重开）
# ----------------------------------------------------------------------


def check_reopen_preserves_state(env: ContractEnv) -> None:
    project = _new_project(env)
    first = _first_commit(env, project)
    assert env.reopen is not None
    env2 = env.reopen()
    svc2 = env2.repo.service
    branch = svc2.get_branch(project.id, "main")
    assert branch.head_commit_id == first.commit_id
    assert svc2.get_project(project.id) is not None
    snapshot = svc2.get_snapshot(project.id, first.commit_id)
    aid = ArtifactId(f"art_{project.id.value}_ch1")
    assert snapshot.artifacts[aid].revision.content == CHAPTER_ONE
    events = asyncio.run(env2.repo.pending(project.id.value, limit=100))
    assert len(events) == 3  # 已发布状态与未发布状态都保留


# ----------------------------------------------------------------------
# 15. 并发提交只有一个成功
# ----------------------------------------------------------------------


def check_concurrent_commit_single_winner(env: ContractEnv) -> None:
    import threading

    project = _new_project(env)
    first = _first_commit(env, project)
    branch = _svc(env).get_branch(project.id, "main")
    aid = ArtifactId(f"art_{project.id.value}_ch1")
    old_rev = _svc(env).get_artifact(aid, first.commit_id).revision

    results: list[Any] = []
    errors: list[HastingsError] = []

    def worker(tag: str) -> None:
        request = CommitRequest(
            project_id=project.id,
            branch_id=branch.id,
            expected_head=first.commit_id,
            actor=AGENT_ACTOR,
            idempotency_key=f"race-{tag}",
            summary=f"竞争提交 {tag}",
            changes=(ArtifactChange(artifact_id=aid,
                                    content={"title": "t", "body": f"版本 {tag}"},
                                    old_revision_id=old_rev.id),),
        )
        try:
            results.append(_svc(env).commit(request))
        except HastingsError as exc:  # noqa: PERF203
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(str(i),)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 1 and len(errors) == 1
    assert errors[0].code == ec.REPO_HEAD_CONFLICT
    assert _svc(env).get_branch(project.id, "main").head_commit_id == results[0].commit_id


# ----------------------------------------------------------------------
# 汇总：契约测试入口
# ----------------------------------------------------------------------

ALL_CHECKS: tuple[Callable[[ContractEnv], None], ...] = (
    check_initial_commit_and_snapshot,
    check_head_conflict,
    check_multi_artifact_atomic,
    check_multi_artifact_failure_rolls_back_all,
    check_no_op,
    check_idempotent_retry,
    check_branch_fork_and_diff,
    check_restore_as_new_commit,
    check_relation_revision,
    check_lease_basic,
    check_commit_and_outbox,
    check_reopen_preserves_state,
    check_concurrent_commit_single_winner,
)
