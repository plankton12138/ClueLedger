"""任务准备 REST 端点测试（Phase 19.4 T19.4c）。

覆盖 §6.2 任务准备端点：POST /task-drafts（显式/从消息）、GET、PATCH（修订
CAS）、/abandon、/start（缺失项拦截、过期基础版本拦截、幂等重放）；发送轮次
的引用项目校验（引用不能跨项目/不存在）。
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hastings.server.v2 import V2Runtime, build_v2_app


class FakeRunner:
    async def stream(self, run: Any, sink: Any) -> str:
        for delta in ["你好", "，作者"]:
            await sink(delta)
        return "你好，作者"


class RecordingStarter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def __call__(self, project_id: str, draft: Any, key: str) -> str:
        self.calls.append((project_id, draft.draft_id, key))
        return f"job_{len(self.calls)}"


@pytest.fixture
def starter() -> RecordingStarter:
    return RecordingStarter()


@pytest.fixture
def runtime(starter: RecordingStarter) -> V2Runtime:
    runtime = V2Runtime.memory()
    runtime.chat.runner_factory = lambda: FakeRunner()
    runtime.chat.model_resolver = lambda: "test-model"
    runtime.chat.binding_resolver = None
    runtime.chat.bound_runner_factory = None
    runtime.task_prep._starter = starter
    return runtime


@pytest.fixture
def client(runtime: V2Runtime) -> Iterator[TestClient]:
    app: FastAPI = build_v2_app(runtime)
    with TestClient(app) as test_client:
        yield test_client


def _create_project(client: TestClient, name: str = "任务项目") -> str:
    return str(client.post("/api/v2/projects", json={"name": name}).json()["project_id"])


def _new_conversation(client: TestClient, project_id: str) -> str:
    return str(
        client.post(
            f"/api/v2/projects/{project_id}/conversations", json={}
        ).json()["conversation_id"]
    )


# -- 创建与查看 -------------------------------------------------------------------


def test_create_draft_explicit_and_get(client: TestClient) -> None:
    project_id = _create_project(client)
    conversation_id = _new_conversation(client, project_id)
    response = client.post(
        f"/api/v2/projects/{project_id}/task-drafts",
        json={"conversation_id": conversation_id, "task_kind": "plan", "instruction": "先规划"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["draft"]["task_kind"] == "plan"
    assert body["draft"]["revision"] == 1
    assert body["draft"]["missing"] == []  # model_resolver 返回已配置；plan 无前置
    assert body["message_id"]
    fetched = client.get(f"/api/v2/projects/{project_id}/task-drafts/{body['draft']['draft_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["draft"]["draft_id"] == body["draft"]["draft_id"]


def test_create_draft_from_message_copies_refs(client: TestClient) -> None:
    project_id = _create_project(client)
    conversation_id = _new_conversation(client, project_id)
    imported = client.post(
        f"/api/v2/projects/{project_id}/chapters/import",
        json={"idempotency_key": "imp1", "title": "第一章", "text": "第一段。"},
    ).json()
    chapter_id = imported["chapter_id"]
    sent = client.post(
        f"/api/v2/projects/{project_id}/conversations/{conversation_id}/turns",
        json={
            "text": "结合第一章讨论",
            "refs": [{
                "ref_id": "r1", "kind": "chapter", "target_id": chapter_id,
                "title": "第一章", "version": "", "saved": True,
            }],
            "idempotency_key": "k1",
        },
    )
    assert sent.status_code == 200, sent.text
    source_message_id = sent.json()["user_message"]["message_id"]
    created = client.post(
        f"/api/v2/projects/{project_id}/task-drafts",
        json={
            "conversation_id": conversation_id,
            "task_kind": "chapter_draft",
            "source_message_id": source_message_id,
        },
    ).json()
    assert created["draft"]["source_message_id"] == source_message_id
    assert created["draft"]["refs"][0]["target_id"] == chapter_id


def test_send_turn_rejects_unknown_chapter_ref(client: TestClient) -> None:
    project_id = _create_project(client)
    conversation_id = _new_conversation(client, project_id)
    response = client.post(
        f"/api/v2/projects/{project_id}/conversations/{conversation_id}/turns",
        json={
            "text": "聊聊",
            "refs": [{
                "ref_id": "r1", "kind": "chapter", "target_id": "art_missing",
                "title": "不存在", "version": "v1", "saved": True,
            }],
            "idempotency_key": "k1",
        },
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "CHAT_REF_NOT_FOUND"


# -- 调整与放弃 -------------------------------------------------------------------


def test_adjust_with_stale_revision_is_409(client: TestClient) -> None:
    project_id = _create_project(client)
    conversation_id = _new_conversation(client, project_id)
    created = client.post(
        f"/api/v2/projects/{project_id}/task-drafts",
        json={"conversation_id": conversation_id, "task_kind": "plan"},
    ).json()
    draft_id = created["draft"]["draft_id"]
    ok = client.patch(
        f"/api/v2/projects/{project_id}/task-drafts/{draft_id}",
        json={"expected_revision": 1, "instruction": "改成规划节拍"},
    )
    assert ok.status_code == 200
    assert ok.json()["draft"]["revision"] == 2
    conflict = client.patch(
        f"/api/v2/projects/{project_id}/task-drafts/{draft_id}",
        json={"expected_revision": 1, "instruction": "旧标签的修改"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "TASK_REVISION_CONFLICT"


def test_abandon_then_start_is_rejected(client: TestClient, starter: RecordingStarter) -> None:
    project_id = _create_project(client)
    conversation_id = _new_conversation(client, project_id)
    created = client.post(
        f"/api/v2/projects/{project_id}/task-drafts",
        json={"conversation_id": conversation_id, "task_kind": "plan"},
    ).json()
    draft_id = created["draft"]["draft_id"]
    assert client.post(
        f"/api/v2/projects/{project_id}/task-drafts/{draft_id}/abandon", json={}
    ).status_code == 200
    response = client.post(
        f"/api/v2/projects/{project_id}/task-drafts/{draft_id}/start",
        json={"expected_revision": 1, "idempotency_key": "k1"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "TASK_DRAFT_ABANDONED"
    assert starter.calls == []


# -- 启动 -------------------------------------------------------------------------


def test_start_binds_job_ref_and_replays_idempotently(
    client: TestClient, starter: RecordingStarter
) -> None:
    project_id = _create_project(client)
    conversation_id = _new_conversation(client, project_id)
    created = client.post(
        f"/api/v2/projects/{project_id}/task-drafts",
        json={"conversation_id": conversation_id, "task_kind": "plan", "instruction": "规划"},
    ).json()
    draft_id = created["draft"]["draft_id"]
    started = client.post(
        f"/api/v2/projects/{project_id}/task-drafts/{draft_id}/start",
        json={"expected_revision": 1, "idempotency_key": "k1"},
    )
    assert started.status_code == 200, started.text
    job_ref = started.json()["job_ref"]
    assert job_ref
    assert starter.calls == [(project_id, draft_id, "k1")]
    replay = client.post(
        f"/api/v2/projects/{project_id}/task-drafts/{draft_id}/start",
        json={"expected_revision": 1, "idempotency_key": "k1"},
    )
    assert replay.status_code == 200
    assert replay.json()["job_ref"] == job_ref
    assert len(starter.calls) == 1


def test_start_with_missing_items_is_400(client: TestClient) -> None:
    project_id = _create_project(client)
    conversation_id = _new_conversation(client, project_id)
    # 空项目 + 未指定目标：chapter_draft 仍有缺失项（TASK_NO_CHAPTER_TARGET）。
    created = client.post(
        f"/api/v2/projects/{project_id}/task-drafts",
        json={"conversation_id": conversation_id, "task_kind": "chapter_draft"},
    ).json()
    assert created["draft"]["missing"], created["draft"]
    response = client.post(
        f"/api/v2/projects/{project_id}/task-drafts/{created['draft']['draft_id']}/start",
        json={"expected_revision": 1, "idempotency_key": "k1"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "TASK_MISSING_ITEMS"
    assert response.json()["error"]["details"]["missing"]


def test_start_with_stale_base_version_is_rejected(client: TestClient) -> None:
    project_id = _create_project(client)
    conversation_id = _new_conversation(client, project_id)
    # 先有内容：草稿创建时定格 head 为基础版本。
    client.post(
        f"/api/v2/projects/{project_id}/chapters/import",
        json={"idempotency_key": "imp1", "title": "第一章", "text": "第一段。"},
    )
    created = client.post(
        f"/api/v2/projects/{project_id}/task-drafts",
        json={"conversation_id": conversation_id, "task_kind": "plan"},
    ).json()
    draft_id = created["draft"]["draft_id"]
    assert created["draft"]["base_version"]
    # 项目前进一格（再导入一章 → head 变化），基础版本过期。
    client.post(
        f"/api/v2/projects/{project_id}/chapters/import",
        json={"idempotency_key": "imp2", "title": "第二章", "text": "第二段。"},
    )
    response = client.post(
        f"/api/v2/projects/{project_id}/task-drafts/{draft_id}/start",
        json={"expected_revision": 1, "idempotency_key": "k1"},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "TASK_BASE_STALE"


# -- 项目隔离 ---------------------------------------------------------------------


def test_cross_project_draft_access_is_404(client: TestClient) -> None:
    p1 = _create_project(client, "甲项目")
    conversation_id = _new_conversation(client, p1)
    created = client.post(
        f"/api/v2/projects/{p1}/task-drafts",
        json={"conversation_id": conversation_id, "task_kind": "plan"},
    ).json()
    p2 = _create_project(client, "乙项目")
    response = client.get(f"/api/v2/projects/{p2}/task-drafts/{created['draft']['draft_id']}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TASK_DRAFT_NOT_FOUND"


# -- 聊天不触发任务 ------------------------------------------------------------------


def test_plain_turn_creates_no_task_drafts(client: TestClient) -> None:
    project_id = _create_project(client)
    conversation_id = _new_conversation(client, project_id)
    client.post(
        f"/api/v2/projects/{project_id}/conversations/{conversation_id}/turns",
        json={"text": "帮我写第一章（只是说说）", "refs": [], "idempotency_key": "k1"},
    )
    deadline = time.time() + 5
    while time.time() < deadline:
        messages = client.get(
            f"/api/v2/projects/{project_id}/conversations/{conversation_id}/messages"
        ).json()["messages"]
        if any(m["role"] == "assistant" and m["status"] == "completed" for m in messages):
            break
        time.sleep(0.02)
    # 没有任何任务消息与 draft 产物：聊天文本本身不进入创作。
    assert not any(m["kind"] == "task_draft" for m in messages)
