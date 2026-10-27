"""订阅连接的完整调用链路（V09-4）。

契约：
- 工具调用要真的走完「模型提出调用 → 线簿执行 → 结果回传 → 收尾回答」；只发出调用
  不算通过。
- 流中途的额度错误与连接中断必须保留失败：已经收到半段文字不是「成功」。
- 凭据在重启后仍然可用；退出登录之后，后续调用不再发生。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from hastings.infrastructure.credentials import InMemoryCredentialStore
from hastings.server.model_auth import AuthOutcome, AuthStart, ModelAuthService
from hastings.server.model_credential import SubscriptionCredential, load_subscription
from hastings.server.model_probe import PROBE_TOOL_NAME, probe_response, probe_tool
from hastings.server.provider_config import (
    ProviderBook,
    ProviderProfile,
    ServerConfig,
    load_server_config,
)
from hastings.server.provider_runtime import subscription_provider_config
from tau_ai.openai_compatible import OpenAICompatibleProvider

ACCESS = "access-token-live"
MODEL = "gpt-5-codex"


class _Provider:
    """续期替身：本组用例都用新鲜凭据，续期不该被调用。"""

    id = "chatgpt"
    name = "ChatGPT 订阅"

    def __init__(self) -> None:
        self.refresh_calls = 0

    def begin(self) -> AuthStart:
        raise AssertionError("本组用例不发起登录")

    async def poll(self, flow: Any) -> AuthOutcome:
        return AuthOutcome(status="pending")

    async def cancel(self, flow: Any) -> None:
        return None

    async def refresh(self, credential: SubscriptionCredential) -> SubscriptionCredential:
        self.refresh_calls += 1
        raise AssertionError("凭据有效时不该续期")


class _TransportProvider(OpenAICompatibleProvider):
    def __init__(self, config: Any, transport: httpx.AsyncBaseTransport) -> None:
        super().__init__(config)
        self._test_transport = transport

    def _get_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._test_transport, trust_env=False)


def _credential() -> SubscriptionCredential:
    return SubscriptionCredential(
        auth_provider="chatgpt",
        access_token=ACCESS,
        refresh_token="refresh-token-1",
        expires_at=1_800_000_000.0,
        account_id="user_0001",
        account_label="reader@example.com",
        metadata={"client_id": "oaiapp_issued_1"},
    )


def _profile() -> ProviderProfile:
    return ProviderProfile(
        name="conn_sub",
        display_name="ChatGPT 订阅",
        base_url="https://api.openai.com/v1",
        api="openai-responses",
        catalog_id="chatgpt",
        auth_method="subscription",
        auth_provider="chatgpt",
        credential_ref="hastings:subscription:conn_sub:abcd1234",
        default_model=MODEL,
        models=[MODEL],
        subscription=_credential(),
    )


def _sse(*events: dict[str, Any]) -> bytes:
    lines: list[str] = []
    for event in events:
        lines.append(f"event: {event['type']}")
        lines.append(f"data: {json.dumps(event)}")
        lines.append("")
    return ("\n".join(lines) + "\n").encode()


def _tool_call_events() -> tuple[dict[str, Any], ...]:
    """工具调用一轮：注册 → 参数流 → 定稿 → 收尾。"""
    arguments = json.dumps({"echo": "ping"})
    return (
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {
                "id": "fc_1",
                "type": "function_call",
                "name": PROBE_TOOL_NAME,
                "call_id": "call_1",
                "arguments": "",
            },
        },
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "fc_1",
            "output_index": 0,
            "delta": arguments,
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "id": "fc_1",
                "type": "function_call",
                "name": PROBE_TOOL_NAME,
                "call_id": "call_1",
                "arguments": arguments,
            },
        },
        {"type": "response.completed", "response": {"status": "completed"}},
    )


def _final_events(text: str = "收到了 ping。") -> tuple[dict[str, Any], ...]:
    return (
        {"type": "response.output_text.delta", "delta": text},
        {"type": "response.completed", "response": {"status": "completed"}},
    )


class _Scripted:
    """按顺序回放 SSE 的传输层，并记下每个请求的提交内容。"""

    def __init__(self, *responses: bytes) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(
            {
                "url": str(request.url),
                "headers": dict(request.headers),
                "body": json.loads(request.content),
            }
        )
        content = self._responses.pop(0) if self._responses else _sse(*_final_events())
        return httpx.Response(200, content=content)


def _factory(transport: httpx.AsyncBaseTransport) -> Any:
    def build(profile: Any) -> OpenAICompatibleProvider:
        config = subscription_provider_config(
            profile, worker=False, max_retries=0, timeout_seconds=10.0, credential_resolver=None
        )
        return _TransportProvider(config, transport)

    return build


def _book(
    tmp_path: Path, *, credential: bool = True
) -> tuple[ProviderBook, InMemoryCredentialStore]:
    store = InMemoryCredentialStore()
    profile = _profile()
    if credential:
        from hastings.server.model_credential import save_subscription

        save_subscription(store, profile.credential_ref, _credential())
    else:
        profile = profile.model_copy(update={"credential_ref": "", "subscription": None})
    path = tmp_path / "config.yaml"
    from hastings.server.provider_config import save_server_config

    save_server_config(ServerConfig(providers=[profile]), path)
    auth = ModelAuthService(providers={"chatgpt": _Provider()})
    book = ProviderBook(
        load_server_config(path),
        boot_name="conn_sub",
        boot_model=MODEL,
        config_path=path,
        credentials=store,
        auth=auth,
    )
    return book, store


# -- 工具调用闭环 -----------------------------------------------------------


def test_tool_call_round_trip_completes() -> None:
    scripted = _Scripted(_sse(*_tool_call_events()), _sse(*_final_events()))
    transport = httpx.MockTransport(scripted)

    result = asyncio.run(probe_tool(_profile(), MODEL, factory=_factory(transport)))

    assert result.tool_call == "passed", result.message
    first, second = scripted.requests
    # 两轮请求共享同一套订阅请求形状。
    for request in (first, second):
        assert request["url"] == "https://api.openai.com/v1/responses"
        assert request["headers"]["authorization"] == f"Bearer {ACCESS}"
        assert request["body"]["store"] is False
        assert request["body"]["stream"] is True
        assert "max_output_tokens" not in request["body"]
        assert any(tool.get("name") == PROBE_TOOL_NAME for tool in request["body"]["tools"])
    # 第二轮必须把工具结果带回去，否则「闭环」只是第一轮的重放。
    outputs = [
        item
        for item in second["body"]["input"]
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    ]
    assert outputs, second["body"]["input"]
    assert "pong" in json.dumps(outputs)


# -- 中途失败 ---------------------------------------------------------------


def _failed_event(code: str, message: str) -> dict[str, Any]:
    return {
        "type": "response.failed",
        "response": {"status": "failed", "error": {"code": code, "message": message}},
    }


def test_usage_limit_after_partial_text_is_failure() -> None:
    scripted = _Scripted(
        _sse(
            {"type": "response.output_text.delta", "delta": "正在写"},
            _failed_event(
                "subscription_sharing_usage_limit_exceeded",
                "You have reached your ChatGPT plan usage limit.",
            ),
        )
    )
    transport = httpx.MockTransport(scripted)

    result = asyncio.run(probe_response(_profile(), MODEL, factory=_factory(transport)))

    assert result.response == "failed"
    assert "usage limit" in result.message


def test_truncated_stream_is_failure_not_success() -> None:
    scripted = _Scripted(_sse({"type": "response.output_text.delta", "delta": "写了一半"}))
    transport = httpx.MockTransport(scripted)

    result = asyncio.run(probe_response(_profile(), MODEL, factory=_factory(transport)))

    assert result.response == "failed"
    assert "没有正常结束" in result.message


# -- 重启与退出 -------------------------------------------------------------


def test_credential_survives_restart(tmp_path: Path) -> None:
    book, store = _book(tmp_path)
    # 模拟重启：从磁盘重读配置，凭据从系统存储装回。
    restarted = ProviderBook(
        load_server_config(book.config_path),
        boot_name="conn_sub",
        boot_model=MODEL,
        config_path=book.config_path,
        credentials=store,
    )
    resolver = restarted.subscription_resolver(restarted.profile("conn_sub"))

    assert resolver is not None
    assert asyncio.run(resolver()).api_key == ACCESS


def test_logout_stops_future_calls(tmp_path: Path) -> None:
    book, store = _book(tmp_path)
    ref = book.profile("conn_sub").credential_ref

    # 退出登录：连接删掉，凭据一并清掉。
    from hastings.server.provider_config import save_server_config

    save_server_config(ServerConfig(providers=[]), book.config_path)
    store.delete(ref)
    restarted = ProviderBook(
        load_server_config(book.config_path),
        boot_name="conn_sub",
        boot_model=MODEL,
        config_path=book.config_path,
        credentials=store,
    )

    assert load_subscription(store, ref) is None
    import pytest

    from hastings.server.provider_config import ProviderConfigError

    with pytest.raises(ProviderConfigError):
        restarted.snapshot()
