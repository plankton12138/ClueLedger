"""Clock 端口：系统时间的唯一注入点（Phase 11 冻结名称，T11.5）。

禁止领域/应用代码直接调用 ``datetime.now()``；Lease 到期、时间线等
一切「现在」都经此注入，测试用 fake Clock 推进（Phase 12 T12.6 无 sleep 测试）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

__all__ = ["Clock"]


@runtime_checkable
class Clock(Protocol):
    """系统时间端口。"""

    def now(self) -> datetime:
        """返回当前时间；实现必须返回带时区的 UTC datetime。"""
