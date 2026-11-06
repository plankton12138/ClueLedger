"""``/api/auth/*`` 接口：只处理 HTTP 语义，账号判断在 ``AuthService``。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from hastings.accounts.config import AccountsConfig
from hastings.accounts.errors import AuthError
from hastings.accounts.middleware import principal_from_state
from hastings.accounts.runtime import AuthRuntime

__all__ = ["create_auth_router", "register_auth_error_handlers"]


class RegisterBody(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


class VerifyEmailBody(BaseModel):
    token: str = Field(max_length=512)


class ResendBody(BaseModel):
    email: str = Field(max_length=254)


class LoginBody(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


class ForgotBody(BaseModel):
    email: str = Field(max_length=254)


class ResetBody(BaseModel):
    token: str = Field(max_length=512)
    new_password: str = Field(max_length=128)


class UserView(BaseModel):
    user_id: str
    email: str
    verified: bool


class MeResponse(BaseModel):
    mode: Literal["local", "server"]
    authenticated: bool
    user: UserView | None
    csrf_token: str | None


class LoginResponse(BaseModel):
    user: UserView
    csrf_token: str


class AcceptedResponse(BaseModel):
    status: Literal["accepted"] = "accepted"


class VerifyResponse(BaseModel):
    verified: bool
    email: str


class ResetResponse(BaseModel):
    reset: bool


def _user_view(user_id: str, email: str, verified: bool) -> UserView:
    return UserView(user_id=user_id, email=email, verified=verified)


def _check_origin(request: Request, config: AccountsConfig) -> None:
    """公开认证接口没有会话可校验 CSRF，至少要拒绝跨站来源。"""
    origin = request.headers.get("origin")
    if origin is None or config.allows_origin(origin, request.headers.get("host")):
        return
    raise AuthError(
        code="AUTH_ORIGIN_REJECTED",
        where="auth.origin",
        why="请求来源不是本站",
        fix="从线簿页面重新操作，不要在其他网站提交",
        status_code=403,
    )


def _client_key(request: Request, config: AccountsConfig) -> str:
    if config.trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client is not None else "unknown"


def _not_enabled() -> AuthError:
    return AuthError(
        code="AUTH_NOT_ENABLED",
        where="auth.mode",
        why="当前运行模式未启用邮箱账号",
        fix="本地使用无需登录；公网部署请以 server 模式启动",
        status_code=404,
    )


def create_auth_router(auth: AuthRuntime | None) -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    def runtime() -> AuthRuntime:
        if auth is None:
            raise _not_enabled()
        return auth

    @router.get("/me", response_model=MeResponse)
    def me(request: Request) -> MeResponse:
        if auth is None:
            return MeResponse(mode="local", authenticated=True, user=None, csrf_token=None)
        principal = auth.service.authenticate(request.cookies.get(auth.config.cookie_name))
        if principal is None:
            return MeResponse(mode="server", authenticated=False, user=None, csrf_token=None)
        return MeResponse(
            mode="server",
            authenticated=True,
            user=_user_view(principal.user_id, principal.email, verified=True),
            csrf_token=principal.csrf_token,
        )

    @router.post("/register", response_model=AcceptedResponse, status_code=202)
    def register(body: RegisterBody, request: Request) -> AcceptedResponse:
        rt = runtime()
        _check_origin(request, rt.config)
        rt.service.register(body.email, body.password, _client_key(request, rt.config))
        return AcceptedResponse()

    @router.post("/verify-email", response_model=VerifyResponse)
    def verify_email(body: VerifyEmailBody, request: Request) -> VerifyResponse:
        rt = runtime()
        _check_origin(request, rt.config)
        account = rt.service.verify_email(body.token)
        return VerifyResponse(verified=True, email=account.email)

    @router.post("/resend-verification", response_model=AcceptedResponse, status_code=202)
    def resend_verification(body: ResendBody, request: Request) -> AcceptedResponse:
        rt = runtime()
        _check_origin(request, rt.config)
        rt.service.resend_verification(body.email, _client_key(request, rt.config))
        return AcceptedResponse()

    @router.post("/login", response_model=LoginResponse)
    def login(body: LoginBody, request: Request, response: Response) -> LoginResponse:
        rt = runtime()
        _check_origin(request, rt.config)
        result = rt.service.login(body.email, body.password, _client_key(request, rt.config))
        response.set_cookie(
            key=rt.config.cookie_name,
            value=result.token,
            max_age=rt.config.session_ttl_seconds,
            httponly=True,
            samesite="lax",
            secure=rt.config.cookie_secure,
            path="/",
        )
        return LoginResponse(
            user=_user_view(result.user.user_id, result.user.email, result.user.verified),
            csrf_token=result.csrf_token,
        )

    @router.post("/logout", status_code=204)
    def logout(request: Request, response: Response) -> Response:
        rt = runtime()
        principal = principal_from_state(request.state)
        if principal is None:
            raise AuthError(
                code="AUTH_SESSION_REQUIRED",
                where="auth.session",
                why="当前没有可退出的会话",
                fix="刷新页面后重试",
                status_code=401,
            )
        rt.service.logout(principal)
        response.delete_cookie(rt.config.cookie_name, path="/")
        response.status_code = 204
        return response

    @router.post("/forgot-password", response_model=AcceptedResponse, status_code=202)
    def forgot_password(body: ForgotBody, request: Request) -> AcceptedResponse:
        rt = runtime()
        _check_origin(request, rt.config)
        rt.service.forgot_password(body.email, _client_key(request, rt.config))
        return AcceptedResponse()

    @router.post("/reset-password", response_model=ResetResponse)
    def reset_password(body: ResetBody, request: Request) -> ResetResponse:
        rt = runtime()
        _check_origin(request, rt.config)
        rt.service.reset_password(body.token, body.new_password)
        return ResetResponse(reset=True)

    return router


def register_auth_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AuthError)
    async def handle_auth_error(request: Request, exc: AuthError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "")
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.code,
                "message": exc.message,
                "request_id": str(request_id),
            },
        )
