"""T13.3a Canon 命令测试：成功 / no-op / 缺失引用 / revision 过期 / 部分不擦除。

命令是纯函数：`fn(catalog, new_id, ...) -> DomainChange`，不触碰 Repository。
关键断言覆盖模块文档 §5 不变量：名称改变不改 ID、部分修改不擦除、被替代事实
保留历史与替代关系。
"""

from __future__ import annotations

import itertools
from collections.abc import Callable

import pytest

from hastings.domain.story import error_codes as ec
from hastings.domain.story import kinds
from hastings.domain.story.canon import commands
from hastings.domain.story.canon.models import Character, Clue, MysteryTruth, Place, RedHerring
from hastings.domain.story.catalog import StoryCatalog
from hastings.domain.story.errors import StoryViolation
from hastings.domain.story.mystery.models import TruthFact
from hastings.domain.story.narrative.models import Beat

MakeCatalog = Callable[..., StoryCatalog]

Where = "story.test"


@pytest.fixture
def next_id() -> Callable[[str], str]:
    counter = itertools.count(1)
    return lambda kind: f"art_new_{next(counter)}"


def _char(artifact_id: str, name: str, **fields: object) -> tuple[str, str, dict[str, object]]:
    return (artifact_id, kinds.KIND_CHARACTER, Character(name=name, **fields).to_content())


def _beat(artifact_id: str, **fields: object) -> tuple[str, str, dict[str, object]]:
    return (artifact_id, kinds.KIND_BEAT, Beat(beat_id=artifact_id, **fields).to_content())


def test_create_character_success(make_catalog: MakeCatalog, next_id: Callable[[str], str]) -> None:
    change = commands.create_character(
        make_catalog([]), next_id, name="绫辻行人", role="protagonist"
    )
    assert not change.is_empty()
    entry = change.artifact_changes[0]
    assert entry.kind == kinds.KIND_CHARACTER
    assert entry.old_revision_id is None
    assert entry.content["name"] == "绫辻行人"


