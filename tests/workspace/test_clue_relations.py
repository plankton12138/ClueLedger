"""v1.0.7 P1：线索关系的数据约束、写入与撤销。

关系表达作者指定的含义；存储只保证端点、说明和方向写法合法，不判断语义是否成立。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hastings.workspace.story_changes import StoryInvalid, apply_change_set, diff_variant
from hastings.workspace.story_models import (
    AddNode,
    CluePlanInput,
    ClueRelation,
    RemoveClueRelation,
    StoryChangeSet,
    StoryCluePlan,
    StoryNode,
    StoryOperation,
    StoryVariant,
    UpsertCluePlan,
    UpsertClueRelation,
)
from hastings.workspace.story_store import StoryStore


def blank() -> StoryVariant:
    return StoryVariant(
        id="main",
        name="主方案",
        revision="rev-1",
        nodes={"n1": StoryNode(id="n1", title="雨夜", action="抵达")},
        clue_plans={
            "clock": StoryCluePlan(clue_id="clock", kind="truth"),
            "ticket": StoryCluePlan(clue_id="ticket", kind="mislead"),
            "blood": StoryCluePlan(clue_id="blood", kind=None),
        },
    )


def change(operations: list[StoryOperation], *, change_id: str = "rel-1") -> StoryChangeSet:
    return StoryChangeSet(id=change_id, base_revision="rev-1", operations=operations)


def apply(operations: list[StoryOperation]) -> StoryVariant:
    return apply_change_set(blank(), change(operations))


def relation(
    relation_id: str = "r1",
    *,
    source: str = "clock",
    target: str = "ticket",
    kind: str = "supports",
    reason: str = "存根签名与笔迹一致，增加登记时间的可信度",
) -> ClueRelation:
    return ClueRelation(
        id=relation_id,
        from_clue_id=source,
        to_clue_id=target,
        kind=kind,  # type: ignore[arg-type]
        reason=reason,
    )


def test_relation_needs_two_different_endpoints_and_a_reason() -> None:
    with pytest.raises(ValueError, match="不能指向自己"):
        relation(source="clock", target="clock")
    with pytest.raises(ValueError, match="说明不能为空"):
        relation(reason="   ")
    with pytest.raises(ValueError):
        ClueRelation(
            id="r1",
            from_clue_id="clock",
            to_clue_id="ticket",
            kind="proves",  # type: ignore[arg-type]
            reason="不支持的归类",
        )


def test_support_keeps_direction() -> None:
    after = apply([UpsertClueRelation(relation=relation())])
    stored = after.clue_relations["r1"]
    assert (stored.from_clue_id, stored.to_clue_id) == ("clock", "ticket")


def test_symmetric_relation_is_deduplicated_in_any_order() -> None:
    first = apply(
        [
            UpsertClueRelation(
                relation=relation(kind="contradicts", reason="登船后不能在 22:00 返回旅馆")
            )
        ]
    )
    reversed_row = relation(
        relation_id="r2",
        source="ticket",
        target="clock",
        kind="contradicts",
        reason="同一前提下的另一种说法",
    )
    with pytest.raises(StoryInvalid, match="关系已存在"):
        apply_change_set(
            first, change([UpsertClueRelation(relation=reversed_row)], change_id="rel-2")
        )


def test_symmetric_relation_is_stored_with_sorted_endpoints() -> None:
    after = apply(
        [
            UpsertClueRelation(
                relation=relation(source="ticket", target="clock", kind="co_misdirects")
            )
        ]
    )
    stored = after.clue_relations["r1"]
    assert (stored.from_clue_id, stored.to_clue_id) == ("clock", "ticket")


def test_relation_graph_may_contain_cycles() -> None:
    after = apply(
        [
            UpsertClueRelation(relation=relation("r1", source="clock", target="ticket")),
            UpsertClueRelation(relation=relation("r2", source="ticket", target="clock")),
        ]
    )
    assert set(after.clue_relations) == {"r1", "r2"}


def test_updating_a_relation_keeps_its_identity() -> None:
    after = apply(
        [
            UpsertClueRelation(relation=relation()),
            UpsertClueRelation(relation=relation(reason="补充依据：存根纸张与票根一致")),
        ]
    )
    assert len(after.clue_relations) == 1
    assert "纸张" in after.clue_relations["r1"].reason


def test_removing_a_relation_is_reversible() -> None:
    before = apply([UpsertClueRelation(relation=relation())])
    after = apply_change_set(
        before, change([RemoveClueRelation(relation_id="r1")], change_id="rel-2")
    )
    assert after.clue_relations == {}
    inverse = diff_variant(after, before)
    restored = apply_change_set(after, change(inverse, change_id="rel-3"))
    assert restored.clue_relations["r1"].reason == before.clue_relations["r1"].reason


def test_removing_unknown_relation_is_rejected() -> None:
    with pytest.raises(StoryInvalid, match="线索关系不存在"):
        apply([RemoveClueRelation(relation_id="missing")])


def test_relations_survive_a_store_roundtrip_and_undo(tmp_path: Path) -> None:
    store = StoryStore(tmp_path / "state")
    store.commit_change(
        variant_id="main",
        change=StoryChangeSet(
            id="seed",
            base_revision="empty",
            operations=[
                AddNode(node=StoryNode(id="n1", title="雨夜", action="抵达")),
                UpsertCluePlan(clue_id="clock", plan=CluePlanInput(kind="truth")),
                UpsertCluePlan(clue_id="ticket", plan=CluePlanInput(kind="mislead")),
            ],
        ),
        request_key="seed",
    )
    store.commit_change(
        variant_id="main",
        change=StoryChangeSet(
            id="rel-1",
            base_revision=store.require_variant("main").revision,
            operations=[UpsertClueRelation(relation=relation())],
        ),
        request_key="rel-1",
    )
    stored = store.require_variant("main")
    assert stored.clue_relations["r1"].kind == "supports"

    store.undo(change_id="rel-1", request_key="undo-rel")
    assert store.require_variant("main").clue_relations == {}


def test_temp_clue_ids_are_replaced_in_bindings_and_relations() -> None:
    from hastings.workspace.story_clue_commit import resolve_clue_references
    from hastings.workspace.story_models import BindProse, CreateClueIdentity, ProseBinding

    change_set = StoryChangeSet(
        id="temp-ids",
        base_revision="rev-1",
        operations=[
            CreateClueIdentity(temp_id="new-clock", title="停住的座钟"),
            UpsertCluePlan(clue_id="new-clock", plan=CluePlanInput(kind="truth")),
            BindProse(
                binding=ProseBinding(
                    id="b1",
                    clue_id="new-clock",
                    document_id="doc_1",
                    path="正文/0003 停住的钟.md",
                    digest="chapter-digest",
                    start=0,
                    end=9,
                    quote="钟停在23:00。",
                )
            ),
            UpsertClueRelation(
                relation=relation(
                    source="new-clock",
                    target="ticket",
                    reason="停钟与船票共同说明时间被改动",
                    kind="co_misdirects",
                )
            ),
        ],
    )
    resolved = resolve_clue_references(change_set, {"new-clock": "clue_real"})
    plan, binding, stored_relation = resolved.operations[1:]
    assert plan.clue_id == "clue_real"  # type: ignore[union-attr]
    assert binding.binding.clue_id == "clue_real"  # type: ignore[union-attr]
    assert stored_relation.relation.from_clue_id == "clue_real"  # type: ignore[union-attr]
    assert stored_relation.relation.to_clue_id == "ticket"  # type: ignore[union-attr]


def seed_identities(root: Path, rows: dict[str, str]) -> None:
    from typing import Any

    from hastings.workspace.clue_models import ClueIdentity
    from hastings.workspace.clue_store import ClueLedgerStore

    ledger = ClueLedgerStore(root)

    def apply(store: Any) -> dict[str, str]:
        for clue_id, title in rows.items():
            store.clues[clue_id] = ClueIdentity(
                clue_id=clue_id,
                title=title,
                clue_revision=f"rev_{clue_id}",
                created_at="2026-11-01T00:00:00+08:00",
                created_by="author",
            )
        return {"clues": ",".join(sorted(rows))}

    ledger.mutate(
        expected_revision=ledger.read().revision,
        request_key="seed-relation-identities",
        kind="test_identity",
        payload={},
        apply=apply,
    )


def relation_variant() -> StoryVariant:
    from hastings.workspace.story_models import UpsertClueRelation

    base = blank()
    return apply_change_set(
        base,
        change(
            [
                UpsertClueRelation(relation=relation("r1", source="clock", target="ticket")),
                UpsertClueRelation(
                    relation=relation(
                        "r2",
                        source="ticket",
                        target="blood",
                        kind="contradicts",
                        reason="同一时刻不能同时在港口与旅馆",
                    )
                ),
            ]
        ),
    )


def test_relation_view_keeps_support_direction_and_isolated_clues(tmp_path: Path) -> None:
    from hastings.workspace.clue_relations import build_relations

    root = tmp_path / "state"
    seed_identities(root, {"clock": "停住的钟", "ticket": "船票", "blood": "伞上的血迹"})
    view = build_relations(root, relation_variant())

    assert {(edge.relation_id, edge.kind) for edge in view.edges} == {
        ("r1", "supports"),
        ("r2", "contradicts"),
    }
    support = next(edge for edge in view.edges if edge.relation_id == "r1")
    assert (support.from_clue_id, support.to_clue_id) == ("clock", "ticket")
    assert {node.clue_id for node in view.nodes} == {"clock", "ticket", "blood"}
    assert view.remaining_count == 0


def test_isolated_clue_is_not_linked_by_shared_kind(tmp_path: Path) -> None:
    from hastings.workspace.clue_relations import build_relations

    root = tmp_path / "state"
    seed_identities(root, {"clock": "停住的钟", "ticket": "船票", "blood": "伞上的血迹"})
    variant = relation_variant()
    view = build_relations(root, variant, focus_clue_id="clock")

    assert [edge.relation_id for edge in view.edges] == ["r1"]
    assert {node.clue_id for node in view.nodes} == {"clock", "ticket"}


def test_focused_view_reports_hidden_neighbours(tmp_path: Path) -> None:
    from hastings.workspace.clue_relations import build_relations

    root = tmp_path / "state"
    seed_identities(root, {"clock": "停住的钟", "ticket": "船票", "blood": "伞上的血迹"})
    variant = relation_variant()
    view = build_relations(root, variant, focus_clue_id="ticket", max_neighbors=1)

    assert len(view.edges) == 1
    assert view.remaining_count == 1
    assert view.nodes[0].clue_id == "ticket"


def test_unknown_focus_is_reported(tmp_path: Path) -> None:
    import pytest

    from hastings.workspace.clue_relations import build_relations

    root = tmp_path / "state"
    seed_identities(root, {"clock": "停住的钟"})
    with pytest.raises(KeyError):
        build_relations(root, relation_variant(), focus_clue_id="missing")


def test_no_relations_is_a_legal_empty_view(tmp_path: Path) -> None:
    from hastings.workspace.clue_relations import build_relations

    root = tmp_path / "state"
    seed_identities(root, {"clock": "停住的钟"})
    view = build_relations(root, blank())

    assert (view.nodes, view.edges, view.remaining_count) == ([], [], 0)
    assert view.view_revision
