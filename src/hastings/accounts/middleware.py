"""请求标识与登录门。

请求标识加在所有 ``/api/*`` 响应上，认证失败的 JSON 也能带同一个编号。
登录门只做「有没有有效会话」和「写请求是否来自本站」；资源归属在 P2 逐项接入。
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from http.cookies import CookieError, SimpleCookie
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from hastings.accounts.config import AccountsConfig
from hastings.accounts.service import AuthService, Principal
from hastings.identity import bind_user_id, reset_user_id

__all__ = ["AuthGuardMiddleware", "RequestContextMiddleware", "principal_from_state"]

_PUBLIC_PATHS = frozenset({"/api/health"})
_PUBLIC_AUTH_PATHS = frozenset(
    {
        "/api/auth/register",
        "/api/auth/verify-email",
        "/api/auth/resend-verification",
        "/api/auth/login",
        "/api/auth/me",
        "/api/auth/forgot-password",
        "/api/auth/reset-password",
    }
)
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _request_id() -> str:
    return "req_" + secrets.token_hex(8)


def _message(code: str, where: str, why: str, fix: str) -> str:
    return f"[{code}] {where}：{why}。建议：{fix}"


def _payload(code: str, where: str, why: str, fix: str, request_id: str) -> dict[str, str]:
    return {"code": code, "message": _message(code, where, why, fix), "request_id": request_id}


def _cookie_value(header: str | None, name: str) -> str | None:
    if not header:
        return None
    jar = SimpleCookie()
    try:
        jar.load(header)
    except CookieError:
        return None
    morsel = jar.get(name)
    return None if morsel is None else str(morsel.value)


_ACCESS = logging.getLogger("clue.access")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _state(scope: Scope) -> dict[str, Any]:
    state = scope.get("state")
    if isinstance(state, dict):
        return state
    return {}


async def _reject(
    scope: Scope, receive: Receive, send: Send, status_code: int, payload: dict[str, str]
) -> None:
    response = JSONResponse(status_code=status_code, content=payload)
    await response(scope, receive, send)


class RequestContextMiddleware:
    """为每个 HTTP 请求生成稳定编号，并写入响应头。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = Headers(scope=scope).get("x-request-id", "")
        request_id = incoming if _REQUEST_ID.fullmatch(incoming) else _request_id()
        state = _state(scope)
        state["request_id"] = request_id
        scope = dict(scope)
        scope["state"] = state

        path = str(scope.get("path", ""))
        is_api = path.startswith("/api/")
        logged = (is_api or path.startswith("/v1/")) and not path.endswith("/health")
        started = time.perf_counter()
        status = 0

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
                headers = MutableHeaders(scope=message)
                headers.append("X-Request-ID", request_id)
                if is_api:
                    # 私有数据不进浏览器缓存：共享设备退出后不能从缓存取回上一个账号的响应。
                    headers["Cache-Control"] = "no-store"
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            if logged:
                principal = state.get("principal")
                user_id = getattr(principal, "user_id", "") if principal is not None else ""
                duration = (time.perf_counter() - started) * 1000
                _ACCESS.info(
                    "%s %s %d %.1fms",
                    scope.get("method", ""),
                    path,
                    status,
                    duration,
                    extra={
                        "request_id": request_id,
                        "method": str(scope.get("method", "")),
                        "path": path,
                        "status": status,
                        "duration_ms": round(duration, 1),
                        "user_id": user_id,
                    },
                )


class AuthGuardMiddleware:
    """server 模式的会话门：公开认证接口与健康检查之外都要求已登录。"""

    def __init__(self, app: ASGIApp, *, service: AuthService, config: AccountsConfig) -> None:
        self.app = app
        self._service = service
        self._config = config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path", "")).rstrip("/") or "/"
        if not path.startswith("/api/") or path in _PUBLIC_AUTH_PATHS or path in _PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        token = _cookie_value(headers.get("cookie"), self._config.cookie_name)
        principal = await asyncio.to_thread(self._service.authenticate, token)
        request_id = str(_state(scope).get("request_id", ""))
        if principal is None:
            await _reject(
                scope,
                receive,
                send,
                401,
                _payload(
                    "AUTH_SESSION_REQUIRED",
                    "auth.session",
                    "需要登录后才能继续",
                    "登录后重试；已填写的内容会保留在浏览器的草稿里",
                    request_id,
                ),
            )
            return

        method = str(scope.get("method", "GET")).upper()
        if method in _WRITE_METHODS:
            origin = headers.get("origin")
            if origin is not None and not self._config.allows_origin(origin, headers.get("host")):
                await _reject(
                    scope,
                    receive,
                    send,
                    403,
                    _payload(
                        "AUTH_ORIGIN_REJECTED",
                        "auth.origin",
                        "请求来源不是本站",
                        "从线簿页面重新操作，不要在其他网站提交",
                        request_id,
                    ),
                )
                return
            supplied = headers.get("x-csrf-token", "").encode("utf-8", "replace")
            if not secrets.compare_digest(supplied, principal.csrf_token.encode("utf-8")):
                await _reject(
                    scope,
                    receive,
                    send,
                    403,
                    _payload(
                        "AUTH_CSRF_FAILED",
                        "auth.csrf",
                        "页面校验令牌缺失或已过期",
                        "刷新页面后重试；未提交的内容仍保留在草稿里",
                        request_id,
                    ),
                )
                return

        state = _state(scope)
        state["principal"] = principal
        scope = dict(scope)
        scope["state"] = state
        identity_token = bind_user_id(principal.user_id)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_user_id(identity_token)


def principal_from_state(state: Any) -> Principal | None:
    principal = getattr(state, "principal", None)
    return principal if isinstance(principal, Principal) else None
