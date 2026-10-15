"""Changes Domain 的 Artifact kind 常量。"""

from __future__ import annotations

CHANGES_PREFIX = "changes."

# ChangeSet：一次「我改了一个早期设定，后面怎么办」的工作流载体
KIND_CHANGE_SET = "changes.change_set"
# ImpactReport：某次影响分析的结果（绑定 ChangeSet 与基础 Commit）
KIND_IMPACT_REPORT = "changes.impact_report"
# RevisionPlan：依据报告生成的四层修订计划（绑定同一 base Commit）
KIND_REVISION_PLAN = "changes.revision_plan"
# ApprovalReceipt：作者批准的机器凭证（绑定 base/scope/报告/计划指纹）
KIND_APPROVAL_RECEIPT = "changes.approval_receipt"
# ExecutionRecord：一次修订执行尝试的 Job 状态（每次尝试一条，含失败留痕）
KIND_EXECUTION_RECORD = "changes.execution"

CHANGES_KINDS = (
    KIND_CHANGE_SET,
    KIND_IMPACT_REPORT,
    KIND_REVISION_PLAN,
    KIND_APPROVAL_RECEIPT,
    KIND_EXECUTION_RECORD,
)


def is_changes_kind(kind: str) -> bool:
    """判断某 Repository Artifact kind 是否由 Changes Domain 持有。"""
    return kind.startswith(CHANGES_PREFIX)
