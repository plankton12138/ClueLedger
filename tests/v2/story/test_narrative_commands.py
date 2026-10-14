"""T13.3b Narrative 命令测试：结构、节拍、移动与作用修改。

关键断言：父级存在且类型匹配、顺序键在同一父级下唯一、移动不改变 ID、
删除 Setup 使 Payoff 断裂（由 T13.5 规则判定），命令本身只忠实维护结构边。
"""

from __future__ import annotations

import itertools
from collections.abc import Callable

import pytest

from hastings.domain.story import error_codes as ec
from hastings.domain.story import kinds
from hastings.domain.story.canon.models import Character, Foreshadow
from hastings.domain.story.catalog import StoryCatalog
from hastings.domain.story.errors import StoryViolation
from hastings.domain.story.mystery.models import TruthFact
from hastings.domain.story.narrative import commands
from hastings.domain.story.narrative.models import Beat, DisclosureSpec, NarrativeNode

MakeCatalog = Callable[..., StoryCatalog]


@pytest.fixture
def next_id() -> Callable[[str], str]:
    counter = itertools.count(1)
    return lambda kind: f"art_new_{next(counter)}"


def _node(artifact_id: str, kind: str, *, parent_id: str | None, order_key: str = "") -> tuple:
    return (
        artifact_id,
        kind,
        NarrativeNode(
            node_id=artifact_id, kind=kind, parent_id=parent_id, order_key=order_key
        ).to_content(),
    )


def _beat(artifact_id: str, *, parent_id: str | None = None, **fields: object) -> tuple:
    return (
        artifact_id,
        kinds.KIND_BEAT,
        Beat(beat_id=artifact_id, parent_id=parent_id, **fields).to_content(),
    )


def _char(artifact_id: str, name: str) -> tuple:
    return (artifact_id, kinds.KIND_CHARACTER, Character(name=name).to_content())


# --- CreateNode -------------------------------------------------------------
def test_create_node_success(make_catalog: MakeCatalog, next_id: Callable[[str], str]) -> None:
    catalog = make_catalog([_node("art_arc", kinds.KIND_ARC, parent_id=None)])
    change = commands.create_node(
        catalog,
        next_id,
        kind=kinds.KIND_CHAPTER,
        title="第一章",
        parent_id="art_arc",
        order_key="010",
    )
    assert change.artifact_changes[0].kind == kinds.KIND_CHAPTER
    assert change.artifact_changes[0].content["parent_id"] == "art_arc"


def test_create_node_invalid_kind(make_catalog: MakeCatalog, next_id: Callable[[str], str]) -> None:
    with pytest.raises(StoryViolation) as exc:
        commands.create_node(make_catalog([]), next_id, kind=kinds.KIND_BEAT, title="x")
    assert exc.value.code == ec.STORY_INVALID_COMMAND


