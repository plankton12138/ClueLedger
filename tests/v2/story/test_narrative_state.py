"""T13.4b StoryStateAtBeat 确定性折叠测试。

三层事实互相独立：真相（与 Beat 顺序无关）、角色认知（按结构顺序折叠）、
读者披露（累积）。关键断言：顺序确定、合法误解不被拦截、未知状态种类不静默
吞掉、未绑定节拍的认知以警告降级处理（泄压阀）。
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from hastings.domain.story import error_codes as ec
from hastings.domain.story import kinds
from hastings.domain.story.canon.models import Character, Relationship
from hastings.domain.story.catalog import StoryCatalog
from hastings.domain.story.errors import StoryViolation
from hastings.domain.story.mystery.models import Disclosure, KnowledgeState, TruthFact
from hastings.domain.story.narrative.models import (
    STATE_CUSTOM,
    STATE_KNOWLEDGE,
    STATE_RELATIONSHIP,
    Beat,
    DisclosureSpec,
    NarrativeNode,
    StateChange,
)
from hastings.domain.story.narrative.state import story_state_at_beat, story_state_final

MakeCatalog = Callable[..., StoryCatalog]


def _char(artifact_id: str, name: str) -> tuple:
    return (artifact_id, kinds.KIND_CHARACTER, Character(name=name).to_content())


def _fact(artifact_id: str, statement: str) -> tuple:
    return (artifact_id, kinds.KIND_TRUTH_FACT, TruthFact(statement=statement).to_content())


def _scene(artifact_id: str) -> tuple:
    return (
        artifact_id,
        kinds.KIND_SCENE,
        NarrativeNode(node_id=artifact_id, kind=kinds.KIND_SCENE).to_content(),
    )


def _beat(artifact_id: str, *, parent_id: str = "art_sc1", **fields: object) -> tuple:
    return (
        artifact_id,
        kinds.KIND_BEAT,
        Beat(beat_id=artifact_id, parent_id=parent_id, **fields).to_content(),
    )


def _knows(artifact_id: str, character_id: str, fact_id: str, mode: str, beat_id: str) -> tuple:
    return (
        artifact_id,
        kinds.KIND_KNOWLEDGE,
        KnowledgeState(
            character_id=character_id, fact_id=fact_id, mode=mode, at_beat_id=beat_id
        ).to_content(),
    )


def _base() -> list[tuple]:
    return [
        _scene("art_sc1"),
        _char("art_c1", "绫辻"),
        _char("art_c2", "管家"),
        _fact("art_f1", "凶手是管家"),
        _beat(
            "art_b1",
            title="初探",
            state_changes=(
                StateChange(
                    kind=STATE_KNOWLEDGE,
                    value="suspects",
                    character_id="art_c1",
                    target_id="art_f1",
                ),
            ),
        ),
        _beat(
            "art_b2",
            title="对质",
            state_changes=(
                StateChange(
                    kind=STATE_KNOWLEDGE, value="knows", character_id="art_c1", target_id="art_f1"
                ),
            ),
            reader_disclosures=(DisclosureSpec(fact_id="art_f1"),),
        ),
    ]


def test_fold_applies_state_changes_in_order(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(_base())
    at_b2 = story_state_at_beat(catalog, "art_b2")
    entry = next(k for k in at_b2.knowledge if k.character_id == "art_c1")
    assert entry.fact_id == "art_f1"
    assert entry.mode == "knows"  # 后一个节拍覆盖先前 suspects
    assert entry.at_beat_id == "art_b2"


def test_state_before_beat_excludes_it(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(_base())
    before = story_state_at_beat(catalog, "art_b1", after=False)
    assert before.knowledge == ()
    before_b2 = story_state_at_beat(catalog, "art_b2", after=False)
    assert {k.mode for k in before_b2.knowledge} == {"suspects"}


def test_knowledge_artifact_applied_at_its_beat(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog([*_base(), _knows("art_k1", "art_c2", "art_f1", "knows", "art_b2")])
    state = story_state_at_beat(catalog, "art_b2")
    c2 = next(k for k in state.knowledge if k.character_id == "art_c2")
    assert c2.mode == "knows"
    before = story_state_at_beat(catalog, "art_b1")
    assert all(k.character_id != "art_c2" for k in before.knowledge)


def test_knowledge_artifact_without_beat_warns_and_applies_at_start(
    make_catalog: MakeCatalog,
) -> None:
    catalog = make_catalog([*_base(), _knows("art_k1", "art_c2", "art_f1", "knows", "")])
    state = story_state_at_beat(catalog, "art_b1", after=False)
    assert any(k.character_id == "art_c2" for k in state.knowledge)
    assert ec.STORY_MISSING_PRECONDITION in {w.code for w in state.warnings}


def test_misbelief_is_legitimate(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(
        [
            *_base(),
            _beat(
                "art_b3",
                state_changes=(
                    StateChange(
                        kind=STATE_KNOWLEDGE,
                        value="misbelieves",
                        character_id="art_c1",
                        target_id="art_f1",
                    ),
                ),
            ),
        ]
    )
    state = story_state_at_beat(catalog, "art_b3")
    assert next(k for k in state.knowledge if k.character_id == "art_c1").mode == "misbelieves"


def test_disclosures_accumulate_from_beat_and_artifact(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(
        [
            *_base(),
            (
                "art_d1",
                kinds.KIND_DISCLOSURE,
                Disclosure(
                    fact_id="art_f1", at_beat_id="art_b1", interpretation="false"
                ).to_content(),
            ),
        ]
    )
    state = story_state_at_beat(catalog, "art_b2")
    pairs = {(d.fact_id, d.interpretation) for d in state.disclosures}
    assert ("art_f1", "false") in pairs  # 来自 Disclosure 对象（beat b1）
    assert ("art_f1", "true") in pairs  # 来自 beat b2 的内联披露


def test_truths_are_independent_of_beat(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(_base())
    early = story_state_at_beat(catalog, "art_b1")
    final = story_state_final(catalog)
    assert early.truths == final.truths == ("art_f1",)


def test_unknown_beat_raises(make_catalog: MakeCatalog) -> None:
    with pytest.raises(StoryViolation) as exc:
        story_state_at_beat(make_catalog(_base()), "art_missing")
    assert exc.value.code == ec.STORY_UNKNOWN_ARTIFACT


def test_unknown_state_change_kind_warns_and_is_recorded(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(
        [
            *_base(),
            _beat(
                "art_b3",
                state_changes=(StateChange(kind="weather", value="rain"),),
            ),
        ]
    )
    state = story_state_at_beat(catalog, "art_b3")
    assert ec.STORY_INVALID_COMMAND in {w.code for w in state.warnings}
    assert any(c.kind == "weather" and c.value == "rain" for c in state.custom)


def test_state_change_missing_ref_warns_without_dropping(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(
        [
            *_base(),
            _beat(
                "art_b3",
                state_changes=(StateChange(kind=STATE_KNOWLEDGE, value="knows"),),
            ),
        ]
    )
    state = story_state_at_beat(catalog, "art_b3")
    assert ec.STORY_MISSING_PRECONDITION in {w.code for w in state.warnings}


def test_relationships_initial_from_canon_and_updated(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(
        [
            *_base(),
            (
                "art_rel",
                kinds.KIND_RELATIONSHIP,
                Relationship(
                    source_id="art_c1", target_id="art_c2", public_state="主仆", true_state="雇佣"
                ).to_content(),
            ),
            _beat(
                "art_b3",
                state_changes=(
                    StateChange(
                        kind=STATE_RELATIONSHIP,
                        value="反目",
                        character_id="art_c1",
                        target_id="art_c2",
                    ),
                ),
            ),
        ]
    )
    before = story_state_at_beat(catalog, "art_b3", after=False)
    initial = next(r for r in before.relationships if r.source_id == "art_c1")
    assert initial.true_state == "雇佣"
    after = story_state_at_beat(catalog, "art_b3")
    updated = next(r for r in after.relationships if r.source_id == "art_c1")
    assert updated.true_state == "反目"
    assert updated.changed_at_beat_id == "art_b3"


def test_final_state_folds_all_beats(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(_base())
    final = story_state_final(catalog)
    assert final.at_beat_id is None
    assert next(k for k in final.knowledge if k.character_id == "art_c1").mode == "knows"


def test_custom_kind_is_recorded_with_beat_and_note(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(
        [
            *_base(),
            _beat(
                "art_b3",
                state_changes=(StateChange(kind=STATE_CUSTOM, value="灯笼熄灭", note="氛围"),),
            ),
        ]
    )
    state = story_state_at_beat(catalog, "art_b3")
    entry = next(c for c in state.custom if c.kind == STATE_CUSTOM)
    assert entry.beat_id == "art_b3"
    assert entry.note == "氛围"
