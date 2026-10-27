"""ChatGPT 订阅授权适配（V09-2）。

官方给「本地开源应用」留的接入方式是：以动态注册入口发起登录，由 OpenAI 在首次授权时
给这个应用签发一个属于它自己的 client_id，之后同一账号的登录与续期都用这个身份。
所以这里既不能复用别人的固定客户端标识，也不能把「签发身份」当成一次性参数丢掉——
凭据里必须带着它，否则续期时会以另一个身份出现。

四件事必须按顺序成立，缺一件都不算登录成功：

1. **state 对上**：回调里的 state 与这次尝试生成的一致，否则丢弃，不换取令牌。
2. **用途被授予**：返回的 scope 里包含 ``chatgpt.tokens.use.direct``，说明这个账号允许
   应用直接使用订阅额度；没有它就不是「额度不够」，而是这条授权不适用。
3. **nonce 对上**：身份令牌是这次尝试签发的，不是别的会话带过来的。
4. **过期时间可解析**：拿不到有效期就无法在失效前续期，宁可当作登录失败。

回调监听只绑 ``127.0.0.1``：这是官方接受的 loopback 形式，端口可以是 1455，也可以在
被占用时换一个；路径固定为 ``/auth/callback``，授权请求与换取令牌必须用同一个地址。
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from hastings.server.model_auth import AuthFlow, AuthOutcome, AuthStart
from hastings.server.model_credential import SubscriptionCredential

__all__ = [
    "AGENT_NAME",
    "AUTHORIZE_URL",
    "CALLBACK_PATH",
    "CALLBACK_PORT",
    "DYNAMIC_CLIENT_ID",
    "RESOURCE",
    "TOKEN_URL",
    "CallbackListener",
    "CallbackResult",
    "ChatGPTCodexAuthProvider",
    "ChatGPTAuthError",
    "ChatGPTHostIdentity",
    "LoopbackCallbackListener",
]

#: 授权地址与令牌地址（官方「Sign in with ChatGPT」开源应用流程）。
AUTHORIZE_URL = "https://auth.openai.com/api/accounts/authorize"
TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"

#: 首次登录的注册入口，不是拿来保存或换取令牌的 client id。
DYNAMIC_CLIENT_ID = "dynamic_agent_client"

#: 订阅额度走这个 audience。
RESOURCE = "https://api.openai.com/v1"

#: 身份用途 + 订阅额度用途；离线刷新需要 offline_access。
SCOPE = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"

#: 允许直接使用订阅额度的用途标记；缺它就说明这次授权不覆盖本应用的用法。
USAGE_SCOPE = "chatgpt.tokens.use.direct"

#: 应用名：官方要求跨安装一致，作者在授权页看到的就是它。
AGENT_NAME = "线簿"

CALLBACK_PATH = "/auth/callback"

#: 回调端口取系统分配的空闲端口。
#:
#: 1455 是 Codex CLI 的惯例端口，作者机器上很可能已经有别的工具占着；而 Windows 上
#: Python 的 ``HTTPServer`` 默认允许重复绑定，两个进程会「都绑定成功」，回调却被随机
#: 分给其中一个——表现就是授权页回来时报「state 不匹配」，还查不出是谁接走的。
#: 官方要求回调保持 127.0.0.1 + ``/auth/callback``，只允许端口变化，所以这里不固定端口。
CALLBACK_PORT = 0

#: 换取令牌的超时；这一步短，卡住通常意味着网络问题而不是模型慢。
TOKEN_TIMEOUT_SECONDS = 30.0


class ChatGPTAuthError(RuntimeError):
    """授权或续期没有完成；``code`` 供上层区分失败原因，消息可直接展示给作者。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class CallbackResult:
    """回调地址带来的查询参数；只做原样携带，不在这里解释。"""

    params: Mapping[str, str]

    def get(self, name: str) -> str:
        return (self.params.get(name) or "").strip()


class CallbackListener(Protocol):
    """本地回调监听：绑定地址、等待一次回调、取回参数、释放端口。"""

    @property
    def redirect_uri(self) -> str: ...

    def start(self) -> None: ...

    def take(self) -> CallbackResult | None: ...

    def close(self) -> None: ...


class _ExclusiveHTTPServer(ThreadingHTTPServer):
    """不与其他进程共享端口的监听器。

    默认的 ``HTTPServer.allow_reuse_address`` 在 Windows 上会让两个进程同时绑定同一
    端口都成功，谁收到连接由系统决定。这里关掉它：端口被别人占着就必须绑定失败，
    我们才能改用一个真正属于自己的端口。
    """

    allow_reuse_address = False
    daemon_threads = True


