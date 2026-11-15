"""xAI Grok 订阅授权适配（V09-3）。

xAI 给个人订阅（SuperGrok / X Premium+）开的入口是 OAuth 2.0 设备码流程：
先申请设备码与用户码，作者在浏览器里输入用户码并同意授权，应用再轮询令牌端点。
三步都不占本机回调端口，不会有「回调端口被别的登录工具占着」这类问题。

公开客户端身份来自 xAI 官方 Grok CLI：client_id 与该 CLI 的用途（``grok-cli:access``）
都是公开值；线簿没有单独注册的应用身份。授权端点取自 auth.x.ai 的 OIDC 配置
（核对日期 2026-11-13）。订阅令牌只用于 Responses 请求，模型列表与连接测试共用它。

设备码有有效期：过期后不再轮询，直接给出「重新登录」的结论。
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from hastings.server.model_auth import AuthFlow, AuthOutcome, AuthStart
from hastings.server.model_credential import SubscriptionCredential

__all__ = [
    "CLIENT_ID",
    "DEVICE_CODE_URL",
    "DEVICE_GRANT",
    "ISSUER",
    "SCOPE",
    "TOKEN_URL",
    "XAIAuthError",
    "XAIGrokAuthProvider",
]

ISSUER = "https://auth.x.ai"
DEVICE_CODE_URL = f"{ISSUER}/oauth2/device/code"
TOKEN_URL = f"{ISSUER}/oauth2/token"

#: xAI 官方 Grok CLI 注册的公共客户端；不是为线簿单独签发的应用身份。
CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"

#: 身份 + 离线刷新 + 账号型 API 访问用途。
SCOPE = "openid profile email offline_access grok-cli:access api:access"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"

#: 账号型访问用途；授权结果里至少要包含其中一个，否则这枚令牌调不了模型。
USAGE_SCOPES = ("grok-cli:access", "api:access")

TOKEN_TIMEOUT_SECONDS = 30.0
DEFAULT_INTERVAL_SECONDS = 5.0
DEFAULT_DEVICE_TTL_SECONDS = 600.0
SLOW_DOWN_STEP_SECONDS = 5.0


class XAIAuthError(RuntimeError):
    """授权或续期没有完成；``code`` 供上层区分失败原因，消息可直接展示给作者。"""

    def __init__(self, code: str, message: str, fix: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.fix = fix


@dataclass(slots=True)
class _XAIFlow:
    """一次设备码登录的进行中状态；同一供应商可以并行多次。"""

    device_code: str
    #: 服务端要求的轮询间隔；``slow_down`` 会把它继续拉长。
    interval: float
    expires_at: float
    next_poll_at: float
    outcome: AuthOutcome | None = None


class XAIGrokAuthProvider:
    """Grok 订阅登录：设备码申请 → 浏览器确认 → 轮询换取可续期凭据。"""

    id = "xai"
    name = "xAI（Grok）"

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._transport = transport
        self._clock = clock

    # -- 登录 -------------------------------------------------------------

    async def begin(self) -> AuthStart:
        payload = await self._post_form(
            DEVICE_CODE_URL,
            {"client_id": CLIENT_ID, "scope": SCOPE},
            "申请登录码",
        )
        device_code = _required(payload, "device_code", "申请登录码")
        user_code = _required(payload, "user_code", "申请登录码")
        complete = _text(payload.get("verification_uri_complete"))
        verification = _text(payload.get("verification_uri")) or _text(
            payload.get("verification_url")
        )
        url = complete or verification
        if not url:
            raise _error(
                "XAI_AUTH_INVALID_RESPONSE",
                "授权服务器没有返回验证地址，登录没有开始。建议：稍后重试。",
            )
        interval = _positive_seconds(payload.get("interval"), DEFAULT_INTERVAL_SECONDS)
        ttl = _positive_seconds(payload.get("expires_in"), DEFAULT_DEVICE_TTL_SECONDS)
        now = self._clock()
        flow = _XAIFlow(
            device_code=device_code,
            interval=interval,
            expires_at=now + ttl,
            next_poll_at=now + interval,
        )
        instructions = (
            "在浏览器里确认授权，完成后回到线簿。"
            if complete
            else "在浏览器里打开授权页，输入用户码并确认授权。"
        )
        return AuthStart(url=url, user_code=user_code, instructions=instructions, flow=flow)

    async def poll(self, flow: AuthFlow | None) -> AuthOutcome:
        attempt = _as_flow(flow)
        if attempt.outcome is not None:
            return attempt.outcome
        now = self._clock()
        if now >= attempt.expires_at:
            attempt.outcome = AuthOutcome(
                status="failed",
                code="XAI_AUTH_EXPIRED",
                message="登录码已过期，请重新发起登录。",
            )
            return attempt.outcome
        if now < attempt.next_poll_at:
            return AuthOutcome(status="pending")
        try:
            status, payload, body = await self._request(
                TOKEN_URL,
                {
                    "grant_type": DEVICE_GRANT,
                    "client_id": CLIENT_ID,
                    "device_code": attempt.device_code,
                },
                "查询授权结果",
            )
        except XAIAuthError as exc:
            attempt.outcome = AuthOutcome(status="failed", code=exc.code, message=_with_fix(exc))
            return attempt.outcome
        now = self._clock()
        if status == 200:
            try:
                credential = self._credential(payload, now)
            except XAIAuthError as exc:
                attempt.outcome = AuthOutcome(status="failed", code=exc.code, message=str(exc))
                return attempt.outcome
            attempt.outcome = AuthOutcome(status="succeeded", credential=credential)
            return attempt.outcome

        error = _text(payload.get("error"))
        description = _text(payload.get("error_description"))
        if error == "authorization_pending":
            attempt.next_poll_at = now + attempt.interval
            return AuthOutcome(status="pending")
        if error == "slow_down" or status == 429:
            attempt.interval += SLOW_DOWN_STEP_SECONDS
            attempt.next_poll_at = now + attempt.interval
            return AuthOutcome(status="pending")
        if error == "access_denied":
            attempt.outcome = AuthOutcome(
                status="failed",
                code="XAI_AUTH_DENIED",
                message="授权没有完成：账号没有同意这次登录。",
            )
            return attempt.outcome
        if error == "expired_token":
            attempt.outcome = AuthOutcome(
                status="failed",
                code="XAI_AUTH_EXPIRED",
                message="登录码已过期，请重新发起登录。",
            )
            return attempt.outcome
        reason = description or error or _short(body)
        attempt.outcome = AuthOutcome(
            status="failed",
            code="XAI_AUTH_EXCHANGE_FAILED",
            message=(
                f"查询授权结果失败（HTTP {status}）：{reason}。"
                "建议：重新登录；若持续失败，检查网络或 xAI 服务状态。"
            ),
        )
        return attempt.outcome

    async def cancel(self, flow: AuthFlow | None) -> None:
        # 设备码流程没有本机监听端口；取消只是不再轮询，由会话服务丢弃即可。
        return None

    # -- 续期 -------------------------------------------------------------

    async def refresh(self, credential: SubscriptionCredential) -> SubscriptionCredential:
        if not credential.refresh_token:
            raise _error(
                "XAI_REFRESH_TOKEN_MISSING",
                "这条订阅凭据没有刷新令牌，无法续期。建议：重新登录一次。",
            )
        client_id = _text(credential.metadata.get("client_id")) or CLIENT_ID
        status, payload, body = await self._request(
            TOKEN_URL,
            {
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": credential.refresh_token,
            },
            "续期",
        )
        if status >= 400:
            raise _error(
                "XAI_AUTH_EXCHANGE_FAILED",
                f"续期失败（HTTP {status}）：{_short(body)}。"
                "建议：重新登录；若持续失败，检查这个账号的订阅状态。",
            )
        access = _required(payload, "access_token", "续期")
        metadata = dict(credential.metadata)
        metadata["client_id"] = client_id
        metadata.setdefault("issuer", ISSUER)
        return SubscriptionCredential(
            auth_provider=self.id,
            access_token=access,
            refresh_token=_text(payload.get("refresh_token")) or credential.refresh_token,
            expires_at=_expiry(payload, access, self._clock()),
            account_id=credential.account_id,
            account_label=credential.account_label,
            metadata=metadata,
        )

    # -- 内部 -------------------------------------------------------------

    def _credential(self, payload: Mapping[str, Any], now: float) -> SubscriptionCredential:
        access = _required(payload, "access_token", "换取令牌")
        refresh = _required(payload, "refresh_token", "换取令牌")
        granted = _text(payload.get("scope"))
        if granted and not set(granted.split()) & set(USAGE_SCOPES):
            raise _error(
                "XAI_AUTH_SCOPE_MISSING",
                "这次授权没有包含使用 Grok 额度的权限。"
                "建议：确认账号有 SuperGrok 或 X Premium+ 订阅后重新登录，"
                "或改用 API Key 连接。",
            )
        claims = _decode_jwt(_text(payload.get("id_token"))) or _decode_jwt(access) or {}
        return SubscriptionCredential(
            auth_provider=self.id,
            access_token=access,
            refresh_token=refresh,
            expires_at=_expiry(payload, access, now),
            account_id=_text(claims.get("sub")),
            account_label=_text(claims.get("email"))
            or _text(claims.get("preferred_username"))
            or _text(claims.get("name")),
            metadata={"client_id": CLIENT_ID, "scope": granted or SCOPE, "issuer": ISSUER},
        )

    async def _post_form(self, url: str, data: dict[str, str], action: str) -> dict[str, Any]:
        status, payload, body = await self._request(url, data, action)
        if status >= 400:
            raise _error(
                "XAI_AUTH_EXCHANGE_FAILED",
                f"{action}失败（HTTP {status}）：{_short(body)}。"
                "建议：稍后重试；若持续失败，检查网络或 xAI 服务状态。",
            )
        return payload

    async def _request(
        self, url: str, data: dict[str, str], action: str
    ) -> tuple[int, dict[str, Any], str]:
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=TOKEN_TIMEOUT_SECONDS
            ) as client:
                response = await client.post(
                    url,
                    data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.InvalidURL as exc:
            raise _error(
                "XAI_AUTH_NETWORK_FAILED",
                f"{action}没有完成：代理配置无法解析（InvalidURL）。",
                fix="检查 HTTP_PROXY、HTTPS_PROXY 与 NO_PROXY 设置后重启应用再试。",
            ) from exc
        except httpx.HTTPError as exc:
            raise _error(
                "XAI_AUTH_NETWORK_FAILED",
                f"{action}没有完成：连不上 auth.x.ai（{type(exc).__name__}）。",
                fix=(
                    "确认当前网络能访问 x.ai；需要代理时，开启系统代理，"
                    "或在启动应用前设置 HTTPS_PROXY（例如 http://127.0.0.1:7890）后重试。"
                ),
            ) from exc
        body = response.text
        try:
            parsed = response.json()
        except ValueError:
            parsed = None
        if response.status_code == 200 and not isinstance(parsed, dict):
            raise _error(
                "XAI_AUTH_INVALID_RESPONSE",
                f"{action}的响应不是 JSON，登录没有完成。",
            )
        return response.status_code, parsed if isinstance(parsed, dict) else {}, body


def _as_flow(flow: AuthFlow | None) -> _XAIFlow:
    if not isinstance(flow, _XAIFlow):
        raise XAIAuthError(
            "XAI_AUTH_STATE_LOST", "这次登录的进行中状态缺失，无法继续；请重新发起登录。"
        )
    return flow


def _error(code: str, message: str, *, fix: str = "") -> XAIAuthError:
    return XAIAuthError(code, message, fix)


def _with_fix(error: XAIAuthError) -> str:
    return f"{error}建议：{error.fix}" if error.fix else str(error)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _required(payload: Mapping[str, Any], name: str, action: str) -> str:
    value = _text(payload.get(name))
    if not value:
        raise _error("XAI_AUTH_INVALID_RESPONSE", f"{action}的响应缺少 {name}，登录没有完成。")
    return value


def _positive_seconds(value: object, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return default
    return max(1.0, float(value))


def _expiry(payload: Mapping[str, Any], access: str, now: float) -> float:
    expires_in = payload.get("expires_in")
    if isinstance(expires_in, int | float) and not isinstance(expires_in, bool):
        return now + float(expires_in)
    claims = _decode_jwt(access)
    exp = claims.get("exp") if claims is not None else None
    if isinstance(exp, int | float) and not isinstance(exp, bool) and exp > 0:
        return float(exp)
    raise _error("XAI_AUTH_INVALID_RESPONSE", "拿不到凭据的有效期，登录没有完成。")


def _decode_jwt(token: str) -> dict[str, Any] | None:
    """只解出载荷：令牌是经 TLS 从令牌端点取回的，这里不重复验签。"""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        raw = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        claims = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    return claims if isinstance(claims, dict) else None


def _short(text: str, limit: int = 200) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[:limit] + "…"
