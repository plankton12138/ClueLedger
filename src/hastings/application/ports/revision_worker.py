"""Revision Worker 端口（T15.6）：把「生成补丁」从执行编排中隔离。

Worker 只看到当前步骤的 Repair Context（``StepContext``），只被允许返回
批准范围内的提案（``PatchProposal``）；范围守卫在领域层
``execution.validate_proposal`` 强制执行，Worker 端口本身不做信任假设
（四透镜②：硬约束放守卫层，端口只定义对话语言）。

第一版实现是测试里的 Fake Worker（返回可预测补丁）；真实 Reviser/Writer
在后续阶段接入同一端口，编排层零改动。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from hastings.domain.changes.execution import PatchProposal, StepContext

__all__ = ["RevisionWorker"]


@runtime_checkable
class RevisionWorker(Protocol):
    """修订工作者：按步骤上下文提案补丁（同步、无副作用假设）。"""

    def propose(self, context: StepContext) -> PatchProposal:
        """为一个修订步提案；返回空提案表示该步无需修改（合法空结果）。"""
