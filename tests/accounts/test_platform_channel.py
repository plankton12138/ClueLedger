"""v1.4 P4：线簿订阅作为服务器注入的模型来源。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from hastings.identity import bind_user_id, reset_user_id
from hastings.relay.internal_auth import verify_grant
from hastings.server.model_factory import configured_provider_factory
from hastings.server.platform_channel import PLATFORM_CONNECTION, PlatformChannel
from hastings.server.provider_config import (
    ProviderBook,
    ServerConfig,
)
from hastings.workspace.errors import WorkspaceError
from tau_ai.openai_compatible import OpenAICompatibleProvider

USER = "usr_platform000000000000000000000"
SECRET = "platform-secret"


def _channel() -> PlatformChannel:
    return PlatformChannel(
        base_url="http://127.0.0.1:8300",
        secret=SECRET,
        models=("deepseek-v4-pro", "deepseek-flash"),
    )


def test_channel_attaches_a_read_only_subscription_profile() -> None:
    channel = _channel()
    config = channel.attach(ServerConfig(providers=[]))
    assert len(config.providers) == 1
    profile = config.providers[0]
    assert profile.name == PLATFORM_CONNECTION
    assert profile.display_name == "线簿订阅"
    assert profile.catalog_id == "line-book"
    assert profile.no_auth is True
    assert profile.api_key.get_secret_value() == ""
    assert profile.models_for() == ["deepseek-v4-pro", "deepseek-flash"]
    assert profile.auth_status == "ready"
    assert profile.base_url == "http://127.0.0.1:8300/v1"

    # 再附加一次不会产生重复连接。
    again = channel.attach(config)
    assert len(again.providers) == 1


def test_grant_is_scoped_to_the_user_and_secret() -> None:
    channel = _channel()
    grant = channel.grant(USER)
    verified = verify_grant(grant, SECRET)
    assert verified is not None and verified.user_id == USER
    assert verify_grant(grant, "other-secret") is None
    assert f"Bearer {grant}" not in channel.profile().api_key.get_secret_value()


def _book(tmp_path: Path) -> ProviderBook:
    channel = _channel()
    config = ServerConfig(
        providers=[channel.profile()],
        active_provider=PLATFORM_CONNECTION,
        active_model="deepseek-flash",
        selection_mode="explicit",
    )
    return ProviderBook(
        config,
        boot_name="",
        boot_model="",
        config_path=tmp_path / "models.yaml",
    )


class _TransportProvider(OpenAICompatibleProvider):
    """把请求交给 MockTransport：只替换传输层，其余行为与生产一致。"""

    def __init__(self, config: Any, transport: httpx.AsyncBaseTransport) -> None:
        super().__init__(config)
        self._test_transport = transport

    def _get_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._test_transport, trust_env=False)


def _sse_chunk() -> bytes:
    chunk = {"choices": [{"index": 0, "delta": {"content": "好"}, "finish_reason": "stop"}]}
    return f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()


def _drain(provider: Any, model: str) -> list[Any]:
    async def run() -> list[Any]:
        events = []
        async for event in provider.stream_response(model=model, system="", messages=[], tools=[]):
            events.append(event)
        return events

    return asyncio.run(run())


def test_provider_factory_mints_a_fresh_grant_for_platform_selection(
    tmp_path: Path,
) -> None:
    channel = _channel()
    book = _book(tmp_path)
    token = bind_user_id(USER)
    try:
        snapshot = configured_provider_factory(book, platform=channel)()
    finally:
        reset_user_id(token)
    assert snapshot.connection_id == PLATFORM_CONNECTION
    assert snapshot.model == "deepseek-flash"
    assert snapshot.limits is not None
    # 快照里不是一枚固定令牌，而是「每次模型调用前解析」的入口。
    resolver = snapshot.provider._config.credential_resolver  # type: ignore[attr-defined]
    assert resolver is not None
    first = asyncio.run(resolver())
    second = asyncio.run(resolver())
    assert first.api_key != second.api_key
    for grant in (first.api_key, second.api_key):
        verified = verify_grant(grant, SECRET)
        assert verified is not None and verified.user_id == USER
    config_text = (
        (tmp_path / "models.yaml").read_text(encoding="utf-8", errors="ignore")
        if (tmp_path / "models.yaml").exists()
        else ""
    )
    assert first.api_key not in config_text


def test_each_model_call_carries_a_fresh_grant(tmp_path: Path) -> None:
    channel = _channel()
    book = _book(tmp_path)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        return httpx.Response(200, content=_sse_chunk())

    token = bind_user_id(USER)
    try:
        snapshot = configured_provider_factory(book, platform=channel)()
        config = replace(snapshot.provider._config, max_retries=0)  # type: ignore[attr-defined]
        provider = _TransportProvider(config, httpx.MockTransport(handler))
        _drain(provider, snapshot.model)
        _drain(provider, snapshot.model)
    finally:
        reset_user_id(token)

    # 一轮里的两次模型调用（工具循环）必须各有各的 jti，否则代理会判成重放。
    assert len(seen) == 2
    assert seen[0] != seen[1]


def test_retries_of_one_model_call_reuse_the_same_grant(tmp_path: Path) -> None:
    channel = _channel()
    book = _book(tmp_path)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        if len(seen) == 1:
            return httpx.Response(500, content=b"upstream down")
        return httpx.Response(200, content=_sse_chunk())

    token = bind_user_id(USER)
    try:
        snapshot = configured_provider_factory(book, platform=channel)()
        config = replace(
            snapshot.provider._config,  # type: ignore[attr-defined]
            max_retries=2,
            max_retry_delay_seconds=0.0,
        )
        provider = _TransportProvider(config, httpx.MockTransport(handler))
        _drain(provider, snapshot.model)
    finally:
        reset_user_id(token)

    # 同一次调用的 HTTP 重试沿用同一份授权，代理才能按 jti 去重、不重复扣点。
    assert len(seen) == 2
    assert seen[0] == seen[1]


def test_platform_selection_without_channel_is_rejected(tmp_path: Path) -> None:
    channel = _channel()
    config = ServerConfig(
        providers=[channel.profile()],
        active_provider=PLATFORM_CONNECTION,
        active_model="deepseek-flash",
        selection_mode="explicit",
    )
    book = ProviderBook(
        config,
        boot_name="",
        boot_model="",
        config_path=tmp_path / "models.yaml",
    )
    token = bind_user_id(USER)
    try:
        with pytest.raises(WorkspaceError) as exc:
            configured_provider_factory(book)()
    finally:
        reset_user_id(token)
    assert exc.value.code == "MODEL_NOT_CONFIGURED"
