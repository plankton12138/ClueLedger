"""账号、会话与挑战的只读视图。"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Account", "SessionRecord"]


@dataclass(frozen=True, slots=True)
class Account:
    user_id: str
    email: str
    email_norm: str
    status: str
    verified_at: int | None
    created_at: int

    @property
    def verified(self) -> bool:
        return self.verified_at is not None


@dataclass(frozen=True, slots=True)
class SessionRecord:
    token_hash: str
    csrf_token: str
    expires_at: int
    last_seen_at: int
    account: Account
