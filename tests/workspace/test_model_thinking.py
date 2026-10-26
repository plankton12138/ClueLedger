"""思考深度：接口、持久化、受理快照与真实适配器的请求形状。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from hastings.infrastructure.credentials import InMemoryCredentialStore
from hastings.server.file_app import configured_provider_factory, create_workspace_app
from hastings.server.model_connections import ModelConnectionService
from hastings.server.model_credential import SubscriptionCredential
from hastings.server.model_thinking import ThinkingLevel, thinking_levels
from hastings.server.provider_config import (
    ProviderBook,
    ProviderProfile,
    ServerConfig,
    load_server_config,
    save_server_config,
)
from hastings.server.provider_runtime import build_provider
from hastings.workspace.state import RecentWorkspaces
from tau_ai.openai_compatible import OpenAICompatibleProvider


def _profile(**updates: Any) -> ProviderProfile:
    fields = dict(
        name="c",
        base_url="https://api.deepseek.com/v1",
        catalog_id="deepseek",
        api="openai-completions",
        no_auth=True,
        models=["deepseek-flash", "deepseek-v4-pro", "unknown"],
        default_model="deepseek-flash",
    )
    fields.update(updates)
    return ProviderProfile(**fields)


def _setup(tmp_path: Path) -> tuple[TestClient, ProviderBook, Path]:
    path = tmp_path / "config.yaml"
    config = ServerConfig(providers=[_profile()])
    save_server_config(config, path)
    store = InMemoryCredentialStore()
    book = ProviderBook(config, boot_name="c", boot_model="deepseek-flash", config_path=path)
    service = ModelConnectionService(book=book, config_path=path, credentials=store)
    app = create_workspace_app(
        workspaces=RecentWorkspaces(tmp_path / "state"),
        provider_factory=configured_provider_factory(book),
        model_service=service,
    )
    return TestClient(app), book, path


def _select(client: TestClient, **updates: Any) -> httpx.Response:
    body = {"connection_id": "c", "model": "deepseek-flash", **updates}
    revision = client.get("/api/model-connections").json()["revision"]
    return client.put("/api/model-selection", json=body, headers={"If-Match": revision})


@pytest.mark.parametrize(
    "model,levels",
    [
        ("gpt-6.1-sol", ("low", "medium", "high", "xhigh", "max")),
        ("gpt-6-astra", ("low", "medium", "high", "xhigh", "max")),
        ("gpt-6-sol", ("off", "low", "medium", "high", "xhigh", "max")),
        ("gpt-5.6-sol", ("off", "low", "medium", "high", "xhigh", "max")),
        ("gpt-5.4", ("off", "low", "medium", "high", "xhigh")),
        ("gpt-5.3-codex", ("low", "medium", "high", "xhigh")),
        ("gpt-5", ("minimal", "low", "medium", "high")),
        ("gpt-4.1", ()),
        ("gpt-5-chat-latest", ()),
        ("unknown", ()),
    ],
)
def test_model_capabilities_do_not_inherit_unsupported_levels(
    model: str,
    levels: tuple[str, ...],
) -> None:
    profile = _profile(catalog_id="openai", base_url="https://api.openai.com/v1")
    assert thinking_levels(profile, model) == levels
    assert thinking_levels(profile, "gpt-5.4-2026-03-05") == (
        "off",
        "low",
        "medium",
        "high",
        "xhigh",
    )


def test_depth_round_trip_restores_after_restart_and_freezes_each_snapshot(tmp_path: Path) -> None:
    client, book, path = _setup(tmp_path)
    initial = client.get("/api/model-selection").json()["selection"]
    assert initial["thinking_level"] is None
    assert initial["thinking_levels"] == ["off", "low", "high", "max"]

    assert _select(client, thinking_level="max").status_code == 200
    accepted = book.snapshot()
    assert _select(client, thinking_level="low").status_code == 200
    following = book.snapshot()
    assert accepted.thinking_level == "max"
    assert following.thinking_level == "low"
    assert accepted.describe()["thinking_level"] == "max"
    assert isinstance(accepted.provider, OpenAICompatibleProvider)
    assert accepted.provider._config.reasoning_effort == "max"

    restored = ProviderBook(load_server_config(path), boot_name="", boot_model="")
    assert restored.selection().thinking_level == "low"
    assert restored.snapshot().thinking_level == "low"


@pytest.mark.parametrize("level", ["medium", "xhigh"])
def test_unsupported_depth_does_not_write_config(tmp_path: Path, level: str) -> None:
    client, book, path = _setup(tmp_path)
    before = path.read_bytes()
    result = _select(client, thinking_level=level)
    assert result.status_code == 400
    assert result.json()["error"]["code"] == "MODEL_THINKING_LEVEL_INVALID"
    assert path.read_bytes() == before
    assert book.selection().thinking_level is None


def test_model_switch_and_clear_remove_depth(tmp_path: Path) -> None:
    client, book, path = _setup(tmp_path)
    assert _select(client, thinking_level="max").status_code == 200
    assert _select(client, model="unknown").status_code == 200
    assert book.selection().thinking_level is None
    assert book.selection().thinking_levels == ()
    assert "active_thinking_level" not in path.read_text(encoding="utf-8")
    assert _select(client, thinking_level="high").status_code == 200
    assert _select(client, thinking_level=None).status_code == 200
    assert book.selection().thinking_level is None
    assert _select(client, thinking_level="max").status_code == 200
    assert _select(client, connection_id=None).status_code == 200
    assert load_server_config(path).active_thinking_level is None


def test_depth_change_uses_existing_revision_conflict_contract(tmp_path: Path) -> None:
    client, _book, path = _setup(tmp_path)
    stale = client.get("/api/model-selection").json()["revision"]
    assert _select(client, thinking_level="low").status_code == 200
    before = path.read_bytes()
    result = client.put(
        "/api/model-selection",
        headers={"If-Match": stale},
        json={
            "connection_id": "c",
            "model": "deepseek-flash",
            "thinking_level": "max",
        },
    )
    assert result.status_code == 409
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "api,model,catalog",
    [
        ("openai-completions", "deepseek-flash", "deepseek"),
        ("openai-completions", "gpt-5.4", "openai"),
        ("openai-responses", "gpt-5.6-sol", "openai"),
        ("openai-responses", "gpt-5.6-sol", "chatgpt"),
    ],
)
@pytest.mark.parametrize("level", [None, "low", "high", "off"])
def test_provider_sends_selected_depth_and_omits_default(
    api: str,
    model: str,
    catalog: str,
    level: ThinkingLevel | None,
) -> None:
    subscription = catalog == "chatgpt"
    profile = _profile(
        api=api,
        catalog_id=catalog,
        base_url="https://api.deepseek.com/v1"
        if catalog == "deepseek"
        else "https://api.openai.com/v1",
        auth_method="subscription" if subscription else "api_key",
        subscription=SubscriptionCredential(
            auth_provider="chatgpt",
            access_token="test-token",
            expires_at=2_000_000_000,
        )
        if subscription
        else None,
    )
    provider = build_provider(profile, worker=True, model=model, thinking_level=level)
    assert isinstance(provider, OpenAICompatibleProvider)
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        if api == "openai-responses":
            data = 'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
        else:
            data = 'data: {"choices":[{"delta":{"content":"好"},"finish_reason":"stop"}]}\n\n'
            data += "data: [DONE]\n\n"
        return httpx.Response(200, content=data.encode())

    async def run() -> list[Any]:
        provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return [
                event
                async for event in provider.stream_response(
                    model=model,
                    system="",
                    messages=[],
                    tools=[],
                )
            ]
        finally:
            await provider.aclose()

    events = asyncio.run(run())
    assert not [event for event in events if event.type == "error"], events
    body = seen[0]
    if subscription:
        assert "max_output_tokens" not in body
    if level is None:
        assert "reasoning_effort" not in body and "reasoning" not in body
        assert "thinking" not in body
    elif api == "openai-responses":
        assert body["reasoning"]["effort"] == ("none" if level == "off" else level)
    elif catalog == "deepseek":
        assert body["thinking"] == {"type": "disabled" if level == "off" else "enabled"}
        assert body.get("reasoning_effort") == (None if level == "off" else level)
    else:
        assert body["reasoning_effort"] == ("none" if level == "off" else level)
