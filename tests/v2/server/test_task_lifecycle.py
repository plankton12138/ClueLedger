"""任务生命周期集成测试（Phase 19.5 T19.5c）。

用脚本化 worker runner（不经真模型）走通真实链路：
讨论 → 任务建议 → 显式启动（定格模型快照）→ worker 执行 → waiting →
候选投影到对话 → 作者显式接受（expected_head 校验 + Commit）→ 结果消息 →
幂等重放；以及「任务已建但卡片未关联」的重启恢复窗口（同键找回同一 Job）。
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hastings.server.v2 import V2Runtime, build_v2_app

BINDING = {"profile": "test", "model": "test-model", "api": "openai-completions"}


class ScriptedWorkerRunner:
    """返回合法 planner 输出：base_commit_id 取自上下文（接受校验需要）。"""

    async def run_with_events(
        self, task_kind: str, context_package: dict[str, Any], sink: Any
    ) -> dict[str, Any]:
        return {
            "kind": "story_proposal",
            "base_commit_id": str(context_package["repository_commit_id"]),
            "items": [],
            "intent_kind": "plan",
            "rationale": "按讨论整理的节拍规划",
        }


@pytest.fixture
def runtime() -> V2Runtime:
    runtime = V2Runtime.memory()
    runtime.chat.runner_factory = lambda: _NoopRunner()
    runtime.chat.model_resolver = lambda: "test-model"
    runtime.model_binding_provider = lambda: dict(BINDING)
    runtime.worker.runner = ScriptedWorkerRunner()
    return runtime


class _NoopRunner:
    async def stream(self, run: Any, sink: Any) -> str:
        await sink("好的")
        return "好的"


@pytest.fixture
def client(runtime: V2Runtime) -> Iterator[TestClient]:
    app: FastAPI = build_v2_app(runtime)
    with TestClient(app) as test_client:
        yield test_client


def _project_with_content(client: TestClient) -> tuple[str, str]:
    project_id = str(
        client.post("/api/v2/projects", json={"name": "生命周期"}).json()["project_id"]
    )
    client.post(
        f"/api/v2/projects/{project_id}/chapters/import",
        json={"idempotency_key": "imp1", "title": "第一章", "text": "第一段。"},
    )
    conversation_id = str(
        client.post(
            f"/api/v2/projects/{project_id}/conversations", json={}
        ).json()["conversation_id"]
    )
    return project_id, conversation_id


def _messages(client: TestClient, project_id: str, conversation_id: str) -> list[dict[str, Any]]:
    return client.get(
        f"/api/v2/projects/{project_id}/conversations/{conversation_id}/messages"
    ).json()["messages"]


def _wait_for(predicate: Any, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_full_lifecycle_from_draft_to_commit(client: TestClient) -> None:
    project_id, conversation_id = _project_with_content(client)
    created = client.post(
        f"/api/v2/projects/{project_id}/task-drafts",
        json={"conversation_id": conversation_id, "task_kind": "plan", "instruction": "规划节拍"},
    ).json()
    draft_id = created["draft"]["draft_id"]

    started = client.post(
        f"/api/v2/projects/{project_id}/task-drafts/{draft_id}/start",
        json={"expected_revision": 1, "idempotency_key": "k1"},
    )
    assert started.status_code == 200, started.text
    job_ref = started.json()["job_ref"]

    # 绑定快照随任务持久化（切模型不改变已启动任务）。
    job = client.get(f"/api/v2/projects/{project_id}/agent-jobs/{job_ref}").json()
    assert job["payload"]["model_binding"] == BINDING

    # worker 执行 → waiting → 投影出候选消息。
    assert _wait_for(
        lambda: any(
            m["kind"] == "candidate" and m["payload"]["job_ref"] == job_ref
            for m in _messages(client, project_id, conversation_id)
        )
    ), _messages(client, project_id, conversation_id)

    messages = _messages(client, project_id, conversation_id)
    candidate = next(m for m in messages if m["kind"] == "candidate")
    assert candidate["payload"]["accepted"] is False
    assert candidate["payload"]["summary"] == "按讨论整理的节拍规划"

    head = client.get(f"/api/v2/projects/{project_id}/manuscript").json()["commit_id"]
    accepted = client.post(
        f"/api/v2/projects/{project_id}/conversations/{conversation_id}"
        f"/messages/{candidate['message_id']}/accept",
        json={"expected_head": head, "idempotency_key": "a1"},
    )
    assert accepted.status_code == 200, accepted.text
    result = accepted.json()
    assert result["commit_id"]
    assert result["job_ref"] == job_ref

    # 正式变更可定位 Commit：接受后 head 前进到该 Commit。
    new_head = client.get(f"/api/v2/projects/{project_id}/manuscript").json()["commit_id"]
    assert new_head == result["commit_id"]

    # 幂等重放：同一候选再接受 → 同一 Commit，不产生重复结果消息。
    replay = client.post(
        f"/api/v2/projects/{project_id}/conversations/{conversation_id}"
        f"/messages/{candidate['message_id']}/accept",
        json={"expected_head": new_head, "idempotency_key": "a2"},
    )
    assert replay.status_code == 200
    assert replay.json()["commit_id"] == result["commit_id"]
    results = [m for m in _messages(client, project_id, conversation_id) if m["kind"] == "result"]
    assert len(results) == 1


def test_recovery_window_rebinds_to_existing_job(
    client: TestClient, runtime: V2Runtime
) -> None:
    """任务已建但卡片未关联：同幂等键重放启动 → 找回既有 Job，不重复创建。"""
    import asyncio

    from hastings.domain.repository.ids import ProjectId

    project_id, conversation_id = _project_with_content(client)
    head = runtime.head(ProjectId(project_id))
    assert head is not None

    # 先直接经工作流创建一个 Job（模拟「任务已建但绑定丢失」的现场）。
    # 输入必须与 draft 启动时完全一致（空指令、空目标），指纹重放才匹配。
    async def seed_job() -> str:
        return await runtime.agent_workflows.plan(
            ProjectId(project_id),
            head.value,
            (),
            "recovery-key",
            "",
            model_binding=dict(BINDING),
        )

    existing_jid = asyncio.run(seed_job())

    created = client.post(
        f"/api/v2/projects/{project_id}/task-drafts",
        json={"conversation_id": conversation_id, "task_kind": "plan"},
    ).json()
    started = client.post(
        f"/api/v2/projects/{project_id}/task-drafts/{created['draft']['draft_id']}/start",
        json={"expected_revision": 1, "idempotency_key": "recovery-key"},
    )
    assert started.status_code == 200, started.text
    assert started.json()["job_ref"] == existing_jid
