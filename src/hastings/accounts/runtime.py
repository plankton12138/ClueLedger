"""账号服务的装配结果：数据库、存储与认证服务共用一套配置。"""

from __future__ import annotations

import time
from dataclasses import dataclass

from hastings.accounts.config import AccountsConfig
from hastings.accounts.db import AccountDatabase
from hastings.accounts.email import EmailSender, FileEmailSender, SmtpEmailSender
from hastings.accounts.service import AuthService
from hastings.accounts.store import AccountsStore

__all__ = ["AuthRuntime", "build_auth_runtime"]


@dataclass(frozen=True, slots=True)
class AuthRuntime:
    config: AccountsConfig
    database: AccountDatabase
    store: AccountsStore
    service: AuthService


def build_auth_runtime(config: AccountsConfig, *, sender: EmailSender | None = None) -> AuthRuntime:
    database = AccountDatabase(config.database_path)
    database.initialize()
    store = AccountsStore(database)
    store.prune_rate_limits(int(time.time()) - 2 * 86400)
    if sender is None:
        if config.smtp is not None:
            sender = SmtpEmailSender(config.smtp)
        else:
            sender = FileEmailSender(config.mail_outbox)
    return AuthRuntime(
        config=config,
        database=database,
        store=store,
        service=AuthService(store, sender, config),
    )