def test_create_node_parent_missing(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    with pytest.raises(StoryViolation) as exc:
        commands.create_node(
            make_catalog([]), next_id, kind=kinds.KIND_CHAPTER, title="x", parent_id="art_none"
        )
    assert exc.value.code == ec.STORY_UNKNOWN_ARTIFACT


def test_create_node_parent_kind_mismatch(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog([_node("art_book", kinds.KIND_BOOK, parent_id=None)])
    with pytest.raises(StoryViolation) as exc:
        # 章节的父级应当是 arc，这里给了 book
        commands.create_node(
            catalog, next_id, kind=kinds.KIND_CHAPTER, title="x", parent_id="art_book"
        )
    assert exc.value.code == ec.STORY_INVALID_COMMAND


def test_create_node_series_has_no_parent(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    change = commands.create_node(make_catalog([]), next_id, kind=kinds.KIND_SERIES, title="系列")
    assert change.artifact_changes[0].content["parent_id"] is None


def test_create_node_duplicate_order_key(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog(
        [
            _node("art_arc", kinds.KIND_ARC, parent_id=None),
            _node("art_ch1", kinds.KIND_CHAPTER, parent_id="art_arc", order_key="010"),
        ]
    )
    with pytest.raises(StoryViolation) as exc:
        commands.create_node(
            catalog,
            next_id,
            kind=kinds.KIND_CHAPTER,
            title="重复序",
            parent_id="art_arc",
            order_key="010",
        )
    assert exc.value.code == ec.STORY_INVALID_COMMAND
    assert "010" in exc.value.why


# --- CreateBeat -------------------------------------------------------------
def test_create_beat_success(make_catalog: MakeCatalog, next_id: Callable[[str], str]) -> None:
    catalog = make_catalog([_node("art_sc1", kinds.KIND_SCENE, parent_id=None)])
    change = commands.create_beat(
        catalog, next_id, parent_id="art_sc1", title="发现尸体", purpose="建立谜面"
    )
    assert change.artifact_changes[0].kind == kinds.KIND_BEAT
    assert change.artifact_changes[0].content["purpose"] == "建立谜面"


def test_create_beat_unknown_pov(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog([_node("art_sc1", kinds.KIND_SCENE, parent_id=None)])
    with pytest.raises(StoryViolation) as exc:
        commands.create_beat(catalog, next_id, parent_id="art_sc1", pov_character_id="art_none")
    assert exc.value.code == ec.STORY_UNKNOWN_ARTIFACT


def test_create_beat_parent_not_a_node(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog([_char("art_c1", "绫辻")])
    with pytest.raises(StoryViolation) as exc:
        commands.create_beat(catalog, next_id, parent_id="art_c1")
    assert exc.value.code == ec.STORY_INVALID_COMMAND


def test_create_beat_emits_setup_payoff_requires_reveals_edges(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog(
        [
            _node("art_sc1", kinds.KIND_SCENE, parent_id=None),
            _beat("art_b0", parent_id="art_sc1"),
            ("art_fs1", kinds.KIND_FORESHADOW, Foreshadow(setup="雨夜里的第二把伞").to_content()),
            ("art_fact1", kinds.KIND_TRUTH_FACT, TruthFact(statement="凶手是管家").to_content()),
        ]
    )
    change = commands.create_beat(
        catalog,
        next_id,
        parent_id="art_sc1",
        title="对质",
        setups=("art_fs1",),
        payoffs=("art_fs1",),
        depends_on=("art_b0",),
        reader_disclosures=(DisclosureSpec(fact_id="art_fact1"),),
    )
    by_type: dict[str, set[str]] = {}
    for rel in change.relation_changes:
        by_type.setdefault(rel.relation_type, set()).add(rel.target.value)
    assert by_type[kinds.REL_SETS_UP] == {"art_fs1"}
    assert by_type[kinds.REL_PAYS_OFF] == {"art_fs1"}
    assert by_type[kinds.REL_REQUIRES] == {"art_b0"}
    assert by_type[kinds.REL_REVEALS] == {"art_fact1"}
    assert all(r.action == "add" for r in change.relation_changes)


def test_create_beat_unknown_setup_ref(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog([_node("art_sc1", kinds.KIND_SCENE, parent_id=None)])
    with pytest.raises(StoryViolation) as exc:
        commands.create_beat(catalog, next_id, parent_id="art_sc1", setups=("art_none",))
    assert exc.value.code == ec.STORY_UNKNOWN_ARTIFACT


# --- MoveBeat ---------------------------------------------------------------
def test_move_beat_keeps_id_and_changes_parent(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog(
        [
            _node("art_sc1", kinds.KIND_SCENE, parent_id=None),
            _node("art_sc2", kinds.KIND_SCENE, parent_id=None),
            _beat("art_b1", parent_id="art_sc1", order_key="010"),
        ]
    )
    change = commands.move_beat(catalog, next_id, beat_id="art_b1", parent_id="art_sc2")
    created = change.artifact_changes[0]
    assert created.artifact_id.value == "art_b1"  # 身份不变
    assert created.kind is None
    assert created.content["parent_id"] == "art_sc2"
    assert {s.target_id for s in change.impact_seeds} == {"art_b1"}


def test_move_beat_same_place_is_noop(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog(
        [
            _node("art_sc1", kinds.KIND_SCENE, parent_id=None),
            _beat("art_b1", parent_id="art_sc1", order_key="010"),
        ]
    )
    change = commands.move_beat(
        catalog, next_id, beat_id="art_b1", parent_id="art_sc1", order_key="010"
    )
    assert change.is_empty()


def test_move_beat_order_collision(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog(
        [
            _node("art_sc1", kinds.KIND_SCENE, parent_id=None),
            _beat("art_b1", parent_id="art_sc1", order_key="010"),
            _beat("art_b2", parent_id="art_sc1", order_key="020"),
        ]
    )
    with pytest.raises(StoryViolation) as exc:
        commands.move_beat(catalog, next_id, beat_id="art_b1", order_key="020")
    assert exc.value.code == ec.STORY_INVALID_COMMAND


def test_move_beat_unknown_beat(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    with pytest.raises(StoryViolation) as exc:
        commands.move_beat(make_catalog([]), next_id, beat_id="art_none")
    assert exc.value.code == ec.STORY_UNKNOWN_ARTIFACT


# --- ReviseBeatPurpose ------------------------------------------------------
def test_revise_beat_purpose_success(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog([_beat("art_b1", purpose="旧作用", title="发现")])
    change = commands.revise_beat_purpose(
        catalog, next_id, beat_id="art_b1", purpose="新作用"
    )
    assert change.artifact_changes[0].content["purpose"] == "新作用"
    assert change.artifact_changes[0].content["title"] == "发现"  # 未提供字段保留
    assert {s.target_id for s in change.impact_seeds} == {"art_b1"}


def test_revise_beat_purpose_noop(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog([_beat("art_b1", purpose="同作用")])
    change = commands.revise_beat_purpose(catalog, next_id, beat_id="art_b1", purpose="同作用")
    assert change.is_empty()


def test_revise_beat_purpose_stale_revision(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog([_beat("art_b1", purpose="旧")])
    with pytest.raises(StoryViolation) as exc:
        commands.revise_beat_purpose(
            catalog, next_id, beat_id="art_b1", purpose="新", expected_revision="rev_stale"
        )
    assert exc.value.code == ec.STORY_STALE_REVISION


def test_revise_beat_purpose_wrong_kind(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog([_node("art_sc1", kinds.KIND_SCENE, parent_id=None)])
    with pytest.raises(StoryViolation) as exc:
        commands.revise_beat_purpose(catalog, next_id, beat_id="art_sc1", purpose="x")
    assert exc.value.code == ec.STORY_INVALID_COMMAND


def test_revise_beat_purpose_emits_downstream_seed(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog(
        [
            _beat("art_b1", purpose="旧"),
            _beat("art_b2", depends_on=("art_b1",)),
        ]
    )
    change = commands.revise_beat_purpose(catalog, next_id, beat_id="art_b1", purpose="新")
    assert {s.target_id for s in change.impact_seeds} == {"art_b1", "art_b2"}


# --- DeleteSetup ------------------------------------------------------------
def test_delete_setup_removes_entry_and_edge(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog(
        [
            ("art_fs1", kinds.KIND_FORESHADOW, Foreshadow(setup="第二把伞").to_content()),
            _beat("art_b1", setups=("art_fs1",)),
            _beat("art_b2", payoffs=("art_fs1",)),
        ]
    )
    change = commands.delete_setup(catalog, next_id, beat_id="art_b1", setup_id="art_fs1")
    assert change.artifact_changes[0].content["setups"] == []  # 该节拍不再建立该伏笔
    edge = change.relation_changes[0]
    assert edge.action == "remove"
    assert edge.relation_type == kinds.REL_SETS_UP
    assert edge.target.value == "art_fs1"
    # 回收方（art_b2）进入复核清单，具体 stale 由 T13.5 规则判定
    assert {s.target_id for s in change.impact_seeds} >= {"art_b1", "art_b2"}


def test_delete_setup_absent_is_noop(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog([_beat("art_b1", setups=("art_fs1",))])
    change = commands.delete_setup(catalog, next_id, beat_id="art_b1", setup_id="art_fs_other")
    assert change.is_empty()


def test_delete_setup_unknown_beat(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    with pytest.raises(StoryViolation) as exc:
        commands.delete_setup(make_catalog([]), next_id, beat_id="art_none", setup_id="x")
    assert exc.value.code == ec.STORY_UNKNOWN_ARTIFACT
