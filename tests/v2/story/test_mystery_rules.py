"""T13.5b 悬疑规则测试：提前知情定位明确、合法误解不拦、公平性阈值可配置。

规则产出 FindingSeed（severity_hint 交由项目策略），领域层只负责确定性判定。
"""

from __future__ import annotations

from collections.abc import Callable

from hastings.domain.story import error_codes as ec
from hastings.domain.story import kinds
from hastings.domain.story.canon.models import Character, Clue
from hastings.domain.story.catalog import StoryCatalog
from hastings.domain.story.mystery.models import Disclosure, KnowledgeState, TruthFact
from hastings.domain.story.mystery.rules import mystery_findings
from hastings.domain.story.narrative.models import (
    STATE_KNOWLEDGE,
    Beat,
    DisclosureSpec,
    NarrativeNode,
    StateChange,
)

MakeCatalog = Callable[..., StoryCatalog]


def _scene(artifact_id: str) -> tuple:
    return (
        artifact_id,
        kinds.KIND_SCENE,
        NarrativeNode(node_id=artifact_id, kind=kinds.KIND_SCENE).to_content(),
    )


def _beat(artifact_id: str, *, order_key: str = "", **fields: object) -> tuple:
    return (
        artifact_id,
        kinds.KIND_BEAT,
        Beat(beat_id=artifact_id, parent_id="art_sc1", order_key=order_key, **fields).to_content(),
    )


def _char(artifact_id: str) -> tuple:
    return (artifact_id, kinds.KIND_CHARACTER, Character(name=artifact_id).to_content())


def _fact(artifact_id: str) -> tuple:
    return (
        artifact_id,
        kinds.KIND_TRUTH_FACT,
        TruthFact(statement=f"事实{artifact_id}").to_content(),
    )


def _knows(artifact_id: str, *, character_id: str, fact_id: str, mode: str, beat_id: str) -> tuple:
    return (
        artifact_id,
        kinds.KIND_KNOWLEDGE,
        KnowledgeState(
            character_id=character_id, fact_id=fact_id, mode=mode, at_beat_id=beat_id
        ).to_content(),
    )


def _disclosure(artifact_id: str, *, fact_id: str, beat_id: str) -> tuple:
    return (
        artifact_id,
        kinds.KIND_DISCLOSURE,
        Disclosure(fact_id=fact_id, at_beat_id=beat_id).to_content(),
    )


def _base_with_reveal() -> list[tuple]:
    return [
        _scene("art_sc1"),
        _char("art_c1"),
        _fact("art_f1"),
        _beat("art_b1", order_key="010"),
        _beat("art_b2", order_key="020", reader_disclosures=(DisclosureSpec(fact_id="art_f1"),)),
    ]


