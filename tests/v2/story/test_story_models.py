"""T13.1 基座与模型测试：kinds/错误码/变更值对象/Canon-Narrative-Mystery 编解码。

关键断言：
- 未知旧字段进入 legacy_metadata，不静默丢失；
- 缺必填字段给出 STORY_MISSING_REQUIRED_FIELD 三要素错误；
- to_content/from_content 往返稳定。
"""

from __future__ import annotations

import pytest

from hastings.domain.repository.ids import ArtifactId
from hastings.domain.repository.requests import ArtifactChange
from hastings.domain.story import error_codes as ec
from hastings.domain.story import kinds
from hastings.domain.story.canon.models import Character, Clue, MysteryTruth
from hastings.domain.story.changes import (
    DomainChange,
    FindingSeed,
    ImpactSeed,
    RelationSpec,
    StoryWarning,
)
from hastings.domain.story.errors import StoryViolation
from hastings.domain.story.mystery.models import Disclosure, KnowledgeState, TruthFact
from hastings.domain.story.narrative.models import Beat, DisclosureSpec, NarrativeNode, StateChange


def _fake_change() -> ArtifactChange:
    return ArtifactChange(
        artifact_id=ArtifactId("art_x"), content={"a": 1}, kind=kinds.KIND_CHARACTER
    )


def test_is_story_kind() -> None:
    assert kinds.is_story_kind(kinds.KIND_CHARACTER)
    assert kinds.is_story_kind(kinds.KIND_BEAT)
    assert not kinds.is_story_kind("manuscript.chapter")
    assert kinds.KIND_CHARACTER in kinds.KNOWN_STORY_KINDS


def test_domain_change_is_empty() -> None:
    # 只有可提交内容（Artifact/Relation 变化）才算「非空」；仅警告/种子/摘要
    # 意味着无需提交，是合法空结果（泄压阀）。
    assert DomainChange().is_empty()
    assert DomainChange(summary="仅提示").is_empty()
    assert DomainChange(warnings=(StoryWarning(code="x", where="w", why="y"),)).is_empty()
    assert not DomainChange(artifact_changes=(_fake_change(),)).is_empty()


def test_relation_spec_to_change_wraps_artifact_id() -> None:
    spec = RelationSpec(
        action="add",
        source="art_a",
        relation_type=kinds.REL_REVEALS,
        target="art_b",
    )
    change = spec.to_change()
    assert change.action == "add"
    assert change.source.value == "art_a"
    assert change.target.value == "art_b"
    assert change.relation_type == kinds.REL_REVEALS


def test_impact_seed_and_finding_seed_defaults() -> None:
    seed = ImpactSeed(target_kind=kinds.KIND_BEAT, target_id="b1", reason="role change")
    assert seed.certainty == "explicit"
    finding = FindingSeed(
        code=ec.STORY_ORPHAN_PAYOFF, where="beat:b9", why="payoff 无 setup", fix="补 setup"
    )
    assert finding.severity_hint == "warning"


def test_character_roundtrip_preserves_legacy_fields() -> None:
    content = Character(name="宫部美雪", role="antagonist", traits=("冷静",)).to_content()
    content["v1_unknown_note"] = "旧字段"
    content["v1_rating"] = 5

    restored = Character.from_content(content)

    assert restored.name == "宫部美雪"
    assert restored.role == "antagonist"
    assert restored.traits == ("冷静",)
    assert restored.legacy_metadata == {"v1_unknown_note": "旧字段", "v1_rating": 5}
    # 往返后未知字段仍在，且不会污染已知字段
    assert restored.to_content()["v1_unknown_note"] == "旧字段"


def test_character_missing_required_field_raises_located_error() -> None:
    with pytest.raises(StoryViolation) as exc:
        Character.from_content({"identity": "无名字"})
    assert exc.value.code == ec.STORY_MISSING_REQUIRED_FIELD
    assert exc.value.where == "canon.character"
    assert "name" in exc.value.why
    assert exc.value.fix


def test_mystery_truth_requires_culprit() -> None:
    with pytest.raises(StoryViolation) as exc:
        MysteryTruth.from_content({"motive": "继承遗产"})
    assert exc.value.code == ec.STORY_MISSING_REQUIRED_FIELD


def test_clue_defaults_and_roundtrip() -> None:
    clue = Clue(statement="门锁内侧有划痕", points_to=("fact-1",), necessity=True)
    restored = Clue.from_content(clue.to_content())
    assert restored.necessity is True
    assert restored.points_to == ("fact-1",)
    assert restored.disclosure_plan == ()


def test_narrative_beat_roundtrip_nested_values() -> None:
    beat = Beat(
        beat_id="beat-1",
        parent_id="scene-1",
        purpose="揭示密室手法",
        state_changes=(
            StateChange(kind="knowledge", value="knows", character_id="c1", target_id="f1"),
        ),
        reader_disclosures=(DisclosureSpec(fact_id="f1"),),
        setups=(),
        payoffs=(),
        order_key="a1",
    )
    restored = Beat.from_content(beat.to_content())
    assert restored.beat_id == "beat-1"
    assert restored.state_changes[0].value == "knows"
    assert restored.reader_disclosures[0].audience == "reader"
    assert restored.parent_id == "scene-1"


def test_narrative_node_roundtrip() -> None:
    node = NarrativeNode(node_id="n1", kind=kinds.KIND_CHAPTER, title="第一章", order_key="a")
    restored = NarrativeNode.from_content(node.to_content())
    assert restored.kind == kinds.KIND_CHAPTER
    assert restored.parent_id is None


def test_three_layer_facts_are_independent_models() -> None:
    fact = TruthFact(statement="凶手从窗户进入").to_content()
    knowledge = KnowledgeState(character_id="c1", fact_id="f1", mode="misbelieves").to_content()
    disclosure = Disclosure(fact_id="f1", at_beat_id="b1").to_content()

    # 三层各自有独立字段，不共享同一结构
    assert "statement" in fact and "mode" not in fact
    assert "mode" in knowledge and "statement" not in knowledge
    assert "interpretation" in disclosure and "mode" not in disclosure
