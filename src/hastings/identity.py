"""当前请求的账号身份（v1.4 P2）。

只保存不可变的 ``user_id``：作品注册、任务归属与模型服务都从这里判断「现在是谁」。
HTTP 中间件在进入应用前设置、退出后恢复；后台任务若脱离请求上下文，必须显式传入
拥有者，不能依赖这里的默认值。
"""

from __future__ import annotations

from contextvars import ContextVar, Token

__all__ = ["bind_user_id", "current_user_id", "reset_user_id"]

_current_user_id: ContextVar[str | None] = ContextVar("clueledger_user_id", default=None)


def current_user_id() -> str | None:
    return _current_user_id.get()


def bind_user_id(user_id: str) -> Token[str | None]:
    return _current_user_id.set(user_id)


def reset_user_id(token: Token[str | None]) -> None:
    _current_user_id.reset(token)
