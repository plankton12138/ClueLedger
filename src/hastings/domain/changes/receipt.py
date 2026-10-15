"""批准凭证 ApprovalReceipt（T15.5，modules/change-impact.md §3）。

Analyzed → Approved 是变更工作流唯一的作者授权点。凭证把「作者确认过什么」
固化为机器可校对的快照：意图、基础版本、范围、影响报告与修订计划的指纹。
签发后任何一项漂移（计划改组、报告重算、base 变化）都使凭证失效，必须重新
签发——授权不靠提示靠校验，硬约束放命令层（四透镜②）。

设计约束：
- **一次批准授权一次执行**：批准即消费（consumed），同提交内原子生效；
  消费或撤销都留痕（resolved_at / resolved_reason），审计可回溯；
- **指纹是纯投影**：``confirm_fingerprint`` 只吃领域模型，不触达 Repository，
  也不含 analyzed_by / at 等易变审计字段——重算产生相同内容不致使凭证失效，
  内容漂移才失效（四透镜③：重算不背锅，漂移才重签）；
- 本模块是纯函数：不做模型调用、不触达 Repository（与 planning 同构）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any

from hastings.domain.changes import error_codes as ec
from hastings.domain.changes.errors import violation
from hastings.domain.changes.impact import (
    SEVERITY_MUST_FIX,
    SEVERITY_REVIEW,
    SEVERITY_SHOULD_FIX,
    ImpactReport,
)
from hastings.domain.changes.models import SCHEMA_VERSION, ChangeSet
from hastings.domain.changes.planning import RevisionPlan

__all__ = [
    "EVENT_RECEIPT_CONSUMED",
    "EVENT_RECEIPT_ISSUED",
    "EVENT_RECEIPT_REVOKED",
    "RECEIPT_ACTIVE",
    "RECEIPT_CONSUMED",
    "RECEIPT_REVOKED",
    "RECEIPT_STATUSES",
    "ApprovalReceipt",
    "confirm_brief",
    "confirm_fingerprint",
    "mark_receipt",
    "require_consumable",
]

RECEIPT_ACTIVE = "active"
RECEIPT_CONSUMED = "consumed"
RECEIPT_REVOKED = "revoked"
RECEIPT_STATUSES = frozenset((RECEIPT_ACTIVE, RECEIPT_CONSUMED, RECEIPT_REVOKED))

# 业务事件（领域契约）：随 Commit 同事务写入 Outbox（ADR-007）
EVENT_RECEIPT_ISSUED = "changes.receipt.issued"
EVENT_RECEIPT_CONSUMED = "changes.receipt.consumed"
EVENT_RECEIPT_REVOKED = "changes.receipt.revoked"


def confirm_fingerprint(change_set: ChangeSet, report: ImpactReport, plan: RevisionPlan) -> str:
    """作者确认内容的指纹：范围 + 报告 + 计划的规范序列化摘要。

    排除身份与审计字段（plan_id / analyzed_by / at 等）：同内容重算指纹不变，
    任何实质漂移（项增删改级、步骤拆并、范围调整）指纹必变。
    """
    plan_body = plan.to_content()
    for identity in ("plan_id", "change_set_id", "base_commit_id"):
        plan_body.pop(identity, None)
    payload = {
        "intent": change_set.intent,
        "base_commit_id": change_set.base_commit_id,
        "scope": change_set.scope.to_content(),
        "report": {
            "items": [item.to_content() for item in report.items],
            "truncated": [t.to_content() for t in report.truncated],
        },
        "plan": plan_body,
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def confirm_brief(
    change_set: ChangeSet, report: ImpactReport, plan: RevisionPlan
) -> dict[str, Any]:
    """作者确认页的展示快照：变化原因、基础版本、允许对象、Patch 数、风险。"""
    return {
        "intent": change_set.intent,
        "base_commit_id": change_set.base_commit_id,
        "scope": change_set.scope.to_content(),
        "step_count": len(plan.steps),
        "target_count": sum(len(step.target_artifact_ids) for step in plan.steps),
        "unassigned_count": len(plan.unassigned),
        "item_count": len(report.items),
        "must_fix_targets": sorted(
            {item.target_artifact_id for item in report.items if item.severity == SEVERITY_MUST_FIX}
        ),
        "should_fix_count": sum(1 for item in report.items if item.severity == SEVERITY_SHOULD_FIX),
        "review_count": sum(1 for item in report.items if item.severity == SEVERITY_REVIEW),
        "risk_count": len(plan.risk_acceptances),
    }


@dataclass(frozen=True, slots=True)
class ApprovalReceipt:
    """批准凭证（kind ``changes.approval_receipt``）：作者授权的机器凭据。

    一个变更集至多一个凭证 Artifact；重新签发复用同一 Artifact（新 Revision），
    历史版本可在审计中回溯。
    """

    receipt_id: str
    change_set_id: str
    base_commit_id: str
    fingerprint: str
    brief: dict[str, Any] = field(default_factory=dict)
    status: str = RECEIPT_ACTIVE
    issued_by: str = ""
    issued_at: str = ""
    resolved_at: str = ""
    resolved_reason: str = ""
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.status not in RECEIPT_STATUSES:
            raise violation(
                ec.CHANGES_INVALID_COMMAND,
                "changes.receipt",
                f"凭证 status 非法：{self.status!r}",
                f"status 必须是 {sorted(RECEIPT_STATUSES)} 之一",
                status=self.status,
            )

    def to_content(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "receipt_id": self.receipt_id,
            "change_set_id": self.change_set_id,
            "base_commit_id": self.base_commit_id,
            "fingerprint": self.fingerprint,
            "brief": self.brief,
            "status": self.status,
            "issued_by": self.issued_by,
            "issued_at": self.issued_at,
            "resolved_at": self.resolved_at,
            "resolved_reason": self.resolved_reason,
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> ApprovalReceipt:
        return cls(
            receipt_id=str(content.get("receipt_id", "")),
            change_set_id=str(content.get("change_set_id", "")),
            base_commit_id=str(content.get("base_commit_id", "")),
            fingerprint=str(content.get("fingerprint", "")),
            brief=dict(content.get("brief") or {}),
            status=str(content.get("status", RECEIPT_ACTIVE)),
            issued_by=str(content.get("issued_by", "")),
            issued_at=str(content.get("issued_at", "")),
            resolved_at=str(content.get("resolved_at", "")),
            resolved_reason=str(content.get("resolved_reason", "")),
            schema_version=int(content.get("schema_version", SCHEMA_VERSION)),
        )


def mark_receipt(receipt: ApprovalReceipt, status: str, *, at: str, note: str) -> ApprovalReceipt:
    """凭证状态落定（消费/撤销）：留痕，不原地修改。"""
    return replace(receipt, status=status, resolved_at=at, resolved_reason=note)


def require_consumable(
    receipt: ApprovalReceipt,
    *,
    change_set: ChangeSet,
    fingerprint: str,
    where: str,
) -> None:
    """凭证可用于批准的完整校验链；每种失效情形都有独立错误码与下一步指引。"""
    if receipt.change_set_id != change_set.change_set_id:
        raise violation(
            ec.CHANGES_RECEIPT_INVALID,
            where,
            f"凭证 {receipt.receipt_id!r} 属于变更集 {receipt.change_set_id!r}，"
            f"不能用于变更集 {change_set.change_set_id!r}",
            "每个变更集使用自己签发的凭证；跨变更集复用一律拒绝",
            receipt_change_set_id=receipt.change_set_id,
        )
    if receipt.status == RECEIPT_CONSUMED:
        raise violation(
            ec.CHANGES_RECEIPT_CONSUMED,
            where,
            f"凭证已于 {receipt.resolved_at} 被消费（一次批准只授权一次执行）",
            "重新签发批准凭证再批准（执行失败重开时也须重新确认）",
            receipt_id=receipt.receipt_id,
            resolved_reason=receipt.resolved_reason,
        )
    if receipt.status == RECEIPT_REVOKED:
        raise violation(
            ec.CHANGES_RECEIPT_REVOKED,
            where,
            f"凭证已于 {receipt.resolved_at} 被作者撤销：{receipt.resolved_reason}",
            "如需继续执行，由作者重新审阅并签发新凭证",
            receipt_id=receipt.receipt_id,
        )
    if receipt.base_commit_id != change_set.base_commit_id:
        raise violation(
            ec.CHANGES_RECEIPT_STALE,
            where,
            f"凭证绑定的基础版本 {receipt.base_commit_id!r} 与变更集当前的"
            f" {change_set.base_commit_id!r} 不一致（base 已变化）",
            "在新基础版本上重新分析、生成计划并重新签发凭证",
            receipt_base=receipt.base_commit_id,
            expected=change_set.base_commit_id,
        )
    if receipt.fingerprint != fingerprint:
        raise violation(
            ec.CHANGES_RECEIPT_STALE,
            where,
            "签发后影响报告或修订计划已变化（范围/内容漂移），凭证过期",
            "重新审阅最新报告与计划（确认页展示变化），再重新签发凭证",
            receipt_id=receipt.receipt_id,
        )
