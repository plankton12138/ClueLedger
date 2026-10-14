"""T13.5a Canon/结构确定性规则测试（FindingSeed）。

规则只产出结构化种子，不改写领域状态：必要线索缺真相、误导缺真实依据、
时间线冲突、Payoff 失去建立方（断裂/ stale）。
"""

from __future__ import annotations

from collections.abc import Callable

from hastings.domain.story import error_codes as ec
from hastings.domain.story import kinds
from hastings.domain.story.canon.models import Clue, Foreshadow, RedHerring, TimelineEvent
from hastings.domain.story.canon.rules import canon_findings
from hastings.domain.story.catalog import StoryCatalog
from hastings.domain.story.mystery.models import TruthFact
from hastings.domain.story.narrative.models import Beat

MakeCatalog = Callable[..., StoryCatalog]


def _beat(artifact_id: str, **fields: object) -> tuple:
    return (artifact_id, kinds.KIND_BEAT, Beat(beat_id=artifact_id, **fields).to_content())


def _foreshadow(artifact_id: str, setup: str) -> tuple:
    return (artifact_id, kinds.KIND_FORESHADOW, Foreshadow(setup=setup).to_content())


def _clue(artifact_id: str, *, necessity: bool, points_to: tuple[str, ...] = ()) -> tuple:
    return (
        artifact_id,
        kinds.KIND_CLUE,
        Clue(statement=f"线索{artifact_id}", necessity=necessity, points_to=points_to).to_content(),
    )


def _red_herring(artifact_id: str, basis: str) -> tuple:
    return (
        artifact_id,
        kinds.KIND_RED_HERRING,
        RedHerring(false_explanation="以为是自杀", basis=basis).to_content(),
    )


def _timeline(
    artifact_id: str, *, time: str, place_id: str, participants: tuple[str, ...]
) -> tuple:
    return (
        artifact_id,
        kinds.KIND_TIMELINE_EVENT,
        TimelineEvent(time=time, place_id=place_id, participants=participants).to_content(),
    )


def test_clean_catalog_has_no_findings(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(
        [
            ("art_f1", kinds.KIND_TRUTH_FACT, TruthFact(statement="真相").to_content()),
            _clue("art_clue1", necessity=True, points_to=("art_f1",)),
            _red_herring("art_rh1", "被告有抑郁病史"),
        ]
    )
    assert canon_findings(catalog) == ()


def test_necessary_clue_without_truth_is_flagged(make_catalog: MakeCatalog) -> None:
    findings = canon_findings(make_catalog([_clue("art_clue1", necessity=True)]))
    assert [f.code for f in findings] == [ec.STORY_CLUE_WITHOUT_TRUTH]
    assert findings[0].severity_hint == "error"
    assert findings[0].where == "art_clue1"


def test_optional_clue_without_truth_is_allowed(make_catalog: MakeCatalog) -> None:
    assert canon_findings(make_catalog([_clue("art_clue1", necessity=False)])) == ()


def test_red_herring_without_cause_is_flagged(make_catalog: MakeCatalog) -> None:
    findings = canon_findings(make_catalog([_red_herring("art_rh1", "")]))
    assert [f.code for f in findings] == [ec.STORY_RED_HERRING_WITHOUT_CAUSE]
    assert findings[0].severity_hint == "error"


def test_timeline_conflict_same_time_diff_place(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(
        [
            _timeline("art_t1", time="22:00", place_id="art_p1", participants=("art_c1",)),
            _timeline("art_t2", time="22:00", place_id="art_p2", participants=("art_c1",)),
        ]
    )
    findings = canon_findings(catalog)
    assert [f.code for f in findings] == [ec.STORY_TIMELINE_CONFLICT]
    assert findings[0].details["participant"] == "art_c1"
    assert findings[0].details["time"] == "22:00"


def test_timeline_same_time_same_place_is_ok(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(
        [
            _timeline("art_t1", time="22:00", place_id="art_p1", participants=("art_c1",)),
            _timeline("art_t2", time="22:00", place_id="art_p1", participants=("art_c1",)),
        ]
    )
    assert canon_findings(catalog) == ()


def test_timeline_diff_time_diff_place_is_ok(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(
        [
            _timeline("art_t1", time="22:00", place_id="art_p1", participants=("art_c1",)),
            _timeline("art_t2", time="23:00", place_id="art_p2", participants=("art_c1",)),
        ]
    )
    assert canon_findings(catalog) == ()


def test_orphan_payoff_is_flagged(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(
        [
            _foreshadow("art_fs1", "第二把伞"),
            _beat("art_b2", payoffs=("art_fs1",)),
        ]
    )
    findings = canon_findings(catalog)
    assert [f.code for f in findings] == [ec.STORY_ORPHAN_PAYOFF]
    assert findings[0].details["foreshadow_id"] == "art_fs1"
    assert findings[0].details["payoff_beats"] == ["art_b2"]


def test_setup_and_payoff_together_is_ok(make_catalog: MakeCatalog) -> None:
    catalog = make_catalog(
        [
            _foreshadow("art_fs1", "第二把伞"),
            _beat("art_b1", setups=("art_fs1",)),
            _beat("art_b2", payoffs=("art_fs1",)),
        ]
    )
    assert canon_findings(catalog) == ()


def test_setup_deleted_makes_payoff_stale(make_catalog: MakeCatalog) -> None:
    # 建立方被撤销（setups 为空）后，回收方成为断裂/ stale 的 Payoff
    catalog = make_catalog(
        [
            _foreshadow("art_fs1", "第二把伞"),
            _beat("art_b1", setups=()),
            _beat("art_b2", payoffs=("art_fs1",)),
        ]
    )
    findings = canon_findings(catalog)
    assert [f.code for f in findings] == [ec.STORY_ORPHAN_PAYOFF]
