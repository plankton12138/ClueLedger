"""执行日志断线补齐、任务迟到与多窗口压力（V03-8.4）。

验收口径（计划 §6.2/§6.3/§9.6、执行指导 §12.2）：

- **断线补齐**：历史分页与实时流共用同一作品游标，重连后从游标继续——
  不多一条、不漏一条；心跳是注释帧，不伪装执行进度。
- **任务迟到**：取消/接管后的旧执行不得发布结果（generation 栅栏）；
  新接管的结果照常落地，状态不跳变。
- **压力**：多作品/多窗口并发下不串数据；分页大小受性能目标封顶；
  图谱首屏节点/边上限不破。

取证口径：延迟只在 ``full`` 规模下按 §9.6 目标断言；默认 ``reduced`` 规模
测正确性与并发不变量，实测数字随构建笔记记录（不拿默认规模冒充全量结论）。

实现侧说明（本文件修正了草稿的三处假设，均有依据）：

1. ``event_id`` 按计划 §M13「每条日志带全局唯一ID」生成——SQLite 后端以
   全局唯一约束去重，跨作品同 ID 的合成事件会被误去重（内存后端不管
   ``event_id``，双后端只有在此前提下同构）。
2. 客户端游标口径沿用 V03-1.6 已交付契约：``next_cursor=None`` 即取尽
   （契约测试 ``test_page_pagination_and_cursor`` 锁定）；断线重连的
   正确做法是每页把游标前进到最后收到的事件，收尾再取一页必须为空。
3. 项目创建不写执行事件（§M13 事件清单只有任务/Agent/模型/读取/等待/
   失败/停止/提交类）；SSE 探针直接向 ``runtime.execution_log`` 补种。
   另外，本机 TestClient 会把响应整体缓冲完才返回（starlette 1.7），
   无限流（``tail>=1``）出不了 ``__enter__``——持续流与心跳在帧生成器
   ``event_stream`` 层直接测，HTTP 层只测有限流（``tail=0``）。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hastings.application.capabilities import PERFORMANCE_TARGETS
from hastings.application.ports.execution_log import LogPage
from hastings.domain.execution_log import ExecutionEvent
from hastings.infrastructure.execution_log import MemoryExecutionLog, SqliteExecutionLog
from hastings.interfaces.api import execution_log as execution_log_api
from hastings.interfaces.api.execution_log import event_stream
from hastings.server.v2 import V2Runtime, build_v2_app

SCALE = os.environ.get("HASTINGS_LOAD_SCALE", "reduced")
FULL = SCALE == "full"


def _event(project_id: str, source: str, seq_source: int = 0) -> ExecutionEvent:
    # event_id 全局唯一（§M13）：带 project_id，避免跨作品撞 ID 被去重。
    return ExecutionEvent(
        event_id=f"evt_{project_id}_{seq_source}_{source}",
        project_id=project_id,
        sequence=0,
        task_id="chat-conv_1",
        attempt_id="turn_1",
        agent_id=None,
        call_id=None,
        parent_call_id=None,
        event_type="model_task.progress",
        occurred_at=datetime.now(UTC).isoformat(),
        summary=f"事件 {source}",
        source_event_id=source,
        version_refs=(),
    )


async def _seed(log: Any, project_id: str, count: int) -> None:
    for index in range(count):
        await log.append_once(f"{project_id}:s{index}", _event(project_id, f"s{index}", index))


def _make_log(backend: str, tmp_path: Any, name: str) -> Any:
    if backend == "memory":
        return MemoryExecutionLog()
    return SqliteExecutionLog(str(tmp_path / name))


# --------------------------------------------------------------------------
# 断线补齐：分页游标（双后端同构）
# --------------------------------------------------------------------------


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
@pytest.mark.anyio
async def test_disconnect_and_resume_from_cursor_has_no_gap_or_duplicate(
    backend: str, tmp_path: Any
) -> None:
    log = _make_log(backend, tmp_path, "log.db")
    await _seed(log, "prj_1", 10)

    seen: list[int] = []
    cursor = 0
    # 前两次断线：每次从上次游标续取一页，游标前进到最后收到的事件
    for _ in range(2):
        page: LogPage = await log.page("prj_1", cursor, 4)
        seen.extend(event.sequence for event in page.events)
        cursor = page.events[-1].sequence
    assert seen == [1, 2, 3, 4, 5, 6, 7, 8]

    # 断线期间服务端又写了 3 条（seq 11–13）：补齐必须覆盖它们
    for index in range(10, 13):
        await log.append_once(f"prj_1:s{index}", _event("prj_1", f"s{index}", index))

    # 第三次重连：从游标 8 继续，逐页排空
    while True:
        page = await log.page("prj_1", cursor, 4)
        seen.extend(event.sequence for event in page.events)
        if page.events:
            cursor = page.events[-1].sequence
        if page.next_cursor is None:
            break

    assert seen == list(range(1, 14)), "断线补齐后必须无重复、无遗漏"
    # 收尾再从同一游标取一次：必须为空（重取不产生漂移）
    tail: LogPage = await log.page("prj_1", cursor, 50)
    assert tail.events == ()
    assert tail.next_cursor is None


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
@pytest.mark.anyio
async def test_replay_same_cursor_yields_same_events(backend: str, tmp_path: Any) -> None:
    """同一游标重复请求（弱网重试）必须返回相同事件，不产生漂移。"""
    log = _make_log(backend, tmp_path, "log2.db")
    await _seed(log, "prj_1", 6)
    first: LogPage = await log.page("prj_1", 3, 2)
    again: LogPage = await log.page("prj_1", 3, 2)
    assert [e.source_event_id for e in first.events] == [e.source_event_id for e in again.events]
    assert first.next_cursor == again.next_cursor


# --------------------------------------------------------------------------
# 实时流（HTTP 有限流）：从游标继续、tail=0 排空积压后关闭
# --------------------------------------------------------------------------


def _sse_frames(response: Any) -> list[str]:
    """同步消费 SSE（本仓库既有手法）：只收 data 帧，注释帧另行收集。"""
    return [
        line.removeprefix("data: ") for line in response.iter_lines() if line.startswith("data: ")
    ]


def test_sse_tail_zero_drains_full_backlog() -> None:
    """tail=0 = 排空当前积压后关闭：积压超过 page_size 也不截断。"""
    runtime = V2Runtime.memory()
    cap = int(PERFORMANCE_TARGETS["execution_log"]["page_size"])
    backlog = cap + 50
    with TestClient(build_v2_app(runtime)) as http:
        pid = http.post("/api/v2/projects", json={"name": "积压"}).json()["project_id"]
        asyncio.run(_seed(runtime.execution_log, pid, backlog))
        with http.stream(
            "GET",
            f"/api/v2/projects/{pid}/execution-events/stream",
            params={"after_sequence": 0, "tail": 0},
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            frames = _sse_frames(response)
    # 流自行结束（iter_lines 走完），且一条不少：超过一页的积压被连续取页排空
    assert len(frames) == backlog
    assert [json.loads(frame)["sequence"] for frame in frames] == list(range(1, backlog + 1))


def test_sse_resume_from_cursor_has_no_duplicate() -> None:
    """从游标续收：只收游标之后的事件；重连再取不重复。"""
    runtime = V2Runtime.memory()
    with TestClient(build_v2_app(runtime)) as http:
        pid = http.post("/api/v2/projects", json={"name": "续收"}).json()["project_id"]
        asyncio.run(_seed(runtime.execution_log, pid, 2))
        page = http.get(f"/api/v2/projects/{pid}/execution-events", params={"limit": 100})
        events = page.json()["events"]
        cursor = page.json()["next_cursor"] or events[-1]["sequence"]
        # 游标之后又来一条（断线期间的积压）
        asyncio.run(runtime.execution_log.append_once(f"{pid}:s2", _event(pid, "s2", 2)))
        with http.stream(
            "GET",
            f"/api/v2/projects/{pid}/execution-events/stream",
            params={"after_sequence": cursor, "tail": 0},
        ) as response:
            frames = _sse_frames(response)
        received = [json.loads(frame) for frame in frames]
        assert [e["sequence"] for e in received] == [3], "只应收到游标之后的新事件"
        # 重连再取：同一游标不重复已收事件
        again = http.get(
            f"/api/v2/projects/{pid}/execution-events",
            params={"after_sequence": cursor, "limit": 100},
        ).json()["events"]
        assert [e["sequence"] for e in again] == [3]


# --------------------------------------------------------------------------
# 持续流与心跳（帧生成器层）：心跳是注释帧，不冒充执行进度
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sse_heartbeat_is_comment_not_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """心跳只证明连接存活：注释帧（: 开头），绝不冒充执行进度。"""
    monkeypatch.setattr(execution_log_api, "_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(execution_log_api, "_HEARTBEAT_EVERY", 2)

    runtime = V2Runtime.memory()
    await _seed(runtime.execution_log, "prj_b", 1)

    frames: list[str] = []
    stream: AsyncIterator[str] = event_stream(runtime, "prj_b", 0, 1)
    try:
        async for frame in stream:
            frames.append(frame)
            if any(frame.startswith(":") for frame in frames):
                break
    finally:
        await stream.aclose()

    comments = [frame for frame in frames if frame.startswith(":")]
    data = [frame for frame in frames if frame.startswith("data: ")]
    assert comments, "应看到心跳注释帧"
    assert all(not frame.startswith("data: ") for frame in comments), "心跳不得是 data 帧"
    # 心跳之外照常推送游标之后的事件（先补历史，再证明存活）
    assert data, "心跳不能取代事件推送"


@pytest.mark.anyio
async def test_page_limit_capped_by_performance_target() -> None:
    """limit 再大也不越过 §9.6 的 page_size：分页大小是承诺，不是请求方说了算。"""
    runtime = V2Runtime.memory()
    await _seed(runtime.execution_log, "prj_c", 5)
    client = build_v2_app(runtime)

    cap = int(PERFORMANCE_TARGETS["execution_log"]["page_size"])
    with TestClient(client) as http:
        pid = http.post("/api/v2/projects", json={"name": "上限"}).json()["project_id"]
        response = http.get(f"/api/v2/projects/{pid}/execution-events", params={"limit": 100000})
        assert response.status_code == 200
        assert len(response.json()["events"]) <= cap


@pytest.mark.anyio
async def test_execution_events_require_existing_project() -> None:
    """归属校验：不存在的项目返回 404 信封，而不是空流。"""
    runtime = V2Runtime.memory()
    client = build_v2_app(runtime)
    with TestClient(client) as http:
        response = http.get("/api/v2/projects/prj_missing/execution-events")
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "REPO_PROJECT_NOT_FOUND"


# --------------------------------------------------------------------------
# 任务迟到：旧代结果不得落地
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_late_result_from_stale_generation_is_discarded(tmp_path: Any) -> None:
    """接管后的旧执行不得发布结果；新代结果照常落地。"""
    runtime = V2Runtime.sqlite(str(tmp_path / "jobs.db"))
    job_id = await runtime.jobs.create("draft", {"project_id": "prj_late"})

    first = await runtime.jobs.claim(job_id, "worker-a", now=datetime.now(UTC), ttl=60)
    generation = int(first["generation"])
    await runtime.jobs.cancel(job_id)

    # 迟到：旧 worker 仍以为自己在跑
    for attempt in ("checkpoint", "renew", "append_event", "finish"):
        with pytest.raises(ValueError):
            if attempt == "checkpoint":
                await runtime.jobs.checkpoint(job_id, generation, {"progress": "旧代"})
            elif attempt == "renew":
                await runtime.jobs.renew(job_id, generation, datetime.now(UTC))
            elif attempt == "append_event":
                await runtime.jobs.append_event(
                    job_id, generation, {"type": "agent.output.proposed"}
                )
            else:
                await runtime.jobs.finish(job_id, generation, "completed", {"text": "旧代结果"})

    state = await runtime.jobs.get(job_id)
    assert state is not None and state["state"] == "cancelled"
    assert state.get("checkpoint") is None, "旧代 checkpoint 不得落地"

    # 重新排队并被接管：新 Job 新一代，可以正常发布（被取消的旧 Job 不复活）
    again = await runtime.jobs.create("draft", {"project_id": "prj_late"})
    second = await runtime.jobs.claim(again, "worker-b", now=datetime.now(UTC), ttl=60)
    assert int(second["generation"]) >= 1
    await runtime.jobs.finish(again, int(second["generation"]), "completed", {"text": "新代结果"})
    final = await runtime.jobs.get(again)
    assert final is not None and final["state"] == "completed"


# --------------------------------------------------------------------------
# 多作品 / 多窗口压力：不串数据、订阅互不干扰
# --------------------------------------------------------------------------


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
@pytest.mark.anyio
async def test_multi_project_concurrent_paging_stays_isolated(backend: str, tmp_path: Any) -> None:
    log = _make_log(backend, tmp_path, "multi.db")
    projects = [f"prj_m{i}" for i in range(6)]
    per_project = 40
    for pid in projects:
        await _seed(log, pid, per_project)

    async def drain(pid: str) -> list[int]:
        cursor = 0
        seen: list[int] = []
        while True:
            page: LogPage = await log.page(pid, cursor, 16)
            seen.extend(event.sequence for event in page.events)
            if not page.events:
                break
            cursor = page.events[-1].sequence
            if page.next_cursor is None:
                break
        return seen

    results = await asyncio.gather(*(drain(pid) for pid in projects))
    for pid, seen in zip(projects, results, strict=True):
        assert seen == list(range(1, per_project + 1)), f"{pid} 的事件在并发下被污染"


@pytest.mark.anyio
async def test_concurrent_subscribers_each_receive_live_events() -> None:
    """多窗口（同一作品多个订阅者）都收到游标之后的新事件，互不干扰。"""
    log = MemoryExecutionLog(poll_interval=0.01)
    await _seed(log, "prj_w", 2)

    async def produce() -> None:
        await asyncio.sleep(0.05)
        for index in range(2, 5):
            await log.append_once(f"prj_w:s{index}", _event("prj_w", f"s{index}", index))

    async def consume() -> list[str]:
        got: list[str] = []
        async for event in log.subscribe("prj_w", 2):  # 游标 2：只收之后的新事件
            got.append(event.source_event_id)
            if len(got) >= 3:
                break
        return got

    producer = asyncio.create_task(produce())
    first, second = await asyncio.wait_for(asyncio.gather(consume(), consume()), timeout=5.0)
    await producer
    assert first == ["s2", "s3", "s4"]
    assert second == first, "两个窗口收到的事件集不一致"


@pytest.mark.anyio
async def test_execution_log_latency_evidence_at_scale() -> None:
    """延迟取证（§9.6）：full 规模按目标断言，reduced 规模只记录不冒充。"""
    projects = 12 if FULL else 3
    per_project = 10_000 // projects if FULL else 400
    log = SqliteExecutionLog(":memory:")
    for index in range(projects):
        await _seed(log, f"prj_s{index}", per_project)

    samples: list[float] = []
    for round_index in range(20):
        pid = f"prj_s{round_index % projects}"
        started = time.perf_counter()
        page: LogPage = await log.page(pid, 0, 200)
        samples.append((time.perf_counter() - started) * 1000)
        assert len(page.events) <= int(PERFORMANCE_TARGETS["execution_log"]["page_size"])
    samples.sort()
    p95 = samples[int(len(samples) * 0.95) - 1]
    # full 规模下 10k 事件的分页 p95 应远低于 1s；reduced 规模只取证不断言
    if FULL:
        assert p95 < 1000, f"分页 p95 {p95:.1f}ms 超过 1s 目标"
    else:
        assert p95 < 5000, f"reduced 规模分页 p95 {p95:.1f}ms 异常（可能是环境问题）"
