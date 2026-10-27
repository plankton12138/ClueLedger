"""ChatGPT 订阅授权适配（V09-2）。

契约（对齐官方「Sign in with ChatGPT」开源应用流程）：
- 首次登录以 ``client_id=dynamic_agent_client`` 发起，附带应用名与稳定的本机标识；
  回调会带回签发给我们自己的 client_id，按账号保存后供后续登录与续期复用。
- 回调地址是 ``127.0.0.1`` 的 loopback，路径固定 ``/auth/callback``；只允许端口变化，
  授权请求与换取令牌必须用同一个地址。
- 换取令牌时校验 state、OIDC nonce 与授予的用途（``chatgpt.tokens.use.direct``）；
  校验不过就不产出凭据，也不把失败说成成功。
- 凭据里带刷新令牌与签发身份：续期要用同一个 client_id。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from hastings.server.model_auth import AuthStart
from hastings.server.model_auth_chatgpt import (
    AUTHORIZE_URL,
    CALLBACK_PATH,
    DYNAMIC_CLIENT_ID,
    RESOURCE,
    TOKEN_URL,
    CallbackResult,
    ChatGPTCodexAuthProvider,
    ChatGPTHostIdentity,
    LoopbackCallbackListener,
)
from hastings.server.model_credential import SubscriptionCredential

SCOPE = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
ISSUED_CLIENT_ID = "oaiapp_issued_1"


class _FakeListener:
    """替身回调监听：认领地址，由测试手动投递查询参数。"""

    def __init__(self, redirect_uri: str = f"http://127.0.0.1:1455{CALLBACK_PATH}") -> None:
        self.redirect_uri = redirect_uri
        self.started = 0
        self.closed = 0
        self._result: CallbackResult | None = None

    def start(self) -> None:
        self.started += 1

    def take(self) -> CallbackResult | None:
        return self._result

    def close(self) -> None:
        self.closed += 1

    def deliver(self, **params: str) -> None:
        self._result = CallbackResult(params=params)


class _TokenServer:
    """替身令牌端点：按请求内容返回一组令牌，并记下实际提交的字段。"""

    def __init__(self, *, scope: str = SCOPE, nonce_override: str = "") -> None:
        self.scope = scope
        self.nonce_override = nonce_override
        self.nonce = ""
        self.seen: dict[str, str] = {}
        self.urls: list[str] = []
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        self.urls.append(str(request.url))
        form = {key: value[0] for key, value in parse_qs(request.content.decode()).items()}
        self.seen.update(form)
        if form.get("grant_type") == "refresh_token":
            return httpx.Response(
                200,
                json={
                    "access_token": _jwt({"exp": 1_700_007_200}),
                    "refresh_token": "refresh-token-2",
                    "expires_in": 3600,
                },
            )
        access = _jwt(
            {
                "exp": 1_700_003_600,
                "https://api.openai.com/auth": {"chatgpt_account_id": "acct_0001"},
            }
        )
        id_token = _jwt(
            {
                "nonce": self.nonce_override or self.nonce,
                "email": "reader@example.com",
                "sub": "user_0001",
                "aud": [form.get("client_id", "")],
                "exp": 1_700_003_600,
            }
        )
        return httpx.Response(
            200,
            json={
                "access_token": access,
                "refresh_token": "refresh-token-1",
                "id_token": id_token,
                "expires_in": 3600,
                "scope": self.scope,
                "client_id": ISSUED_CLIENT_ID,
            },
        )


class _FailingServer:
    """替身令牌端点：返回错误状态。"""

    def __init__(self, status: int = 400) -> None:
        self.status = status
        self.calls = 0

    def __call__(self, _request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return httpx.Response(self.status, json={"error": "invalid_grant"})


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _jwt(claims: Mapping[str, Any]) -> str:
    header = _b64(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    payload = _b64(json.dumps(claims).encode())
    return f"{header}.{payload}.signature"


def _provider(
    tmp_path: Path, handler: Any = None
) -> tuple[ChatGPTCodexAuthProvider, _FakeListener, ChatGPTHostIdentity]:
    listener = _FakeListener()
    identity = ChatGPTHostIdentity(tmp_path / "chatgpt-host.json")
    provider = ChatGPTCodexAuthProvider(
        identity=identity,
        transport=httpx.MockTransport(handler) if handler is not None else None,
        listener_factory=lambda: listener,
        clock=lambda: 1_700_000_000.0,
    )
    return provider, listener, identity


def _begin(provider: ChatGPTCodexAuthProvider) -> tuple[AuthStart, dict[str, list[str]]]:
    start = provider.begin()
    assert start.flow is not None
    return start, parse_qs(urlparse(start.url).query)


def _poll(provider: ChatGPTCodexAuthProvider, start: AuthStart) -> Any:
    return asyncio.run(provider.poll(start.flow))


# -- 授权地址 ---------------------------------------------------------------


def test_begin_uses_own_registration_identity(tmp_path: Path) -> None:
    provider, listener, _identity = _provider(tmp_path)

    start, query = _begin(provider)

    url = urlparse(start.url)
    assert f"{url.scheme}://{url.netloc}{url.path}" == AUTHORIZE_URL
    assert query["response_type"] == ["code"]
    # 首次登录走动态注册入口，不是别人的固定客户端标识。
    assert query["client_id"] == [DYNAMIC_CLIENT_ID]
    assert query["agent_name_hint"] == ["线簿"]
    assert query["ext_agent_host_id"][0].startswith("urn:uuid:")
    assert query["redirect_uri"] == [listener.redirect_uri]
    assert query["resource"] == [RESOURCE]
    assert set(query["scope"][0].split()) == set(SCOPE.split())
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"][0] and query["nonce"][0]
    assert listener.started == 1


def test_host_identity_is_stable_and_persisted(tmp_path: Path) -> None:
    path = tmp_path / "chatgpt-host.json"

    host_id = ChatGPTHostIdentity(path).host_id
    again = ChatGPTHostIdentity(path)

    assert again.host_id == host_id
    assert json.loads(path.read_text(encoding="utf-8"))["host_id"] == host_id


def test_each_attempt_uses_fresh_state_and_verifier(tmp_path: Path) -> None:
    provider, _listener, _identity = _provider(tmp_path)

    _start_a, query_a = _begin(provider)
    _start_b, query_b = _begin(provider)

    assert query_a["state"] != query_b["state"]
    assert query_a["nonce"] != query_b["nonce"]
    assert query_a["code_challenge"] != query_b["code_challenge"]


# -- 回调校验 ---------------------------------------------------------------


def test_state_mismatch_is_rejected_before_exchange(tmp_path: Path) -> None:
    server = _TokenServer()
    provider, listener, _identity = _provider(tmp_path, server)
    start, query = _begin(provider)
    server.nonce = query["nonce"][0]
    listener.deliver(code="auth-code-1", state="not-the-state")

    outcome = _poll(provider, start)

    assert outcome.status == "failed"
    assert outcome.code == "CHATGPT_AUTH_STATE_MISMATCH"
    assert server.calls == 0


def test_denied_authorization_reports_code(tmp_path: Path) -> None:
    provider, listener, _identity = _provider(tmp_path)
    start, query = _begin(provider)
    listener.deliver(error="access_denied", state=query["state"][0])

    outcome = _poll(provider, start)

    assert outcome.status == "failed"
    assert outcome.code == "CHATGPT_AUTH_DENIED"


def test_pending_until_callback_arrives(tmp_path: Path) -> None:
    provider, listener, _identity = _provider(tmp_path)
    start, _query = _begin(provider)

    assert _poll(provider, start).status == "pending"
    # 还没收到回调时不能关掉监听：作者可能还在浏览器里。
    assert listener.closed == 0


# -- 换取令牌 ---------------------------------------------------------------


def test_successful_login_returns_structured_credential(tmp_path: Path) -> None:
    server = _TokenServer()
    provider, listener, identity = _provider(tmp_path, server)
    start, query = _begin(provider)
    server.nonce = query["nonce"][0]
    listener.deliver(code="auth-code-1", state=query["state"][0], client_id=ISSUED_CLIENT_ID)

    outcome = _poll(provider, start)

    assert outcome.status == "succeeded"
    credential = outcome.credential
    assert isinstance(credential, SubscriptionCredential)
    assert credential.auth_provider == "chatgpt"
    assert credential.refresh_token == "refresh-token-1"
    assert credential.expires_at == 1_700_003_600.0
    assert credential.account_id == "user_0001"
    assert credential.account_label == "reader@example.com"
    # 续期要用签发给我们自己的身份，不是动态注册入口。
    assert credential.metadata["client_id"] == ISSUED_CLIENT_ID
    # 换取令牌的提交内容：同一个 client_id、同一个回调地址、同一个 resource。
    assert server.urls == [TOKEN_URL]
    assert server.seen["grant_type"] == "authorization_code"
    assert server.seen["client_id"] == ISSUED_CLIENT_ID
    assert server.seen["redirect_uri"] == listener.redirect_uri
    assert server.seen["resource"] == RESOURCE
    assert server.seen["code"] == "auth-code-1"
    # PKCE：提交的 verifier 必须能还原授权时的 challenge。
    digest = hashlib.sha256(server.seen["code_verifier"].encode("ascii")).digest()
    assert _b64(digest) == query["code_challenge"][0]
    # 签发的 client_id 按账号记住。
    assert identity.client_id_for("user_0001") == ISSUED_CLIENT_ID
    assert listener.closed == 1


def test_missing_usage_scope_is_rejected(tmp_path: Path) -> None:
    server = _TokenServer(scope="openid profile email")
    provider, listener, _identity = _provider(tmp_path, server)
    start, query = _begin(provider)
    server.nonce = query["nonce"][0]
    listener.deliver(code="auth-code-1", state=query["state"][0])

    outcome = _poll(provider, start)

    assert outcome.status == "failed"
    assert outcome.code == "CHATGPT_AUTH_SCOPE_MISSING"


def test_nonce_mismatch_is_rejected(tmp_path: Path) -> None:
    server = _TokenServer(nonce_override="some-other-nonce")
    provider, listener, _identity = _provider(tmp_path, server)
    start, query = _begin(provider)
    listener.deliver(code="auth-code-1", state=query["state"][0])

    outcome = _poll(provider, start)

    assert outcome.status == "failed"
    assert outcome.code == "CHATGPT_AUTH_NONCE_MISMATCH"


def test_exchange_error_is_reported_not_silently_successful(tmp_path: Path) -> None:
    server = _FailingServer(400)
    provider, listener, _identity = _provider(tmp_path, server)
    start, query = _begin(provider)
    listener.deliver(code="auth-code-1", state=query["state"][0])

    outcome = _poll(provider, start)

    assert outcome.status == "failed"
    assert outcome.code == "CHATGPT_AUTH_EXCHANGE_FAILED"
    assert "400" in outcome.message


def test_poll_is_idempotent_after_success(tmp_path: Path) -> None:
    server = _TokenServer()
    provider, listener, _identity = _provider(tmp_path, server)
    start, query = _begin(provider)
    server.nonce = query["nonce"][0]
    listener.deliver(code="auth-code-1", state=query["state"][0])

    first = _poll(provider, start)
    second = _poll(provider, start)

    assert first.status == second.status == "succeeded"
    assert second.credential == first.credential
    assert listener.closed == 1
    assert server.calls == 1


def test_cancel_closes_callback_listener(tmp_path: Path) -> None:
    provider, listener, _identity = _provider(tmp_path)
    start, _query = _begin(provider)

    asyncio.run(provider.cancel(start.flow))

    assert listener.closed == 1


# -- 续期 -------------------------------------------------------------------


def _stored_credential() -> SubscriptionCredential:
    return SubscriptionCredential(
        auth_provider="chatgpt",
        access_token="old-access",
        refresh_token="refresh-token-1",
        expires_at=1_699_999_000.0,
        account_id="user_0001",
        account_label="reader@example.com",
        metadata={"client_id": ISSUED_CLIENT_ID},
    )


def test_refresh_uses_issued_client_id_and_rotates_tokens(tmp_path: Path) -> None:
    server = _TokenServer()
    provider, _listener, _identity = _provider(tmp_path, server)

    refreshed = asyncio.run(provider.refresh(_stored_credential()))

    assert server.seen["grant_type"] == "refresh_token"
    assert server.seen["client_id"] == ISSUED_CLIENT_ID
    assert server.seen["refresh_token"] == "refresh-token-1"
    assert refreshed.refresh_token == "refresh-token-2"
    # 响应里有 expires_in 时以它为准（注入时钟 + 3600 秒）。
    assert refreshed.expires_at == 1_700_003_600.0
    # 账号身份与签发身份在续期后保持不变，否则下一次刷新找不到自己的 client_id。
    assert refreshed.account_id == "user_0001"
    assert refreshed.metadata["client_id"] == ISSUED_CLIENT_ID


def test_refresh_error_is_reported(tmp_path: Path) -> None:
    provider, _listener, _identity = _provider(tmp_path, _FailingServer(400))

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(provider.refresh(_stored_credential()))

    assert "400" in str(caught.value)


def test_credential_repr_never_carries_tokens(tmp_path: Path) -> None:
    server = _TokenServer()
    provider, listener, _identity = _provider(tmp_path, server)
    start, query = _begin(provider)
    server.nonce = query["nonce"][0]
    listener.deliver(code="auth-code-1", state=query["state"][0])

    credential = _poll(provider, start).credential

    assert credential is not None
    assert credential.access_token not in repr(credential)
    assert credential.refresh_token not in repr(credential)


# -- 本机回调监听 -----------------------------------------------------------


def test_loopback_listener_takes_first_callback(tmp_path: Path) -> None:
    listener = LoopbackCallbackListener(port=0)
    listener.start()
    try:
        parsed = urlparse(listener.redirect_uri)
        assert parsed.hostname == "127.0.0.1"
        assert parsed.path == CALLBACK_PATH

        # 本机回调不经系统代理：环境里有 HTTP_PROXY 时会把请求转走。
        with httpx.Client(trust_env=False, timeout=5) as client:
            response = client.get(f"{listener.redirect_uri}?code=abc&state=xyz")

        assert response.status_code == 200
        result = listener.take()
        assert result is not None
        assert result.get("code") == "abc"
        assert result.get("state") == "xyz"
        # 取走一次即清空：服务轮询不会把同一次回调算成两次结果。
        assert listener.take() is None
    finally:
        listener.close()


def test_loopback_listener_ignores_other_paths() -> None:
    listener = LoopbackCallbackListener(port=0)
    listener.start()
    try:
        port = urlparse(listener.redirect_uri).port
        with httpx.Client(trust_env=False, timeout=5) as client:
            response = client.get(f"http://127.0.0.1:{port}/not-the-callback?code=abc")

        assert response.status_code == 404
        assert listener.take() is None
    finally:
        listener.close()


# -- 与授权会话服务衔接 -----------------------------------------------------


def test_provider_satisfies_service_contract(tmp_path: Path) -> None:
    from hastings.server.model_auth import SubscriptionAuthProvider

    provider, _listener, _identity = _provider(tmp_path)

    assert isinstance(provider, SubscriptionAuthProvider)


def test_service_completes_chatgpt_login(tmp_path: Path) -> None:
    from hastings.server.model_auth import ModelAuthService

    server = _TokenServer()
    provider, listener, _identity = _provider(tmp_path, server)
    auth = ModelAuthService(providers={provider.id: provider})

    session = asyncio.run(auth.create("chatgpt"))
    query = parse_qs(urlparse(session.auth_url).query)
    server.nonce = query["nonce"][0]
    listener.deliver(code="auth-code-1", state=query["state"][0])
    done = asyncio.run(auth.status(session.id))

    assert done.status == "succeeded"
    assert done.account == "r…@example.com"
    credential = auth.peek(session.id, provider_id="chatgpt")
    assert credential.refresh_token == "refresh-token-1"
    assert credential.metadata["client_id"] == ISSUED_CLIENT_ID



def test_loopback_listener_never_shares_a_bound_port() -> None:
    # Windows 上 Python 的 HTTPServer 默认允许重复绑定同一端口：两个进程都「成功」，
    # 回调却被随机分给其中一个。作者机器上 1455 常被别的工具占着，必须彻底避开。
    first = LoopbackCallbackListener(port=0)
    first.start()
    try:
        taken = urlparse(first.redirect_uri).port
        second = LoopbackCallbackListener(port=taken)
        second.start()
        try:
            assert urlparse(second.redirect_uri).port != taken
        finally:
            second.close()
    finally:
        first.close()


def test_loopback_listener_avoids_the_codex_convention_port() -> None:
    listener = LoopbackCallbackListener()
    listener.start()
    try:
        assert urlparse(listener.redirect_uri).port != 1455
    finally:
        listener.close()
