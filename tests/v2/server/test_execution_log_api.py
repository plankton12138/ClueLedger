"""执行日志 REST/SSE 端点测试（V03-1.6）。

验收合同（计划 L907）：真实普通对话调用有 task/attempt 标识；无过程支持如实
显示（普通对话 runner 不公开推理过程，只有生命周期事件，事件仍真实存在）。
执行器用可控 FakeRunner（同 test_chat_api），经 ``runtime.chat`` 注入。
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hastings.application.errors import ViolationError
from hastings.server.v2 import V2Runtime, build_v2_app


class FakeRunner:
    def __init__(self) -> None:
        self.calls = 0
        self.pace = 0.0

    async def stream(self, run: Any, sink: Any) -> str:
        self.calls += 1
        for delta in ["第一段", "，第二段"]:
            await sink(delta)
            if self.pace:
                await asyncio_sleep()
        return "第一段，第二段"


class FailingRunner:
    async def stream(self, run: Any, sink: Any) -> str:
        raise ViolationError(
            code="CHAT_PROVIDER_UNAVAILABLE",
            where="chat.runner",
            why="模型端点不可达",
            fix="确认网络与端点地址后重试",
        )


def asyncio_sleep() -> Any:
    import asyncio

    return asyncio.sleep(0.15)


@pytest.fixture
def runtime() -> V2Runtime:
    runtime = V2Runtime.memory()
    runtime.chat.model_resolver = lambda: "test-model"
    runtime.chat.binding_resolver = None
    runtime.chat.bound_runner_factory = None
    return runtime


@pytest.fixture
def client(runtime: V2Runtime) -> Iterator[TestClient]:
    app: FastAPI = build_v2_app(runtime)
    with TestClient(app) as test_client:
        yield test_client


def _create_project(client: TestClient) -> str:
    return str(client.post("/api/v2/projects", json={"name": "日志项目"}).json()["project_id"])


def _new_conversation(client: TestClient, project_id: str) -> str:
    return str(
        client.post(f"/api/v2/projects/{project_id}/conversations", json={}).json()[
            "conversation_id"
        ]
    )


def _send_turn(client: TestClient, project_id: str, conversation_id: str, key: str) -> str:
    sent = client.post(
        f"/api/v2/projects/{project_id}/conversations/{conversation_id}/turns",
        json={"text": "聊聊开头", "refs": [], "idempotency_key": key},
    )
    assert sent.status_code == 200, sent.text
    return str(sent.json()["turn"]["turn_id"])


def _events(client: TestClient, project_id: str, **params: Any) -> dict[str, Any]:
    return client.get(f"/api/v2/projects/{project_id}/execution-events", params=params).json()


def _wait_for_events(
    client: TestClient, project_id: str, event_type: str, timeout: float = 5.0
) -> list[dict[str, Any]]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = [
            e
            for e in _events(client, project_id, limit=200)["events"]
            if e["event_type"] == event_type
        ]
        if found:
            return found
        time.sleep(0.02)
    return []


# -- 生命周期事件投影 ----------------------------------------------------------------


def test_turn_projects_lifecycle_events_with_task_attempt(
    client: TestClient, runtime: V2Runtime
) -> None:
    runtime.chat.runner_factory = lambda: FakeRunner()
    project_id = _create_project(client)
    conversation_id = _new_conversation(client, project_id)
    turn_id = _send_turn(client, project_id, conversation_id, "k1")

    started = _wait_for_events(client, project_id, "model_task.started")
    completed = _wait_for_events(client, project_id, "model_task.completed")
    assert started and completed
    first = started[0]
    # 验收：真实普通对话调用有 task/attempt 标识。
    assert first["task_id"] == f"chat-{conversation_id}"
    assert first["attempt_id"] == turn_id
    assert first["project_id"] == project_id
    assert first["source_event_id"] == f"{turn_id}:started"
    assert "test-model" in first["summary"]
    assert first["event_id"]
    assert first["occurred_at"]
    # 事件链因果完整：started 在 completed 之前，sequence 作品内单调。
    sequences = [e["sequence"] for e in _events(client, project_id, limit=200)["events"]]
    assert sequences == sorted(sequences)


def test_no_process_support_still_records_real_events(
    client: TestClient, runtime: V2Runtime
) -> None:
    """普通对话无公开过程：只有生命周期事件，但事件真实存在（如实显示）。"""
    runtime.chat.runner_factory = lambda: FakeRunner()
    project_id = _create_project(client)
    conversation_id = _new_conversation(client, project_id)
    _send_turn(client, project_id, conversation_id, "k1")
    _wait_for_events(client, project_id, "model_task.completed")

    events = _events(client, project_id, limit=200)["events"]
    types = {e["event_type"] for e in events}
    assert types == {"model_task.started", "model_task.completed"}
    # 全部事件都属于该对话任务通道，无伪造的过程事件。
    assert all(e["task_id"] == f"chat-{conversation_id}" for e in events)


def test_failed_turn_records_error_code(client: TestClient, runtime: V2Runtime) -> None:
    runtime.chat.runner_factory = lambda: FailingRunner()
    project_id = _create_project(client)
    conversation_id = _new_conversation(client, project_id)
    _send_turn(client, project_id, conversation_id, "k1")

    failed = _wait_for_events(client, project_id, "model_task.failed")
    assert failed
    assert "CHAT_PROVIDER_UNAVAILABLE" in failed[0]["summary"]


def test_stop_and_retry_attempts_are_separate(client: TestClient, runtime: V2Runtime) -> None:
    runner = FakeRunner()
    runner.pace = 0.2
    runtime.chat.runner_factory = lambda: runner
    project_id = _create_project(client)
    conversation_id = _new_conversation(client, project_id)
    first_turn = _send_turn(client, project_id, conversation_id, "k1")
    cancelled = client.post(
        f"/api/v2/projects/{project_id}/conversations/{conversation_id}/turns/{first_turn}/cancel",
        json={},
    )
    assert cancelled.status_code == 200, cancelled.text
    stopped = _wait_for_events(client, project_id, "model_task.stopped")
    assert stopped
    assert stopped[0]["attempt_id"] == first_turn

    # 重试产生新尝试：不同 attempt_id，互不混淆。
    retried = client.post(
        f"/api/v2/projects/{project_id}/conversations/{conversation_id}/turns/{first_turn}/retry"
    )
    assert retried.status_code == 200, retried.text
    second_turn = str(retried.json()["turn_id"])
    assert second_turn != first_turn
    _wait_for_events(client, project_id, "model_task.completed")

    started = _wait_for_events(client, project_id, "model_task.started")
    attempts = {e["attempt_id"] for e in started}
    assert {first_turn, second_turn} <= attempts
    assert all(e["task_id"] == f"chat-{conversation_id}" for e in started)


# -- 查询与通道 -------------------------------------------------------------------


def test_filter_by_task_and_type(client: TestClient, runtime: V2Runtime) -> None:
    runtime.chat.runner_factory = lambda: FakeRunner()
    project_id = _create_project(client)
    conv_a = _new_conversation(client, project_id)
    conv_b = _new_conversation(client, project_id)
    _send_turn(client, project_id, conv_a, "ka")
    _send_turn(client, project_id, conv_b, "kb")
    _wait_for_events(client, project_id, "model_task.completed")
    deadline = time.time() + 5
    while time.time() < deadline:
        if len(_wait_for_events(client, project_id, "model_task.completed")) >= 2:
            break
        time.sleep(0.02)

    by_task = _events(client, project_id, task_id=f"chat-{conv_a}")["events"]
    assert by_task
    assert all(e["task_id"] == f"chat-{conv_a}" for e in by_task)
    by_type = _events(client, project_id, type="model_task.completed")["events"]
    assert len(by_type) == 2
    assert all(e["event_type"] == "model_task.completed" for e in by_type)


def test_pagination_after_sequence_has_no_gap_or_duplicate(
    client: TestClient, runtime: V2Runtime
) -> None:
    runtime.chat.runner_factory = lambda: FakeRunner()
    project_id = _create_project(client)
    conversation_id = _new_conversation(client, project_id)
    _send_turn(client, project_id, conversation_id, "k1")
    _wait_for_events(client, project_id, "model_task.completed")

    full = _events(client, project_id, limit=200)["events"]
    first_page = _events(client, project_id, after_sequence=0, limit=1)
    assert len(first_page["events"]) == 1
    assert first_page["next_cursor"] == first_page["events"][0]["sequence"]
    rest = _events(client, project_id, after_sequence=first_page["next_cursor"], limit=200)[
        "events"
    ]
    # 断线补齐：分页拼接与全量一致，无重复无遗漏。
    assert [e["event_id"] for e in first_page["events"] + rest] == [e["event_id"] for e in full]


def test_stream_snapshot_tail_zero(client: TestClient, runtime: V2Runtime) -> None:
    runtime.chat.runner_factory = lambda: FakeRunner()
    project_id = _create_project(client)
    conversation_id = _new_conversation(client, project_id)
    _send_turn(client, project_id, conversation_id, "k1")
    _wait_for_events(client, project_id, "model_task.completed")

    with client.stream(
        "GET", f"/api/v2/projects/{project_id}/execution-events/stream?after_sequence=0&tail=0"
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        frames = [
            line.removeprefix("data: ")
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]
    events = [json.loads(frame) for frame in frames]
    assert {e["event_type"] for e in events} >= {"model_task.started", "model_task.completed"}


def test_unknown_project_returns_404_envelope(client: TestClient) -> None:
    response = client.get("/api/v2/projects/prj_missing/execution-events")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "REPO_PROJECT_NOT_FOUND"
    assert error["where"] and error["why"] and error["fix"]
    stream = client.get("/api/v2/projects/prj_missing/execution-events/stream")
    assert stream.status_code == 404
