"""订阅授权会话（V09-1）。

订阅登录不是一次请求，而是一段时间：打开授权页、等作者在浏览器里同意、拿回回调、
换取令牌、核对这次授权确实覆盖订阅权益。这段时间里的状态是临时的，不该写进配置——
保存失败或作者取消之后，磁盘上不应该留下一个「半个账号」。

因此这里只管三件事：

- **会话**：创建时向供应商适配要一个授权地址；之后每次查询都去问适配一次进度。
  会话自带有效期，超时视为失效，不再接受保存。
- **归属**：授权成功只是拿到了凭据，凭据要等连接保存成功才被取走；取走一次就作废，
  同一份授权不会变成两个连接。
- **脱敏**：对外只给授权地址、设备验证码、账号标识与错误码，令牌永远不出这一层。

供应商差异（浏览器授权、设备验证码、动态注册、稳定设备标识）全部落在
``SubscriptionAuthProvider`` 实现里；这一层不认识任何一家供应商。
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from hastings.server.model_credential import SubscriptionCredential, mask_account
from hastings.workspace.errors import WorkspaceError

__all__ = [
    "AuthFlow",
    "AuthOutcome",
    "AuthProviderInfo",
    "AuthSessionView",
    "AuthStart",
    "AuthStatus",
    "ModelAuthService",
    "RefreshableSubscriptionProvider",
    "SubscriptionAuthProvider",
]

#: 会话进度；``expired`` 只在查询时由有效期推出，适配不会自己报这个状态。
AuthStatus = Literal["pending", "succeeded", "failed", "cancelled", "expired"]

#: 适配的一次轮询结果；``pending`` 表示「作者还在浏览器里」。
OutcomeStatus = Literal["pending", "succeeded", "failed"]


@dataclass(frozen=True, slots=True)
class AuthStart:
    """开始一次授权时给作者看的东西。"""

    url: str
    #: 设备验证码流程才有的短码；浏览器流程留空。
    user_code: str = ""
    instructions: str = ""
    #: 这次尝试的进行中状态（verifier / state / 回调监听），由适配创建、服务转交。
    #: 一个供应商可以同时有多个会话，所以状态不能挂在供应商对象上。
    flow: AuthFlow | None = None


@dataclass(frozen=True, slots=True)
class AuthOutcome:
    """一次进度查询的结果；成功时携带凭据，失败时携带错误码。"""

    status: OutcomeStatus
    credential: SubscriptionCredential | None = None
    code: str = ""
    message: str = ""


@dataclass(frozen=True, slots=True)
class AuthProviderInfo:
    id: str
    name: str


@runtime_checkable
class AuthFlow(Protocol):
    """一次授权进行中的状态；内容由适配自己定义，服务只负责原样转交。"""


@runtime_checkable
class SubscriptionAuthProvider(Protocol):
    """一家订阅来源的授权适配：只描述流程，不描述界面。"""

    @property
    def id(self) -> str: ...

    @property
    def name(self) -> str: ...

    def begin(self) -> AuthStart:
        """发起授权并返回浏览器地址；本地回调监听由适配自己持有。"""

    async def poll(self, flow: AuthFlow | None) -> AuthOutcome:
        """查询一次进度；可以重复调用，成功之后再调用仍应返回同一结果。"""

    async def cancel(self, flow: AuthFlow | None) -> None:
        """放弃这次授权并释放临时资源；已结束的会话调用应静默成功。"""


@runtime_checkable
class RefreshableSubscriptionProvider(Protocol):
    """能续期的订阅来源；不支持续期时登录仍成立，只是到期要重新登录。"""

    async def refresh(self, credential: SubscriptionCredential) -> SubscriptionCredential: ...


@dataclass(frozen=True, slots=True)
class AuthSessionView:
    """会话的对外形状：够界面显示进度，不够拿去调用供应商。"""

    id: str
    provider_id: str
    provider_name: str
    status: AuthStatus
    auth_url: str = ""
    user_code: str = ""
    instructions: str = ""
    expires_at: float = 0.0
    account: str = ""
    code: str = ""
    message: str = ""


@dataclass(slots=True)
class _Session:
    id: str
    provider: SubscriptionAuthProvider
    status: AuthStatus
    start: AuthStart
    expires_at: float
    #: 这次授权的进行中状态；由适配创建，服务只转交，不解释内容。
    flow: AuthFlow | None = None
    credential: SubscriptionCredential | None = None
    code: str = ""
    message: str = ""
    consumed: bool = False

    def view(self) -> AuthSessionView:
        credential = self.credential
        return AuthSessionView(
            id=self.id,
            provider_id=self.provider.id,
            provider_name=self.provider.name,
            status=self.status,
            auth_url=self.start.url,
            user_code=self.start.user_code,
            instructions=self.start.instructions,
            expires_at=self.expires_at,
            account=mask_account(credential.account_label, credential.account_id)
            if credential is not None
            else "",
            code=self.code,
            message=self.message,
        )


class ModelAuthService:
    """订阅授权会话的登记处；一个进程内一份，进程退出即清空。"""

    def __init__(
        self,
        *,
        providers: Mapping[str, SubscriptionAuthProvider],
        clock: Callable[[], float] = time.time,
        ttl_seconds: float = 600.0,
    ) -> None:
        self._providers = dict(providers)
        self._clock = clock
        self._ttl = ttl_seconds
        self._sessions: dict[str, _Session] = {}

    # -- 供应商 -----------------------------------------------------------

    def providers(self) -> tuple[AuthProviderInfo, ...]:
        return tuple(
            AuthProviderInfo(id=item.id, name=item.name) for item in self._providers.values()
        )

    def provider_name(self, provider_id: str) -> str:
        provider = self._providers.get(provider_id)
        return provider.name if provider is not None else provider_id

    # -- 会话 -------------------------------------------------------------

    async def create(self, provider_id: str) -> AuthSessionView:
        """发起一次授权；同一个供应商可以有多个并行会话。"""
        provider = self._providers.get(provider_id)
        if provider is None:
            raise WorkspaceError(
                code="MODEL_AUTH_PROVIDER_UNKNOWN",
                where="model.auth",
                why=f"没有可用的订阅来源：{provider_id!r}",
                fix="刷新订阅来源列表后重新选择；暂不支持该服务的订阅登录",
                details={"provider": provider_id},
            )
        await self._purge()
        try:
            start = provider.begin()
        except WorkspaceError:
            raise
        except Exception as exc:  # 适配起不来（端口被占用等）不能变成 500
            raise WorkspaceError(
                code="MODEL_AUTH_UNAVAILABLE",
                where="model.auth",
                why=f"无法开始这次授权：{exc}",
                fix="稍后重试；若持续失败，检查本机回调端口是否被别的程序占用",
                details={"provider": provider_id},
            ) from exc
        session = _Session(
            id=os.urandom(12).hex(),
            provider=provider,
            status="pending",
            start=start,
            expires_at=self._clock() + self._ttl,
            flow=start.flow,
        )
        self._sessions[session.id] = session
        return session.view()

    async def status(self, session_id: str) -> AuthSessionView:
        """查询进度；``pending`` 时向适配要一次最新结果。"""
        session = self._require(session_id)
        if session.status == "pending":
            if self._expired(session):
                await self._expire(session, "授权会话已超时，请重新登录")
            else:
                await self._poll(session)
        return session.view()

    async def cancel(self, session_id: str) -> AuthSessionView:
        """取消授权并清理临时状态；之后查询同一 ID 视为不存在。"""
        session = self._require(session_id)
        if session.status == "pending":
            with contextlib.suppress(Exception):
                await session.provider.cancel(session.flow)
            session.status = "cancelled"
            session.credential = None
        view = session.view()
        self._sessions.pop(session_id, None)
        return view

    # -- 续期 -------------------------------------------------------------

    async def refresh(self, credential: SubscriptionCredential) -> SubscriptionCredential:
        """按凭据里的来源续期；不知道来源或来源不支持续期时明确报错。"""
        provider = self._providers.get(credential.auth_provider)
        if not isinstance(provider, RefreshableSubscriptionProvider):
            raise WorkspaceError(
                code="MODEL_AUTH_REFRESH_UNSUPPORTED",
                where="model.auth",
                why=f"订阅来源 {credential.auth_provider!r} 不支持自动续期",
                fix="在「模型设置」里重新登录这个连接",
                details={"provider": credential.auth_provider},
            )
        return await provider.refresh(credential)

    # -- 归属 -------------------------------------------------------------

    def peek(self, session_id: str, *, provider_id: str = "") -> SubscriptionCredential:
        """取一份可用凭据但不作废（测试模型、读取模型列表时用）。"""
        session = self._require(session_id)
        self._require_usable(session, provider_id)
        if session.consumed:
            # 已经归属给某个连接的授权不能再拿出来用：否则同一份登录会变成两个连接。
            raise WorkspaceError(
                code="MODEL_AUTH_SESSION_USED",
                where="model.auth",
                why="这次授权已经用于保存另一个连接",
                fix="重新登录一次，或直接使用已保存的连接",
                details={"session": session_id},
            )
        assert session.credential is not None  # _require_usable 已保证
        return session.credential

    def consume(self, session_id: str, *, provider_id: str = "") -> SubscriptionCredential:
        """把凭据归属给刚保存的连接；一次授权只能用一次。"""
        credential = self.peek(session_id, provider_id=provider_id)
        session = self._sessions[session_id]
        session.consumed = True
        return credential

    # -- 内部 -------------------------------------------------------------

    async def _poll(self, session: _Session) -> None:
        try:
            outcome = await session.provider.poll(session.flow)
        except WorkspaceError:
            raise
        except Exception as exc:  # 适配内部故障不能变成「一直转圈」
            session.status = "failed"
            session.code = getattr(exc, "code", type(exc).__name__)
            session.message = f"授权查询失败：{exc}"
            return
        if outcome.status == "pending":
            return
        if outcome.status == "failed":
            session.status = "failed"
            session.code = outcome.code or "MODEL_AUTH_FAILED"
            session.message = outcome.message or "授权没有完成"
            return
        if outcome.credential is None:
            session.status = "failed"
            session.code = "MODEL_AUTH_INVALID"
            session.message = "授权返回成功但没有凭据"
            return
        session.status = "succeeded"
        session.credential = outcome.credential

    def _require(self, session_id: str) -> _Session:
        session = self._sessions.get(session_id)
        if session is None:
            raise WorkspaceError(
                code="MODEL_AUTH_SESSION_NOT_FOUND",
                where="model.auth",
                why="授权会话不存在，可能已取消、已超时或进程重启过",
                fix="重新发起一次登录",
                details={"session": session_id},
            )
        return session

    def _require_usable(self, session: _Session, provider_id: str) -> None:
        if self._expired(session):
            session.status = "expired"
            raise WorkspaceError(
                code="MODEL_AUTH_SESSION_EXPIRED",
                where="model.auth",
                why="授权会话已超时，凭据不再可用",
                fix="重新发起一次登录",
                details={"session": session.id},
            )
        if provider_id and provider_id != session.provider.id:
            raise WorkspaceError(
                code="MODEL_AUTH_PROVIDER_MISMATCH",
                where="model.auth",
                why=f"这次授权来自 {session.provider.name}，与连接选择的订阅来源不一致",
                fix="按连接的订阅来源重新登录，或改回原来的来源",
                details={"session": session.id, "provider": provider_id},
            )
        if session.status != "succeeded" or session.credential is None:
            raise WorkspaceError(
                code="MODEL_AUTH_SESSION_NOT_COMPLETED",
                where="model.auth",
                why="这次登录还没有完成",
                fix="在浏览器里完成授权后再保存连接",
                details={"session": session.id, "status": session.status},
            )

    def _expired(self, session: _Session) -> bool:
        if session.status == "expired":
            return True
        return session.status in ("pending", "succeeded") and self._clock() >= session.expires_at

    async def _expire(self, session: _Session, message: str) -> None:
        """把会话标记为过期并放掉它手里的资源（主要是本机回调端口）。"""
        session.status = "expired"
        session.message = message
        with contextlib.suppress(Exception):
            await session.provider.cancel(session.flow)

    async def _purge(self) -> None:
        """清掉已过期的会话：临时状态不该无限堆积。

        清理要把适配手上的回调监听一起关掉。只丢会话不关监听，端口和线程会一直攒着，
        最后表现为「登录再也打不开」。
        """
        now = self._clock()
        stale = [
            session_id
            for session_id, session in self._sessions.items()
            if session.status != "succeeded" and now >= session.expires_at
        ]
        for session_id in stale:
            session = self._sessions.pop(session_id, None)
            if session is not None:
                await self._expire(session, "授权会话已超时，请重新登录")
