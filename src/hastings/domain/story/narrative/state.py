"""StoryStateAtBeat：按结构顺序折叠三层事实（Phase 13 T13.4b；modules §2.3/§6）。

三层互相独立：
- 真相层（TruthFact）不随 Beat 变化，任何位置的折叠结果都一样；
- 角色认知层（KnowledgeState）按结构顺序累积，后序 Beat 覆盖前序；
- 读者披露层（Disclosure）按结构顺序累积，同一事实可先后以不同解释出现。

折叠来源有两处且都被采纳（保持单一口径）：Beat 内联的 state_changes /
reader_disclosures，以及独立的 KnowledgeState / Disclosure 对象（按 at_beat_id
归位）。misbelieves 是合法认知，折叠不拦截；提前知情由 T13.5 规则判定。

泄压阀：未绑定/悬空引用的认知与披露以警告降级处理（应用于起点），未知状态
种类记入 custom 并告警，绝不静默丢弃（四透镜④ 可观测性）。
"""

from __future__ import annotations

from dataclasses import dataclass

from hastings.domain.story import error_codes as ec
from hastings.domain.story import kinds
from hastings.domain.story.canon.models import Relationship
from hastings.domain.story.catalog import StoryCatalog, StoryEntry
from hastings.domain.story.changes import StoryWarning
from hastings.domain.story.errors import violation
from hastings.domain.story.mystery.models import Disclosure, KnowledgeState
from hastings.domain.story.narrative.models import (
    STATE_CUSTOM,
    STATE_KNOWLEDGE,
    STATE_RELATIONSHIP,
    Beat,
    StateChange,
)
from hastings.domain.story.narrative.order import ordered_beats

__all__ = [
    "DisclosedFact",
    "KnowledgeEntry",
    "RelationshipState",
    "StateEntry",
    "StoryState",
    "story_state_at_beat",
    "story_state_final",
]


@dataclass(frozen=True, slots=True)
class KnowledgeEntry:
    """某角色对某事实的当前认知。"""

    character_id: str
    fact_id: str
    mode: str
    at_beat_id: str | None
    note: str = ""


@dataclass(frozen=True, slots=True)
class DisclosedFact:
    """读者已获得的事实与解释（true / false / partial）。"""

    fact_id: str
    audience: str
    interpretation: str
    at_beat_id: str | None


@dataclass(frozen=True, slots=True)
class StateEntry:
    """custom 或未知种类的状态变化；按应用顺序记录，绝不丢弃。"""

    kind: str
    value: str
    beat_id: str
    character_id: str | None
    target_id: str | None
    note: str = ""


@dataclass(frozen=True, slots=True)
class RelationshipState:
    """关系在某位置的公开/真实状态；changed_at_beat_id 为最后改变者。"""

    source_id: str
    target_id: str
    public_state: str = ""
    true_state: str = ""
    evolution: str = ""
    changed_at_beat_id: str | None = None


@dataclass(frozen=True, slots=True)
class StoryState:
    """某 Beat（含）之后（或之前）的故事状态快照；与 Commit 绑定。"""

    commit_id: str | None
    at_beat_id: str | None
    truths: tuple[str, ...]
    knowledge: tuple[KnowledgeEntry, ...]
    relationships: tuple[RelationshipState, ...]
    custom: tuple[StateEntry, ...]
    disclosures: tuple[DisclosedFact, ...]
    warnings: tuple[StoryWarning, ...]


def _record_knowledge(
    catalog: StoryCatalog,
    warnings: list[StoryWarning],
    knowledge: dict[tuple[str, str], KnowledgeEntry],
    *,
    character_id: str | None,
    fact_id: str | None,
    mode: str,
    at_beat_id: str | None,
    note: str,
    where: str,
) -> None:
    if not character_id or not fact_id:
        warnings.append(
            StoryWarning(
                code=ec.STORY_MISSING_PRECONDITION,
                where=where,
                why="认知变化缺少 character_id 或 target_id（事实）",
                fix="补齐角色与事实引用后重新记录",
            )
        )
        return
    if catalog.get(character_id) is None:
        warnings.append(
            StoryWarning(
                code=ec.STORY_UNKNOWN_ARTIFACT,
                where=where,
                why=f"角色 {character_id!r} 不在当前快照中",
                fix="改用快照内存在的角色 ID",
            )
        )
    if catalog.get(fact_id) is None:
        warnings.append(
            StoryWarning(
                code=ec.STORY_UNKNOWN_ARTIFACT,
                where=where,
                why=f"事实 {fact_id!r} 不在当前快照中",
                fix="改用快照内存在的真相事实 ID",
            )
        )
    knowledge[(character_id, fact_id)] = KnowledgeEntry(
        character_id=character_id, fact_id=fact_id, mode=mode, at_beat_id=at_beat_id, note=note
    )


