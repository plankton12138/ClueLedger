"""本地模型任务记录 REST 测试（V03-1.4）：/api/local-model-tasks 四端点。

钉住 HTTP 契约（服务层不变量另见 tests/v2/server/test_local_model_service.py）：
- POST 创建下载/测试任务（201），request_id 幂等重放（200）、同键不同内容 409；
- GET 回读运行中任务带重检；?active=true 是恢复入口，缺参数 422；
- cancel 如实回报取消范围，终态再取消 409；
- 任务记录与响应无任何凭据字段（连接快照白名单）。
原 POST /api/ollama/pull 纯转发流已删除收敛——本文件即替代覆盖。

测试经 ``with client:`` 长驻 portal：HTTP 与后台下载协程复用同一事件循环
（TestClient 每请求独立循环会在请求结束杀掉后台任务，见项目踩坑记录）。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from hastings.application.services.local_model_service import (
    DownloadFrame,
    LocalModelTaskService,
)
from hastings.infrastructure.runtime import SystemClock
from hastings.server.app import create_app
from hastings.server.provider_config import ProviderBook, ProviderProfile, ServerConfig
from test_server_app import stub_registry

Handler = Callable[[httpx.Request], httpx.Response]

_TERMINAL = {"succeeded", "failed", "stopped"}


def _book() -> ProviderBook:
    """注入 ollama 档案（OpenAI 兼容 /v1 地址 + no_auth），不写磁盘 config.yaml。"""
    return ProviderBook(
        ServerConfig(
            providers=[
                ProviderProfile(
                    name="ollama",
                    base_url="http://localhost:11434/v1",
                    no_auth=True,
                    default_model="qwen3:4b",
                )
            ]
        ),
        boot_name="ollama",
        boot_model="qwen3:4b",
    )


def _client(
    handler: Handler,
    *,
    local_model_tasks: LocalModelTaskService | None = None,
) -> TestClient:
    return TestClient(
        create_app(
            stub_registry(Path(".")),
            book=_book(),
            ollama_transport=httpx.MockTransport(handler),
            local_model_tasks=local_model_tasks,
        )
    )


def _wait_terminal(client: TestClient, task_id: str, timeout_s: float = 5.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout_s
    body: dict[str, object] = {}
    while time.monotonic() < deadline:
        body = client.get(f"/api/local-model-tasks/{task_id}").json()
        if body["state"] in _TERMINAL:
            return body
        time.sleep(0.02)
    return body


def _pull_frames_handler() -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        assert request.url.path == "/api/pull"
        return httpx.Response(
            200,
            content=(
                b'{"status": "pulling manifest"}\n'
                b'{"status": "downloading", "total": 100, "completed": 50}\n'
                b'{"status": "verifying sha256 digest"}\n'
                b'{"status": "success"}\n'
            ),
        )

    return handler


def _create_task(client: TestClient, **overrides: object) -> object:
    payload: dict[str, object] = {
        "request_id": "req-1",
        "action": "download",
        "model": "qwen3:4b",
        "connection_id": "ollama",
        **overrides,
    }
    return client.post("/api/local-model-tasks", json=payload)


# --- POST：创建 / 幂等 / 冲突 / 守门 ----------------------------------------


def test_create_download_completes_with_progress_and_semantics() -> None:
    client = _client(_pull_frames_handler())
    with client:
        created = _create_task(client)

        assert created.status_code == 201
        body = created.json()
        assert body["state"] in ("queued", "running")
        # OpenAI 兼容档案地址去掉 /v1 → Ollama 服务根（下载直连原生端口）
        assert body["base_url"] == "http://localhost:11434"
        assert body["provider"] == "ollama"
        assert "api_key" not in created.text and "credential" not in created.text

        final = _wait_terminal(client, str(body["task_id"]))

    assert final["state"] == "succeeded"
    assert final["percent"] == 100
    assert final["stage"] == "下载完成"
    assert "下载完成不等于" in str(final["detail"])  # ≠ 加载 ≠ 测试通过


def test_replay_same_request_id_returns_200_same_task() -> None:
    client = _client(_pull_frames_handler())
    with client:
        first = _create_task(client)
        replay = _create_task(client)

    assert replay.status_code == 200
    assert replay.json()["task_id"] == first.json()["task_id"]


def test_same_request_id_different_content_conflicts_409() -> None:
    client = _client(_pull_frames_handler())
    with client:
        _create_task(client)
        conflict = _create_task(client, model="qwen2.5:3b")

    assert conflict.status_code == 409
    assert "OPERATION_KEY_REUSED" in conflict.json()["detail"]


def test_invalid_model_name_returns_422_with_three_elements() -> None:
    client = _client(_pull_frames_handler())
    with client:
        response = _create_task(client, model="../escape")

    assert response.status_code == 422
    assert "模型名不合法" in response.json()["detail"]
    assert "怎样算对" in response.json()["detail"]


def test_unknown_connection_returns_404_with_available_list() -> None:
    client = _client(_pull_frames_handler())
    with client:
        response = _create_task(client, connection_id="ghost")

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "连接不存在" in detail and "ollama" in detail


def test_error_frame_lands_in_task_record_not_http_error() -> None:
    """Ollama 报错是任务结果数据：failed + 三要素，不是 5xx。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(200, content=b'{"error": "manifest not found"}\n')

    client = _client(handler)
    with client:
        body = _create_task(client).json()
        final = _wait_terminal(client, str(body["task_id"]))

    assert final["state"] == "failed"
    assert "manifest not found" in str(final["error"])
    assert "怎样算对" in str(final["error"])


