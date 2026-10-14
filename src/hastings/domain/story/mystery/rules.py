"""悬疑确定性规则（Phase 13 T13.5b；modules §5 不变量 5/11）。

两条规则：
- 提前知情（不变量 5）：某角色以「knows」认知某事实的节拍，早于该事实向读者
  首次披露的节拍（或该事实从未披露）即违规；misbelieves/suspects 是合法认知，
  不参与判定。定位精确到「角色 / 事实 / 知情节拍 / 披露节拍」。
- 公平性截止（不变量 11）：必要线索的披露位置必须早于项目策略设定的截止点。
  阈值由项目配置（fairness_cutoff），非悬疑项目传 None 即整体跳过，绝不把
  百分比硬编码成所有题材共用。

规则只产出 FindingSeed，severity_hint 交由项目策略决定。
"""

from __future__ import annotations

from hastings.domain.story import error_codes as ec
from hastings.domain.story import kinds
from hastings.domain.story.canon.models import Clue
from hastings.domain.story.catalog import StoryCatalog
from hastings.domain.story.changes import FindingSeed
from hastings.domain.story.mystery.models import Disclosure, KnowledgeState
from hastings.domain.story.narrative.models import STATE_KNOWLEDGE, Beat
from hastings.domain.story.narrative.order import ordered_beats

__all__ = ["mystery_findings"]

_KNOWLEDGE_KNOWS = "knows"


def _collect_knowledge(catalog: StoryCatalog) -> list[tuple[str, str, str, str | None]]:
    """(character_id, fact_id, mode, beat_id)：合并认知对象与节拍内联状态变化。"""
    result: list[tuple[str, str, str, str | None]] = []
    seen: set[tuple[str, str, str, str | None]] = set()
    for entry in catalog.by_kind(kinds.KIND_KNOWLEDGE):
        state = KnowledgeState.from_content(entry.content)
        item = (state.character_id, state.fact_id, state.mode, state.at_beat_id)
        if item not in seen:
            seen.add(item)
            result.append(item)
    for entry in catalog.beats():
        beat = Beat.from_content(entry.content)
        for change in beat.state_changes:
            if change.kind == STATE_KNOWLEDGE and change.character_id and change.target_id:
                item = (change.character_id, change.target_id, change.value, entry.artifact_id)
                if item not in seen:
                    seen.add(item)
                    result.append(item)
    return result


def _collect_reveals(catalog: StoryCatalog) -> dict[str, set[str]]:
    """事实 -> 向读者披露过它的节拍集合（合并内联披露与 Disclosure 对象）。"""
    result: dict[str, set[str]] = {}
    for entry in catalog.by_kind(kinds.KIND_DISCLOSURE):
        disclosure = Disclosure.from_content(entry.content)
        if disclosure.at_beat_id:
            result.setdefault(disclosure.fact_id, set()).add(disclosure.at_beat_id)
    for entry in catalog.beats():
        beat = Beat.from_content(entry.content)
        for spec in beat.reader_disclosures:
            result.setdefault(spec.fact_id, set()).add(entry.artifact_id)
    return result


def _knowledge_findings(catalog: StoryCatalog) -> list[FindingSeed]:
    beats = ordered_beats(catalog)
    order = {entry.artifact_id: index for index, entry in enumerate(beats)}
    reveals = _collect_reveals(catalog)

    def earliest_reveal(fact_id: str) -> str | None:
        candidates = reveals.get(fact_id)
        if not candidates:
            return None
        return min(candidates, key=lambda beat_id: order.get(beat_id, len(order)))

    findings: list[FindingSeed] = []
    for character_id, fact_id, mode, beat_id in _collect_knowledge(catalog):
        if mode != _KNOWLEDGE_KNOWS:
            continue
        reveal_beat = earliest_reveal(fact_id)
        know_index = order.get(beat_id, -1) if beat_id else -1
        reveal_index = order.get(reveal_beat, len(order)) if reveal_beat else len(order)
        if know_index < reveal_index:
            findings.append(
                FindingSeed(
                    code=ec.STORY_INVALID_KNOWLEDGE_ORDER,
                    where=f"knowledge:{character_id}->{fact_id}",
                    why=(
                        f"角色 {character_id} 在节拍 {beat_id!r} 就以「已知」为前提，"
                        f"但事实 {fact_id} 向读者披露的位置是 {reveal_beat!r}（更晚或从未）"
                    ),
                    fix="把认知推到披露之后，或改为 suspects/misbelieves；如为伏笔请先安排暴露线索",
                    severity_hint="error",
                    details={
                        "character_id": character_id,
                        "fact_id": fact_id,
                        "mode": mode,
                        "know_beat": beat_id,
                        "reveal_beat": reveal_beat,
                    },
                )
            )
    return findings


def _fairness_findings(catalog: StoryCatalog, fairness_cutoff: float | None) -> list[FindingSeed]:
    if fairness_cutoff is None:
        return []
    beats = ordered_beats(catalog)
    total = len(beats)
    order = {entry.artifact_id: index for index, entry in enumerate(beats)}
    # 截止「位置」：允许披露的最靠后索引（0 基）
    cutoff_index = fairness_cutoff * total - 1
    findings: list[FindingSeed] = []
    for entry in catalog.by_kind(kinds.KIND_CLUE):
        clue = Clue.from_content(entry.content)
        if not clue.necessity:
            continue
        indices = [order[beat_id] for beat_id in clue.disclosure_plan if beat_id in order]
        if not indices:
            findings.append(
                FindingSeed(
                    code=ec.STORY_FAIRNESS_VIOLATION,
                    where=entry.artifact_id,
                    why="必要线索没有安排在结构中的任何披露位置",
                    fix="在 disclosure_plan 里指定一个早于公平性截止点的 Beat",
                    severity_hint="warning",
                    details={
                        "clue_id": entry.artifact_id,
                        "cutoff": fairness_cutoff,
                        "total_beats": total,
                    },
                )
            )
            continue
        first_index = min(indices)
        if first_index > cutoff_index:
            first_beat = next(b for b in beats if order[b.artifact_id] == first_index).artifact_id
            findings.append(
                FindingSeed(
                    code=ec.STORY_FAIRNESS_VIOLATION,
                    where=entry.artifact_id,
                    why=(
                        f"必要线索最早在位置 {first_index}/{total} 披露，晚于公平性截止点"
                        f"（{fairness_cutoff:.0%}）"
                    ),
                    fix="把披露提前到截止点之前，或调整项目公平性策略（阈值是项目策略，不是硬编码）",
                    severity_hint="warning",
                    details={
                        "clue_id": entry.artifact_id,
                        "first_disclosure_beat": first_beat,
                        "position": first_index,
                        "total_beats": total,
                        "cutoff": fairness_cutoff,
                    },
                )
            )
    return findings


def mystery_findings(
    catalog: StoryCatalog, *, fairness_cutoff: float | None = None
) -> tuple[FindingSeed, ...]:
    """一次体检悬疑规则；fairness_cutoff=None 表示跳过公平性判定。"""
    return (
        *_knowledge_findings(catalog),
        *_fairness_findings(catalog, fairness_cutoff),
    )
