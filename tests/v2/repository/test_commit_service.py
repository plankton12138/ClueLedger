"""Commit Service 专项（T12.4）：固定顺序、每个写点故障注入、原子回滚。"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from conftest import SYSTEM_ACTOR, ContractEnv, FakeIds

from hastings.application.services.commit_service import CommitService
from hastings.domain.repository.ids import ArtifactId
from hastings.domain.repository.requests import ArtifactChange, CommitRequest, RelationChange

WRITE_OPS = (
    "insert_artifact",
    "insert_revision",
    "insert_relation",
    "insert_commit",
    "set_branch_head",
    "append_outbox_events",
    "put_idempotent_result",
)

# 每个写点之后、下一个写点之前注入异常，验证已写入的部分被整体回滚
INJECTED_FAULT = "事务中注入的故障（测试）"


class FaultTxn:
    """包装真实 txn：指定写点在写入成功后抛异常，制造「半提交」现场。"""

    def __init__(self, inner: Any, fault_op: str) -> None:
        self._inner = inner
        self._fault_op = fault_op

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _maybe_fault(self, op: str) -> None:
        if op == self._fault_op:
            raise RuntimeError(INJECTED_FAULT)

    def insert_artifact(self, artifact: Any) -> None:
        self._inner.insert_artifact(artifact)
        self._maybe_fault("insert_artifact")

    def insert_revision(self, revision: Any) -> None:
        self._inner.insert_revision(revision)
        self._maybe_fault("insert_revision")

    def insert_relation(self, row: Any) -> None:
        self._inner.insert_relation(row)
        self._maybe_fault("insert_relation")

    def insert_commit(self, commit: Any, entries: Any) -> None:
        self._inner.insert_commit(commit, entries)
        self._maybe_fault("insert_commit")

    def set_branch_head(self, branch_id: Any, new_head: Any, expected_version: int) -> None:
        self._inner.set_branch_head(branch_id, new_head, expected_version)
        self._maybe_fault("set_branch_head")

    def append_outbox_events(self, events: Any) -> None:
        self._inner.append_outbox_events(events)
        self._maybe_fault("append_outbox_events")

    def put_idempotent_result(
        self, project_id: Any, key: str, fingerprint: str, result: Any
    ) -> None:
        self._inner.put_idempotent_result(project_id, key, fingerprint, result)
        self._maybe_fault("put_idempotent_result")


class FaultStorage:
    """包装 storage：把真实事务里包上 FaultTxn。"""

    def __init__(self, inner: Any, fault_op: str) -> None:
        self._inner = inner
        self._fault_op = fault_op

    def now(self) -> Any:
        return self._inner.now()

    def transaction(self) -> Any:
        inner_cm = self._inner.transaction()

        @contextmanager
        def cm() -> Iterator[FaultTxn]:
            with inner_cm as txn:
                yield FaultTxn(txn, self._fault_op)

        return cm()


class RecordingStorage:
    """记录写点调用顺序，验证固定提交顺序（T12.4）。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: list[str] = []

    def now(self) -> Any:
        return self._inner.now()

    def transaction(self) -> Any:
        inner_cm = self._inner.transaction()

        @contextmanager
        def cm() -> Iterator[Any]:
            with inner_cm as txn:
                yield _RecordingTxn(txn, self.calls)

        return cm()


class _RecordingTxn:
    def __init__(self, inner: Any, calls: list[str]) -> None:
        self._inner = inner
        self._calls = calls

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def insert_artifact(self, artifact: Any) -> None:
        self._calls.append("insert_artifact")
        self._inner.insert_artifact(artifact)

    def insert_revision(self, revision: Any) -> None:
        self._calls.append("insert_revision")
        self._inner.insert_revision(revision)

    def insert_relation(self, row: Any) -> None:
        self._calls.append("insert_relation")
        self._inner.insert_relation(row)

    def insert_commit(self, commit: Any, entries: Any) -> None:
        self._calls.append("insert_commit")
        self._inner.insert_commit(commit, entries)

    def set_branch_head(self, branch_id: Any, new_head: Any, expected_version: int) -> None:
        self._calls.append("set_branch_head")
        self._inner.set_branch_head(branch_id, new_head, expected_version)

    def append_outbox_events(self, events: Any) -> None:
        self._calls.append("append_outbox_events")
        self._inner.append_outbox_events(events)

    def put_idempotent_result(
        self, project_id: Any, key: str, fingerprint: str, result: Any
    ) -> None:
        self._calls.append("put_idempotent_result")
        self._inner.put_idempotent_result(project_id, key, fingerprint, result)


def _setup_project(env: ContractEnv) -> Any:
    service = env.repo.service
    project = service.create_project("fog-manor")
    return project


def _second_commit_request(env: ContractEnv, project: Any) -> CommitRequest:
    service = env.repo.service
    branch = service.get_branch(project.id, "main")
    ch1, ch2 = ArtifactId("art_ch1"), ArtifactId("art_ch2")
    return CommitRequest(
        project_id=project.id,
        branch_id=branch.id,
        expected_head=branch.head_commit_id,
        actor=SYSTEM_ACTOR,
        idempotency_key="second-1",
        summary="新增两章及一条引用",
        changes=(
            ArtifactChange(artifact_id=ch1, content={"title": "第一章"}, kind="chapter"),
            ArtifactChange(artifact_id=ch2, content={"title": "第二章"}, kind="chapter"),
        ),
        relations=(RelationChange(action="add", source=ch1, relation_type="follows", target=ch2),),
    )


def _snapshot_state(env: ContractEnv, project: Any) -> tuple[Any, int, int]:
    service = env.repo.service
    branch = service.get_branch(project.id, "main")
    outbox = asyncio.run(env.repo.pending(project.id.value, limit=100))
    return branch.head_commit_id, len(outbox), branch.version