def _record_disclosure(
    catalog: StoryCatalog,
    warnings: list[StoryWarning],
    disclosures: list[DisclosedFact],
    seen: set[tuple[str, str, str]],
    *,
    fact_id: str,
    audience: str,
    interpretation: str,
    at_beat_id: str | None,
    where: str,
) -> None:
    if not fact_id:
        warnings.append(
            StoryWarning(
                code=ec.STORY_MISSING_PRECONDITION,
                where=where,
                why="披露缺少 fact_id",
                fix="补齐被披露的真相事实 ID",
            )
        )
        return
    if catalog.get(fact_id) is None:
        warnings.append(
            StoryWarning(
                code=ec.STORY_UNKNOWN_ARTIFACT,
                where=where,
                why=f"披露的事实 {fact_id!r} 不在当前快照中",
                fix="改用快照内存在的真相事实 ID",
            )
        )
    key = (fact_id, audience, interpretation)
    if key in seen:
        return
    seen.add(key)
    disclosures.append(
        DisclosedFact(
            fact_id=fact_id, audience=audience, interpretation=interpretation, at_beat_id=at_beat_id
        )
    )


def _record_relationship(
    warnings: list[StoryWarning],
    relationships: dict[tuple[str, str], RelationshipState],
    change: StateChange,
    beat_id: str,
    where: str,
) -> None:
    if not change.character_id or not change.target_id:
        warnings.append(
            StoryWarning(
                code=ec.STORY_MISSING_PRECONDITION,
                where=where,
                why="关系状态变化缺少 character_id 或 target_id",
                fix="给出关系两端的人物 ID 后重新记录",
            )
        )
        return
    key = (change.character_id, change.target_id)
    existing = relationships.get(key)
    relationships[key] = RelationshipState(
        source_id=change.character_id,
        target_id=change.target_id,
        public_state=existing.public_state if existing else "",
        true_state=change.value,
        evolution=existing.evolution if existing else "",
        changed_at_beat_id=beat_id,
    )