def test_create_character_missing_name(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    with pytest.raises(StoryViolation) as exc:
        commands.create_character(make_catalog([]), next_id, name="  ")
    assert exc.value.code == ec.STORY_MISSING_REQUIRED_FIELD


def test_create_character_invalid_role(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    with pytest.raises(StoryViolation) as exc:
        commands.create_character(make_catalog([]), next_id, name="A", role="villain")
    assert exc.value.code == ec.STORY_INVALID_COMMAND


def test_create_character_duplicate_id_rejected(
    make_catalog: MakeCatalog,
) -> None:
    catalog = make_catalog([_char("art_c1", "旧角色")])
    with pytest.raises(StoryViolation) as exc:
        commands.create_character(catalog, lambda kind: "art_c1", name="新角色")
    assert exc.value.code == ec.STORY_DUPLICATE_ID


def test_create_character_same_name_allowed(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog([_char("art_c1", "同名")])
    change = commands.create_character(catalog, next_id, name="同名")
    assert change.artifact_changes[0].artifact_id.value != "art_c1"


def test_revise_character_partial_keeps_unset(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog([_char("art_c1", "绫辻", secret="旧秘密", goal="旧目标")])
    change = commands.revise_character(catalog, next_id, character_id="art_c1", secret="新秘密")
    content = change.artifact_changes[0].content
    assert content["secret"] == "新秘密"
    assert content["goal"] == "旧目标"  # 未提供字段保持不变
    assert content["name"] == "绫辻"
    assert change.artifact_changes[0].old_revision_id is not None


def test_revise_character_same_value_is_legal_noop(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog([_char("art_c1", "绫辻", secret="同值")])
    change = commands.revise_character(catalog, next_id, character_id="art_c1", secret="同值")
    assert change.is_empty()


def test_revise_character_stale_revision(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog([_char("art_c1", "绫辻")])
    with pytest.raises(StoryViolation) as exc:
        commands.revise_character(
            catalog, next_id, character_id="art_c1", expected_revision="rev_outdated", secret="x"
        )
    assert exc.value.code == ec.STORY_STALE_REVISION
    assert exc.value.details["expected"] == "rev_outdated"


def test_revise_character_expected_revision_matches(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog([_char("art_c1", "绫辻")])
    change = commands.revise_character(
        catalog, next_id, character_id="art_c1", expected_revision="rev_art_c1", secret="x"
    )
    assert not change.is_empty()


def test_revise_character_unknown_ref(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    with pytest.raises(StoryViolation) as exc:
        commands.revise_character(make_catalog([]), next_id, character_id="art_missing", secret="x")
    assert exc.value.code == ec.STORY_UNKNOWN_ARTIFACT


def test_revise_character_wrong_kind(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog([("art_p1", kinds.KIND_PLACE, Place(name="宅邸").to_content())])
    with pytest.raises(StoryViolation) as exc:
        commands.revise_character(catalog, next_id, character_id="art_p1", secret="x")
    assert exc.value.code == ec.STORY_INVALID_COMMAND


def test_change_role_emits_motivates_edges_and_seeds(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog(
        [
            _char("art_c1", "绫辻", role="support"),
            _beat("art_b1", pov_character_id="art_c1"),
            _beat("art_b2", participants=("art_c1",)),
        ]
    )
    change = commands.change_character_role(
        catalog, next_id, character_id="art_c1", role="protagonist"
    )
    assert change.artifact_changes[0].content["role"] == "protagonist"
    edge_targets = {(r.source.value, r.target.value, r.action) for r in change.relation_changes}
    assert edge_targets == {("art_c1", "art_b1", "add"), ("art_c1", "art_b2", "add")}
    assert all(r.relation_type == kinds.REL_MOTIVATES for r in change.relation_changes)
    assert {s.target_id for s in change.impact_seeds} == {"art_b1", "art_b2"}


def test_change_role_removes_edge_when_not_driving(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog(
        [_char("art_c1", "绫辻", role="antagonist"), _beat("art_b1", pov_character_id="art_c1")]
    )
    change = commands.change_character_role(catalog, next_id, character_id="art_c1", role="witness")
    assert {r.action for r in change.relation_changes} == {"remove"}


def test_change_role_same_role_is_noop(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog([_char("art_c1", "绫辻", role="protagonist")])
    change = commands.change_character_role(
        catalog, next_id, character_id="art_c1", role="protagonist"
    )
    assert change.is_empty()


def test_change_role_invalid_role(make_catalog: MakeCatalog, next_id: Callable[[str], str]) -> None:
    catalog = make_catalog([_char("art_c1", "绫辻")])
    with pytest.raises(StoryViolation) as exc:
        commands.change_character_role(catalog, next_id, character_id="art_c1", role="mastermind")
    assert exc.value.code == ec.STORY_INVALID_COMMAND


def test_change_role_stale_revision(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog([_char("art_c1", "绫辻", role="support")])
    with pytest.raises(StoryViolation) as exc:
        commands.change_character_role(
            catalog,
            next_id,
            character_id="art_c1",
            role="protagonist",
            expected_revision="rev_stale",
        )
    assert exc.value.code == ec.STORY_STALE_REVISION


def _truth_catalog(make_catalog: MakeCatalog) -> StoryCatalog:
    return make_catalog(
        [
            _char("art_culprit", "凶手"),
            _char("art_acc", "协助者"),
            (
                "art_t1",
                kinds.KIND_TIMELINE_EVENT,
                {"schema_version": 1, "time": "22:00", "place_id": "", "participants": []},
            ),
        ]
    )


def test_define_mystery_truth_success(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    change = commands.define_mystery_truth(
        _truth_catalog(make_catalog),
        next_id,
        culprit_id="art_culprit",
        accomplice_ids=("art_acc",),
        true_timeline=("art_t1",),
        motive="遗产",
    )
    content = change.artifact_changes[0].content
    assert change.artifact_changes[0].kind == kinds.KIND_MYSTERY_TRUTH
    assert content["culprit_id"] == "art_culprit"
    assert content["accomplice_ids"] == ["art_acc"]


def test_define_mystery_truth_unknown_culprit(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    with pytest.raises(StoryViolation) as exc:
        commands.define_mystery_truth(_truth_catalog(make_catalog), next_id, culprit_id="art_x")
    assert exc.value.code == ec.STORY_UNKNOWN_ARTIFACT


def test_define_mystery_truth_unknown_accomplice(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    with pytest.raises(StoryViolation) as exc:
        commands.define_mystery_truth(
            _truth_catalog(make_catalog),
            next_id,
            culprit_id="art_culprit",
            accomplice_ids=("art_missing",),
        )
    assert exc.value.code == ec.STORY_UNKNOWN_ARTIFACT


def test_define_mystery_truth_unknown_timeline_ref(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    with pytest.raises(StoryViolation) as exc:
        commands.define_mystery_truth(
            _truth_catalog(make_catalog),
            next_id,
            culprit_id="art_culprit",
            true_timeline=("art_missing",),
        )
    assert exc.value.code == ec.STORY_UNKNOWN_ARTIFACT


def test_plan_clue_emits_requires_edge(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog(
        [("art_f1", kinds.KIND_TRUTH_FACT, TruthFact(statement="刀伤非自伤").to_content())]
    )
    change = commands.plan_clue(
        catalog,
        next_id,
        statement="门锁内侧划痕",
        source="书房",
        points_to=("art_f1",),
        necessity=True,
    )
    assert change.artifact_changes[0].kind == kinds.KIND_CLUE
    assert change.artifact_changes[0].content["necessity"] is True
    assert change.relation_changes[0].relation_type == kinds.REL_REQUIRES
    assert change.relation_changes[0].target.value == "art_f1"


def test_plan_clue_missing_truth_ref(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    with pytest.raises(StoryViolation) as exc:
        commands.plan_clue(make_catalog([]), next_id, statement="线索", points_to=("art_none",))
    assert exc.value.code == ec.STORY_UNKNOWN_ARTIFACT


def test_plan_clue_empty_statement(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    with pytest.raises(StoryViolation) as exc:
        commands.plan_clue(make_catalog([]), next_id, statement="")
    assert exc.value.code == ec.STORY_MISSING_REQUIRED_FIELD


def test_plan_clue_unknown_disclosure_beat(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    with pytest.raises(StoryViolation) as exc:
        commands.plan_clue(
            make_catalog([]), next_id, statement="线索", disclosure_plan=("art_nope",)
        )
    assert exc.value.code == ec.STORY_UNKNOWN_ARTIFACT


def test_plan_red_herring_requires_cause(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    with pytest.raises(StoryViolation) as exc:
        commands.plan_red_herring(
            make_catalog([]), next_id, false_explanation="以为是自杀", basis=""
        )
    assert exc.value.code == ec.STORY_MISSING_REQUIRED_FIELD


def test_plan_red_herring_success(make_catalog: MakeCatalog, next_id: Callable[[str], str]) -> None:
    change = commands.plan_red_herring(
        make_catalog([]),
        next_id,
        false_explanation="以为是自杀",
        basis="被告有抑郁病史，且当晚曾独处",
    )
    assert change.artifact_changes[0].kind == kinds.KIND_RED_HERRING
    assert change.artifact_changes[0].content["basis"].startswith("被告")


def test_record_knowledge_change_emits_changes_state_edge(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog(
        [
            _char("art_c1", "绫辻"),
            ("art_f1", kinds.KIND_TRUTH_FACT, TruthFact(statement="真相").to_content()),
            _beat("art_b1"),
        ]
    )
    change = commands.record_knowledge_change(
        catalog,
        next_id,
        character_id="art_c1",
        fact_id="art_f1",
        mode="knows",
        at_beat_id="art_b1",
    )
    assert change.artifact_changes[0].kind == kinds.KIND_KNOWLEDGE
    assert change.artifact_changes[0].content["mode"] == "knows"
    edge = change.relation_changes[0]
    assert edge.relation_type == kinds.REL_CHANGES_STATE
    assert edge.source.value == "art_b1"
    assert edge.target.value == "art_c1"
    assert edge.metadata["fact_id"] == "art_f1"


def test_record_knowledge_change_invalid_mode(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog(
        [
            _char("art_c1", "绫辻"),
            ("art_f1", kinds.KIND_TRUTH_FACT, TruthFact(statement="真相").to_content()),
        ]
    )
    with pytest.raises(StoryViolation) as exc:
        commands.record_knowledge_change(
            catalog, next_id, character_id="art_c1", fact_id="art_f1", mode="believes"
        )
    assert exc.value.code == ec.STORY_INVALID_COMMAND


def test_record_knowledge_change_unknown_beat(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog(
        [
            _char("art_c1", "绫辻"),
            ("art_f1", kinds.KIND_TRUTH_FACT, TruthFact(statement="真相").to_content()),
        ]
    )
    with pytest.raises(StoryViolation) as exc:
        commands.record_knowledge_change(
            catalog,
            next_id,
            character_id="art_c1",
            fact_id="art_f1",
            at_beat_id="art_missing",
        )
    assert exc.value.code == ec.STORY_UNKNOWN_ARTIFACT


def test_record_knowledge_change_unknown_fact(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog([_char("art_c1", "绫辻")])
    with pytest.raises(StoryViolation) as exc:
        commands.record_knowledge_change(
            catalog, next_id, character_id="art_c1", fact_id="art_missing"
        )
    assert exc.value.code == ec.STORY_UNKNOWN_ARTIFACT


def test_supersede_keeps_old_and_records_relation(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog([("art_clue1", kinds.KIND_CLUE, Clue(statement="旧线索").to_content())])
    change = commands.supersede_canon_fact(
        catalog,
        next_id,
        old_id="art_clue1",
        content=Clue(statement="更准确的线索").to_content(),
        reason="作者更正",
    )
    assert len(change.artifact_changes) == 1
    created = change.artifact_changes[0]
    assert created.kind == kinds.KIND_CLUE
    assert created.artifact_id.value == "art_new_1"
    # 旧事实未被修改或删除（无指向旧 ID 的内容变化）
    assert all(c.artifact_id.value != "art_clue1" for c in change.artifact_changes)
    relation = change.relation_changes[0]
    assert relation.relation_type == kinds.REL_SUPERSEDES
    assert relation.source.value == "art_new_1"
    assert relation.target.value == "art_clue1"
    assert {s.target_id for s in change.impact_seeds} == {"art_clue1"}


def test_supersede_unknown_old_fact(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    with pytest.raises(StoryViolation) as exc:
        commands.supersede_canon_fact(
            make_catalog([]), next_id, old_id="art_missing", content={"schema_version": 1}
        )
    assert exc.value.code == ec.STORY_UNKNOWN_ARTIFACT


def test_supersede_rejects_non_canon_kind(
    make_catalog: MakeCatalog, next_id: Callable[[str], str]
) -> None:
    catalog = make_catalog([_beat("art_b1")])
    with pytest.raises(StoryViolation) as exc:
        commands.supersede_canon_fact(
            catalog, next_id, old_id="art_b1", content={"schema_version": 1}
        )
    assert exc.value.code == ec.STORY_INVALID_COMMAND


def test_supersede_duplicate_new_id(
    make_catalog: MakeCatalog,
) -> None:
    catalog = make_catalog([("art_clue1", kinds.KIND_CLUE, Clue(statement="旧线索").to_content())])
    with pytest.raises(StoryViolation) as exc:
        commands.supersede_canon_fact(
            catalog,
            lambda kind: "art_clue1",
            old_id="art_clue1",
            content=Clue(statement="新线索").to_content(),
        )
    assert exc.value.code == ec.STORY_DUPLICATE_ID


def test_mystery_truth_and_red_herring_are_independent_models() -> None:
    # 类型健全性：命令使用领域模型而非裸 dict
    assert isinstance(MysteryTruth(culprit_id="x"), MysteryTruth)
    assert isinstance(RedHerring(false_explanation="x", basis="y"), RedHerring)
