"""三状态投影与提醒指纹：只陈述应用能确定的事实。

状态按单个节点的事实聚合，不因为「别的章节还有未归属正文」就把所有节点判成需核对。
提醒指纹只纳入相关内容：作者暂缓一次提醒后，改标题、在引文前插入文字、刷新页面都不
应重新提醒；关联片段、构思意图、关联目标或核对依据变化时才重新提醒。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from hastings.workspace.story_bindings import Anchor
from hastings.workspace.story_models import ProseBinding, StoryVariant

__all__ = [
    "AttentionView",
    "BindingFact",
    "FindingFact",
    "Status",
    "binding_fact",
    "node_attention",
    "node_status",
    "reminder_visible",
    "warning_fingerprint",
]

Status = Literal["ready", "draft", "attention"]
#: 需核对的原因；与状态分开返回，界面据此说明「哪一侧变了」。
Reason = Literal[
    "review_finding",
    "anchor_missing",
    "anchor_ambiguous",
    "anchor_unavailable",
    "not_checked",
    "plan_changed",
    "prose_changed",
]

_VALID_ANCHORS = ("exact", "relocated")


@dataclass(frozen=True, slots=True)
class BindingFact:
    """一条关联参与状态与指纹计算的事实；不含绝对偏移与页面位置。"""

    binding_id: str
    document_id: str
    path: str
    clue_id: str | None
    anchor: str
    current_span_digest: str | None
    baseline_contract_digest: str | None
    baseline_span_digest: str | None


@dataclass(frozen=True, slots=True)
class FindingFact:
    """一条未解决的核对结果；身份之外还要比较内容摘要。"""

    finding_id: str
    content_digest: str


def binding_fact(binding: ProseBinding, anchor: Anchor) -> BindingFact:
    return BindingFact(
        binding_id=binding.id,
        document_id=binding.document_id,
        path=binding.path,
        clue_id=binding.clue_id,
        anchor=anchor.state,
        current_span_digest=(
            None
            if anchor.span is None
            else hashlib.sha256(anchor.span.quote.encode("utf-8")).hexdigest()
        ),
        baseline_contract_digest=binding.contract_digest,
        baseline_span_digest=binding.span_digest or None,
    )


def node_status(
    bindings: Sequence[BindingFact], *, has_open_finding: bool, current_contract: str
) -> tuple[Status, tuple[Reason, ...]]:
    """单个节点的状态与原因；``ready`` 只表示关联与最近基准一致。"""
    reasons: set[Reason] = set()
    if has_open_finding:
        reasons.add("review_finding")
    for item in bindings:
        if item.anchor == "missing":
            reasons.add("anchor_missing")
        elif item.anchor == "ambiguous":
            reasons.add("anchor_ambiguous")
        elif item.anchor not in _VALID_ANCHORS:
            reasons.add("anchor_unavailable")
        if item.baseline_contract_digest is None or item.baseline_span_digest is None:
            # 能定位不代表已确认符合构思；缺少基准仍需要核对。
            reasons.add("not_checked")
            continue
        if item.baseline_contract_digest != current_contract:
            reasons.add("plan_changed")
        if item.baseline_span_digest != item.current_span_digest:
            reasons.add("prose_changed")
    if reasons:
        return "attention", tuple(sorted(reasons))
    return ("ready", ()) if bindings else ("draft", ())


def warning_fingerprint(
    *,
    workspace_id: str,
    variant_id: str,
    node_id: str,
    current_contract: str,
    bindings: Sequence[BindingFact],
    findings: Sequence[FindingFact],
) -> str:
    """提醒指纹：同一差异在无关改动后保持相同，相关内容变化后必然不同。"""
    payload = {
        "workspace_id": workspace_id,
        "variant_id": variant_id,
        "node_id": node_id,
        "contract": current_contract,
        "bindings": [
            {
                "binding_id": item.binding_id,
                "document_id": item.document_id,
                "path": item.path,
                "clue_id": item.clue_id,
                # 精确与唯一重定位是同一有效结果：插入无关文字不产生新提醒。
                "anchor_state": "valid" if item.anchor in _VALID_ANCHORS else item.anchor,
                "current_span_digest": item.current_span_digest,
                "baseline_contract_digest": item.baseline_contract_digest,
                "baseline_span_digest": item.baseline_span_digest,
            }
            for item in sorted(bindings, key=lambda row: row.binding_id)
        ],
        "findings": [
            {"id": item.finding_id, "content_digest": item.content_digest}
            for item in sorted(findings, key=lambda row: row.finding_id)
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def reminder_visible(current_fingerprint: str, deferred_fingerprint: str | None) -> bool:
    """暂缓只隐藏同一份提醒；指纹变化后重新突出展示。"""
    return current_fingerprint != deferred_fingerprint


class AttentionView(BaseModel):
    """单个节点的提醒读模型；状态与提醒是否突出展示分开表达。"""

    node_id: str
    status: Status
    reasons: list[Reason] = Field(default_factory=list)
    fingerprint: str
    reminder_visible: bool
    binding_ids: list[str] = Field(default_factory=list)


def node_attention(
    variant: StoryVariant,
    node_id: str,
    *,
    workspace_id: str,
    bindings: Sequence[BindingFact],
    findings: Sequence[FindingFact],
    contract: str,
) -> AttentionView:
    status, reasons = node_status(
        bindings, has_open_finding=bool(findings), current_contract=contract
    )
    fingerprint = warning_fingerprint(
        workspace_id=workspace_id,
        variant_id=variant.id,
        node_id=node_id,
        current_contract=contract,
        bindings=bindings,
        findings=findings,
    )
    receipt = variant.attention_receipts.get(node_id)
    return AttentionView(
        node_id=node_id,
        status=status,
        reasons=list(reasons),
        fingerprint=fingerprint,
        reminder_visible=reminder_visible(
            fingerprint, receipt.fingerprint if receipt is not None else None
        ),
        binding_ids=[item.binding_id for item in bindings],
    )