def test_early_knowledge_is_flagged_with_location(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(
        [
            *_base_with_reveal(),
            _knows(
                "art_k1", character_id="art_c1", fact_id="art_f1", mode="knows", beat_id="art_b1"
            ),
        ]
    )
    findings = mystery_findings(catalog)
    assert [f.code for f in findings] == [ec.STORY_INVALID_KNOWLEDGE_ORDER]
    assert findings[0].severity_hint == "error"
    assert findings[0].details["know_beat"] == "art_b1"
    assert findings[0].details["reveal_beat"] == "art_b2"


def test_legitimate_misbelief_is_not_flagged(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(
        [
            *_base_with_reveal(),
            _knows(
                "art_k1",
                character_id="art_c1",
                fact_id="art_f1",
                mode="misbelieves",
                beat_id="art_b1",
            ),
        ]
    )
    assert mystery_findings(catalog) == ()


def test_suspect_before_reveal_is_not_flagged(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(
        [
            *_base_with_reveal(),
            _knows(
                "art_k1", character_id="art_c1", fact_id="art_f1", mode="suspects", beat_id="art_b1"
            ),
        ]
    )
    assert mystery_findings(catalog) == ()


def test_knows_after_reveal_is_ok(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(
        [
            *_base_with_reveal(),
            _knows(
                "art_k1", character_id="art_c1", fact_id="art_f1", mode="knows", beat_id="art_b2"
            ),
        ]
    )
    assert mystery_findings(catalog) == ()


def test_knows_never_disclosed_is_flagged(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(
        [
            _scene("art_sc1"),
            _char("art_c1"),
            _fact("art_f1"),
            _beat("art_b1", order_key="010"),
            _beat("art_b2", order_key="020"),
            _knows(
                "art_k1", character_id="art_c1", fact_id="art_f1", mode="knows", beat_id="art_b2"
            ),
        ]
    )
    findings = mystery_findings(catalog)
    assert [f.code for f in findings] == [ec.STORY_INVALID_KNOWLEDGE_ORDER]
    assert findings[0].details["reveal_beat"] is None


def test_inline_state_change_early_knowledge_is_flagged(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(
        [
            _scene("art_sc1"),
            _char("art_c1"),
            _fact("art_f1"),
            _beat(
                "art_b1",
                order_key="010",
                state_changes=(
                    StateChange(
                        kind=STATE_KNOWLEDGE,
                        value="knows",
                        character_id="art_c1",
                        target_id="art_f1",
                    ),
                ),
            ),
            _beat(
                "art_b2", order_key="020", reader_disclosures=(DisclosureSpec(fact_id="art_f1"),)
            ),
        ]
    )
    assert [f.code for f in mystery_findings(catalog)] == [ec.STORY_INVALID_KNOWLEDGE_ORDER]


def test_fairness_cutoff_flags_late_necessary_clue(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(
        [
            _scene("art_sc1"),
            _fact("art_f1"),
            _beat("art_b1", order_key="010"),
            _beat("art_b2", order_key="020"),
            _beat("art_b3", order_key="030"),
            _beat("art_b4", order_key="040"),
            (
                "art_clue1",
                kinds.KIND_CLUE,
                Clue(
                    statement="必要线索",
                    necessity=True,
                    points_to=("art_f1",),
                    disclosure_plan=("art_b4",),
                ).to_content(),
            ),
        ]
    )
    findings = mystery_findings(catalog, fairness_cutoff=0.5)
    assert [f.code for f in findings] == [ec.STORY_FAIRNESS_VIOLATION]
    assert findings[0].severity_hint == "warning"


def test_fairness_cutoff_passes_early_clue(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(
        [
            _scene("art_sc1"),
            _fact("art_f1"),
            _beat("art_b1", order_key="010"),
            _beat("art_b2", order_key="020"),
            _beat("art_b3", order_key="030"),
            _beat("art_b4", order_key="040"),
            (
                "art_clue1",
                kinds.KIND_CLUE,
                Clue(
                    statement="必要线索",
                    necessity=True,
                    points_to=("art_f1",),
                    disclosure_plan=("art_b1",),
                ).to_content(),
            ),
        ]
    )
    assert mystery_findings(catalog, fairness_cutoff=0.5) == ()


def test_fairness_disabled_for_non_mystery_project(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(
        [
            _scene("art_sc1"),
            _fact("art_f1"),
            _beat("art_b1", order_key="010"),
            _beat("art_b2", order_key="020"),
            (
                "art_clue1",
                kinds.KIND_CLUE,
                Clue(
                    statement="必要线索", necessity=True, disclosure_plan=("art_b2",)
                ).to_content(),
            ),
        ]
    )
    assert mystery_findings(catalog, fairness_cutoff=None) == ()


def test_necessary_clue_without_disclosure_is_flagged(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(
        [
            _scene("art_sc1"),
            _fact("art_f1"),
            _beat("art_b1", order_key="010"),
            (
                "art_clue1",
                kinds.KIND_CLUE,
                Clue(statement="必要线索", necessity=True, points_to=("art_f1",)).to_content(),
            ),
        ]
    )
    assert [f.code for f in mystery_findings(catalog, fairness_cutoff=0.9)] == [
        ec.STORY_FAIRNESS_VIOLATION
    ]
