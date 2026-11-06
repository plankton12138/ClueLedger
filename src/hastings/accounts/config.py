"""账号服务配置；由 server 入口从参数与环境装配。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

__all__ = ["AccountsConfig", "SmtpSettings", "normalize_origin"]


def normalize_origin(value: str) -> str:
    """来源只比较 scheme + host + port，忽略尾部斜杠与路径。"""
    parts = urlsplit(value.strip())
    if not parts.scheme or not parts.netloc:
        return value.strip().rstrip("/")
    return f"{parts.scheme}://{parts.netloc}".lower()


@dataclass(frozen=True, slots=True)
class SmtpSettings:
    host: str
    port: int
    username: str
    password: str
    from_address: str
    starttls: bool = True


@dataclass(frozen=True, slots=True)
class AccountsConfig:
    database_path: Path
    public_base_url: str
    mail_outbox: Path
    allowed_origins: tuple[str, ...] = field(default_factory=tuple)
    smtp: SmtpSettings | None = None
    cookie_name: str = "clue_session"
    session_ttl_seconds: int = 30 * 24 * 3600
    verification_ttl_seconds: int = 10 * 60
    reset_ttl_seconds: int = 10 * 60
    code_max_attempts: int = 5
    trust_proxy: bool = False

    @property
    def public_base(self) -> str:
        return self.public_base_url.rstrip("/")

    @property
    def public_origin(self) -> str:
        return normalize_origin(self.public_base_url)

    @property
    def cookie_secure(self) -> bool:
        return urlsplit(self.public_base_url).scheme.lower() == "https"

    @property
    def allowed_origin_set(self) -> frozenset[str]:
        return frozenset({self.public_origin, *(normalize_origin(o) for o in self.allowed_origins)})

    def allows_origin(self, origin: str, host: str | None) -> bool:
        """来源必须是公共地址、显式允许项，或与当前 Host 同源。"""
        normalized = origin.strip().rstrip("/")
        if not normalized or normalized == "null":
            return False
        if normalized.lower() in self.allowed_origin_set:
            return True
        if not host:
            return False
        netloc = urlsplit(normalized).netloc
        return bool(netloc) and netloc.lower() == host.lower()