def _fold(catalog: StoryCatalog, *, target: str | None, after: bool) -> StoryState:
    ordered = ordered_beats(catalog)
    included: tuple[StoryEntry, ...] = ordered
    at_label: str | None = None
    if target is not None:
        index = next(
            (i for i, entry in enumerate(ordered) if entry.artifact_id == target),
            -1,
        )
        if index < 0:
            raise violation(
                ec.STORY_UNKNOWN_ARTIFACT,
                "StoryStateAtBeat.beat_id",
                f"节拍 {target!r} 不在结构顺序中",
                "改用结构中存在的 Beat ID（先用 NarrativeTree 查询可用节拍）",
                beat_id=target,
            )
        included = ordered[: index + 1] if after else ordered[:index]
        at_label = target

    warnings: list[StoryWarning] = []
    knowledge: dict[tuple[str, str], KnowledgeEntry] = {}
    relationships: dict[tuple[str, str], RelationshipState] = {}
    custom: list[StateEntry] = []
    disclosures: list[DisclosedFact] = []
    disclosed_keys: set[tuple[str, str, str]] = set()

    for entry in catalog.by_kind(kinds.KIND_RELATIONSHIP):
        rel = Relationship.from_content(entry.content)
        relationships[(rel.source_id, rel.target_id)] = RelationshipState(
            source_id=rel.source_id,
            target_id=rel.target_id,
            public_state=rel.public_state,
            true_state=rel.true_state,
            evolution=rel.evolution,
        )

    known_beats = {entry.artifact_id for entry in ordered}
    knowledge_by_beat: dict[str, list[KnowledgeState]] = {}
    disclosure_by_beat: dict[str, list[Disclosure]] = {}
    unbound_knowledge: list[KnowledgeState] = []
    unbound_disclosures: list[Disclosure] = []
    for entry in catalog.by_kind(kinds.KIND_KNOWLEDGE):
        ks = KnowledgeState.from_content(entry.content)
        if ks.at_beat_id and ks.at_beat_id in known_beats:
            knowledge_by_beat.setdefault(ks.at_beat_id, []).append(ks)
        else:
            unbound_knowledge.append(ks)
            warnings.append(
                StoryWarning(
                    code=(
                        ec.STORY_UNKNOWN_ARTIFACT
                        if ks.at_beat_id
                        else ec.STORY_MISSING_PRECONDITION
                    ),
                    where=f"narrative.state:{entry.artifact_id}",
                    why=f"认知未绑定到有效节拍（at_beat_id={ks.at_beat_id!r}），按起点应用",
                    fix="记录时提供结构中存在的 at_beat_id",
                )
            )
    for entry in catalog.by_kind(kinds.KIND_DISCLOSURE):
        disclosure = Disclosure.from_content(entry.content)
        if disclosure.at_beat_id and disclosure.at_beat_id in known_beats:
            disclosure_by_beat.setdefault(disclosure.at_beat_id, []).append(disclosure)
        else:
            unbound_disclosures.append(disclosure)
            warnings.append(
                StoryWarning(
                    code=(
                        ec.STORY_UNKNOWN_ARTIFACT
                        if disclosure.at_beat_id
                        else ec.STORY_MISSING_PRECONDITION
                    ),
                    where=f"narrative.state:{entry.artifact_id}",
                    why=f"披露未绑定到有效节拍（at_beat_id={disclosure.at_beat_id!r}），按起点应用",
                    fix="记录时提供结构中存在的 at_beat_id",
                )
            )
    for k_group in knowledge_by_beat.values():
        k_group.sort(key=lambda k: (k.character_id, k.fact_id))
    for d_group in disclosure_by_beat.values():
        d_group.sort(key=lambda d: (d.fact_id, d.audience))
    unbound_knowledge.sort(key=lambda k: (k.character_id, k.fact_id))
    unbound_disclosures.sort(key=lambda d: (d.fact_id, d.audience))

    for ks in unbound_knowledge:
        _record_knowledge(
            catalog,
            warnings,
            knowledge,
            character_id=ks.character_id,
            fact_id=ks.fact_id,
            mode=ks.mode,
            at_beat_id=None,
            note=ks.note,
            where=f"narrative.state:{ks.character_id}:{ks.fact_id}",
        )
    for disclosure in unbound_disclosures:
        _record_disclosure(
            catalog,
            warnings,
            disclosures,
            disclosed_keys,
            fact_id=disclosure.fact_id,
            audience=disclosure.audience,
            interpretation=disclosure.interpretation,
            at_beat_id=None,
            where=f"narrative.state:{disclosure.fact_id}",
        )

    for entry in included:
        beat = Beat.from_content(entry.content)
        where = f"narrative.state:{entry.artifact_id}"
        for change in beat.state_changes:
            if change.kind == STATE_KNOWLEDGE:
                _record_knowledge(
                    catalog,
                    warnings,
                    knowledge,
                    character_id=change.character_id,
                    fact_id=change.target_id,
                    mode=change.value,
                    at_beat_id=entry.artifact_id,
                    note=change.note,
                    where=where,
                )
            elif change.kind == STATE_RELATIONSHIP:
                _record_relationship(warnings, relationships, change, entry.artifact_id, where)
            else:
                if change.kind != STATE_CUSTOM:
                    warnings.append(
                        StoryWarning(
                            code=ec.STORY_INVALID_COMMAND,
                            where=where,
                            why=f"状态变化种类 {change.kind!r} 未知，已按自定义状态记录",
                            fix="改用 knowledge/relationship/custom 之一，或让规则显式处理该种类",
                        )
                    )
                custom.append(
                    StateEntry(
                        kind=change.kind,
                        value=change.value,
                        beat_id=entry.artifact_id,
                        character_id=change.character_id,
                        target_id=change.target_id,
                        note=change.note,
                    )
                )
        for spec in beat.reader_disclosures:
            _record_disclosure(
                catalog,
                warnings,
                disclosures,
                disclosed_keys,
                fact_id=spec.fact_id,
                audience=spec.audience,
                interpretation=spec.interpretation,
                at_beat_id=entry.artifact_id,
                where=where,
            )
        for ks in knowledge_by_beat.get(entry.artifact_id, ()):
            _record_knowledge(
                catalog,
                warnings,
                knowledge,
                character_id=ks.character_id,
                fact_id=ks.fact_id,
                mode=ks.mode,
                at_beat_id=entry.artifact_id,
                note=ks.note,
                where=where,
            )
        for disclosure in disclosure_by_beat.get(entry.artifact_id, ()):
            _record_disclosure(
                catalog,
                warnings,
                disclosures,
                disclosed_keys,
                fact_id=disclosure.fact_id,
                audience=disclosure.audience,
                interpretation=disclosure.interpretation,
                at_beat_id=entry.artifact_id,
                where=where,
            )

    truths = tuple(sorted(entry.artifact_id for entry in catalog.by_kind(kinds.KIND_TRUTH_FACT)))
    return StoryState(
        commit_id=catalog.commit_id,
        at_beat_id=at_label,
        truths=truths,
        knowledge=tuple(sorted(knowledge.values(), key=lambda k: (k.character_id, k.fact_id))),
        relationships=tuple(
            sorted(relationships.values(), key=lambda r: (r.source_id, r.target_id))
        ),
        custom=tuple(custom),
        disclosures=tuple(disclosures),
        warnings=tuple(warnings),
    )


def story_state_final(catalog: StoryCatalog) -> StoryState:
    """折叠全部节拍，得到当前 Commit 下的最终状态。"""
    return _fold(catalog, target=None, after=True)


def story_state_at_beat(catalog: StoryCatalog, beat_id: str, *, after: bool = True) -> StoryState:
    """某 Beat 之后（after=True）或之前（after=False）的状态。"""
    return _fold(catalog, target=beat_id, after=after)