class LoopbackCallbackListener:
    """127.0.0.1 上的单次回调监听；默认由系统分配端口，不与别的进程共享。"""

    def __init__(self, *, port: int = CALLBACK_PORT, path: str = CALLBACK_PATH) -> None:
        self._port = port
        self._path = path
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._result: CallbackResult | None = None
        self._redirect_uri = ""

    @property
    def redirect_uri(self) -> str:
        return self._redirect_uri

    def start(self) -> None:
        for candidate in (self._port, 0):
            try:
                server = _ExclusiveHTTPServer(("127.0.0.1", candidate), self._handler())
            except OSError:
                continue
            self._server = server
            self._redirect_uri = (
                f"http://127.0.0.1:{server.server_address[1]}{self._path}"
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self._thread = thread
            return
        raise ChatGPTAuthError(
            "CHATGPT_AUTH_CALLBACK_UNAVAILABLE",
            "无法在本机启动授权回调监听（127.0.0.1 上没有可用端口）。"
            "建议：检查是否有别的程序占用了这个端口后重试。"
        )

    def take(self) -> CallbackResult | None:
        with self._lock:
            result, self._result = self._result, None
        return result

    def close(self) -> None:
        server, self._server = self._server, None
        thread, self._thread = self._thread, None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=1)

    def _record(self, params: Mapping[str, str]) -> None:
        with self._lock:
            if self._result is None:
                self._result = CallbackResult(params=dict(params))

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        listener = self
        path = self._path

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                parsed = urlparse(self.path)
                if parsed.path != path:
                    self._reply(404, "授权回调路径不正确。")
                    return
                params = {key: values[0] for key, values in parse_qs(parsed.query).items()}
                listener._record(params)  # noqa: SLF001 - 同模块内的监听状态
                self._reply(200, "登录已返回线簿，可以关闭这个页面。")

            def log_message(self, _format: str, *_args: Any) -> None:
                return

            def _reply(self, status: int, message: str) -> None:
                body = message.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler


