"""xAI Grok 订阅授权适配（V09-3）。

契约：
- 登录走 OAuth 2.0 设备码：先申请设备码与用户码，再按服务端给的间隔轮询令牌端点。
- 设备码过期、账号拒绝、用途缺失、网络失败各给独立错误码，不把失败说成成功。
- 凭据里带刷新令牌与公开客户端身份；续期沿用同一个 client_id，并接住轮换后的刷新令牌。
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from hastings.server.model_auth import AuthStart, ModelAuthService
from hastings.server.model_auth_xai import (
    CLIENT_ID,
    DEVICE_CODE_URL,
    DEVICE_GRANT,
    SCOPE,
    XAIAuthError,
    XAIGrokAuthProvider,
)
from hastings.server.model_credential import SubscriptionCredential
from hastings.workspace.errors import WorkspaceError

NOW = 1_700_000_000.0


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _jwt(claims: Mapping[str, Any]) -> str:
    header = _b64(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    payload = _b64(json.dumps(claims).encode())
    return f"{header}.{payload}.signature"


def _token_payload(*, scope: str = SCOPE, refresh: str = "refresh-token-1") -> dict[str, Any]:
    return {
        "access_token": "access-token-1",
        "refresh_token": refresh,
        "id_token": _jwt({"sub": "user_0001", "email": "reader@example.com"}),
        "expires_in": 3600,
        "scope": scope,
        "token_type": "Bearer",
    }


class _Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> float:
        return self.now


class _Server:
    """替身授权服务器：记录表单，按队列返回令牌响应。"""

    def __init__(self, *steps: httpx.Response) -> None:
        self.steps = list(steps)
        self.device_requests: list[dict[str, str]] = []
        self.token_requests: list[dict[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        form = {key: value[0] for key, value in parse_qs(request.content.decode()).items()}
        if str(request.url) == DEVICE_CODE_URL:
            self.device_requests.append(form)
            return httpx.Response(
                200,
                json={
                    "device_code": "device-code-1",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://x.ai/device",
                    "verification_uri_complete": "https://x.ai/device?user_code=ABCD-EFGH",
                    "expires_in": 600,
                    "interval": 5,
                },
            )
        self.token_requests.append(form)
        if not self.steps:
            return httpx.Response(400, json={"error": "authorization_pending"})
        return self.steps.pop(0)


def _provider(server: _Server, clock: _Clock | None = None) -> tuple[XAIGrokAuthProvider, _Clock]:
    clock = clock or _Clock()
    provider = XAIGrokAuthProvider(transport=httpx.MockTransport(server), clock=clock)
    return provider, clock


def _begin(provider: XAIGrokAuthProvider) -> AuthStart:
    return asyncio.run(provider.begin())


def _poll(provider: XAIGrokAuthProvider, start: AuthStart) -> Any:
    return asyncio.run(provider.poll(start.flow))


def _pending() -> httpx.Response:
    return httpx.Response(400, json={"error": "authorization_pending"})


def _success(**overrides: Any) -> httpx.Response:
    return httpx.Response(200, json=_token_payload(**overrides))


# -- 设备码 -----------------------------------------------------------------


def test_begin_requests_device_code_with_public_client() -> None:
    server = _Server()
    provider, _clock = _provider(server)

    start = _begin(provider)

    assert server.device_requests == [{"client_id": CLIENT_ID, "scope": SCOPE}]
    # 有完整验证地址时优先用它：浏览器打开即带用户码。
    assert start.url == "https://x.ai/device?user_code=ABCD-EFGH"
    assert start.user_code == "ABCD-EFGH"
    assert "确认授权" in start.instructions
    assert start.flow is not None


def _connection_refused(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("连接被拒绝", request=request)


def test_network_failure_explains_proxy_option() -> None:
    provider = XAIGrokAuthProvider(
        transport=httpx.MockTransport(_connection_refused), clock=_Clock()
    )

    with pytest.raises(XAIAuthError) as caught:
        asyncio.run(provider.begin())

    assert caught.value.code == "XAI_AUTH_NETWORK_FAILED"
    assert "auth.x.ai" in str(caught.value)
    assert "HTTPS_PROXY" in caught.value.fix


def test_service_surfaces_provider_fix_without_double_suggestion() -> None:
    provider = XAIGrokAuthProvider(
        transport=httpx.MockTransport(_connection_refused), clock=_Clock()
    )
    auth = ModelAuthService(providers={"xai": provider})

    with pytest.raises(WorkspaceError) as caught:
        asyncio.run(auth.create("xai"))

    assert caught.value.code == "MODEL_AUTH_UNAVAILABLE"
    assert "HTTPS_PROXY" in caught.value.fix
    assert "建议：确认当前网络能访问 x.ai" not in caught.value.why


def test_first_poll_respects_server_interval() -> None:
    server = _Server(_success())
    provider, clock = _provider(server)
    start = _begin(provider)

    assert _poll(provider, start).status == "pending"
    assert server.token_requests == []

    clock.now += 5
    outcome = _poll(provider, start)

    assert outcome.status == "succeeded"
    assert server.token_requests[0]["grant_type"] == DEVICE_GRANT
    assert server.token_requests[0]["client_id"] == CLIENT_ID
    assert server.token_requests[0]["device_code"] == "device-code-1"


def test_authorization_pending_keeps_polling() -> None:
    server = _Server(_pending(), _success())
    provider, clock = _provider(server)
    start = _begin(provider)

    clock.now += 5
    assert _poll(provider, start).status == "pending"
    clock.now += 5
    assert _poll(provider, start).status == "succeeded"
    assert len(server.token_requests) == 2


def test_slow_down_increases_interval() -> None:
    server = _Server(httpx.Response(400, json={"error": "slow_down"}), _success())
    provider, clock = _provider(server)
    start = _begin(provider)

    clock.now += 5
    assert _poll(provider, start).status == "pending"
    clock.now += 5
    # slow_down 后间隔变成 10 秒：这一次只在本地等待，不再发请求。
    assert _poll(provider, start).status == "pending"
    assert len(server.token_requests) == 1
    clock.now += 5
    assert _poll(provider, start).status == "succeeded"


def test_denied_authorization_is_reported() -> None:
    server = _Server(httpx.Response(400, json={"error": "access_denied"}))
    provider, clock = _provider(server)
    start = _begin(provider)

    clock.now += 5
    outcome = _poll(provider, start)

    assert outcome.status == "failed"
    assert outcome.code == "XAI_AUTH_DENIED"


def test_expired_device_code_stops_polling() -> None:
    server = _Server()
    provider, clock = _provider(server)
    start = _begin(provider)

    clock.now += 601
    outcome = _poll(provider, start)

    assert outcome.status == "failed"
    assert outcome.code == "XAI_AUTH_EXPIRED"
    assert server.token_requests == []


# -- 凭据 -------------------------------------------------------------------


def test_successful_login_returns_structured_credential() -> None:
    server = _Server(_success())
    provider, clock = _provider(server)
    start = _begin(provider)
    clock.now += 5

    outcome = _poll(provider, start)

    assert outcome.status == "succeeded"
    credential = outcome.credential
    assert isinstance(credential, SubscriptionCredential)
    assert credential.auth_provider == "xai"
    assert credential.access_token == "access-token-1"
    assert credential.refresh_token == "refresh-token-1"
    assert credential.expires_at == clock.now + 3600
    assert credential.account_id == "user_0001"
    assert credential.account_label == "reader@example.com"
    assert credential.metadata["client_id"] == CLIENT_ID
    assert credential.metadata["issuer"] == "https://auth.x.ai"


def test_missing_usage_scope_is_rejected() -> None:
    server = _Server(_success(scope="openid profile email"))
    provider, clock = _provider(server)
    start = _begin(provider)
    clock.now += 5

    outcome = _poll(provider, start)

    assert outcome.status == "failed"
    assert outcome.code == "XAI_AUTH_SCOPE_MISSING"


def test_poll_is_idempotent_after_success() -> None:
    server = _Server(_success())
    provider, clock = _provider(server)
    start = _begin(provider)
    clock.now += 5

    first = _poll(provider, start)
    second = _poll(provider, start)

    assert first.status == second.status == "succeeded"
    assert second.credential == first.credential
    assert len(server.token_requests) == 1


# -- 续期 -------------------------------------------------------------------


def _credential() -> SubscriptionCredential:
    return SubscriptionCredential(
        auth_provider="xai",
        access_token="old-access",
        refresh_token="refresh-token-1",
        expires_at=NOW - 10,
        account_id="user_0001",
        account_label="reader@example.com",
        metadata={"client_id": CLIENT_ID},
    )


def test_refresh_uses_saved_client_and_rotates_tokens() -> None:
    server = _Server(_success(refresh="refresh-token-2"))
    provider, _clock = _provider(server)

    refreshed = asyncio.run(provider.refresh(_credential()))

    assert server.token_requests[0]["grant_type"] == "refresh_token"
    assert server.token_requests[0]["client_id"] == CLIENT_ID
    assert server.token_requests[0]["refresh_token"] == "refresh-token-1"
    assert refreshed.access_token == "access-token-1"
    assert refreshed.refresh_token == "refresh-token-2"
    assert refreshed.expires_at == NOW + 3600
    # 账号身份与客户端身份必须保留，否则下一次续期会认错账号。
    assert refreshed.account_id == "user_0001"
    assert refreshed.account_label == "reader@example.com"
    assert refreshed.metadata["client_id"] == CLIENT_ID


def test_refresh_error_is_reported() -> None:
    server = _Server(httpx.Response(400, json={"error": "invalid_grant"}))
    provider, _clock = _provider(server)

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(provider.refresh(_credential()))

    assert "400" in str(caught.value)


# -- 与授权会话服务衔接 -----------------------------------------------------


def test_service_completes_xai_login() -> None:
    server = _Server(_success())
    provider, clock = _provider(server)
    auth = ModelAuthService(providers={"xai": provider})

    session = asyncio.run(auth.create("xai"))
    clock.now += 5
    done = asyncio.run(auth.status(session.id))

    assert done.status == "succeeded"
    assert done.account == "r…@example.com"
    credential = auth.peek(session.id, provider_id="xai")
    assert credential.refresh_token == "refresh-token-1"


def test_provider_satisfies_service_contract() -> None:
    from hastings.server.model_auth import (
        RefreshableSubscriptionProvider,
        SubscriptionAuthProvider,
    )

    provider, _clock = _provider(_Server())

    assert isinstance(provider, SubscriptionAuthProvider)
    assert isinstance(provider, RefreshableSubscriptionProvider)
