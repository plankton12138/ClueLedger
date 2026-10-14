"""Lease 专项（T12.6）：注入 Clock 测试到期，无 sleep；双后端。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeClock, FakeIds

from hastings.domain.repository import error_codes as ec
from hastings.domain.repository.ids import ArtifactId, JobId
from hastings.domain.repository.models import lease_resource_key


@pytest.fixture(params=["memory", "sqlite"])
def lease_env(request: pytest.FixtureRequest, tmp_path: Path) -> Callable[[], Any]:
    if request.param == "memory":
        from hastings.infrastructure.repository.memory import build_memory_repository

        def make() -> Any:
            clock, ids = FakeClock(), FakeIds()
            return build_memory_repository(clock, ids), clock

        return make

    def make() -> Any:
        clock, ids = FakeClock(), FakeIds()
        return build_sqlite(str(tmp_path / "lease.sqlite3"), clock, ids), clock

    return make


def build_sqlite(path: Path, clock: FakeClock, ids: FakeIds) -> Any:
    from hastings.infrastructure.repository.sqlite import build_sqlite_repository

    return build_sqlite_repository(path, clock=clock, ids=ids)


def test_expired_lease_can_be_taken_over(lease_env: Callable[[], Any]) -> None:
    repo, clock = lease_env()
    key = lease_resource_key(ArtifactId("art_a"), ArtifactId("art_b"))
    job_a, job_b = JobId("job_a"), JobId("job_b")

    lease = repo.acquire(key, job_a, ttl_seconds=60)
    clock.advance(61)  # 到期：无 sleep，推时钟

    taken = repo.acquire(key, job_b, ttl_seconds=60)
    assert taken.version > lease.version
    with pytest.raises(Exception) as err:  # noqa: PT011
        repo.renew(lease, ttl_seconds=60)  # 旧 owner 续租必须失败
    assert ec.REPO_LEASE_CONFLICT in str(err.value)


def test_commit_with_expired_lease_is_rejected(lease_env: Callable[[], Any]) -> None:
    repo, clock = lease_env()
    service = repo.service
    project = service.create_project("lease-proj")
    branch = service.get_branch(project.id, "main")
    aid = ArtifactId("art_locked")
    key = lease_resource_key(project.id, aid)
    lease = repo.acquire(key, JobId("job_writer"), ttl_seconds=60)

    from conftest import SYSTEM_ACTOR

    from hastings.domain.repository.requests import ArtifactChange, CommitRequest

    def make_request(lease_version: int, key_suffix: str) -> CommitRequest:
        return CommitRequest(
            project_id=project.id,
            branch_id=branch.id,
            expected_head=service.get_branch(project.id, "main").head_commit_id,
            actor=SYSTEM_ACTOR,
            idempotency_key=f"locked-write-{key_suffix}",
            summary="持有租约的写入",
            changes=(ArtifactChange(artifact_id=aid, content={"body": "x"}, kind="chapter"),),
            required_leases=((key, "job_writer", lease_version),),
        )

    # 租约有效：提交成功
    result = service.commit(make_request(lease.version, "ok"))
    assert result.is_committed

    # 租约过期（无人重新获取，行内版本号未变）：提交必须被拒绝
    clock.advance(61)
    request = make_request(lease.version, "expired")
    with pytest.raises(Exception) as err:  # noqa: PT011
        service.commit(request)
    assert ec.REPO_LEASE_CONFLICT in str(err.value)
    # head 未移动
    assert service.get_branch(project.id, "main").head_commit_id == result.commit_id


def test_wrong_owner_or_version_cannot_commit(lease_env: Callable[[], Any]) -> None:
    repo, _clock = lease_env()
    service = repo.service
    project = service.create_project("lease-proj2")
    branch = service.get_branch(project.id, "main")
    aid = ArtifactId("art_locked2")
    key = lease_resource_key(project.id, aid)
    lease = repo.acquire(key, JobId("job_owner"), ttl_seconds=60)

    from conftest import SYSTEM_ACTOR

    from hastings.domain.repository.requests import ArtifactChange, CommitRequest

    def make(owner: str, version: int) -> CommitRequest:
        return CommitRequest(
            project_id=project.id,
            branch_id=branch.id,
            expected_head=None,
            actor=SYSTEM_ACTOR,
            idempotency_key=f"wrong-{owner}-{version}",
            summary="租约校验",
            changes=(ArtifactChange(artifact_id=aid, content={"body": "y"}, kind="chapter"),),
            required_leases=((key, owner, version),),
        )

    for owner, version in (("job_other", lease.version), ("job_owner", lease.version + 99)):
        with pytest.raises(Exception) as err:  # noqa: PT011
            service.commit(make(owner, version))
        assert ec.REPO_LEASE_CONFLICT in str(err.value)
    # 校验通过的正确持有者可以提交
    result = service.commit(make("job_owner", lease.version))
    assert result.is_committed
