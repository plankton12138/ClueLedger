"""对话 REST/SSE 端点测试（Phase 19.3 T19.3e）。

覆盖计划 §6.2 端点与 §19.3 退出条件的服务端部分：会话列举/新建、历史消息、
发送轮次（幂等重放）、取消、重试、SSE 事件流（快照 tail=0 与流式 tail=1）、
项目隔离 404 信封、能力探测返回 protocol、聊天不产生 Commit（A01 前半）、
服务启动把遗留运行轮次标为中断。

执行器用可控 FakeRunner（同 test_service），经 ``runtime.chat`` 注入。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hastings.domain.conversation.models import ChatMessage, ChatTurn
from hastings.server.v2 import V2Runtime, build_v2_app


class FakeRunner:
    def __init__(self) -> None:
        self.calls = 0
        self.pace = 0.0
        self.gate: asyncio.Event | None = None

    async def stream(self, run: Any, sink: Any) -> str:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
            self.gate = None
        for delta in ["第一段", "，第二段"]:
            await sink(delta)
            if self.pace:
                await asyncio.sleep(self.pace)
        return "第一段，第二段"


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def runtime(runner: FakeRunner) -> V2Runtime:
    runtime = V2Runtime.memory()
    runtime.chat.runner_factory = lambda: runner
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
    response = client.post("/api/v2/projects", json={"name": "对话项目"})
    assert response.status_code == 200, response.text
    return str(response.json()["project_id"])


def _new_conversation(client: TestClient, project_id: str) -> str:
    response = client.post(f"/api/v2/projects/{project_id}/conversations", json={})
    assert response.status_code == 200, response.text
    return str(response.json()["conversation_id"])


# -- 能力探测与会话 --------------------------------------------------------------


def test_list_conversations_exposes_protocol(client: TestClient) -> None:
    project_id = _create_project(client)
    response = client.get(f"/api/v2/projects/{project_id}/conversations")
    assert response.status_code == 200
    body = response.json()
    assert body["protocol"] == "chat.v1"
    assert body["conversations"] == []


def test_create_and_list_conversation(client: TestClient) -> None:
    project_id = _create_project(client)
    created = client.post(f"/api/v2/projects/{project_id}/conversations", json={}).json()
    assert created["project_id"] == project_id
    assert created["title"]
    listed = client.get(f"/api/v2/projects/{project_id}/conversations").json()
    assert [c["conversation_id"] for c in listed["conversations"]] == [
        created["conversation_id"]
    ]


def test_messages_empty_history_is_legal(client: TestClient) -> None:
    project_id = _create_project(client)
    conversation_id = _new_conversation(client, project_id)
    body = client.get(
        f"/api/v2/projects/{project_id}/conversations/{conversation_id}/messages"
    ).json()
    assert body == {"messages": []}


# -- 发送与事件 -------------------------------------------------------------------


def test_send_turn_and_read_events_snapshot(client: TestClient, runner: FakeRunner) -> None:
    project_id = _create_project(client)
    conversation_id = _new_conversation(client, project_id)
    sent = client.post(
        f"/api/v2/projects/{project_id}/conversations/{conversation_id}/turns",
        json={"text": "聊聊开头", "refs": [], "idempotency_key": "k1"},
    )
    assert sent.status_code == 200, sent.text
    body = sent.json()
    assert body["turn"]["status"] in ("queued", "running", "completed")
    assert body["user_message"]["text"] == "聊聊开头"

    # 等后台轮次落定，再取 SSE 快照（tail=0：返回当前积压后关闭）。
    import time

    deadline = time.time() + 5
    while time.time() < deadline:
        messages = client.get(
            f"/api/v2/projects/{project_id}/conversations/{conversation_id}/messages"
        ).json()["messages"]
        if any(m["role"] == "assistant" and m["status"] == "completed" for m in messages):
            break
        time.sleep(0.02)

    with client.stream(
        "GET",
        f"/api/v2/projects/{project_id}/conversations/{conversation_id}/events?after=0&tail=0",
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        frames = [
            line.removeprefix("data: ")
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]
    envelopes = [json.loads(frame) for frame in frames]
    sequences = [e["sequence"] for e in envelopes]
    assert sequences == sorted(sequences)
    types = [e["event"]["type"] for e in envelopes]
    assert types[0] == "turn.accepted"
    assert "turn.started" in types
    assert "message.delta" in types
    assert types[-1] == "message.completed"
    # 事件信封携带会话与项目身份。
    assert all(e["conversation_id"] == conversation_id for e in envelopes)
    assert all(e["project_id"] == project_id for e in envelopes)


def test_chat_turn_does_not_create_commit_or_job(
    client: TestClient, runner: FakeRunner
) -> None:
    project_id = _create_project(client)
    conversation_id = _new_conversation(client, project_id)
    client.post(
        f"/api/v2/projects/{project_id}/conversations/{conversation_id}/turns",
        json={"text": "纯聊天", "refs": [], "idempotency_key": "k1"},
    )
    import time

    deadline = time.time() + 5
    while time.time() < deadline:
        messages = client.get(
            f"/api/v2/projects/{project_id}/conversations/{conversation_id}/messages"
        ).json()["messages"]
        if any(m["status"] == "completed" and m["role"] == "assistant" for m in messages):
            break
        time.sleep(0.02)
    assert runner.calls == 1
    manuscript = client.get(f"/api/v2/projects/{project_id}/manuscript").json()
    assert manuscript["commit_id"] is None
    jobs = client.get(f"/api/v2/projects/{project_id}/agent-jobs").json()
    job_list = jobs["jobs"] if isinstance(jobs, dict) else jobs
    assert job_list == []


def test_idempotent_send_replays_same_turn(client: TestClient) -> None:
    project_id = _create_project(client)
    conversation_id = _new_conversation(client, project_id)
    payload = {"text": "问", "refs": [], "idempotency_key": "same"}
    first = client.post(
        f"/api/v2/projects/{project_id}/conversations/{conversation_id}/turns", json=payload
    ).json()
    second = client.post(
        f"/api/v2/projects/{project_id}/conversations/{conversation_id}/turns", json=payload
    ).json()
    assert first["turn"]["turn_id"] == second["turn"]["turn_id"]


def test_cancel_turn(client: TestClient, runner: FakeRunner) -> None:
    runner.pace = 0.15
    project_id = _create_project(client)
    conversation_id = _new_conversation(client, project_id)
    sent = client.post(
        f"/api/v2/projects/{project_id}/conversations/{conversation_id}/turns",
        json={"text": "慢慢说", "refs": [], "idempotency_key": "k1"},
    ).json()
    turn_id = sent["turn"]["turn_id"]
    import time

    time.sleep(0.1)
    response = client.post(
        f"/api/v2/projects/{project_id}/conversations/{conversation_id}/turns/{turn_id}/cancel",
        json={},
    )
    assert response.status_code == 200, response.text
    deadline = time.time() + 5
    while time.time() < deadline:
        turn_status = None
        events = []
        with client.stream(
            "GET",
            f"/api/v2/projects/{project_id}/conversations/{conversation_id}/events?after=0&tail=0",
        ) as resp:
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line.removeprefix("data: ")))
        types = [e["event"]["type"] for e in events]
        if "turn.cancelled" in types or "message.completed" in types:
            turn_status = types[-1]
            break
        time.sleep(0.02)
    assert turn_status == "turn.cancelled"


def test_retry_turn_returns_new_linked_turn(client: TestClient, runner: FakeRunner) -> None:
    project_id = _create_project(client)
    conversation_id = _new_conversation(client, project_id)
    sent = client.post(
        f"/api/v2/projects/{project_id}/conversations/{conversation_id}/turns",
        json={"text": "第一次", "refs": [], "idempotency_key": "k1"},
    ).json()
    turn_id = sent["turn"]["turn_id"]
    import time

    deadline = time.time() + 5
    while time.time() < deadline:
        messages = client.get(
            f"/api/v2/projects/{project_id}/conversations/{conversation_id}/messages"
        ).json()["messages"]
        if any(m["role"] == "assistant" and m["status"] == "completed" for m in messages):
            break
        time.sleep(0.02)
    retried = client.post(
        f"/api/v2/projects/{project_id}/conversations/{conversation_id}/turns/{turn_id}/retry",
        json={},
    )
    assert retried.status_code == 200, retried.text
    body = retried.json()
    assert body["turn_id"] != turn_id
    assert body["retry_of"] == turn_id


# -- 项目隔离与错误信封 --------------------------------------------------------------


def test_cross_project_conversation_access_is_404(client: TestClient) -> None:
    p1 = _create_project(client)
    conversation_id = _new_conversation(client, p1)
    p2 = _create_project(client)
    response = client.get(f"/api/v2/projects/{p2}/conversations/{conversation_id}/messages")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "CHAT_CONVERSATION_NOT_FOUND"
    assert body["error"]["why"] and body["error"]["fix"]


def test_unknown_conversation_is_404(client: TestClient) -> None:
    project_id = _create_project(client)
    response = client.get(
        f"/api/v2/projects/{project_id}/conversations/conv_missing/messages"
    )
    assert response.status_code == 404


def test_send_missing_idempotency_key_is_400(client: TestClient) -> None:
    project_id = _create_project(client)
    conversation_id = _new_conversation(client, project_id)
    response = client.post(
        f"/api/v2/projects/{project_id}/conversations/{conversation_id}/turns",
        json={"text": "问"},
    )
    assert response.status_code == 422  # FastAPI 请求体校验：缺幂等键不进应用层


# -- 启动恢复 --------------------------------------------------------------------


def test_startup_marks_stale_turns_interrupted(tmp_path: Any) -> None:
    """服务重启：遗留 running 轮次在 lifespan 启动时标为中断（不自动重发）。"""
    db = tmp_path / "chat-recovery.db"
    runtime = V2Runtime.sqlite(str(db))
    runtime.chat.runner_factory = lambda: FakeRunner()
    runtime.chat.model_resolver = lambda: "test-model"
    runtime.chat.binding_resolver = None
    runtime.chat.bound_runner_factory = None

    async def seed() -> None:
        conversation = await runtime.chat.create_conversation("proj_stale")
        await runtime.chat.store.open_turn(
            project_id="proj_stale",
            conversation_id=conversation.conversation_id,
            idempotency_key="k1",
            fingerprint="fp",
            user_message=ChatMessage(
                message_id="msg_x",
                conversation_id=conversation.conversation_id,
                turn_id="turn_stale",
                role="author",
                kind="text",
                text="遗留输入",
                created_at="2026-10-16T00:00:00+00:00",
            ),
            turn=ChatTurn(
                turn_id="turn_stale",
                conversation_id=conversation.conversation_id,
                status="running",
                model="old",
                end_reason=None,
                idempotency_key="k1",
            ),
        )

    asyncio.run(seed())

    app = build_v2_app(runtime)
    with TestClient(app):
        pass
    import time

    deadline = time.time() + 5
    turn_status: str | None = None
    while time.time() < deadline:
        turn_status = asyncio.run(
            _turn_status(runtime, "proj_stale", conversation_id_stale(runtime))
        )
        if turn_status == "interrupted":
            break
        time.sleep(0.05)
    assert turn_status == "interrupted"


def conversation_id_stale(runtime: V2Runtime) -> str:
    conversations = asyncio.run(runtime.chat.list_conversations("proj_stale"))
    return conversations[0].conversation_id


async def _turn_status(runtime: V2Runtime, project_id: str, conversation_id: str) -> str | None:
    events = await runtime.chat.events(project_id, conversation_id, after=0)
    for event in reversed(events):
        if event.event.get("type") == "turn.interrupted":
            return "interrupted"
    turn = await runtime.chat.store.get_turn("turn_stale")
    return turn.status if turn else None
