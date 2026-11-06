"""账号数据的读写：所有「先查后写」的竞争都收敛到数据库约束或单条原子语句。"""

from __future__ import annotations

import sqlite3

from hastings.accounts.db import AccountDatabase
from hastings.accounts.models import Account, SessionRecord

__all__ = ["AccountExists", "AccountsStore"]


class AccountExists(Exception):
    """邮箱唯一比较值已存在；由认证服务转为统一的「已受理」响应。"""


def _optional_int(value: object) -> int | None:
    return None if value is None else int(str(value))


def _account(row: sqlite3.Row) -> Account:
    return Account(
        user_id=str(row["user_id"]),
        email=str(row["email"]),
        email_norm=str(row["email_norm"]),
        status=str(row["status"]),
        verified_at=_optional_int(row["verified_at"]),
        created_at=int(row["created_at"]),
    )


class AccountsStore:
    def __init__(self, database: AccountDatabase) -> None:
        self._db = database

    def create_account(
        self, *, user_id: str, email: str, email_norm: str, password_hash: str, now: int
    ) -> Account:
        try:
            with self._db.write() as conn:
                conn.execute(
                    "INSERT INTO accounts "
                    "(user_id, email, email_norm, password_hash, status, verified_at, "
                    " created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, 'active', NULL, ?, ?)",
                    (user_id, email, email_norm, password_hash, now, now),
                )
        except sqlite3.IntegrityError as exc:
            raise AccountExists(email_norm) from exc
        return Account(
            user_id=user_id,
            email=email,
            email_norm=email_norm,
            status="active",
            verified_at=None,
            created_at=now,
        )

    def account_by_email(self, email_norm: str) -> Account | None:
        with self._db.read() as conn:
            row = conn.execute(
                "SELECT user_id, email, email_norm, status, verified_at, created_at "
                "FROM accounts WHERE email_norm = ?",
                (email_norm,),
            ).fetchone()
        return None if row is None else _account(row)

    def password_hash(self, user_id: str) -> str | None:
        with self._db.read() as conn:
            row = conn.execute(
                "SELECT password_hash FROM accounts WHERE user_id = ?", (user_id,)
            ).fetchone()
        return None if row is None else str(row["password_hash"])

    def account(self, user_id: str) -> Account | None:
        with self._db.read() as conn:
            row = conn.execute(
                "SELECT user_id, email, email_norm, status, verified_at, created_at "
                "FROM accounts WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return None if row is None else _account(row)

    def verify_with_challenge(self, token_hash: str, purpose: str, now: int) -> Account | None:
        """凭证消费与标记验证在同一写事务内完成，避免「已消费但未生效」。"""
        with self._db.write() as conn:
            row = conn.execute(
                "UPDATE auth_challenges SET consumed_at = ? "
                "WHERE token_hash = ? AND purpose = ? AND consumed_at IS NULL "
                "  AND expires_at > ? "
                "RETURNING user_id",
                (now, token_hash, purpose, now),
            ).fetchone()
            if row is None:
                return None
            user_id = str(row["user_id"])
            conn.execute(
                "UPDATE accounts SET verified_at = COALESCE(verified_at, ?), updated_at = ? "
                "WHERE user_id = ?",
                (now, now, user_id),
            )
            account = conn.execute(
                "SELECT user_id, email, email_norm, status, verified_at, created_at "
                "FROM accounts WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return None if account is None else _account(account)

    def reset_with_challenge(
        self, token_hash: str, purpose: str, now: int, password_hash: str
    ) -> str | None:
        """重置密码、标记邮箱已验证并撤销旧会话，三件事一次提交。"""
        with self._db.write() as conn:
            row = conn.execute(
                "UPDATE auth_challenges SET consumed_at = ? "
                "WHERE token_hash = ? AND purpose = ? AND consumed_at IS NULL "
                "  AND expires_at > ? "
                "RETURNING user_id",
                (now, token_hash, purpose, now),
            ).fetchone()
            if row is None:
                return None
            user_id = str(row["user_id"])
            conn.execute(
                "UPDATE accounts SET password_hash = ?, verified_at = COALESCE(verified_at, ?), "
                "updated_at = ? WHERE user_id = ?",
                (password_hash, now, now, user_id),
            )
            conn.execute(
                "UPDATE auth_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                (now, user_id),
            )
        return user_id

    def create_session(
        self,
        *,
        token_hash: str,
        user_id: str,
        csrf_token: str,
        now: int,
        expires_at: int,
    ) -> None:
        with self._db.write() as conn:
            conn.execute(
                "INSERT INTO auth_sessions "
                "(token_hash, user_id, csrf_token, created_at, last_seen_at, "
                " expires_at, revoked_at) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL)",
                (token_hash, user_id, csrf_token, now, now, expires_at),
            )

    def session(self, token_hash: str, now: int) -> SessionRecord | None:
        with self._db.read() as conn:
            row = conn.execute(
                "SELECT s.token_hash, s.csrf_token, s.expires_at, s.last_seen_at, "
                "       a.user_id, a.email, a.email_norm, a.status, a.verified_at, a.created_at "
                "FROM auth_sessions s JOIN accounts a ON a.user_id = s.user_id "
                "WHERE s.token_hash = ? AND s.revoked_at IS NULL AND s.expires_at > ? "
                "  AND a.status = 'active'",
                (token_hash, now),
            ).fetchone()
        if row is None:
            return None
        return SessionRecord(
            token_hash=str(row["token_hash"]),
            csrf_token=str(row["csrf_token"]),
            expires_at=int(row["expires_at"]),
            last_seen_at=int(row["last_seen_at"]),
            account=_account(row),
        )

    def touch_session(self, token_hash: str, now: int) -> None:
        with self._db.write() as conn:
            conn.execute(
                "UPDATE auth_sessions SET last_seen_at = ? WHERE token_hash = ?",
                (now, token_hash),
            )

    def revoke_session(self, token_hash: str, now: int) -> None:
        with self._db.write() as conn:
            conn.execute(
                "UPDATE auth_sessions SET revoked_at = ? "
                "WHERE token_hash = ? AND revoked_at IS NULL",
                (now, token_hash),
            )

    def revoke_all_sessions(self, user_id: str, now: int) -> None:
        with self._db.write() as conn:
            conn.execute(
                "UPDATE auth_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                (now, user_id),
            )

    def replace_challenge(
        self,
        *,
        token_hash: str,
        user_id: str,
        purpose: str,
        now: int,
        expires_at: int,
    ) -> None:
        with self._db.write() as conn:
            conn.execute(
                "UPDATE auth_challenges SET consumed_at = ? "
                "WHERE user_id = ? AND purpose = ? AND consumed_at IS NULL",
                (now, user_id, purpose),
            )
            conn.execute(
                "INSERT INTO auth_challenges "
                "(token_hash, user_id, purpose, created_at, expires_at, consumed_at) "
                "VALUES (?, ?, ?, ?, ?, NULL)",
                (token_hash, user_id, purpose, now, expires_at),
            )

    def bump_rate_limit(self, scope: str, key: str, window_start: int) -> int:
        with self._db.write() as conn:
            row = conn.execute(
                "INSERT INTO auth_rate_limits (scope, key, window_start, count) "
                "VALUES (?, ?, ?, 1) "
                "ON CONFLICT (scope, key, window_start) DO UPDATE SET count = count + 1 "
                "RETURNING count",
                (
                    scope,
                    key,
                    window_start,
                ),
            ).fetchone()
        return 0 if row is None else int(row["count"])

    def prune_rate_limits(self, before: int) -> None:
        with self._db.write() as conn:
            conn.execute("DELETE FROM auth_rate_limits WHERE window_start < ?", (before,))
