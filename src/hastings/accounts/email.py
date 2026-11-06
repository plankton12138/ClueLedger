"""邮件投递：生产走 SMTP，未配置时写入文件投递目录。

文件投递只用于本地与测试。邮件里的链接等同于登录凭证，不能进入作品正文、
错误响应或结构化日志；文件投递为了本地可用会打印链接，生产配置 SMTP 后不再打印。
"""

from __future__ import annotations

import json
import logging
import smtplib
import time
from dataclasses import asdict, dataclass
from email.message import EmailMessage as SmtpMessage
from pathlib import Path
from typing import Protocol

from hastings.accounts.config import SmtpSettings

__all__ = [
    "FileEmailSender",
    "OutgoingEmail",
    "SmtpEmailSender",
    "read_outbox",
]

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OutgoingEmail:
    to: str
    subject: str
    body: str
    created_at: int


class EmailSender(Protocol):
    def send(self, message: OutgoingEmail) -> None: ...


class FileEmailSender:
    def __init__(self, outbox: Path) -> None:
        self.outbox = Path(outbox)

    def send(self, message: OutgoingEmail) -> None:
        self.outbox.mkdir(parents=True, exist_ok=True)
        name = f"{message.created_at}-{time.time_ns()}.json"
        (self.outbox / name).write_text(
            json.dumps(asdict(message), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _LOG.info("邮件已写入文件投递：%s（%s）\n%s", self.outbox / name, message.to, message.body)


class SmtpEmailSender:
    def __init__(self, settings: SmtpSettings) -> None:
        self._settings = settings

    def send(self, message: OutgoingEmail) -> None:
        settings = self._settings
        envelope = SmtpMessage()
        envelope["From"] = settings.from_address
        envelope["To"] = message.to
        envelope["Subject"] = message.subject
        envelope.set_content(message.body)
        with smtplib.SMTP(settings.host, settings.port, timeout=20) as client:
            client.ehlo()
            if settings.starttls:
                client.starttls()
                client.ehlo()
            if settings.username:
                client.login(settings.username, settings.password)
            client.send_message(envelope)


def read_outbox(outbox: Path) -> list[OutgoingEmail]:
    """测试与本地诊断用：按写入顺序读出文件投递。"""
    if not outbox.is_dir():
        return []
    messages: list[OutgoingEmail] = []
    for path in sorted(outbox.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        messages.append(
            OutgoingEmail(
                to=str(data["to"]),
                subject=str(data["subject"]),
                body=str(data["body"]),
                created_at=int(data["created_at"]),
            )
        )
    return messages
