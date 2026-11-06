"""写作服务到订阅代理的短期授权。

授权只说明「哪个账号在调用」，不携带模型密钥；每次受理一轮重新签发，重试沿用同一
份，因此代理可以用 ``jti`` 作为幂等请求号。浏览器永远看不到这份授权。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

__all__ = ["Grant", "mint_grant", "verify_grant"]

_PREFIX = "v1"


@dataclass(frozen=True, slots=True)
class Grant:
    user_id: str
    jti: str
    expires_at: int


def mint_grant(
    user_id: str,
    secret: str,
    *,
    ttl_seconds: int = 900,
    now: int | None = None,
) -> str:
    if not user_id or not secret:
        raise ValueError("grant requires user and secret")
    issued = int(time.time()) if now is None else now
    jti = secrets.token_hex(12)
    payload = f"{_PREFIX}:{user_id}:{issued + ttl_seconds}:{jti}"
    signature = hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{payload}:{signature}"


def verify_grant(token: str, secret: str, *, now: int | None = None) -> Grant | None:
    if not token or not secret:
        return None
    parts = token.split(":")
    if len(parts) != 5 or parts[0] != _PREFIX:
        return None
    _, user_id, expiry_text, jti, signature = parts
    payload = f"{_PREFIX}:{user_id}:{expiry_text}:{jti}"
    expected = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        expires_at = int(expiry_text)
    except ValueError:
        return None
    current = int(time.time()) if now is None else now
    if expires_at <= current:
        return None
    return Grant(user_id=user_id, jti=jti, expires_at=expires_at)
