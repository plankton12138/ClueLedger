"""服务端日志：本地可读，生产输出 JSON；访问日志不包含查询串或正文。"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

__all__ = ["JsonFormatter", "configure_logging"]

_ACCESS_FIELDS = ("request_id", "method", "path", "status", "duration_ms", "user_id")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in _ACCESS_FIELDS:
            value = getattr(record, key, None)
            if value not in (None, ""):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(*, json_mode: bool) -> None:
    root = logging.getLogger()
    handler = logging.StreamHandler(sys.stdout)
    if json_mode:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.handlers = [handler]
    root.setLevel(logging.INFO)
