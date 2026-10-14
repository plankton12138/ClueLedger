"""AgentRunner 端口：无状态工作者调用的唯一入口（Phase 11 冻结名称，T11.5）。

工作者（策划/撰写/修订/审计/观察）经此运行：输入 Context Package（ADR-006），
输出领域定义的 Proposal/Finding 结构。Runner 的返回绝不是提交证据（ADR-003）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

__all__ = ["AgentRunner"]


@runtime_checkable
class AgentRunner(Protocol):
    """无状态工作者运行端口。"""

    async def run(
        self, task_kind: str, context_package: Mapping[str, object]
    ) -> Mapping[str, object]:
        """运行一次工作者调用，返回 Proposal/Finding 结构化数据。

        ``task_kind`` 区分工作流（draft/repair/audit/style_learning 等）；
        失败必须可分类（违规 / 合法空 / 环境故障，DESIGN.md P8）。
        """
