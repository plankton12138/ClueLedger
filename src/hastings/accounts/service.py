"""认证服务：注册、验证、登录、会话、找回与重置。

服务层不接触 HTTP：Cookie、来源与请求 ID 由接口层处理。这样支付进程与后台任务
可以复用同一套账号判断，而不需要伪造一个浏览器请求。
"""

from __future__ import annotations

import logging
import secrets
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

from hastings.accounts.config import AccountsConfig
from hastings.accounts.email import EmailSender, OutgoingEmail
from hastings.accounts.errors import AuthError
from hastings.accounts.models import Account
from hastings.accounts.passwords import hash_password, verify_password
from hastings.accounts.store import AccountExists, AccountsStore
from hastings.accounts.tokens import hash_token, new_token

__all__ = ["AuthService", "LoginResult", "Principal"]

_LOG = logging.getLogger(__name__)

_SESSION_TOUCH_SECONDS = 300

_RATE_LIMITS: dict[str, tuple[int, int]] = {
    "register:client": (20, 3600),
    "register:email": (5, 3600),
    "login:client": (60, 900),
    "login:email": (10, 900),
    "resend:client": (10, 3600),
    "resend:email": (3, 3600),
    "forgot:client": (10, 3600),
    "forgot:email": (3, 3600),
    "verify:client": (30, 900),
    "verify:email": (10, 900),
    "reset:client": (30, 900),
    "reset:email": (10, 900),
}


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    email: str
    csrf_token: str
    session_token_hash: str


@dataclass(frozen=True, slots=True)
class LoginResult:
    token: str
    csrf_token: str
    expires_at: int
    user: Account


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return hash_password("timing-equalizer-not-a-real-password")


def _now() -> int:
    return int(time.time())


def _invalid_email() -> AuthError:
    return AuthError(
        code="AUTH_INVALID_INPUT",
        where="auth.email",
        why="邮箱格式不正确",
        fix="填写完整的邮箱地址，例如 name@example.com",
        status_code=400,
    )


def normalize_email(raw: str) -> tuple[str, str]:
    """返回展示值与唯一比较值；不删除点号或 ``+`` 后缀。"""
    value = unicodedata.normalize("NFKC", raw or "").strip()
    if not value or len(value) > 254 or any(ch.isspace() for ch in value):
        raise _invalid_email()
    if value.count("@") != 1:
        raise _invalid_email()
    local, domain = value.rsplit("@", 1)
    if not local or len(local) > 64 or not domain or len(domain) > 189:
        raise _invalid_email()
    if domain.startswith(".") or domain.endswith(".") or ".." in domain or "." not in domain:
        raise _invalid_email()
    return value, value.casefold()


def validate_password(password: str) -> None:
    if not 8 <= len(password) <= 128:
        raise AuthError(
            code="AUTH_PASSWORD_POLICY",
            where="auth.password",
            why="密码长度需要为 8—128 个字符",
            fix="换一个更长的密码后重试",
            status_code=400,
        )


def new_user_id() -> str:
    return "usr_" + secrets.token_hex(16)


def new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


