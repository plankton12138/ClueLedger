"""Outbox Publisher 专项（T12.7）：顺序发布、崩溃重投、event_id 去重。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeClock, FakeIds

from hastings.infrastructure.events.outbox import DeduplicatingSink, OutboxPublisher


@pytest.fixture(params=["memory", "sqlite"])
def repo_env(request: pytest.FixtureRequest, tmp_path: Path) -> Any:
    if request.param == "memory":
        from hastings.infrastructure.repository.memory import build_memory_repository

        repo = build_memory_repository(FakeClock(), FakeIds())
    else:
        from hastings.infrastructure.repository.sqlite import build_sqlite_repository

        repo = build_sqlite_repository(
            tmp_path / "outbox.sqlite3", clock=FakeClock(), ids=FakeIds()
        )
    service = repo.service
    project = service.create_project("outbox-proj")
    branch = service.get_branch(project.id, "main")
    from conftest import SYSTEM_ACTOR

    from hastings.domain.repository.ids import ArtifactId
    from hastings.domain.repository.requests import ArtifactChange, CommitRequest

    for i in range(2):
        head = service.get_branch(project.id, "main").head_commit_id
        service.commit(
            CommitRequest(
                project_id=project.id,
                branch_id=branch.id,
                expected_head=head,
                actor=SYSTEM_ACTOR,
                idempotency_key=f"c{i}",
                summary=f"第 {i} 次提交",
                changes=(
                    ArtifactChange(
                        artifact_id=ArtifactId(f"art_{i}"), content={"n": i}, kind="chapter"
                    ),
                ),
            )
        )
    return repo, project


def test_publisher_publishes_in_sequence_order_and_marks(repo_env: Any) -> None:
    repo, project = repo_env
    sink = DeduplicatingSink()
    publisher = OutboxPublisher(repo, sink)

    published = asyncio.run(publisher.publish_pending(project.id.value))
    assert published == 6  # 2 次提交 × 3 事件
    sequences = [e["sequence"] for e in sink.effects]
    assert sequences == sorted(sequences)
    # 全部标记后 pending 清空
    remaining = asyncio.run(repo.pending(project.id.value, 100))
    assert remaining == []


def test_crash_between_publish_and_mark_redelivers_without_duplicate_effect(
    repo_env: Any,
) -> None:
    """发布成功后、标记前崩溃 → 重投；消费端按 event_id 去重，业务效果只有一次。"""
    repo, project = repo_env
    sink = DeduplicatingSink()
    publisher = OutboxPublisher(repo, sink)

    original_mark = repo.mark_published
    state = {"crashed": False}

    async def crashing_mark(event_id: str) -> None:
        if not state["crashed"]:
            state["crashed"] = True
            raise RuntimeError("模拟标记前进程崩溃")
        await original_mark(event_id)

    repo.mark_published = crashing_mark  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="崩溃"):
        asyncio.run(publisher.publish_pending(project.id.value))
    # 崩溃前已有事件发布但未标记
    assert len(sink.effects) >= 1

    repo.mark_published = original_mark  # type: ignore[method-assign]
    # 恢复后重跑：重复投递不产生重复业务效果
    asyncio.run(publisher.publish_pending(project.id.value))
    effect_ids = [e["id"] for e in sink.effects]
    assert len(effect_ids) == len(set(effect_ids))
    remaining = asyncio.run(repo.pending(project.id.value, 100))
    assert remaining == []


def test_publisher_failure_keeps_event_pending(repo_env: Any) -> None:
    """sink 失败 → 事件保持待发布，恢复后可重试发布。"""
    repo, project = repo_env

    class FailingSink:
        def __init__(self) -> None:
            self.fail = True
            self.published: list[Any] = []

        async def publish(self, event: Any) -> None:
            if self.fail:
                raise RuntimeError("下游不可用")
            self.published.append(event)

    sink = FailingSink()
    publisher = OutboxPublisher(repo, sink)
    with pytest.raises(RuntimeError, match="下游不可用"):
        asyncio.run(publisher.publish_pending(project.id.value))
    # 事件仍在 pending（顺序保持）
    pending = asyncio.run(repo.pending(project.id.value, 100))
    assert len(pending) == 6

    sink.fail = False
    published = asyncio.run(publisher.publish_pending(project.id.value))
    assert published == 6
    assert asyncio.run(repo.pending(project.id.value, 100)) == []


def test_handler_failure_is_retried_not_swallowed(repo_env: Any) -> None:
    """handler 失败后不得记入 seen：事件保持 pending，重投时 handler 再次执行。"""
    repo, project = repo_env
    calls: list[str] = []
    state = {"fail": True}

    async def flaky_handler(event: Any) -> None:
        event_id = str(event["id"])
        calls.append(event_id)
        if state["fail"]:
            raise RuntimeError("业务副作用失败")

    sink = DeduplicatingSink(handler=flaky_handler)
    publisher = OutboxPublisher(repo, sink)

    with pytest.raises(RuntimeError, match="业务副作用失败"):
        asyncio.run(publisher.publish_pending(project.id.value))
    assert len(calls) == 1  # 第一个事件 handler 失败，发布即停
    assert not sink.seen_event_ids  # 失败的事件未记账，不会在重试时被吞
    assert sink.effects == []

    state["fail"] = False
    published = asyncio.run(publisher.publish_pending(project.id.value))
    assert published == 6
    # handler 对每个事件恰好执行一次：失败的那次被重试，成功的不重复
    assert len(calls) == 7
    assert len(sink.effects) == 6
    assert asyncio.run(repo.pending(project.id.value, 100)) == []
