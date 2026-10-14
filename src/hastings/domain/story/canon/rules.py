"""Canon 与结构的确定性规则（Phase 13 T13.5a；modules §5 不变量 6/7/8/9）。

规则是纯读函数：只产出结构化 FindingSeed（哪里错/为什么错/怎样算对 + 详情），
severity_hint 交由项目策略决定，领域层不据此改写状态、也不抛异常。硬不变量的
拒绝由命令层与 Guard 负责，本模块负责「事后体检」。

覆盖：必要线索缺真相（7）、误导缺真实依据（8）、时间线冲突（6）、Payoff 失去
建立方即断裂/ stale（9）。
"""

from __future__ import annotations

from hastings.domain.story import error_codes as ec
from hastings.domain.story import kinds
from hastings.domain.story.canon.models import Clue, RedHerring, TimelineEvent
from hastings.domain.story.catalog import StoryCatalog
from hastings.domain.story.changes import FindingSeed
from hastings.domain.story.narrative.models import Beat

__all__ = ["canon_findings"]


def _clue_findings(catalog: StoryCatalog) -> list[FindingSeed]:
    findings: list[FindingSeed] = []
    for entry in catalog.by_kind(kinds.KIND_CLUE):
        clue = Clue.from_content(entry.content)
        if clue.necessity and not clue.points_to:
            findings.append(
                FindingSeed(
                    code=ec.STORY_CLUE_WITHOUT_TRUTH,
                    where=entry.artifact_id,
                    why="必要线索没有关联任何真相（points_to 为空）",
                    fix="用 PlanClue 补 points_to（指向 TruthFact/MysteryTruth），或改为非必要线索",
                    severity_hint="error",
                    details={"statement": clue.statement},
                )
            )
    return findings


def _red_herring_findings(catalog: StoryCatalog) -> list[FindingSeed]:
    findings: list[FindingSeed] = []
    for entry in catalog.by_kind(kinds.KIND_RED_HERRING):
        red_herring = RedHerring.from_content(entry.content)
        if not red_herring.basis.strip():
            findings.append(
                FindingSeed(
                    code=ec.STORY_RED_HERRING_WITHOUT_CAUSE,
                    where=entry.artifact_id,
                    why="误导项没有真实的人物或世界原因（basis 为空）",
                    fix="补上能使该误解成立的依据（人物动机或世界规则）；误导不能只有元目的",
                    severity_hint="error",
                    details={"false_explanation": red_herring.false_explanation},
                )
            )
    return findings


def _timeline_findings(catalog: StoryCatalog) -> list[FindingSeed]:
    index: dict[tuple[str, str], dict[str, list[str]]] = {}
    for entry in catalog.by_kind(kinds.KIND_TIMELINE_EVENT):
        event = TimelineEvent.from_content(entry.content)
        for participant in event.participants:
            index.setdefault((participant, event.time), {}).setdefault(event.place_id, []).append(
                entry.artifact_id
            )
    findings: list[FindingSeed] = []
    for (participant, time), places in sorted(index.items()):
        distinct = sorted(place for place in places if place)
        if len(distinct) > 1:
            events = sorted(e for ids in places.values() for e in ids)
            findings.append(
                FindingSeed(
                    code=ec.STORY_TIMELINE_CONFLICT,
                    where=f"timeline:{participant}@{time}",
                    why=f"角色 {participant} 在同一时间 {time!r} 出现在不同地点：{distinct}",
                    fix="调整其中一处的时间或地点，或把并发事件标为不同时刻",
                    severity_hint="error",
                    details={
                        "participant": participant,
                        "time": time,
                        "places": distinct,
                        "events": events,
                    },
                )
            )
    return findings


def _payoff_findings(catalog: StoryCatalog) -> list[FindingSeed]:
    setups: dict[str, list[str]] = {}
    payoffs: dict[str, list[str]] = {}
    for entry in catalog.beats():
        beat = Beat.from_content(entry.content)
        for foreshadow_id in beat.setups:
            setups.setdefault(foreshadow_id, []).append(entry.artifact_id)
        for foreshadow_id in beat.payoffs:
            payoffs.setdefault(foreshadow_id, []).append(entry.artifact_id)
    findings: list[FindingSeed] = []
    for foreshadow_id, payoff_beats in sorted(payoffs.items()):
        if foreshadow_id not in setups:
            findings.append(
                FindingSeed(
                    code=ec.STORY_ORPHAN_PAYOFF,
                    where=foreshadow_id,
                    why=f"伏笔 {foreshadow_id} 有回收却没有建立方，Payoff 断裂/ stale",
                    fix="补回建立该伏笔的 Setup（Beat.setups），或撤销对应的 Payoff",
                    severity_hint="error",
                    details={
                        "foreshadow_id": foreshadow_id,
                        "payoff_beats": sorted(payoff_beats),
                    },
                )
            )
    return findings


def canon_findings(catalog: StoryCatalog) -> tuple[FindingSeed, ...]:
    """一次体检全部 Canon/结构规则；结果顺序确定（按规则分块、块内按 ID）。"""
    findings: list[FindingSeed] = []
    findings.extend(_clue_findings(catalog))
    findings.extend(_red_herring_findings(catalog))
    findings.extend(_timeline_findings(catalog))
    findings.extend(_payoff_findings(catalog))
    return tuple(findings)