class AuthService:
    def __init__(
        self,
        store: AccountsStore,
        sender: EmailSender,
        config: AccountsConfig,
        *,
        now: Callable[[], int] = _now,
    ) -> None:
        self._store = store
        self._sender = sender
        self._config = config
        self._now = now

    def register(self, email: str, password: str, client_key: str) -> None:
        display, norm = normalize_email(email)
        validate_password(password)
        now = self._now()
        self._rate("register:client", client_key, now)
        self._rate("register:email", norm, now)
        existing = self._store.account_by_email(norm)
        if existing is not None:
            if not existing.verified:
                self._send_verification(existing, now)
            return
        try:
            account = self._store.create_account(
                user_id=new_user_id(),
                email=display,
                email_norm=norm,
                password_hash=hash_password(password),
                now=now,
            )
        except AccountExists:
            raced = self._store.account_by_email(norm)
            if raced is not None and not raced.verified:
                self._send_verification(raced, now)
            return
        self._send_verification(account, now)

    def resend_verification(self, email: str, client_key: str) -> None:
        _, norm = normalize_email(email)
        now = self._now()
        self._rate("resend:client", client_key, now)
        self._rate("resend:email", norm, now)
        account = self._store.account_by_email(norm)
        if account is not None and not account.verified:
            self._send_verification(account, now)

    def verify_email(self, email: str, code: str, client_key: str) -> Account:
        _, norm = normalize_email(email)
        now = self._now()
        self._rate("verify:client", client_key, now)
        self._rate("verify:email", norm, now)
        clean = code.strip()
        if len(clean) != 6 or not clean.isdigit():
            raise self._code_error("mismatch")
        account = self._store.account_by_email(norm)
        if account is None:
            raise self._code_error("mismatch")
        status, verified = self._store.verify_email_code(
            account.user_id,
            hash_token(clean),
            now=now,
            max_attempts=self._config.code_max_attempts,
        )
        if verified is None:
            raise self._code_error(status)
        return verified

    def login(self, email: str, password: str, client_key: str) -> LoginResult:
        _, norm = normalize_email(email)
        now = self._now()
        self._rate("login:client", client_key, now)
        self._rate("login:email", norm, now)
        account = self._store.account_by_email(norm)
        if account is None:
            verify_password(password, _dummy_hash())
            raise self._bad_credentials()
        encoded = self._store.password_hash(account.user_id)
        if encoded is None or not verify_password(password, encoded):
            raise self._bad_credentials()
        if account.status != "active":
            raise AuthError(
                code="AUTH_ACCOUNT_DISABLED",
                where="auth.login",
                why="该账号已被停用",
                fix="联系线簿支持确认账号状态",
                status_code=403,
            )
        if not account.verified:
            raise AuthError(
                code="AUTH_EMAIL_UNVERIFIED",
                where="auth.login",
                why="邮箱尚未验证",
                fix="输入注册时收到的 6 位验证码，或重新发送验证码",
                status_code=403,
            )
        token = new_token()
        csrf = new_token()
        expires_at = now + self._config.session_ttl_seconds
        self._store.create_session(
            token_hash=hash_token(token),
            user_id=account.user_id,
            csrf_token=csrf,
            now=now,
            expires_at=expires_at,
        )
        return LoginResult(token=token, csrf_token=csrf, expires_at=expires_at, user=account)

    def authenticate(self, token: str | None) -> Principal | None:
        if not token:
            return None
        now = self._now()
        record = self._store.session(hash_token(token), now)
        if record is None:
            return None
        if now - record.last_seen_at >= _SESSION_TOUCH_SECONDS:
            self._store.touch_session(record.token_hash, now)
        return Principal(
            user_id=record.account.user_id,
            email=record.account.email,
            csrf_token=record.csrf_token,
            session_token_hash=record.token_hash,
        )

    def logout(self, principal: Principal) -> None:
        self._store.revoke_session(principal.session_token_hash, self._now())

    def forgot_password(self, email: str, client_key: str) -> None:
        _, norm = normalize_email(email)
        now = self._now()
        self._rate("forgot:client", client_key, now)
        self._rate("forgot:email", norm, now)
        account = self._store.account_by_email(norm)
        if account is None:
            return
        if not account.verified:
            self._send_verification(account, now)
            return
        code = new_code()
        self._store.replace_code(
            code_hash=hash_token(code),
            user_id=account.user_id,
            purpose="reset_password",
            now=now,
            expires_at=now + self._config.reset_ttl_seconds,
        )
        minutes = max(1, self._config.reset_ttl_seconds // 60)
        self._deliver(
            account.email,
            "重置线簿密码",
            "你好：\n\n"
            f"你的线簿密码重置验证码：{code}\n\n"
            f"{minutes} 分钟内有效。请勿将验证码告诉他人。\n"
            "设置成功后，其他设备上的登录会失效。\n\n"
            "如果这不是你的操作，请忽略这封邮件。\n",
            now,
        )

    def reset_password(self, email: str, code: str, password: str, client_key: str) -> None:
        _, norm = normalize_email(email)
        validate_password(password)
        now = self._now()
        self._rate("reset:client", client_key, now)
        self._rate("reset:email", norm, now)
        clean = code.strip()
        if len(clean) != 6 or not clean.isdigit():
            raise self._code_error("mismatch")
        account = self._store.account_by_email(norm)
        if account is None:
            raise self._code_error("mismatch")
        status = self._store.reset_with_code(
            account.user_id,
            hash_token(clean),
            now=now,
            max_attempts=self._config.code_max_attempts,
            password_hash=hash_password(password),
        )
        if status != "ok":
            raise self._code_error(status)

    def _rate(self, scope: str, key: str, now: int) -> None:
        limit, window = _RATE_LIMITS[scope]
        window_start = now - now % window
        count = self._store.bump_rate_limit(scope, key, window_start)
        if count > limit:
            raise AuthError(
                code="AUTH_RATE_LIMITED",
                where="auth.rate",
                why="操作过于频繁",
                fix="稍后再试；如果一直失败，等待当前时间窗口结束后重试",
                status_code=429,
            )

    def _send_verification(self, account: Account, now: int) -> None:
        code = new_code()
        self._store.replace_code(
            code_hash=hash_token(code),
            user_id=account.user_id,
            purpose="verify_email",
            now=now,
            expires_at=now + self._config.verification_ttl_seconds,
        )
        minutes = max(1, self._config.verification_ttl_seconds // 60)
        self._deliver(
            account.email,
            "验证你的线簿邮箱",
            "你好：\n\n"
            f"你的线簿邮箱验证码：{code}\n\n"
            f"{minutes} 分钟内有效。请勿将验证码告诉他人。\n"
            "验证后即可返回网站登录。\n\n"
            "如果这不是你的操作，请忽略这封邮件。\n",
            now,
        )

    def _deliver(self, to: str, subject: str, body: str, now: int) -> None:
        try:
            self._sender.send(OutgoingEmail(to=to, subject=subject, body=body, created_at=now))
        except Exception as exc:
            _LOG.exception("认证邮件发送失败")
            raise AuthError(
                code="AUTH_EMAIL_FAILED",
                where="auth.email.send",
                why="验证邮件暂时无法发送",
                fix="稍后重新发送；如果持续失败，联系线簿支持检查邮件配置",
                status_code=503,
            ) from exc

    @staticmethod
    def _bad_credentials() -> AuthError:
        return AuthError(
            code="AUTH_INVALID_CREDENTIALS",
            where="auth.login",
            why="邮箱或密码不正确",
            fix="核对邮箱与密码后重试；忘记密码可使用找回功能",
            status_code=401,
        )

    @staticmethod
    def _code_error(status: str) -> AuthError:
        if status == "attempts":
            return AuthError(
                code="AUTH_CODE_ATTEMPTS",
                where="auth.code",
                why="验证码尝试次数过多",
                fix="重新发送验证码后再试",
                status_code=429,
            )
        return AuthError(
            code="AUTH_CODE_INVALID",
            where="auth.code",
            why="验证码无效、已使用或已过期",
            fix="核对邮箱中的 6 位验证码，或重新发送",
            status_code=400,
        )
