"""0.3 已退役 HTTP 入口（V03-8.6）。

被排除的功能在 HTTP 层统一拒绝：底层服务与历史数据保留（按用例复用/只读），
但 0.3 不提供启动入口——旧 UI/API 都不能意外启动它们。

拒绝语义是 **410 Gone**：入口曾经存在、本版不再提供，不是用户写错参数。
错误码 ``FEATURE_RETIRED_IN_03`` 稳定对外，调用方按 code 分支，不解析文案。
"""

from __future__ import annotations

from typing import NoReturn

from hastings.application.errors import ViolationError

__all__ = ["FEATURE_RETIRED", "retired"]

FEATURE_RETIRED = "FEATURE_RETIRED_IN_03"


def retired(*, where: str, why: str, fix: str) -> NoReturn:
    """拒绝 0.3 已退役的入口；``fix`` 必须给出本版的替代路径。"""
    raise ViolationError(code=FEATURE_RETIRED, where=where, why=why, fix=fix)
