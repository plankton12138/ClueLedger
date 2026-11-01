"""v1.0.7 P1：三状态投影与提醒指纹。

状态由当前事实计算，暂缓记录只影响提醒是否突出展示；用例比较实际指纹，不检查文案。
"""

from __future__ import annotations

from pathlib import Path

from hastings.workspace.story_attention import (
    AttentionView,
    BindingFact,
    FindingFact,
    binding_fact,
    node_attention,
    node_status,
    reminder_visible,
    warning_fingerprint,
)
from hastings.workspace.story_bindings import Anchor, Span
from hastings.workspace.story_models import (
    AcknowledgeAttention,
    AddNode,
    AttentionReceipt,
    BindProse,
    ProseBinding,
    StoryChangeSet,
    StoryCluePlan,
    StoryNode,
    StoryVariant,
)
from hastings.workspace.story_store import StoryStore

CONTRACT = "contract-a"
QUOTE_DIGEST = "span-a"


def fact(
    binding_id: str = "b1",
    *,
    anchor: str = "exact",
    baseline_contract: str | None = CONTRACT,
    baseline_span: str | None = QUOTE_DIGEST,
    current_span: str | None = QUOTE_DIGEST,
    clue_id: str | None = None,
) -> BindingFact:
    return BindingFact(
        binding_id=binding_id,
        document_id="doc_1",
        path="正文/0001 雨夜.md",
        clue_id=clue_id,
        anchor=anchor,  # type: ignore[arg-type]
        current_span_digest=current_span,
        baseline_contract_digest=baseline_contract,
        baseline_span_digest=baseline_span,
    )


def status_of(
    bindings: list[BindingFact], *, has_open_finding: bool = False, contract: str = CONTRACT
) -> tuple[str, tuple[str, ...]]:
    return node_status(bindings, has_open_finding=has_open_finding, current_contract=contract)


def test_node_without_bindings_is_draft() -> None:
    assert status_of([]) == ("draft", ())


def test_unchecked_binding_needs_attention() -> None:
    status, reasons = status_of([fact(baseline_contract=None)])
    assert status == "attention"
    assert reasons == ("not_checked",)


def test_binding_matching_its_baseline_is_ready() -> None:
    assert status_of([fact()]) == ("ready", ())


def test_plan_and_prose_changes_are_separate_reasons() -> None:
    plan_changed = status_of([fact(baseline_contract="contract-old")])
    assert plan_changed == ("attention", ("plan_changed",))
    prose_changed = status_of([fact(current_span="span-new")])
    assert prose_changed == ("attention", ("prose_changed",))
    both = status_of([fact(baseline_contract="contract-old", current_span="span-new")])
    assert both == ("attention", ("plan_changed", "prose_changed"))


def test_broken_anchor_blocks_highlights_and_keeps_reference() -> None:
    assert status_of([fact(anchor="missing")]) == ("attention", ("anchor_missing",))
    assert status_of([fact(anchor="ambiguous")]) == ("attention", ("anchor_ambiguous",))


def test_relocated_anchor_is_still_ready() -> None:
    assert status_of([fact(anchor="relocated")]) == ("ready", ())


def test_open_finding_needs_attention_without_any_binding() -> None:
    assert status_of([], has_open_finding=True) == ("attention", ("review_finding",))


def test_one_broken_binding_makes_the_whole_node_need_attention() -> None:
    status, reasons = status_of([fact("b1"), fact("b2", baseline_contract=None)])
    assert status == "attention"
    assert reasons == ("not_checked",)


def fingerprint(
    *,
    contract: str = CONTRACT,
    bindings: list[BindingFact] | None = None,
    findings: list[FindingFact] | None = None,
) -> str:
    return warning_fingerprint(
        workspace_id="ws-1",
        variant_id="main",
        node_id="n1",
        current_contract=contract,
        bindings=bindings or [fact()],
        findings=findings or [],
    )


def test_fingerprint_ignores_position_shift_and_title() -> None:
    assert fingerprint() == fingerprint(bindings=[fact(anchor="relocated")])


def test_fingerprint_ignores_binding_order() -> None:
    assert fingerprint(bindings=[fact("b1"), fact("b2")]) == fingerprint(
        bindings=[fact("b2"), fact("b1")]
    )


def test_fingerprint_changes_when_contract_or_quote_changes() -> None:
    assert fingerprint() != fingerprint(contract="contract-b")
    assert fingerprint() != fingerprint(bindings=[fact(current_span="span-new")])


def test_fingerprint_changes_when_clue_target_changes() -> None:
    assert fingerprint() != fingerprint(bindings=[fact(clue_id="c1")])