# --- 读回与恢复（active 重检）------------------------------------------------


def test_read_unknown_task_returns_404() -> None:
    client = _client(_pull_frames_handler())
    with client:
        response = client.get("/api/local-model-tasks/lmt_missing")

    assert response.status_code == 404
    assert "任务不存在" in response.json()["detail"]


def test_active_listing_requires_active_param() -> None:
    client = _client(_pull_frames_handler())
    with client:
        response = client.get("/api/local-model-tasks")

    assert response.status_code == 422
    assert "active=true" in response.json()["detail"]


def test_active_lists_running_task_with_recheck() -> None:
    async def slow_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        await asyncio.sleep(2.0)
        return httpx.Response(200, content=b'{"status": "success"}\n')

    client = _client(slow_handler)
    with client:
        body = _create_task(client).json()

        listed = client.get("/api/local-model-tasks?active=true").json()
        assert any(t["task_id"] == body["task_id"] for t in listed["tasks"])

        # 收尾：取消，避免 with 退出时后台任务悬挂
        client.post(f"/api/local-model-tasks/{body['task_id']}/cancel")


def test_recheck_upgrades_to_succeeded_when_model_already_installed() -> None:
    """任务进行中模型已出现在已装列表：读回如实升级 succeeded（不凭旧进度）。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:4b", "size": 100}]})
        await asyncio.sleep(2.0)  # pull 慢：给读路径重检留窗口
        return httpx.Response(200, content=b'{"status": "success"}\n')

    client = _client(handler)
    with client:
        body = _create_task(client).json()
        final = _wait_terminal(client, str(body["task_id"]))

    assert final["state"] == "succeeded"
    assert "重检发现模型已在已装列表" in str(final["detail"])


# --- 取消（取消范围如实回报）--------------------------------------------------


def test_cancel_running_download_reports_honest_scope_then_409() -> None:
    async def slow_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        await asyncio.sleep(2.0)
        return httpx.Response(200, content=b'{"status": "success"}\n')

    client = _client(slow_handler)
    with client:
        body = _create_task(client).json()
        task_id = str(body["task_id"])

        cancelled = client.post(f"/api/local-model-tasks/{task_id}/cancel")

        assert cancelled.status_code == 200
        cancelled_body = cancelled.json()
        assert cancelled_body["state"] == "stopped"
        assert cancelled_body["stage"] == "已取消"
        detail = str(cancelled_body["detail"])
        assert "仅限本应用发起" in detail  # 取消范围如实：不声称删除共享文件
        assert "断点续传" in detail

        again = client.post(f"/api/local-model-tasks/{task_id}/cancel")

    assert again.status_code == 409
    assert "LOCAL_MODEL_TASK_CONFLICT" in again.json()["detail"]


# --- 调用测试动作（test）------------------------------------------------------


def _fake_service(test_reply: str = "OK") -> LocalModelTaskService:
    """注入假 test 执行器（真实推理执行器见 _build_local_model_task_service）。"""

    async def download(model: str, base_url: str) -> AsyncIterator[DownloadFrame]:
        del model, base_url
        return
        yield  # pragma: no cover —— 空流，本组测试只覆盖 test 动作

    async def test_call(provider: str, model: str) -> str:
        del provider, model
        return test_reply

    async def recheck(model: str, base_url: str) -> tuple[bool, bool]:
        del model, base_url
        return (True, False)

    return LocalModelTaskService(
        clock=SystemClock(),
        new_task_id=lambda: f"lmt_test_{time.monotonic_ns()}",
        download_executor=download,
        test_executor=test_call,
        recheck=recheck,
    )


def test_test_action_via_saved_connection_succeeds() -> None:
    client = _client(_pull_frames_handler(), local_model_tasks=_fake_service())
    with client:
        created = _create_task(client, action="test")

        assert created.status_code == 201
        final = _wait_terminal(client, str(created.json()["task_id"]))

    assert final["state"] == "succeeded"
    assert final["stage"] == "测试通过"
    assert "推理测试通过" in str(final["detail"])


def test_test_action_empty_reply_fails_honestly() -> None:
    client = _client(_pull_frames_handler(), local_model_tasks=_fake_service(test_reply="  "))
    with client:
        body = _create_task(client, action="test").json()
        final = _wait_terminal(client, str(body["task_id"]))

    assert final["state"] == "failed"
    assert "CHAT_EMPTY_COMPLETION" in str(final["error"])


def test_test_action_real_wiring_unreachable_fails_with_attribution() -> None:
    """真实装配（默认服务）：端点不可达 → failed 环境归因，不伪造测试通过。"""
    client = _client(_pull_frames_handler())
    with client:
        body = _create_task(client, action="test").json()
        final = _wait_terminal(client, str(body["task_id"]), timeout_s=10.0)

    assert final["state"] == "failed"
    assert str(final["error"]) != ""
