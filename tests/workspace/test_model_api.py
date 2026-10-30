"""模型设置接口与装配（V04-9B）。

契约：
- 应用级 ``/api/model-*`` 与作品无关；响应只给脱敏信息与配置版本。
- 写操作要求 ``If-Match``；过期版本返回 409 且不改文件。
- 保存、删除、选择一次返回「列表 + 选择 + 版本」，前端不必二次请求。
- 设置接口与写作轮次共用同一配置：受理后的轮次使用受理时的快照。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from hastings.infrastructure.credentials import InMemoryCredentialStore
from hastings.server.file_app import configured_provider_factory, create_workspace_app
from hastings.server.model_connections import ModelConnectionService
from hastings.server.provider_config import ProviderBook, load_server_config
from hastings.workspace.state import RecentWorkspaces
from tau_agent.messages import AssistantMessage, TextContent
from tau_agent.provider_events import AssistantDoneEvent, AssistantStartEvent

DRAFT = {
    "display_name": "我的 DeepSeek",
    "catalog_id": "deepseek",
    "base_url": "https://api.deepseek.com/v1",
    "api": "openai-completions",
    "models": ["deepseek-chat", "deepseek-reasoner"],
    "default_model": "deepseek-chat",
    "api_key": "sk-api-key-4321",
}


class _RecordingProvider:
    """记录每次调用用的模型；用于证明「受理后不受切换影响」。"""

    def __init__(self) -> None:
        self.models: list[str] = []

    def stream_response(self, **kwargs: Any) -> AsyncIterator[Any]:
        self.models.append(kwargs["model"])

        async def generator() -> AsyncIterator[Any]:
            yield AssistantStartEvent(partial=AssistantMessage())
            yield AssistantDoneEvent(
                reason="stop",
                message=AssistantMessage(
                    content=[TextContent(text="这一轮结束。")], stop_reason="stop"
                ),
            )

        return generator()

    async def aclose(self) -> None:
        return None


def _build(
    tmp_path: Path, *, transport: httpx.AsyncBaseTransport | None = None
) -> tuple[TestClient, ModelConnectionService, ProviderBook, Path]:
    path = tmp_path / "config.yaml"
    store = InMemoryCredentialStore()
    book = ProviderBook(
        load_server_config(path), boot_name="", boot_model="", config_path=path, credentials=store
    )
    service = ModelConnectionService(
        book=book, config_path=path, credentials=store, transport=transport
    )
    app = create_workspace_app(
        workspaces=RecentWorkspaces(tmp_path / "state"),
        provider_factory=configured_provider_factory(book),
        model_service=service,
    )
    return TestClient(app), service, book, path


def _revision(client: TestClient) -> str:
    return str(client.get("/api/model-connections").json()["revision"])


def _create(client: TestClient, **overrides: object) -> dict[str, Any]:
    body = dict(DRAFT)
    body.update(overrides)
    response = client.post(
        "/api/model-connections", json=body, headers={"If-Match": _revision(client)}
    )
    assert response.status_code == 200, response.text
    return response.json()


# -- 读取 -------------------------------------------------------------------


def test_providers_endpoint_exposes_backend_catalog(tmp_path: Path) -> None:
    client, _service, _book, _path = _build(tmp_path)

    response = client.get("/api/model-providers")

    assert response.status_code == 200
    presets = response.json()
    ids = [item["id"] for item in presets]
    assert "deepseek" in ids and "custom" in ids
    qwen = next(item for item in presets if item["id"] == "qwen")
    assert [region["id"] for region in qwen["regions"]] == ["beijing", "singapore"]
    custom = next(item for item in presets if item["id"] == "custom")
    assert len(custom["api_options"]) == 4


def test_empty_state_has_revision_and_no_selection(tmp_path: Path) -> None:
    client, _service, _book, _path = _build(tmp_path)

    body = client.get("/api/model-connections").json()

    assert body["connections"] == []
    assert body["selection"]["source"] == "none"
    assert body["revision"]


# -- 写入 -------------------------------------------------------------------


def test_create_returns_masked_connection_and_never_the_key(tmp_path: Path) -> None:
    client, _service, _book, path = _build(tmp_path)

    body = _create(client)

    connection = body["connections"][0]
    assert connection["display_name"] == "我的 DeepSeek"
    assert connection["masked_key"] == "…4321"
    assert connection["has_key"] is True
    assert connection["credential_storage"] == "session"
    assert "api_key" not in connection
    assert "sk-api-key-4321" not in str(body)
    assert "sk-api-key-4321" not in path.read_text(encoding="utf-8")


def test_writes_require_matching_revision(tmp_path: Path) -> None:
    client, service, _book, path = _build(tmp_path)
    stale = _revision(client)
    _create(client)
    before = path.read_text(encoding="utf-8")

    missing = client.post("/api/model-connections", json=DRAFT)
    outdated = client.post("/api/model-connections", json=DRAFT, headers={"If-Match": stale})

    assert missing.status_code == 409
    assert missing.json()["error"]["code"] == "MODEL_CONFIG_CONFLICT"
    assert outdated.status_code == 409
    assert path.read_text(encoding="utf-8") == before
    assert service.view().revision == _revision(client)


def test_update_and_delete_round_trip(tmp_path: Path) -> None:
    client, _service, _book, _path = _build(tmp_path)
    name = _create(client)["connections"][0]["name"]

    updated = client.patch(
        f"/api/model-connections/{name}",
        json={**DRAFT, "display_name": "改过的名字", "api_key": None},
        headers={"If-Match": _revision(client)},
    )
    assert updated.status_code == 200
    assert updated.json()["connections"][0]["display_name"] == "改过的名字"

    deleted = client.delete(
        f"/api/model-connections/{name}", headers={"If-Match": _revision(client)}
    )

    assert deleted.status_code == 200
    assert deleted.json()["connections"] == []
    assert deleted.json()["selection"]["source"] == "none"


def test_selection_endpoints_persist_choice(tmp_path: Path) -> None:
    client, _service, _book, _path = _build(tmp_path)
    name = _create(client)["connections"][0]["name"]

    response = client.put(
        "/api/model-selection",
        json={"connection_id": name, "model": "deepseek-reasoner"},
        headers={"If-Match": _revision(client)},
    )

    assert response.status_code == 200
    assert response.json()["selection"] == {
        "connection_id": name,
        "model": "deepseek-reasoner",
        "source": "saved",
        "available": True,
        "reason": "",
        "thinking_level": None,
        "thinking_levels": [],
    }
    read = client.get("/api/model-selection").json()
    assert read["selection"]["model"] == "deepseek-reasoner"
    assert read["revision"] == _revision(client)


def test_clearing_selection_returns_none_source(tmp_path: Path) -> None:
    client, _service, _book, _path = _build(tmp_path)
    name = _create(client)["connections"][0]["name"]
    client.put(
        "/api/model-selection",
        json={"connection_id": name, "model": "deepseek-chat"},
        headers={"If-Match": _revision(client)},
    )

    response = client.put(
        "/api/model-selection",
        json={"connection_id": None, "model": ""},
        headers={"If-Match": _revision(client)},
    )

    assert response.status_code == 200
    assert response.json()["selection"]["source"] == "none"


def test_unknown_connection_returns_not_found(tmp_path: Path) -> None:
    client, _service, _book, _path = _build(tmp_path)

    response = client.patch(
        "/api/model-connections/nope",
        json=DRAFT,
        headers={"If-Match": _revision(client)},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MODEL_CONNECTION_NOT_FOUND"


# -- 发现与测试（不改配置） --------------------------------------------------


def test_discover_endpoint_uses_draft_without_saving(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.deepseek.com/v1/models"
        return httpx.Response(200, json={"data": [{"id": "deepseek-chat"}]})

    client, _service, _book, path = _build(tmp_path, transport=httpx.MockTransport(handler))
    before = _revision(client)

    response = client.post(
        "/api/model-connections/discover",
        json={
            "catalog_id": "deepseek",
            "base_url": "https://api.deepseek.com/v1",
            "api": "openai-completions",
            "api_key": "sk-draft",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["models"] == ["deepseek-chat"]
    assert body["connection_id"] is None
    assert body["revision"] == before
    assert not path.exists()


def test_discover_reuses_stored_key_by_connection_id(tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json={"data": [{"id": "m"}]})

    client, _service, _book, _path = _build(tmp_path, transport=httpx.MockTransport(handler))
    name = _create(client)["connections"][0]["name"]

    response = client.post(
        "/api/model-connections/discover",
        json={
            "connection_id": name,
            "catalog_id": "deepseek",
            "base_url": "https://api.deepseek.com/v1",
            "api": "openai-completions",
        },
    )

    assert response.status_code == 200
    assert seen == ["Bearer sk-api-key-4321"]


def test_probe_endpoint_reports_two_independent_facts(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    client, service, _book, _path = _build(tmp_path, transport=httpx.MockTransport(handler))

    class _Provider:
        def stream_response(self, **kwargs: Any) -> AsyncIterator[Any]:
            async def generator() -> AsyncIterator[Any]:
                yield AssistantDoneEvent(
                    reason="stop",
                    message=AssistantMessage(
                        content=[TextContent(text="你好")], stop_reason="stop"
                    ),
                )

            return generator()

    service._probe_factory = lambda _profile: _Provider()  # noqa: SLF001 - 测试注入

    with client:
        accepted = client.post(
            "/api/model-connections/probe",
            json={
                "kind": "response",
                "model": "deepseek-chat",
                "catalog_id": "deepseek",
                "base_url": "https://api.deepseek.com/v1",
                "api": "openai-completions",
                "api_key": "sk-draft",
            },
        )

        # 受理即返回操作标识；测试本身在后台跑，结果按操作读取。
        assert accepted.status_code == 202
        body = accepted.json()
        assert body["connection_id"] is None
        operation = body["operation_id"]

        snapshot = _wait_operation(client, operation)
        assert snapshot["status"] == "completed"

        result = client.get(f"/api/operations/{operation}/result").json()
        receipt = next(item for item in result["receipts"] if item["kind"] == "probe")
        assert receipt["outcome"]["response"] == "passed"
        assert receipt["outcome"]["tool_call"] == "untested"


def _wait_operation(
    client: TestClient, operation_id: str, statuses: set[str] | None = None
) -> dict[str, Any]:
    import time

    wanted = statuses or {"completed", "failed", "paused"}
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        body = client.get(f"/api/operations/{operation_id}").json()
        if body["status"] in wanted:
            return body
        time.sleep(0.05)
    raise AssertionError("连接测试没有在预期时间内结束")


class _SlowThenFastProvider:
    """第一次响应挂起等待取消，第二次直接通过；用来验证暂停与继续。"""

    def __init__(self) -> None:
        self.calls = 0

    def stream_response(self, **kwargs: Any) -> AsyncIterator[Any]:
        index = self.calls
        self.calls += 1

        async def generator() -> AsyncIterator[Any]:
            if index == 0:
                while True:
                    await asyncio.sleep(0.02)
            yield AssistantDoneEvent(
                reason="stop",
                message=AssistantMessage(content=[TextContent(text="你好")], stop_reason="stop"),
            )

        return generator()

    async def aclose(self) -> None:
        return None


def test_probe_can_be_paused_and_continued_without_a_fake_result(tmp_path: Path) -> None:
    client, service, _book, _path = _build(tmp_path)
    provider = _SlowThenFastProvider()
    service._probe_factory = lambda _profile: provider  # noqa: SLF001 - 测试注入

    payload = {
        "kind": "response",
        "model": "deepseek-chat",
        "catalog_id": "deepseek",
        "base_url": "https://api.deepseek.com/v1",
        "api": "openai-completions",
        "api_key": "sk-draft",
        "request_key": "pr1",
    }
    with client:
        accepted = client.post("/api/model-connections/probe", json=payload)
        assert accepted.status_code == 202
        operation = accepted.json()["operation_id"]

        running = _wait_operation(client, operation, {"running"})
        assert running["purpose"] == "probe"
        # 连接测试没有可解释的补充要求，不宣称能插话。
        assert running["capabilities"]["interject"] is True

        paused = client.post(
            f"/api/operations/{operation}/pause", json={"request_key": "p1"}
        ).json()
        assert paused["status"] == "paused"
        assert paused["capabilities"]["continue"] is True

        # 取消不等于通过：结果里没有伪造的测试收据。
        result = client.get(f"/api/operations/{operation}/result").json()
        assert result["status"] == "paused"
        assert [item for item in result["receipts"] if item["kind"] == "probe"] == []

        rejected = client.post(
            f"/api/operations/{operation}/interject",
            json={"request_key": "i1", "expected_generation": 1, "text": "更慢一点"},
        )
        assert rejected.status_code == 400
        assert rejected.json()["error"]["code"] == "OPERATION_INPUT_INVALID"

        resumed = client.post(
            f"/api/operations/{operation}/continue",
            json={"request_key": "c1", "expected_generation": 1},
        ).json()
        assert resumed["generation"] == 2

        done = _wait_operation(client, operation)
        assert done["status"] == "completed"
        receipt = next(
            item
            for item in client.get(f"/api/operations/{operation}/result").json()["receipts"]
            if item["kind"] == "probe"
        )
        assert receipt["outcome"]["response"] == "passed"


# -- 与写作轮次共用同一配置 --------------------------------------------------


def _workspace(client: TestClient, tmp_path: Path) -> str:
    root = tmp_path / "作品"
    created = client.post("/api/file-workspaces", json={"path": str(root), "create": True})
    return str(created.json()["id"])


def _wait_finished(client: TestClient, workspace_id: str, conversation_id: str) -> list[Any]:
    import time

    url = f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/messages"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        events = client.get(url).json()["events"]
        if any(event["type"] == "run_finished" for event in events):
            return list(events)
        time.sleep(0.05)
    raise AssertionError("等待轮次结束超时")


def test_accepted_turn_keeps_its_snapshot_after_switching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _RecordingProvider()
    built = iter([provider])

    # 让设置接口保存的连接在装配时构造出记录型假 provider。
    def fake_snapshot(
        profile: Any, model: str, *, worker: bool = True, thinking_level: str | None = None
    ) -> Any:
        from hastings.server.provider_runtime import ModelSnapshot

        return ModelSnapshot(
            connection_id=profile.name,
            display_name=profile.title,
            model=model,
            provider=next(built),
        )

    monkeypatch.setattr("hastings.server.provider_config.snapshot", fake_snapshot)
    client, service, _book, _path = _build(tmp_path)
    first = _create(client)["connections"][0]["name"]
    client.put(
        "/api/model-selection",
        json={"connection_id": first, "model": "deepseek-chat"},
        headers={"If-Match": _revision(client)},
    )
    workspace_id = _workspace(client, tmp_path)

    # 首次发送即建会话：受理时就固定这一轮的连接与模型。
    started = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={"text": "先写一句", "request_key": "k-snapshot"},
    )
    assert started.status_code == 202
    conversation_id = str(started.json()["conversation_id"])

    events = _wait_finished(client, workspace_id, conversation_id)
    turn_started = next(event for event in events if event["type"] == "turn_started")
    assert turn_started["connection_id"] == first
    assert turn_started["model"] == "deepseek-chat"
    assert provider.models == ["deepseek-chat"]
    # 事件里不出现连接凭据。
    assert "sk-api-key-4321" not in str(events)
    assert service.view().selection.connection_id == first


# -- 系统识别容量 -----------------------------------------------------------


@pytest.mark.parametrize("field", ["model_limits", "model_metadata"])
def test_client_cannot_submit_capacity_on_create_or_update(tmp_path: Path, field: str) -> None:
    client, _service, _book, path = _build(tmp_path)
    payload = {field: {"deepseek-chat": {"context_window_tokens": 131_072}}}
    response = client.post(
        "/api/model-connections", json={**DRAFT, **payload}, headers={"If-Match": _revision(client)}
    )
    assert response.status_code == 422
    assert not path.exists()
    name = _create(client)["connections"][0]["name"]
    before = path.read_bytes()
    response = client.patch(
        f"/api/model-connections/{name}",
        json={**DRAFT, **payload},
        headers={"If-Match": _revision(client)},
    )
    assert response.status_code == 422
    assert path.read_bytes() == before


def test_server_discovery_capacity_is_saved_without_client_roundtrip(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "deepseek-chat", "context_window": 64_000},
                ]
            },
        )

    client, _service, _book, _path = _build(tmp_path, transport=httpx.MockTransport(handler))
    discovered = client.post(
        "/api/model-connections/discover",
        json={
            "catalog_id": DRAFT["catalog_id"],
            "base_url": DRAFT["base_url"],
            "api": DRAFT["api"],
            "api_key": DRAFT["api_key"],
        },
    )
    assert discovered.status_code == 200
    assert discovered.json()["limits"]["deepseek-chat"]["context_window_tokens"] == 64_000
    connection = _create(client)["connections"][0]
    assert connection["model_limits"] == {}
    assert connection["model_metadata"]["deepseek-chat"]["context_window_tokens"] == 64_000
    name = connection["name"]
    updated = client.patch(
        f"/api/model-connections/{name}",
        json={**DRAFT, "api_key": None},
        headers={"If-Match": _revision(client)},
    )
    assert updated.status_code == 200
    assert updated.json()["connections"][0]["model_metadata"] == connection["model_metadata"]
    moved = client.patch(
        f"/api/model-connections/{name}",
        json={**DRAFT, "base_url": "https://other.example/v1", "api_key": None},
        headers={"If-Match": _revision(client)},
    )
    assert moved.status_code == 200
    assert moved.json()["connections"][0]["model_metadata"] == {}


def test_probe_interjection_retargets_without_changing_credentials(tmp_path: Path) -> None:
    client, service, _, _ = _build(tmp_path)
    provider = _SlowThenFastProvider()
    service._probe_factory = lambda _profile: provider
    payload = {
        "kind": "response",
        "model": "deepseek-chat",
        "catalog_id": "deepseek",
        "base_url": "https://api.deepseek.com/v1",
        "api": "openai-completions",
        "api_key": "sk-draft",
        "request_key": "retarget",
    }
    with client:
        operation_id = client.post("/api/model-connections/probe", json=payload).json()[
            "operation_id"
        ]
        _wait_operation(client, operation_id, {"running"})
        response = client.post(
            f"/api/operations/{operation_id}/interject",
            json={"request_key": "target", "expected_generation": 1, "text": "响应测试"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["generation"] == 2
        assert _wait_operation(client, operation_id)["status"] == "completed"
        replay = client.post(
            f"/api/operations/{operation_id}/interject",
            json={"request_key": "target", "expected_generation": 1, "text": "响应测试"},
        )
        assert replay.status_code == 200
        assert provider.calls == 2
        persisted = str(client.app.state.operations.read(operation_id).to_dict())
        assert "sk-draft" not in persisted