@pytest.mark.parametrize("fault_op", WRITE_OPS)
def test_fault_at_every_write_point_rolls_back(fault_op: str) -> None:
    """每个写点注入异常 → 整个提交回滚，无半提交（计划 T12.4）。"""
    from conftest import FakeClock

    from hastings.infrastructure.repository.memory import build_memory_repository

    clock, ids = FakeClock(), FakeIds()
    repo = build_memory_repository(clock, ids)
    env = ContractEnv(repo=repo, clock=clock, ids=ids)
    project = _setup_project(env)
    faulted = CommitService(FaultStorage(repo._storage, fault_op), FakeIds())
    before = _snapshot_state(env, project)

    with pytest.raises(RuntimeError, match="注入的故障"):
        faulted.commit(_second_commit_request(env, project))

    after = _snapshot_state(env, project)
    assert before == after, f"写点 {fault_op} 注入故障后出现半提交"


def test_commit_write_order_is_fixed() -> None:
    """写点顺序：artifact → revision → commit → branch → outbox → idempotency。"""
    from conftest import FakeClock

    from hastings.infrastructure.repository.memory import build_memory_repository

    clock, ids = FakeClock(), FakeIds()
    repo = build_memory_repository(clock, ids)
    env = ContractEnv(repo=repo, clock=clock, ids=ids)
    project = _setup_project(env)

    recorder = RecordingStorage(repo._storage)
    CommitService(recorder, FakeIds()).commit(_second_commit_request(env, project))

    assert recorder.calls == [
        "insert_artifact",  # ch1
        "insert_artifact",  # ch2
        "insert_revision",
        "insert_revision",
        "insert_commit",
        "insert_relation",
        "set_branch_head",
        "append_outbox_events",
        "put_idempotent_result",
    ]


def test_revision_conflict_on_stale_old_revision() -> None:
    """old_revision 不是基础快照有效版本 → REPO_REVISION_CONFLICT。"""
    from conftest import FakeClock

    from hastings.domain.repository import error_codes as ec
    from hastings.domain.repository.ids import RevisionId
    from hastings.infrastructure.repository.memory import build_memory_repository

    clock, ids = FakeClock(), FakeIds()
    repo = build_memory_repository(clock, ids)
    env = ContractEnv(repo=repo, clock=clock, ids=ids)
    service = env.repo.service
    project = _setup_project(env)
    branch = service.get_branch(project.id, "main")
    aid = ArtifactId("art_ch1")
    request = CommitRequest(
        project_id=project.id,
        branch_id=branch.id,
        expected_head=None,
        actor=SYSTEM_ACTOR,
        idempotency_key="init-1",
        summary="初始提交",
        changes=(ArtifactChange(artifact_id=aid, content={"body": "v1"}, kind="chapter"),),
    )
    first = service.commit(request)
    assert first.is_committed

    stale = CommitRequest(
        project_id=project.id,
        branch_id=branch.id,
        expected_head=first.commit_id,
        actor=SYSTEM_ACTOR,
        idempotency_key="stale-rev",
        summary="基于不存在的旧版本",
        changes=(
            ArtifactChange(
                artifact_id=aid,
                content={"body": "v2"},
                old_revision_id=RevisionId("rev_does_not_exist"),
            ),
        ),
    )
    with pytest.raises(Exception) as err:  # noqa: PT011
        service.commit(stale)
    assert ec.REPO_REVISION_CONFLICT in str(err.value)


def test_restore_via_public_service() -> None:
    """restore 走公开端口；恢复内容=历史版本，产生新 Revision/Commit。"""
    from conftest import FakeClock

    from hastings.infrastructure.repository.memory import build_memory_repository

    clock, ids = FakeClock(), FakeIds()
    repo = build_memory_repository(clock, ids)
    env = ContractEnv(repo=repo, clock=clock, ids=ids)
    service = env.repo.service
    project = _setup_project(env)
    branch = service.get_branch(project.id, "main")
    aid = ArtifactId("art_ch1")
    first = service.commit(
        CommitRequest(
            project_id=project.id,
            branch_id=branch.id,
            expected_head=None,
            actor=SYSTEM_ACTOR,
            idempotency_key="v1",
            summary="v1",
            changes=(ArtifactChange(artifact_id=aid, content={"body": "v1"}, kind="chapter"),),
        )
    )
    v1_rev_id = service.get_artifact(aid, first.commit_id).revision.id
    branch = service.get_branch(project.id, "main")
    second = service.commit(
        CommitRequest(
            project_id=project.id,
            branch_id=branch.id,
            expected_head=first.commit_id,
            actor=SYSTEM_ACTOR,
            idempotency_key="v2",
            summary="v2",
            changes=(
                ArtifactChange(artifact_id=aid, content={"body": "v2"}, old_revision_id=v1_rev_id),
            ),
        )
    )
    assert second.is_committed

    restored = service.restore(
        project_id=project.id,
        branch_id=branch.id,
        expected_head=second.commit_id,
        artifact_id=aid,
        at_commit_id=first.commit_id,
        actor=SYSTEM_ACTOR,
        idempotency_key="restore",
        summary="恢复 v1",
    )
    assert restored.is_committed
    view = service.get_artifact(aid, restored.commit_id)
    assert view is not None and view.revision is not None
    assert view.revision.content == {"body": "v1"}
    # 幂等重放
    again = service.restore(
        project_id=project.id,
        branch_id=branch.id,
        expected_head=restored.commit_id,
        artifact_id=aid,
        at_commit_id=first.commit_id,
        actor=SYSTEM_ACTOR,
        idempotency_key="restore",
        summary="恢复 v1",
    )
    assert again == restored
