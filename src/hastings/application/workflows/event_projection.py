"""0.3 确定性投影消费端（V03-0.4）：切断模型调度，保留事件流。

0.2 的 Outbox 订阅者是 ``AnalysisDispatcher``——章提交事件自动创建分析
Job、再派发风格观察（作者一次保存被夹带另一项模型功能，风险表第一条）。
0.3 装配改为本投影端：

- 事件照常发布并标记（Outbox 不积压、历史事件可回看）；
- 不创建任何 Job、不调用任何模型（零隐式模型调用，I06）；
- 按事件类型计数并可按 event_id 去重，供旧自动链路分类报告核对。

切断的是**模型调度**，不是事件流本身（执行指导 §4.4）。历史事件与旧资料
保留；``AnalysisDispatcher`` 类随 ``analysis_dispatch`` 模块留作单元回归，
不再进入 0.3 装配。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["CountingProjectionSink"]


class CountingProjectionSink:
    """按事件类型计数的确定性投影消费端；重投不重复计数。"""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.seen_event_ids: set[str] = set()

    async def publish(self, event: Mapping[str, Any]) -> None:
        event_id = str(event["id"])
        if event_id in self.seen_event_ids:
            return  # 重投去重：Outbox 崩溃窗口会再次投递已发布事件
        self.seen_event_ids.add(event_id)
        event_type = str(event.get("type", "unknown"))
        self.counts[event_type] = self.counts.get(event_type, 0) + 1

    def report(self) -> dict[str, int]:
        """事件类型 → 数量的只读快照（分类报告核对用）。"""
        return dict(self.counts)
