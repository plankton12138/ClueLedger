"""生产运行时件：真实 Clock 与 IdGenerator（Phase 14 T14.8）。

端口在 ``application/ports``（Phase 11 冻结）；此前只有测试桩，服务端装配
（V2 REST 面）第一次需要真实实现。UUID 派生 ID 满足端口契约：全局稳定、
不可复用、形如 ``<prefix>_<唯一后缀>``。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

__all__ = ["SystemClock", "UuidIdGenerator"]


class SystemClock:
    """``Clock`` 的真实实现：UTC 墙钟。"""

    def now(self) -> datetime:
        return datetime.now(UTC)


class UuidIdGenerator:
    """``IdGenerator`` 的真实实现：UUID4 派生，进程内外都不复用。"""

    def new_id(self, prefix: str) -> str:
        return f"{prefix}_{uuid4().hex[:16]}"
