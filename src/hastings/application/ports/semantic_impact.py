"""可失败的语义补充；不获得仓库写端口，不替换确定性报告。"""

from typing import Any, Protocol

from hastings.domain.changes.impact import ImpactItem


class SemanticImpact(Protocol):
    def __call__(self, context: dict[str, Any]) -> tuple[ImpactItem, ...]:
        """返回带置信度的候选；失败由编排层记录为覆盖不足。"""
