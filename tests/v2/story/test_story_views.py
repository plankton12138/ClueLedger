"""T13.7 查询投影测试：派生结果携带 Commit ID、顺序确定、绝不写回。

断言要点：
- CanonSnapshot 排除被 supersedes 的旧事实但仍列出替代关系（不变量 3）；
- NarrativeTree 与 structural_order 同序，含节拍，stale 由 Payoff 断裂推出；
- DisclosureTimeline 合并读者披露与角色认知两个通道，按结构位置排序；
- MysteryFairnessView 只列必要线索，阈值可配置，附规则 findings；
- OpenSetupsView 只列未回收伏笔，逾期仅在项目给出 deadline 时判定。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hastings.domain.repository.ids import ArtifactId, CommitId
from hastings.domain.repository.models import RelationRevision
from hastings.domain.story import error_codes as ec
from hastings.domain.story import kinds
from hastings.domain.story.canon.models import (
    Character,
    Clue,
    Foreshadow,
)
from hastings.domain.story.catalog import StoryCatalog
from hastings.domain.story.mystery.models import Disclosure, TruthFact
from hastings.domain.story.narrative.models import Beat, DisclosureSpec, NarrativeNode, StateChange
from hastings.domain.story.views import (
    ClueReveal,
    canon_snapshot,
    disclosure_timeline,
    mystery_fairness,
    narrative_tree,
    open_setups,
)

MakeCatalog = Callable[..., StoryCatalog]


def _entry(artifact_id: str, kind: str, content: dict[str, Any]) -> tuple:
    return (artifact_id, kind, content)


def _char(artifact_id: str, name: str, **kwargs: Any) -> tuple:
    return _entry(artifact_id, kinds.KIND_CHARACTER, Character(name=name, **kwargs).to_content())


def _truth(artifact_id: str, statement: str) -> tuple:
    return _entry(artifact_id, kinds.KIND_TRUTH_FACT, TruthFact(statement=statement).to_content())


def _foreshadow(artifact_id: str, setup: str, expected_payoff: str = "") -> tuple:
    return _entry(
        artifact_id,
        kinds.KIND_FORESHADOW,
        Foreshadow(setup=setup, expected_payoff=expected_payoff).to_content(),
    )


def _node(
    artifact_id: str, kind: str, *, parent_id: str | None = None, order_key: str = ""
) -> tuple:
    return _entry(
        artifact_id,
        kind,
        NarrativeNode(
            node_id=artifact_id, kind=kind, parent_id=parent_id, order_key=order_key
        ).to_content(),
    )


def _beat(
    artifact_id: str,
    *,
    parent_id: str | None = None,
    order_key: str = "",
    setups: tuple[str, ...] = (),
    payoffs: tuple[str, ...] = (),
    disclosures: tuple[DisclosureSpec, ...] = (),
    state_changes: tuple[StateChange, ...] = (),
) -> tuple:
    return _entry(
        artifact_id,
        kinds.KIND_BEAT,
        Beat(
            beat_id=artifact_id,
            parent_id=parent_id,
            order_key=order_key,
            setups=setups,
            payoffs=payoffs,
            reader_disclosures=disclosures,
            state_changes=state_changes,
        ).to_content(),
    )


def _rel(
    source: str, relation_type: str, target: str, *, metadata: dict[str, Any] | None = None
) -> RelationRevision:
    return RelationRevision(
        id=f"rel_{source}_{relation_type}_{target}",
        commit_id=CommitId("cmt_1"),
        action="add",
        source=ArtifactId(source),
        relation_type=relation_type,
        target=ArtifactId(target),
        metadata=dict(metadata or {}),
    )


# --------------------------------------------------------------------------
# CanonSnapshot
# --------------------------------------------------------------------------
def test_canon_snapshot_excludes_superseded_fact(make_catalog: MakeCatalog) -> None:
    entries = [
        _char("art_c1", "美咲", identity="旧身份"),
        _char("art_c2", "美咲", identity="新身份"),
    ]
    relations = (_rel("art_c2", kinds.REL_SUPERSEDES, "art_c1", metadata={"reason": "身份改写"}),)
    view = canon_snapshot(make_catalog(entries, relations=relations))
    assert [f.artifact_id for f in view.facts] == ["art_c2"]
    assert [(s.old_id, s.new_id, s.reason) for s in view.superseded] == [
        ("art_c1", "art_c2", "身份改写")
    ]
    assert view.commit_id == "cmt_1"


def test_canon_snapshot_carries_commit_id(make_catalog: MakeCatalog) -> None:
    entries = [_char("art_c1", "美咲")]
    view = canon_snapshot(make_catalog(entries, commit_id="cmt_7"))
    assert view.commit_id == "cmt_7"
    assert [f.artifact_id for f in view.facts] == ["art_c1"]


# --------------------------------------------------------------------------
# NarrativeTree
# --------------------------------------------------------------------------
def test_narrative_tree_nests_and_matches_structural_order(make_catalog: MakeCatalog) -> None:
    entries = [
        _node("art_series", kinds.KIND_SERIES),
        _node("art_book", kinds.KIND_BOOK, parent_id="art_series"),
        _node("art_ch1", kinds.KIND_CHAPTER, parent_id="art_book", order_key="010"),
        _beat("art_b1", parent_id="art_ch1", order_key="010"),
        _beat("art_b2", parent_id="art_ch1", order_key="020"),
    ]
    tree = narrative_tree(make_catalog(entries))
    assert tree.order == ("art_series", "art_book", "art_ch1", "art_b1", "art_b2")
    (series,) = tree.roots
    (book,) = series.children
    (chapter,) = book.children
    assert [c.artifact_id for c in chapter.children] == ["art_b1", "art_b2"]
    assert chapter.status == "ok"
    assert tree.warnings == ()


def test_narrative_tree_marks_stale_payoff(make_catalog: MakeCatalog) -> None:
    entries = [
        _node("art_ch1", kinds.KIND_CHAPTER, parent_id=None),
        _foreshadow("art_f1", "门锁上的划痕"),
        _beat("art_b1", parent_id="art_ch1", payoffs=("art_f1",), order_key="010"),
    ]
    tree = narrative_tree(make_catalog(entries))
    # 该伏笔没有任何 Setup，回收方的 Payoff 断裂 → stale 自底向上汇总到子树
    (chapter,) = tree.roots
    (beat,) = chapter.children
    assert beat.stale is True
    assert beat.status == "stale"
    assert chapter.status == "stale"


def test_narrative_tree_marks_planned_empty_node(make_catalog: MakeCatalog) -> None:
    entries = [_node("art_ch1", kinds.KIND_CHAPTER)]
    tree = narrative_tree(make_catalog(entries))
    (chapter,) = tree.roots
    assert chapter.status == "planned"
    assert chapter.children == ()


# --------------------------------------------------------------------------
# DisclosureTimeline
# --------------------------------------------------------------------------
def test_disclosure_timeline_merges_reader_and_character(make_catalog: MakeCatalog) -> None:
    entries = [
        _char("art_c1", "美咲"),
        _truth("art_t1", "门是在午夜落锁的"),
        _beat(
            "art_b1",
            parent_id="art_ch1",
            order_key="010",
            disclosures=(DisclosureSpec(fact_id="art_t1", audience="reader"),),
            state_changes=(
                StateChange(
                    kind="knowledge", value="knows", character_id="art_c1", target_id="art_t1"
                ),
            ),
        ),
        _node("art_ch1", kinds.KIND_CHAPTER, parent_id=None),
    ]
    timeline = disclosure_timeline(make_catalog(entries))
    assert [(e.fact_id, e.audience, e.channel, e.position) for e in timeline.events] == [
        ("art_t1", "reader", "reveal", 0),
        ("art_t1", "art_c1", "knowledge", 0),
    ]


def test_disclosure_timeline_orders_by_structure_position(make_catalog: MakeCatalog) -> None:
    entries = [
        _char("art_c1", "美咲"),
        _truth("art_t1", "真凶是管家"),
        _truth("art_t2", "钥匙被调换"),
        _node("art_ch1", kinds.KIND_CHAPTER, parent_id=None),
        _beat(
            "art_b1",
            parent_id="art_ch1",
            order_key="010",
            disclosures=(DisclosureSpec(fact_id="art_t1"),),
        ),
        _beat(
            "art_b2",
            parent_id="art_ch1",
            order_key="020",
            disclosures=(DisclosureSpec(fact_id="art_t2"),),
        ),
    ]
    timeline = disclosure_timeline(make_catalog(entries))
    assert [e.fact_id for e in timeline.events] == ["art_t1", "art_t2"]
    assert [e.position for e in timeline.events] == [0, 1]


def test_disclosure_timeline_unbound_disclosure_warns(make_catalog: MakeCatalog) -> None:
    entries = [
        _truth("art_t1", "真凶是管家"),
        _entry("art_d1", kinds.KIND_DISCLOSURE, Disclosure(fact_id="art_t1").to_content()),
    ]
    timeline = disclosure_timeline(make_catalog(entries))
    assert timeline.events[0].position is None
    assert [w.code for w in timeline.warnings] == [ec.STORY_MISSING_PRECONDITION]


# --------------------------------------------------------------------------
# MysteryFairnessView
# --------------------------------------------------------------------------
def _mystery_entries() -> list[tuple]:
    return [
        _char("art_c1", "美咲"),
        _truth("art_t1", "门锁被人在午夜撬开"),
        _entry(
            "art_cl1",
            kinds.KIND_CLUE,
            Clue(
                statement="门口的划痕",
                points_to=("art_t1",),
                necessity=True,
                disclosure_plan=("art_b1",),
            ).to_content(),
        ),
        _entry(
            "art_cl2",
            kinds.KIND_CLUE,
            Clue(statement="无关的旧报纸", necessity=False).to_content(),
        ),
        _node("art_ch1", kinds.KIND_CHAPTER, parent_id=None),
        _beat(
            "art_b1",
            parent_id="art_ch1",
            order_key="010",
            disclosures=(DisclosureSpec(fact_id="art_t1"),),
        ),
        _beat("art_b2", parent_id="art_ch1", order_key="020"),
    ]


def test_mystery_fairness_lists_only_necessary_clues(make_catalog: MakeCatalog) -> None:
    view = mystery_fairness(make_catalog(_mystery_entries()))
    assert [c.clue_id for c in view.clues] == ["art_cl1"]
    (clue,) = view.clues
    assert clue.points_to == ("art_t1",)
    assert clue.disclosure_beats == ("art_b1",)
    assert clue.first_position == 0
    assert clue.reveals == (ClueReveal(fact_id="art_t1", revealed_at="art_b1"),)
    assert clue.within_cutoff is None  # 未配置阈值时不做判定


def test_mystery_fairness_cutoff_is_configurable(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(_mystery_entries())
    relaxed = mystery_fairness(catalog, fairness_cutoff=0.5)
    assert relaxed.clues[0].within_cutoff is True
    assert relaxed.findings == ()

    strict = mystery_fairness(catalog, fairness_cutoff=0.25)  # 截止点落在第 1 拍之前
    assert strict.clues[0].within_cutoff is False
    assert [f.code for f in strict.findings] == [ec.STORY_FAIRNESS_VIOLATION]
    assert strict.fairness_cutoff == 0.25


# --------------------------------------------------------------------------
# OpenSetupsView
# --------------------------------------------------------------------------
def _setups_entries() -> list[tuple]:
    return [
        _foreshadow("art_f1", "门锁上的划痕", expected_payoff="证明有人撬锁"),
        _foreshadow("art_f2", "茶杯缺口"),
        _node("art_ch1", kinds.KIND_CHAPTER, parent_id=None),
        _beat("art_b1", parent_id="art_ch1", order_key="010", setups=("art_f1",)),
        _beat("art_b2", parent_id="art_ch1", order_key="020", payoffs=("art_f2",)),
        _beat("art_b3", parent_id="art_ch1", order_key="030", setups=("art_f2",)),
    ]


def test_open_setups_lists_unpaid_foreshadow(make_catalog: MakeCatalog) -> None:
    view = open_setups(make_catalog(_setups_entries()))
    # art_f1 建立未回收 → open；art_f2 已回收 → 不在列表中
    assert [s.foreshadow_id for s in view.open_setups] == ["art_f1"]
    (setup,) = view.open_setups
    assert setup.setup == "门锁上的划痕"
    assert setup.setup_beats == ("art_b1",)
    assert setup.first_position == 0
    assert setup.overdue is False  # 未给 deadline 时不做逾期判定
    assert view.deadline is None


def test_open_setups_overdue_requires_deadline(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(_setups_entries())
    view = open_setups(catalog, deadline=0.5)
    assert view.open_setups[0].overdue is True
    assert view.deadline == 0.5


def test_open_setups_empty_is_legal_result(make_catalog: MakeCatalog) -> None:
    entries = [
        _foreshadow("art_f1", "门锁上的划痕"),
        _node("art_ch1", kinds.KIND_CHAPTER),
        _beat("art_b1", parent_id="art_ch1", setups=("art_f1",), payoffs=("art_f1",)),
    ]
    view = open_setups(make_catalog(entries))
    assert view.open_setups == ()