def test_fingerprint_tracks_finding_content_not_just_identity() -> None:
    before = fingerprint(findings=[FindingFact("f1", "content-a")])
    after = fingerprint(findings=[FindingFact("f1", "content-b")])
    assert before != after


def test_deferred_reminder_stays_hidden_until_facts_change() -> None:
    current = fingerprint()
    assert reminder_visible(current, None) is True
    assert reminder_visible(current, current) is False
    assert reminder_visible(fingerprint(contract="contract-b"), current) is True


def variant_with(binding: ProseBinding, *, receipt: str | None = None) -> StoryVariant:
    node = StoryNode(id="n1", title="雨夜", action="抵达", event_span=None)
    data: dict[str, object] = {
        "id": "main",
        "name": "主方案",
        "revision": "rev-1",
        "nodes": {"n1": node},
        "clue_plans": {
            "c1": StoryCluePlan(clue_id="c1", kind="truth"),
        },
        "bindings": {"b1": binding},
    }
    if receipt is not None:
        data["attention_receipts"] = {
            "n1": AttentionReceipt(
                node_id="n1",
                fingerprint=receipt,
                noted_at="2026-11-01T00:00:00+08:00",
            )
        }
    return StoryVariant.model_validate(data)


def checked_binding() -> ProseBinding:
    return ProseBinding(
        id="b1",
        node_id="n1",
        document_id="doc_1",
        path="正文/0001 雨夜.md",
        digest="chapter-digest",
        span_digest=QUOTE_DIGEST,
        start=0,
        end=9,
        quote="钟停在23:00。",
        contract_digest=CONTRACT,
    )


def test_view_projects_status_and_reminder_visibility() -> None:
    view = node_attention(
        variant_with(checked_binding()),
        "n1",
        workspace_id="ws-1",
        bindings=[fact()],
        findings=[],
        contract=CONTRACT,
    )
    assert isinstance(view, AttentionView)
    assert (view.status, view.reasons, view.reminder_visible) == ("ready", [], True)

    deferred = node_attention(
        variant_with(checked_binding(), receipt=fingerprint()),
        "n1",
        workspace_id="ws-1",
        bindings=[fact()],
        findings=[],
        contract=CONTRACT,
    )
    assert (deferred.status, deferred.reminder_visible) == ("ready", False)


def test_binding_fact_reads_anchor_and_baselines_from_the_binding() -> None:
    binding = checked_binding()
    row = binding_fact(binding, Anchor(state="exact", span=Span(0, 9, binding.quote)))
    assert row.anchor == "exact"
    assert row.baseline_contract_digest == CONTRACT
    assert row.baseline_span_digest == QUOTE_DIGEST


def store_with_binding(tmp_path: Path) -> StoryStore:
    store = StoryStore(tmp_path / "state")
    store.commit_change(
        variant_id="main",
        change=StoryChangeSet(
            id="seed",
            base_revision="empty",
            operations=[
                AddNode(node=StoryNode(id="n1", title="雨夜", action="抵达")),
                BindProse(binding=checked_binding()),
            ],
        ),
        request_key="seed",
    )
    return store


def test_acknowledging_a_reminder_is_idempotent_and_undoable(tmp_path: Path) -> None:
    store = store_with_binding(tmp_path)
    revision = store.require_variant("main").revision
    change = StoryChangeSet(
        id="ack-1",
        base_revision=revision,
        operations=[AcknowledgeAttention(node_id="n1", fingerprint="fp-1")],
    )
    store.commit_change(variant_id="main", change=change, request_key="ack-1")
    variant = store.require_variant("main")
    assert variant.attention_receipts["n1"].fingerprint == "fp-1"

    again = store.commit_change(variant_id="main", change=change, request_key="ack-1")
    assert again.revision == variant.revision

    store.undo(change_id="ack-1", request_key="undo-ack")
    assert "n1" not in store.require_variant("main").attention_receipts


def test_acknowledging_updates_the_same_receipt(tmp_path: Path) -> None:
    store = store_with_binding(tmp_path)
    for index, fingerprint_value in enumerate(("fp-1", "fp-2"), start=1):
        store.commit_change(
            variant_id="main",
            change=StoryChangeSet(
                id=f"ack-{index}",
                base_revision=store.require_variant("main").revision,
                operations=[AcknowledgeAttention(node_id="n1", fingerprint=fingerprint_value)],
            ),
            request_key=f"ack-{index}",
        )
    variant = store.require_variant("main")
    assert list(variant.attention_receipts) == ["n1"]
    assert variant.attention_receipts["n1"].fingerprint == "fp-2"