class ChatGPTHostIdentity:
    """这台机器在 ChatGPT 侧的身份，以及每个账号拿到的签发 client id。

    都放在应用状态目录里：不是密钥，但丢了就要重新注册一次，所以和账号一起落盘。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, Any] | None = None

    @property
    def host_id(self) -> str:
        data = self._load()
        host_id = data.get("host_id")
        if isinstance(host_id, str) and host_id.strip():
            return host_id
        created = f"urn:uuid:{uuid.uuid4()}"
        data["host_id"] = created
        self._save(data)
        return created

    def client_id_for(self, account_id: str) -> str:
        if not account_id:
            return ""
        clients = self._load().get("clients")
        if not isinstance(clients, dict):
            return ""
        value = clients.get(account_id)
        return value.strip() if isinstance(value, str) else ""

    def remember_client_id(self, account_id: str, client_id: str) -> None:
        """记下这个账号签发给本应用的 client id；写盘失败不影响这次登录。"""
        if not account_id or not client_id:
            return
        data = self._load()
        clients = data.get("clients")
        if not isinstance(clients, dict):
            clients = {}
        clients[account_id] = client_id
        data["clients"] = clients
        self._save(data)

    def _load(self) -> dict[str, Any]:
        if self._data is not None:
            return self._data
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        self._data = raw if isinstance(raw, dict) else {}
        return self._data

    def _save(self, data: dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError:
            # 身份文件写不下去时登录仍成立：凭据自己带着 client id，续期不依赖这个文件。
            return


@dataclass(slots=True)
class _ChatGPTFlow:
    """一次登录尝试的进行中状态；同一供应商可以并行多次。"""

    state: str
    nonce: str
    verifier: str
    redirect_uri: str
    listener: CallbackListener
    outcome: AuthOutcome | None = None


class ChatGPTCodexAuthProvider:
    """ChatGPT 订阅登录：动态注册入口 → 回调校验 → 换取可续期凭据。"""

    id = "chatgpt"
    name = "ChatGPT 订阅"

    def __init__(
        self,
        *,
        identity: ChatGPTHostIdentity,
        transport: httpx.AsyncBaseTransport | None = None,
        listener_factory: Callable[[], CallbackListener] | None = None,
        clock: Callable[[], float] = time.time,
        agent_name: str = AGENT_NAME,
    ) -> None:
        self._identity = identity
        self._transport = transport
        self._listener_factory = listener_factory or LoopbackCallbackListener
        self._clock = clock
        self._agent_name = agent_name

    # -- 登录 -------------------------------------------------------------

    def begin(self) -> AuthStart:
        listener = self._listener_factory()
        listener.start()
        verifier, challenge = _pkce_pair()
        flow = _ChatGPTFlow(
            state=secrets.token_hex(16),
            nonce=secrets.token_hex(16),
            verifier=verifier,
            redirect_uri=listener.redirect_uri,
            listener=listener,
        )
        params = {
            "response_type": "code",
            "client_id": DYNAMIC_CLIENT_ID,
            "agent_name_hint": self._agent_name,
            "ext_agent_host_id": self._identity.host_id,
            "redirect_uri": flow.redirect_uri,
            "scope": SCOPE,
            "resource": RESOURCE,
            "state": flow.state,
            "nonce": flow.nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return AuthStart(
            url=f"{AUTHORIZE_URL}?{urlencode(params)}",
            instructions="在浏览器里选择要使用的 ChatGPT 账号并确认授权，完成后回到线簿。",
            flow=flow,
        )

    async def poll(self, flow: AuthFlow | None) -> AuthOutcome:
        attempt = _as_flow(flow)
        if attempt.outcome is not None:
            return attempt.outcome
        result = attempt.listener.take()
        if result is None:
            return AuthOutcome(status="pending")
        attempt.listener.close()
        attempt.outcome = await self._finish(attempt, result)
        return attempt.outcome

    async def cancel(self, flow: AuthFlow | None) -> None:
        if flow is None:
            return
        _as_flow(flow).listener.close()

    async def _finish(self, flow: _ChatGPTFlow, result: CallbackResult) -> AuthOutcome:
        error = result.get("error")
        if error:
            description = result.get("error_description") or error
            return AuthOutcome(
                status="failed",
                code="CHATGPT_AUTH_DENIED",
                message=f"授权没有完成：{description}",
            )
        if result.get("state") != flow.state:
            return AuthOutcome(
                status="failed",
                code="CHATGPT_AUTH_STATE_MISMATCH",
                message=(
                    "回调与这次登录不匹配，已丢弃。常见原因是本机另一个工具"
                    "（例如 Codex CLI 或其它登录中的程序）也占用了 127.0.0.1 的回调端口。"
                    "建议：关掉那个工具后重新登录。"
                ),
            )
        code = result.get("code")
        if not code:
            return AuthOutcome(
                status="failed",
                code="CHATGPT_AUTH_INVALID_RESPONSE",
                message="回调里没有授权码，登录没有完成。",
            )
        # 首次注册时服务端会在这里带回签发给本应用的 client id；之后沿用同一个。
        issued_client_id = result.get("client_id") or DYNAMIC_CLIENT_ID
        try:
            return AuthOutcome(
                status="succeeded",
                credential=await self._exchange(flow, code, issued_client_id),
            )
        except ChatGPTAuthError as exc:
            code_name = exc.code or "CHATGPT_AUTH_FAILED"
            return AuthOutcome(status="failed", code=code_name, message=str(exc))

    async def _exchange(
        self, flow: _ChatGPTFlow, code: str, client_id: str
    ) -> SubscriptionCredential:
        payload = await self._post(
            {
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "code_verifier": flow.verifier,
                "redirect_uri": flow.redirect_uri,
                "resource": RESOURCE,
            },
            "换取令牌",
        )
        access = _required(payload, "access_token", "换取令牌")
        refresh = _required(payload, "refresh_token", "换取令牌")
        granted = str(payload.get("scope") or "")
        if USAGE_SCOPE not in granted.split():
            raise _error(
                "CHATGPT_AUTH_SCOPE_MISSING",
                "这次授权没有包含直接使用订阅额度的权限"
                f"（缺少 {USAGE_SCOPE}）。建议：确认这个 ChatGPT 账号的套餐支持应用使用，"
                "或改用 API Key 连接。",
            )
        identity = _decode_jwt(_required(payload, "id_token", "换取令牌"))
        if identity is None:
            raise _error("CHATGPT_AUTH_INVALID_RESPONSE", "身份令牌无法解析，登录没有完成。")
        if identity.get("nonce") != flow.nonce:
            raise _error(
                "CHATGPT_AUTH_NONCE_MISMATCH",
                "身份令牌不是你这次登录签发的，已丢弃；请重新登录。",
            )
        account_id = str(identity.get("sub") or "") or _account_id_from_access(access)
        account_label = str(identity.get("email") or "")
        # 服务端在令牌响应里回传签发身份时以它为准：回调缺这个字段时也能拿到正确的身份。
        issued = str(payload.get("client_id") or "") or client_id
        self._identity.remember_client_id(account_id, issued)
        return SubscriptionCredential(
            auth_provider=self.id,
            access_token=access,
            refresh_token=refresh,
            expires_at=_expiry(payload, access, self._clock()),
            account_id=account_id,
            account_label=account_label,
            metadata={"client_id": issued, "resource": RESOURCE, "scope": granted},
        )

    # -- 续期 -------------------------------------------------------------

    async def refresh(self, credential: SubscriptionCredential) -> SubscriptionCredential:
        """用保存的签发身份续期；刷新令牌会轮换，所以必须把新的写回去。"""
        client_id = str(credential.metadata.get("client_id") or "") or self._identity.client_id_for(
            credential.account_id
        )
        if not client_id:
            raise _error(
                "CHATGPT_REFRESH_CLIENT_MISSING",
                "这条订阅凭据缺少签发身份，无法续期。建议：重新登录一次。",
            )
        payload = await self._post(
            {
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": credential.refresh_token,
                "resource": RESOURCE,
            },
            "续期",
        )
        access = _required(payload, "access_token", "续期")
        rotated = str(payload.get("refresh_token") or "") or credential.refresh_token
        metadata = dict(credential.metadata)
        metadata["client_id"] = str(payload.get("client_id") or "") or client_id
        return SubscriptionCredential(
            auth_provider=self.id,
            access_token=access,
            refresh_token=rotated,
            expires_at=_expiry(payload, access, self._clock()),
            account_id=credential.account_id,
            account_label=credential.account_label,
            metadata=metadata,
        )

    # -- 内部 -------------------------------------------------------------

    async def _post(self, data: dict[str, str], action: str) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=TOKEN_TIMEOUT_SECONDS
            ) as client:
                response = await client.post(
                    TOKEN_URL,
                    data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.HTTPError as exc:
            raise _error(
                "CHATGPT_AUTH_EXCHANGE_FAILED",
                f"{action}没有完成：{type(exc).__name__}。建议：检查网络后重新登录。",
            ) from exc
        if response.status_code >= 400:
            raise _error(
                "CHATGPT_AUTH_EXCHANGE_FAILED",
                f"{action}失败（HTTP {response.status_code}）：{_short(response.text)}。"
                "建议：重新登录；若持续失败，检查这个账号的订阅状态。",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise _error(
                "CHATGPT_AUTH_INVALID_RESPONSE", f"{action}的响应不是 JSON，登录没有完成。"
            ) from exc
        if not isinstance(payload, dict):
            raise _error(
                "CHATGPT_AUTH_INVALID_RESPONSE", f"{action}的响应格式不对，登录没有完成。"
            )
        return payload


def _as_flow(flow: AuthFlow | None) -> _ChatGPTFlow:
    if not isinstance(flow, _ChatGPTFlow):
        raise ChatGPTAuthError(
            "CHATGPT_AUTH_STATE_LOST", "这次登录的进行中状态缺失，无法继续；请重新发起登录。"
        )
    return flow


def _error(code: str, message: str) -> ChatGPTAuthError:
    return ChatGPTAuthError(code, message)


def _required(payload: Mapping[str, Any], name: str, action: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise _error(
            "CHATGPT_AUTH_INVALID_RESPONSE", f"{action}的响应缺少 {name}，登录没有完成。"
        )
    return value.strip()


def _expiry(payload: Mapping[str, Any], access: str, now: float) -> float:
    expires_in = payload.get("expires_in")
    if isinstance(expires_in, int | float) and not isinstance(expires_in, bool):
        return now + float(expires_in)
    claims = _decode_jwt(access)
    if claims is not None:
        exp = claims.get("exp")
        if isinstance(exp, int | float) and not isinstance(exp, bool) and exp > 0:
            return float(exp)
    raise _error("CHATGPT_AUTH_INVALID_RESPONSE", "拿不到凭据的有效期，登录没有完成。")


def _account_id_from_access(access: str) -> str:
    claims = _decode_jwt(access)
    if claims is None:
        return ""
    auth = claims.get("https://api.openai.com/auth")
    if not isinstance(auth, dict):
        return ""
    value = auth.get("chatgpt_account_id")
    return value.strip() if isinstance(value, str) else ""


def _decode_jwt(token: str) -> dict[str, Any] | None:
    """只解出载荷：令牌是经 TLS 从令牌端点取回的，这里不重复验签。"""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        raw = _b64decode(parts[1])
        claims = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    return claims if isinstance(claims, dict) else None


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = _b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _short(text: str, limit: int = 200) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[:limit] + "…"
