"""构思与正文的核对读模型：语义契约摘要、关联状态与三状态提醒。

这一层只回答应用能确定的事：节点契约是否变化、关联引文是否仍在、作者是否已经暂缓过
同一份提醒。语义是否真的冲突交给模型核对，不在这里伪装成已经判断过了。

v1.0.7 起不再有「必须先选同步处理方式才能保存」的闸门，也没有七种中间状态：每个节点
只有 ``ready / draft / attention``，原因单独返回。
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, Field

from hastings.workspace.files import FileView, Workspace
from hastings.workspace.story_attention import (
    BindingFact,
    FindingFact,
    binding_fact,
    node_status,
    reminder_visible,
    warning_fingerprint,
)
from hastings.workspace.story_bindings import Anchor, anchor_state
from hastings.workspace.story_changes import object_digest
from hastings.workspace.story_knowledge import effective_knowledge
from hastings.workspace.story_models import ProseBinding, StoryNode, StoryVariant

__all__ = [
    "AttentionNodeView",
    "AttentionCounts",
    "StoryAttentionView",
    "binding_state",
    "build_attention_view",
    "contract_digest",
    "contract_projection",
    "current_span",
]

#: 一条关联相对最近核对基准的状态；只在节点内部聚合，不再作为界面状态。
BindingSyncState = Literal[
    "aligned",
    "outline_ahead",
    "prose_ahead",
    "diverged",
    "unchecked",
]


class AttentionNodeView(BaseModel):
    node_id: str
    title: str
    status: Literal["ready", "draft", "attention"]
    reasons: list[str] = Field(default_factory=list)
    fingerprint: str
    reminder_visible: bool
    #: 作者暂缓这份提醒时的时间，仅用于说明「稍后处理过」。
    deferred_at: str = ""
    binding_ids: list[str] = Field(default_factory=list)
    chapter_paths: list[str] = Field(default_factory=list)


class AttentionCounts(BaseModel):
    ready: int = 0
    draft: int = 0
    attention: int = 0
    deferred: int = 0


class StoryAttentionView(BaseModel):
    variant_id: str
    variant_revision: str
    ledger_revision: str = ""
    view_revision: str = ""
    counts: AttentionCounts
    nodes: list[AttentionNodeView] = Field(default_factory=list)


def _applicable_constraints(
    variant: StoryVariant, node: StoryNode, subjects: set[str]
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for constraint in variant.constraints.values():
        scope = constraint.scope
        if (
            scope.kind == "book"
            or (scope.kind == "node" and scope.target_id == node.id)
            or (scope.kind == "character" and scope.target_id in subjects)
        ):
            result.append(constraint.model_dump(mode="json"))
    return sorted(result, key=lambda item: str(item["id"]))


def contract_projection(variant: StoryVariant, node: StoryNode) -> dict[str, object]:
    """正文必须兑现的节点内容；标题、阶段顺序等展示字段不进入契约。"""
    knowledge = effective_knowledge(variant, [node.id])
    subjects = {change.subject_id for change in knowledge}
    event_times = [
        {
            "before": relation.before_node_id,
            "after": relation.after_node_id,
        }
        for relation in variant.event_times
        if node.id in (relation.before_node_id, relation.after_node_id)
    ]
    clue_plans = variant.clue_plans
    return {
        "action": node.action,
        "change": node.change,
        "time_note": node.time_note,
        "planned_chapter_number": node.planned_chapter_number,
        "event_span": (
            node.event_span.model_dump(mode="json") if node.event_span is not None else None
        ),
        "clue_uses": [
            {
                "clue_id": use.clue_id,
                "roles": list(use.roles),
                "presentation": use.presentation,
                "mode": use.mode,
                "speaker_id": use.speaker_id,
                "expected_interpretation": use.expected_interpretation,
                # 线索用途属于作者安排；它改变时，承诺的读者理解也变了。
                "kind": clue_plans[use.clue_id].kind if use.clue_id in clue_plans else None,
            }
            for use in node.clue_uses
        ],
        "knowledge": [change.model_dump(mode="json") for change in knowledge],
        "event_times": event_times,
        "constraints": _applicable_constraints(variant, node, subjects),
    }


def contract_digest(variant: StoryVariant, node: StoryNode) -> str:
    return object_digest(contract_projection(variant, node))


def _digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def current_span(binding: ProseBinding, text: str) -> tuple[int, int, str] | None:
    """关联引文在当前正文里的位置与区间摘要；位置失效时返回 None。"""
    if (
        0 <= binding.start < binding.end <= len(text)
        and text[binding.start : binding.end] == binding.quote
    ):
        return binding.start, binding.end, _digest_text(binding.quote)
    if binding.quote and text.count(binding.quote) == 1:
        start = text.find(binding.quote)
        if start >= 0:
            return start, start + len(binding.quote), _digest_text(binding.quote)
    return None


def _bound_digest(binding: ProseBinding) -> str:
    return binding.span_digest or _digest_text(binding.quote)


def _prose_digest(binding: ProseBinding, file: FileView | None) -> str:
    if file is None:
        return ""
    span = current_span(binding, file.text)
    return span[2] if span is not None else ""


def binding_state(binding: ProseBinding, contract: str, prose: str) -> str:
    """一条关联相对最近基准的状态；没有基准时为 ``unchecked``。"""
    if binding.contract_digest is None:
        return "unchecked"
    outline_changed = binding.contract_digest != contract
    prose_changed = _bound_digest(binding) != prose
    if outline_changed and prose_changed:
        return "diverged"
    if outline_changed:
        return "outline_ahead"
    if prose_changed:
        return "prose_ahead"
    return "aligned"


def _view_revision(variant: StoryVariant, nodes: list[AttentionNodeView]) -> str:
    return object_digest(
        [
            variant.revision,
            [[row.node_id, row.status, row.fingerprint, row.reminder_visible] for row in nodes],
        ]
    )


def build_attention_view(
    workspace: Workspace,
    variant: StoryVariant,
    *,
    chapter_path: str | None = None,
    ledger_revision: str = "",
) -> StoryAttentionView:
    """按节点投影三状态；读不到正文仍要给出可解释的状态，不把失败当成就绪。"""
    files: dict[str, FileView | None] = {}

    def file_of(path: str) -> FileView | None:
        if path not in files:
            try:
                files[path] = workspace.read(path)
            except Exception:  # noqa: BLE001 - 读不到正文时状态按关联不可用处理
                files[path] = None
        return files[path]

    by_node: dict[str, list[ProseBinding]] = defaultdict(list)
    for binding in variant.bindings.values():
        if binding.node_id is not None:
            by_node[binding.node_id].append(binding)

    chapters = {
        entry.path: entry.chapter_number
        for entry in workspace.catalog().chapters
        if entry.exists and entry.chapter_number is not None and entry.path
    }
    # 本版没有持久化「未解决核对问题」的存储：讨论中的发现不作为常驻状态。
    findings: dict[str, tuple[FindingFact, ...]] = {}

    rows: list[AttentionNodeView] = []
    for node in sorted(variant.nodes.values(), key=lambda item: item.id):
        bindings = sorted(by_node.get(node.id, []), key=lambda row: row.id)
        if chapter_path is not None and all(binding.path != chapter_path for binding in bindings):
            continue
        facts: list[BindingFact] = []
        for binding in bindings:
            file = file_of(binding.path)
            anchor = (
                Anchor(state="unavailable")
                if file is None
                else anchor_state(binding, file.text, file.digest)
            )
            facts.append(binding_fact(binding, anchor))
        contract = contract_digest(variant, node)
        node_findings = findings.get(node.id, ())
        status, reasons = node_status(
            facts,
            has_open_finding=bool(node_findings),
            current_contract=contract,
        )
        fingerprint = warning_fingerprint(
            workspace_id=str(workspace.root),
            variant_id=variant.id,
            node_id=node.id,
            current_contract=contract,
            bindings=facts,
            findings=node_findings,
        )
        receipt = variant.attention_receipts.get(node.id)
        rows.append(
            AttentionNodeView(
                node_id=node.id,
                title=node.title,
                status=status,
                reasons=list(reasons),
                fingerprint=fingerprint,
                reminder_visible=reminder_visible(
                    fingerprint, receipt.fingerprint if receipt is not None else None
                ),
                deferred_at=receipt.noted_at if receipt is not None else "",
                binding_ids=[row.id for row in bindings],
                chapter_paths=sorted({row.path for row in bindings if row.path in chapters}),
            )
        )

    counts = AttentionCounts()
    for row in rows:
        setattr(counts, row.status, getattr(counts, row.status) + 1)
        if not row.reminder_visible:
            counts.deferred += 1
    return StoryAttentionView(
        variant_id=variant.id,
        variant_revision=variant.revision,
        ledger_revision=ledger_revision,
        view_revision=_view_revision(variant, rows),
        counts=counts,
        nodes=rows,
    )
