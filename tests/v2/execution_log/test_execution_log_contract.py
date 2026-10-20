"""ExecutionLog 契约测试（V03-1.6）：内存与 SQLite 双后端同构。

契约点（guide §3.5 + M13）：append_once 按源事件 ID 幂等、作品内 sequence 单调、
跨项目隔离、page 分页与 next_cursor、task/type 筛选、subscribe 从游标起实时
产出。历史分页、实时订阅、断线补齐共用同一事件 ID 空间——任何后端不满足
即视为破坏契约。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

from hastings.application.ports.execution_log import LogPage
from hastings.domain.execution_log import ExecutionEvent, VersionedRef
from hastings.infrastructure.execution_log import MemoryExecutionLog, SqliteExecutionLog

P1 = "prj_a"
P2 = "prj_b"


def _event(
    project_id: str = P1,
    source: str = "turn_1:started",
    event_type: str = "model_task.started",
    task_id: str = "chat-conv_1",
    attempt_id: str = "turn_1",
) -> ExecutionEvent:
    return ExecutionEvent(
        event_id=f"evt_{source}",
        project_id=project_id,
        sequence=0,
        task_id=task_id,
        attempt_id=attempt_id,
        agent_id=None,
        call_id=None,
        parent_call_id=None,
        event_type=event_type,
        occurred_at="2026-10-17T12:00:00+08:00",
        summary="回复尝试开始",
        source_event_id=source,
        version_refs=(),
    )


BackendFactory = Callable[[], Any]


@pytest.fixture(params=["memory", "sqlite"])
def make_log(request: pytest.FixtureRequest, tmp_path: Any) -> BackendFactory:
    if request.param == "memory":
        return MemoryExecutionLog
    return lambda: SqliteExecutionLog(str(tmp_path / "execution-log.db"))


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# -- append_once 幂等 ---------------------------------------------------------------


def test_append_once_is_idempotent_by_source(make_log: BackendFactory) -> None:
    log = make_log()
    assert run(log.append_once("turn_1:started", _event())) is True
    assert run(log.append_once("turn_1:started", _event())) is False
    page: LogPage = run(log.page(P1, 0, 50))
    assert len(page.events) == 1


def test_append_assigns_monotonic_sequence_per_project(make_log: BackendFactory) -> None:
    log = make_log()
    for i in range(3):
        run(log.append_once(f"turn_1:s{i}", _event(source=f"turn_1:s{i}")))
    page: LogPage = run(log.page(P1, 0, 50))
    assert [e.sequence for e in page.events] == [1, 2, 3]
    # 另一项目独立计数，从 1 开始。
    run(log.append_once("turn_9:x", _event(project_id=P2, source="turn_9:x")))
    other: LogPage = run(log.page(P2, 0, 50))
    assert [e.sequence for e in other.events] == [1]


def test_projects_are_isolated(make_log: BackendFactory) -> None:
    log = make_log()
    run(log.append_once("turn_1:started", _event(project_id=P1)))
    page: LogPage = run(log.page(P2, 0, 50))
    assert page.events == ()


def test_page_filters_by_task_and_type(make_log: BackendFactory) -> None:
    log = make_log()
    run(log.append_once("t1:started", _event(source="t1:started")))
    run(
        log.append_once(
            "t1:completed",
            _event(source="t1:completed", event_type="model_task.completed"),
        )
    )
    run(
        log.append_once(
            "t2:started",
            _event(source="t2:started", task_id="chat-conv_2", attempt_id="turn_2"),
        )
    )
    by_type: LogPage = run(log.page(P1, 0, 50, event_type="model_task.completed"))
    assert [e.source_event_id for e in by_type.events] == ["t1:completed"]
    by_task: LogPage = run(log.page(P1, 0, 50, task_id="chat-conv_2"))
    assert [e.source_event_id for e in by_task.events] == ["t2:started"]
    absent: LogPage = run(log.page(P2, 0, 50, task_id="chat-conv_2"))
    assert absent.events == ()


def test_page_pagination_and_cursor(make_log: BackendFactory) -> None:
    log = make_log()
    for i in range(5):
        run(log.append_once(f"s{i}", _event(source=f"s{i}")))
    first: LogPage = run(log.page(P1, 0, 2))
    assert [e.sequence for e in first.events] == [1, 2]
    assert first.next_cursor == 2
    second: LogPage = run(log.page(P1, first.next_cursor or 0, 2))
    assert [e.sequence for e in second.events] == [3, 4]
    third: LogPage = run(log.page(P1, second.next_cursor or 0, 2))
    assert [e.sequence for e in third.events] == [5]
    assert third.next_cursor is None


def test_page_preserves_event_payload(make_log: BackendFactory) -> None:
    log = make_log()
    event = replace(
        _event(),
        agent_id="agent_writer",
        call_id="call_1",
        version_refs=(VersionedRef(kind="chapter", id="ch_1", revision=3),),
    )
    run(log.append_once("turn_1:started", event))
    page: LogPage = run(log.page(P1, 0, 50))
    stored = page.events[0]
    assert stored.agent_id == "agent_writer"
    assert stored.call_id == "call_1"
    assert stored.version_refs[0].kind == "chapter"
    assert stored.version_refs[0].revision == 3


# -- subscribe 实时流 ----------------------------------------------------------------


@pytest.mark.anyio
async def test_subscribe_streams_new_events_after_cursor(make_log: BackendFactory) -> None:
    log = make_log()
    await log.append_once("turn_1:started", _event())

    async def produce() -> None:
        await asyncio.sleep(0.05)
        await log.append_once("turn_1:completed", _event(source="turn_1:completed"))
        await log.append_once("turn_2:started", _event(source="turn_2:started"))

    producer = asyncio.create_task(produce())

    async def consume() -> list[ExecutionEvent]:
        got: list[ExecutionEvent] = []
        async for event in log.subscribe(P1, 1):  # 游标 1：只收 started 之后的新事件
            got.append(event)
            if len(got) >= 2:
                break
        return got

    got = await asyncio.wait_for(consume(), timeout=2.0)
    await producer
    assert [e.source_event_id for e in got] == ["turn_1:completed", "turn_2:started"]
    # 断线补齐：从 0 分页无重复无遗漏。
    page: LogPage = await log.page(P1, 0, 50)
    assert [e.source_event_id for e in page.events] == [
        "turn_1:started",
        "turn_1:completed",
        "turn_2:started",
    ]


@pytest.mark.anyio
async def test_subscribe_skips_history_before_cursor(make_log: BackendFactory) -> None:
    log = make_log()
    await log.append_once("turn_1:started", _event())

    async def consume() -> list[ExecutionEvent]:
        got: list[ExecutionEvent] = []
        async for event in log.subscribe(P1, 1):
            got.append(event)
            break  # 游标之前的历史不回放；第一个事件即游标之后的
        return got

    async def produce() -> None:
        await asyncio.sleep(0.05)
        await log.append_once("turn_1:completed", _event(source="turn_1:completed"))

    producer = asyncio.create_task(produce())
    got = await asyncio.wait_for(consume(), timeout=2.0)
    await producer
    assert [e.source_event_id for e in got] == ["turn_1:completed"]


# -- 序列化往返（领域模型单元） --------------------------------------------------------


def test_execution_event_payload_round_trip() -> None:
    event = replace(_event(), version_refs=(VersionedRef(kind="chapter", id="ch_1", revision=3),))
    restored = ExecutionEvent.from_payload(event.to_payload())
    assert restored == event


def test_execution_event_payload_whitelist() -> None:
    payload = _event().to_payload()
    assert set(payload) == {
        "event_id",
        "project_id",
        "sequence",
        "task_id",
        "attempt_id",
        "agent_id",
        "call_id",
        "parent_call_id",
        "event_type",
        "occurred_at",
        "summary",
        "source_event_id",
        "version_refs",
    }
