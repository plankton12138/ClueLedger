"""订阅连接的请求适配（V09-3）。

契约：
- 订阅走 Responses 接口：强制流式、关闭服务端存储，且不带 ``max_output_tokens``
  （订阅侧不接受这个字段，带上会整轮失败）。
- 凭据在每次请求前解析：快过期就续期，同一账号的续期串行执行，避免同一枚刷新令牌被
  花两次；续期后的新令牌要写回凭据存储，否则下一次启动会拿着已作废的令牌。
- 模型目录同样用订阅令牌读取，并接受 ``models`` 数组这种返回形状——「列表拿到了、
  写作用不了」是两套凭据才会出现的故障。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from hastings.infrastructure.credentials import InMemoryCredentialStore
from hastings.server.model_auth import AuthOutcome, AuthStart, ModelAuthService
from hastings.server.model_connections import ConnectionDraft, ModelConnectionService
from hastings.server.model_credential import SubscriptionCredential, load_subscription
from hastings.server.model_probe import discover_models
from hastings.server.provider_catalog import preset
from hastings.server.provider_config import (
    ProviderBook,
    ProviderConfigError,
    ProviderProfile,
    ServerConfig,
    load_server_config,
    save_server_config,
)
from hastings.server.provider_runtime import subscription_provider_config
from tau_ai.openai_compatible import OpenAICompatibleProvider

ACCESS = "access-token-old"
REFRESHED_ACCESS = "access-token-new"


def _credential(**overrides: object) -> SubscriptionCredential:
    fields: dict[str, object] = {
        "auth_provider": "chatgpt",
        "access_token": ACCESS,
        "refresh_token": "refresh-token-1",
        "expires_at": 1_800_000_000.0,
        "account_id": "user_0001",
        "account_label": "reader@example.com",
        "metadata": {"client_id": "oaiapp_issued_1"},
    }
    fields.update(overrides)
    return SubscriptionCredential(**fields)  # type: ignore[arg-type]


class _RefreshingProvider:
    """替身授权适配：只实现续期，用来验证运行时解析。"""

    id = "chatgpt"
    name = "ChatGPT 订阅"

    def __init__(self, *, fail: bool = False) -> None:
        self.refresh_calls = 0
        self.fail = fail

    def begin(self) -> AuthStart:
        raise AssertionError("这个替身只用于续期")

    async def poll(self, flow: Any) -> AuthOutcome:
        return AuthOutcome(status="pending")

    async def cancel(self, flow: Any) -> None:
        return None

    async def refresh(self, credential: SubscriptionCredential) -> SubscriptionCredential:
        self.refresh_calls += 1
        await asyncio.sleep(0)  # 让并发路径真的交错
        if self.fail:
            raise RuntimeError("刷新令牌已失效")
        return _credential(
            access_token=REFRESHED_ACCESS,
            refresh_token="refresh-token-2",
            expires_at=1_900_000_000.0,
        )


def _book(
    tmp_path: Path, *, credential: SubscriptionCredential | None = None, fail_refresh: bool = False
) -> tuple[ProviderBook, InMemoryCredentialStore, _RefreshingProvider, str]:
    credential = credential or _credential()
    path = tmp_path / "config.yaml"
    store = InMemoryCredentialStore()
    ref = "hastings:subscription:conn_sub:abcd1234"
    from hastings.server.model_credential import save_subscription

    save_subscription(store, ref, credential)
    profile = ProviderProfile(
        name="conn_sub",
        display_name="ChatGPT 订阅",
        base_url="https://api.openai.com/v1",
        api="openai-responses",
        catalog_id="chatgpt",
        auth_method="subscription",
        auth_provider="chatgpt",
        credential_ref=ref,
        default_model="gpt-5-codex",
        models=["gpt-5-codex"],
    )
    save_server_config(ServerConfig(providers=[profile]), path)
    provider = _RefreshingProvider(fail=fail_refresh)
    auth = ModelAuthService(providers={provider.id: provider})
    book = ProviderBook(
        ServerConfig(providers=[profile]),
        boot_name="conn_sub",
        boot_model="gpt-5-codex",
        config_path=path,
        credentials=store,
        auth=auth,
    )
    return book, store, provider, ref


def _profile(book: ProviderBook) -> ProviderProfile:
    profile = book.profile("conn_sub")
    assert profile is not None
    return profile


class _TransportProvider(OpenAICompatibleProvider):
    """把请求交给 MockTransport：只替换传输层，其余行为与生产一致。"""

    def __init__(self, config: Any, transport: httpx.AsyncBaseTransport) -> None:
        super().__init__(config)
        self._test_transport = transport

    def _get_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._test_transport, trust_env=False)


def _sse(*events: dict[str, Any]) -> bytes:
    lines: list[str] = []
    for event in events:
        lines.append(f"event: {event['type']}")
        lines.append(f"data: {json.dumps(event)}")
        lines.append("")
    return ("\n".join(lines) + "\n").encode()


def _completed(text: str = "写完了。") -> tuple[dict[str, Any], ...]:
    return (
        {"type": "response.output_text.delta", "delta": text},
        {"type": "response.completed", "response": {"status": "completed"}},
    )


def _drain(provider: Any, model: str = "gpt-5-codex") -> list[Any]:
    async def run() -> list[Any]:
        events = []
        async for event in provider.stream_response(
            model=model, system="", messages=[], tools=[]
        ):
            events.append(event)
        return events

    return asyncio.run(run())


# -- 目录 -------------------------------------------------------------------


def test_catalog_exposes_subscription_source() -> None:
    entry = preset("chatgpt")

    assert entry is not None
    assert entry.auth_providers == ("chatgpt",)
    # 订阅只走 Responses，模型目录用标准列表接口。
    assert entry.api == "openai-responses"
    assert entry.discovery == "openai-models"


# -- 请求形状 ---------------------------------------------------------------


def test_subscription_request_is_stateless_streaming_without_output_limit(
    tmp_path: Path,
) -> None:
    book, _store, _provider, _ref = _book(tmp_path)
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=_sse(*_completed()))

    config = subscription_provider_config(
        _profile(book),
        worker=True,
        max_retries=0,
        timeout_seconds=10.0,
        credential_resolver=book.subscription_resolver(_profile(book)),
    )
    provider = _TransportProvider(config, httpx.MockTransport(handler))

    events = _drain(provider)

    assert seen["url"] == "https://api.openai.com/v1/responses"
    assert seen["headers"]["authorization"] == f"Bearer {ACCESS}"
    assert seen["body"]["stream"] is True
    assert seen["body"]["store"] is False
    # 订阅侧不接受输出上限：带上这个字段整轮会失败。
    assert "max_output_tokens" not in seen["body"]
    assert events[-1].message.stop_reason == "stop"


def test_snapshot_resolves_credential_before_each_request(tmp_path: Path) -> None:
    book, _store, _provider, _ref = _book(tmp_path)

    snapshot = book.snapshot()

    provider = snapshot.provider
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider._config.api == "openai-responses"  # noqa: SLF001
    assert provider._config.max_tokens is None  # noqa: SLF001
    # 写作轮次拿到的不是一个固定令牌，而是「每次请求前解析一次」的入口。
    assert provider._config.credential_resolver is not None  # noqa: SLF001


# -- 运行时凭据 -------------------------------------------------------------


def test_valid_credential_is_used_without_refreshing(tmp_path: Path) -> None:
    book, _store, provider, _ref = _book(tmp_path)
    resolver = book.subscription_resolver(_profile(book))
    assert resolver is not None

    auth = asyncio.run(resolver())

    assert auth.api_key == ACCESS
    assert provider.refresh_calls == 0


def test_expired_credential_is_refreshed_and_persisted(tmp_path: Path) -> None:
    book, store, provider, ref = _book(
        tmp_path, credential=_credential(expires_at=1_600_000_000.0)
    )
    resolver = book.subscription_resolver(_profile(book))
    assert resolver is not None

    auth = asyncio.run(resolver())

    assert auth.api_key == REFRESHED_ACCESS
    assert provider.refresh_calls == 1
    # 轮换后的刷新令牌必须写回存储，否则下次启动拿到的是已作废的那一枚。
    stored = load_subscription(store, ref)
    assert stored is not None
    assert stored.access_token == REFRESHED_ACCESS
    assert stored.refresh_token == "refresh-token-2"
    # 内存视图同步更新，同一轮之后的请求不再续期。
    assert _profile(book).subscription is not None
    assert asyncio.run(resolver()).api_key == REFRESHED_ACCESS
    assert provider.refresh_calls == 1


def test_concurrent_requests_refresh_once(tmp_path: Path) -> None:
    book, _store, provider, _ref = _book(
        tmp_path, credential=_credential(expires_at=1_600_000_000.0)
    )
    resolver = book.subscription_resolver(_profile(book))
    assert resolver is not None

    async def both() -> list[str]:
        first, second = await asyncio.gather(resolver(), resolver())
        return [first.api_key, second.api_key]

    keys = asyncio.run(both())

    assert keys == [REFRESHED_ACCESS, REFRESHED_ACCESS]
    # 同一账号的续期串行：并发请求只花一次刷新令牌。
    assert provider.refresh_calls == 1


def test_refresh_failure_is_reported(tmp_path: Path) -> None:
    book, _store, _provider, _ref = _book(
        tmp_path, credential=_credential(expires_at=1_600_000_000.0), fail_refresh=True
    )
    resolver = book.subscription_resolver(_profile(book))
    assert resolver is not None

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(resolver())

    assert "刷新令牌已失效" in str(caught.value)


def test_missing_credential_blocks_snapshot(tmp_path: Path) -> None:
    book, store, _provider, ref = _book(tmp_path)
    store.delete(ref)
    # 重新装配置：凭据在加载时物化，先在存储里抹掉才能测到「没有凭据」这一态。
    fresh = ProviderBook(
        load_server_config(book.config_path),
        boot_name="conn_sub",
        boot_model="gpt-5-codex",
        config_path=book.config_path,
        credentials=store,
    )

    with pytest.raises(ProviderConfigError) as caught:
        fresh.snapshot()

    assert "订阅" in str(caught.value)


# -- 模型目录 ---------------------------------------------------------------


def test_discovery_reads_models_array_with_subscription_token(tmp_path: Path) -> None:
    book, _store, _provider, _ref = _book(tmp_path)
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers["authorization"]
        seen["url"] = str(request.url)
        return httpx.Response(
            200, json={"models": [{"id": "gpt-5-codex"}, "o4-mini", {"id": "gpt-5.1"}]}
        )

    result = asyncio.run(
        discover_models(
            _profile(book),
            discovery="openai-models",
            transport=httpx.MockTransport(handler),
        )
    )

    assert result.models == ["gpt-5-codex", "o4-mini", "gpt-5.1"]
    assert result.complete is True
    assert seen["url"] == "https://api.openai.com/v1/models"
    assert seen["authorization"] == f"Bearer {ACCESS}"


def test_discovery_refreshes_before_listing(tmp_path: Path) -> None:
    book, store, provider, ref = _book(
        tmp_path, credential=_credential(expires_at=1_600_000_000.0)
    )
    auth = ModelAuthService(providers={provider.id: provider})
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers["authorization"]
        return httpx.Response(200, json={"models": [{"id": "gpt-5-codex"}]})

    service = ModelConnectionService(
        book=book,
        config_path=book.config_path,
        credentials=store,
        auth=auth,
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(
        service.discover(
            ConnectionDraft(
                catalog_id="chatgpt",
                base_url="https://api.openai.com/v1",
                api="openai-responses",
            ),
            connection_id="conn_sub",
        )
    )

    assert result.models == ["gpt-5-codex"]
    # 列表请求用的是续期后的令牌，且新令牌已回写存储。
    assert seen["authorization"] == f"Bearer {REFRESHED_ACCESS}"
    stored = load_subscription(store, ref)
    assert stored is not None and stored.access_token == REFRESHED_ACCESS


def test_discovery_reads_subscription_slugs_and_skips_hidden_models(tmp_path: Path) -> None:
    # 官方目录每项是 slug + visibility，不是 id；只看 visibility == "list" 的项。
    book, _store, _provider, _ref = _book(tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "models": [
                    {"slug": "gpt-6.1-sol", "display_name": "Sol", "visibility": "list"},
                    {"slug": "gpt-5.2-codex", "display_name": "Codex", "visibility": "list"},
                    {"slug": "internal-preview", "visibility": "hidden"},
                ]
            },
        )

    result = asyncio.run(
        discover_models(
            _profile(book),
            discovery="openai-models",
            transport=httpx.MockTransport(handler),
        )
    )

    # 保留服务端顺序，跳过不可展示的模型。
    assert result.models == ["gpt-6.1-sol", "gpt-5.2-codex"]
    assert result.complete is True
