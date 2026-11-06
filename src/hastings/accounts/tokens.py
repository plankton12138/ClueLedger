"""一次性凭证：明文只进邮件，数据库只存哈希。"""

from __future__ import annotations

import hashlib
import secrets

__all__ = ["hash_token", "new_token"]


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
