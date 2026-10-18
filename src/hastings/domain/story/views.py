"""Story 查询投影（Phase 13 T13.7；modules/story-domain.md §6）。

投影是派生结果，不得反向成为写入入口：全部是纯读函数 `f(catalog) -> view`，
不触碰 Repository、不产生 DomainChange。每个视图都携带 `commit_id`，且领域层
不做任何缓存——调用方每次显式给出目标 Commit，head 前移后重新构造目录即可，
因此不可能返回旧结果（计划 T13.7「缓存必须带 Commit ID」）。

五个投影：
- `CanonSnapshot`：指定 Commit 下的有效 Canon 事实（排除已被替代者，不变量 3）；
- `NarrativeTree`：结构树与每个节点的 stale/status；
- `DisclosureTimeline`：事实何时向读者与哪些角色披露（两个通道合并）；
- `MysteryFairnessView`：必要线索的出现位置、解释与揭晓位置，附规则 findings；
- `OpenSetupsView`：尚未回收或已经逾期的 Setup。

`StoryStateAtBeat` 的折叠结果由 `narrative/state.py` 提供（同样携带 commit_id），
本模块不重复实现。
"""

from __future__ import annotations

from dataclasses import dataclass

from hastings.domain.story import error_codes as ec
from hastings.domain.story import kinds
from hastings.domain.story.canon.models import Clue, Foreshadow
from hastings.domain.story.catalog import StoryCatalog, StoryEntry, display_name
from hastings.domain.story.changes import FindingSeed, StoryWarning
from hastings.domain.story.mystery.models import Disclosure, KnowledgeState
from hastings.domain.story.mystery.rules import mystery_findings
from hastings.domain.story.narrative.models import STATE_KNOWLEDGE, Beat
from hastings.domain.story.narrative.order import ordered_beats, sort_key, structural_order

__all__ = [
    "CanonSnapshot",
    "ClueReveal",
    "DisclosureEvent",
    "DisclosureTimeline",
    "MysteryFairnessView",
    "NarrativeNodeView",
    "NarrativeTree",
    "NecessaryClueView",
    "OpenSetupsView",
    "SetupView",
    "SupersededFact",
    "canon_snapshot",
    "disclosure_timeline",
    "mystery_fairness",
    "narrative_tree",
    "open_setups",
    "position_map",
    "reader_reveals",
]

_SEVERITY_ERROR = "error"
_SEVERITY_WARNING = "warning"

# 节点/节拍状态（NarrativeTree.status）
STATUS_OK = "ok"
STATUS_STALE = "stale"
STATUS_PLANNED = "planned"

# 披露通道
CHANNEL_REVEAL = "reveal"  # 向读者披露
CHANNEL_KNOWLEDGE = "knowledge"  # 向角色披露（认知状态）


def _order_key(entry: StoryEntry) -> str:
    raw = entry.content.get("order_key") or ""
    return raw if isinstance(raw, str) else str(raw)


def position_map(catalog: StoryCatalog) -> dict[str, int]:
    """结构顺序中节拍 ID -> 序号；未绑定/未知节拍查不到即为 None 语义。

    公开供线索图复用：叙述位置筛选（「截至某章」）与披露时间线、公平性判定
    共用同一份序号口径，不各建一份。
    """
    return {entry.artifact_id: index for index, entry in enumerate(ordered_beats(catalog))}


# --------------------------------------------------------------------------
# CanonSnapshot
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class SupersededFact:
    """一条替代关系：新事实取代旧事实，旧事实保留（不变量 3）。"""

    old_id: str
    new_id: str
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CanonSnapshot:
    """指定 Commit 下的有效 Canon 事实；已被替代的旧事实不在 facts 中。"""

    commit_id: str | None
    facts: tuple[StoryEntry, ...]
    superseded: tuple[SupersededFact, ...]


def canon_snapshot(catalog: StoryCatalog) -> CanonSnapshot:
    """全量 Canon 事实视图，排除被 supersedes 边指向的旧事实。"""
    superseded: dict[str, SupersededFact] = {}
    for relation in catalog.relations:
        if relation.relation_type != kinds.REL_SUPERSEDES:
            continue
        old_id = relation.target.value
        if old_id in superseded:
            continue
        reason = relation.metadata.get("reason", "")
        superseded[old_id] = SupersededFact(
            old_id=old_id,
            new_id=relation.source.value,
            reason=reason if isinstance(reason, str) else str(reason),
        )
    facts = tuple(entry for entry in catalog.canon() if entry.artifact_id not in superseded)
    return CanonSnapshot(
        commit_id=catalog.commit_id,
        facts=facts,
        superseded=tuple(superseded[key] for key in sorted(superseded)),
    )


# --------------------------------------------------------------------------
# NarrativeTree
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class NarrativeNodeView:
    """结构树节点：自底向上汇总 status，stale 来自 Payoff 断裂。"""

    artifact_id: str
    kind: str
    title: str
    order_key: str
    status: str
    stale: bool
    children: tuple[NarrativeNodeView, ...]


@dataclass(frozen=True, slots=True)
class NarrativeTree:
    """结构树 + 结构顺序 + 顺序告警；顺序与 structural_order 完全一致。"""

    commit_id: str | None
    roots: tuple[NarrativeNodeView, ...]
    order: tuple[str, ...]
    warnings: tuple[StoryWarning, ...]


def _stale_beats(catalog: StoryCatalog) -> set[str]:
    """回收方 stale：某节拍的 Payoff 引用了没有任何建立方的伏笔（不变量 9）。"""
    established: set[str] = set()
    payoffs: list[tuple[str, str]] = []
    for entry in catalog.beats():
        beat = Beat.from_content(entry.content)
        established.update(beat.setups)
        payoffs.extend((entry.artifact_id, foreshadow_id) for foreshadow_id in beat.payoffs)
    return {beat_id for beat_id, foreshadow_id in payoffs if foreshadow_id not in established}


def narrative_tree(catalog: StoryCatalog) -> NarrativeTree:
    """把结构节点与节拍展开为嵌套树；派生结果，不写回。"""
    ordering = structural_order(catalog)
    stale = _stale_beats(catalog)

    node_ids = {entry.artifact_id for entry in catalog.nodes()}
    children: dict[str, list[StoryEntry]] = {}
    roots: list[StoryEntry] = []
    for entry in (*catalog.nodes(), *catalog.beats()):
        parent_id = entry.content.get("parent_id")
        if isinstance(parent_id, str) and parent_id in node_ids:
            children.setdefault(parent_id, []).append(entry)
        else:
            roots.append(entry)

    def build(entry: StoryEntry) -> NarrativeNodeView:
        built = tuple(
            build(child) for child in sorted(children.get(entry.artifact_id, []), key=sort_key)
        )
        is_stale = entry.artifact_id in stale
        if is_stale or any(child.status == STATUS_STALE for child in built):
            status = STATUS_STALE
        elif not built and entry.kind in kinds.NARRATIVE_NODE_KINDS:
            status = STATUS_PLANNED  # 结构节点尚无子级 = 仅有骨架的规划位
        else:
            status = STATUS_OK
        return NarrativeNodeView(
            artifact_id=entry.artifact_id,
            kind=entry.kind,
            title=display_name(entry.kind, entry.content),
            order_key=_order_key(entry),
            status=status,
            stale=is_stale,
            children=built,
        )

    return NarrativeTree(
        commit_id=catalog.commit_id,
        roots=tuple(build(root) for root in sorted(roots, key=sort_key)),
        order=tuple(entry.artifact_id for entry in ordering.entries),
        warnings=ordering.warnings,
    )


# --------------------------------------------------------------------------
# DisclosureTimeline
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class DisclosureEvent:
    """一次披露：某事实向某受众（reader 或角色 ID）在某个节拍被交出。"""

    fact_id: str
    audience: str
    channel: str  # reveal（读者）/ knowledge（角色认知）
    interpretation: str  # 读者为 true/false/partial，角色为认知模式
    at_beat_id: str | None
    position: int | None  # 结构顺序位置；None 表示未绑定到有效节拍


@dataclass(frozen=True, slots=True)
class DisclosureTimeline:
    """按结构位置排序的披露时间线；未绑定节拍的披露以警告降级记录。"""

    commit_id: str | None
    events: tuple[DisclosureEvent, ...]
    warnings: tuple[StoryWarning, ...]


def _event_sort_key(event: DisclosureEvent) -> tuple[int, int, str, int, str]:
    position = event.position if event.position is not None else 0
    return (
        event.position is None,  # 未绑定的排到最后
        position,
        event.fact_id,
        0 if event.channel == CHANNEL_REVEAL else 1,  # 同位置先读者后角色
        event.audience,
    )


def disclosure_timeline(catalog: StoryCatalog) -> DisclosureTimeline:
    """合并读者披露与角色认知两个通道，按结构位置排序。"""
    positions = position_map(catalog)
    warnings: list[StoryWarning] = []
    events: list[DisclosureEvent] = []

    def add(
        *,
        fact_id: str,
        audience: str,
        channel: str,
        interpretation: str,
        at_beat_id: str | None,
        where: str,
    ) -> None:
        position = positions.get(at_beat_id) if at_beat_id else None
        if position is None:
            warnings.append(
                StoryWarning(
                    code=(
                        ec.STORY_UNKNOWN_ARTIFACT if at_beat_id else ec.STORY_MISSING_PRECONDITION
                    ),
                    where=where,
                    why=f"披露未绑定到有效节拍（at_beat_id={at_beat_id!r}），按起点记录",
                    fix="记录时提供结构中存在的 at_beat_id",
                )
            )
        events.append(
            DisclosureEvent(
                fact_id=fact_id,
                audience=audience,
                channel=channel,
                interpretation=interpretation,
                at_beat_id=at_beat_id,
                position=position,
            )
        )

    for entry in catalog.by_kind(kinds.KIND_DISCLOSURE):
        disclosure = Disclosure.from_content(entry.content)
        add(
            fact_id=disclosure.fact_id,
            audience=disclosure.audience,
            channel=CHANNEL_REVEAL,
            interpretation=disclosure.interpretation,
            at_beat_id=disclosure.at_beat_id,
            where=f"disclosure:{entry.artifact_id}",
        )
    for entry in catalog.by_kind(kinds.KIND_KNOWLEDGE):
        state = KnowledgeState.from_content(entry.content)
        add(
            fact_id=state.fact_id,
            audience=state.character_id,
            channel=CHANNEL_KNOWLEDGE,
            interpretation=state.mode,
            at_beat_id=state.at_beat_id,
            where=f"knowledge:{entry.artifact_id}",
        )
    for entry in catalog.beats():
        beat = Beat.from_content(entry.content)
        where = f"narrative.beat:{entry.artifact_id}"
        for spec in beat.reader_disclosures:
            add(
                fact_id=spec.fact_id,
                audience=spec.audience,
                channel=CHANNEL_REVEAL,
                interpretation=spec.interpretation,
                at_beat_id=entry.artifact_id,
                where=where,
            )
        for change in beat.state_changes:
            if change.kind != STATE_KNOWLEDGE or not change.character_id or not change.target_id:
                continue
            add(
                fact_id=change.target_id,
                audience=change.character_id,
                channel=CHANNEL_KNOWLEDGE,
                interpretation=change.value,
                at_beat_id=entry.artifact_id,
                where=where,
            )

    events.sort(key=_event_sort_key)
    return DisclosureTimeline(
        commit_id=catalog.commit_id, events=tuple(events), warnings=tuple(warnings)
    )


# --------------------------------------------------------------------------
# MysteryFairnessView
# --------------------------------------------------------------------------
def reader_reveals(catalog: StoryCatalog) -> dict[str, set[str]]:
    """事实 -> 向读者披露过它的节拍集合（合并内联披露与 Disclosure 对象）。

    公开供线索图复用：`points_to` 指向的事实「已在正文出现」的位置口径与
    公平性判定同源。
    """
    result: dict[str, set[str]] = {}
    for entry in catalog.by_kind(kinds.KIND_DISCLOSURE):
        disclosure = Disclosure.from_content(entry.content)
        if disclosure.at_beat_id:
            result.setdefault(disclosure.fact_id, set()).add(disclosure.at_beat_id)
    for entry in catalog.beats():
        for spec in Beat.from_content(entry.content).reader_disclosures:
            result.setdefault(spec.fact_id, set()).add(entry.artifact_id)
    return result


@dataclass(frozen=True, slots=True)
class ClueReveal:
    """线索指向的某事实，最早向读者揭晓的位置。"""

    fact_id: str
    revealed_at: str | None


@dataclass(frozen=True, slots=True)
class NecessaryClueView:
    """必要线索：出现位置、指向的事实与揭晓位置。"""

    clue_id: str
    statement: str
    points_to: tuple[str, ...]
    disclosure_beats: tuple[str, ...]
    first_position: int | None
    reveals: tuple[ClueReveal, ...]
    within_cutoff: bool | None  # None = 未配置阈值或线索无有效披露位置


@dataclass(frozen=True, slots=True)
class MysteryFairnessView:
    """必要线索的公平性视图；findings 复用确定性规则，不另起口径。"""

    commit_id: str | None
    total_beats: int
    fairness_cutoff: float | None
    clues: tuple[NecessaryClueView, ...]
    findings: tuple[FindingSeed, ...]


def mystery_fairness(
    catalog: StoryCatalog, *, fairness_cutoff: float | None = None
) -> MysteryFairnessView:
    """只统计必要线索；fairness_cutoff=None 表示该项目不做公平性判定。"""
    positions = position_map(catalog)
    total = len(positions)
    reveals = reader_reveals(catalog)
    cutoff_index = fairness_cutoff * total - 1 if fairness_cutoff is not None else None

    clues: list[NecessaryClueView] = []
    for entry in catalog.by_kind(kinds.KIND_CLUE):
        clue = Clue.from_content(entry.content)
        if not clue.necessity:
            continue
        plan = tuple(
            sorted((b for b in clue.disclosure_plan if b in positions), key=positions.__getitem__)
        )
        first_position = positions[plan[0]] if plan else None
        within_cutoff: bool | None
        if cutoff_index is None or first_position is None:
            within_cutoff = None
        else:
            within_cutoff = first_position <= cutoff_index
        clue_reveals = tuple(
            ClueReveal(
                fact_id=fact_id,
                revealed_at=_earliest(reveals.get(fact_id, set()), positions),
            )
            for fact_id in clue.points_to
        )
        clues.append(
            NecessaryClueView(
                clue_id=entry.artifact_id,
                statement=clue.statement,
                points_to=clue.points_to,
                disclosure_beats=plan,
                first_position=first_position,
                reveals=clue_reveals,
                within_cutoff=within_cutoff,
            )
        )
    return MysteryFairnessView(
        commit_id=catalog.commit_id,
        total_beats=total,
        fairness_cutoff=fairness_cutoff,
        clues=tuple(clues),
        findings=mystery_findings(catalog, fairness_cutoff=fairness_cutoff),
    )


def _earliest(beat_ids: set[str], positions: dict[str, int]) -> str | None:
    if not beat_ids:
        return None
    return min(beat_ids, key=lambda beat_id: (positions.get(beat_id, len(positions)), beat_id))


# --------------------------------------------------------------------------
# OpenSetupsView
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class SetupView:
    """一个尚未回收的 Setup；overdue 仅在项目给出 deadline 时判定。"""

    foreshadow_id: str
    setup: str
    expected_payoff: str
    setup_beats: tuple[str, ...]
    first_position: int | None
    overdue: bool


@dataclass(frozen=True, slots=True)
class OpenSetupsView:
    """未回收/逾期的 Setup 清单；无未回收项是合法空结果（§7 泄压阀）。"""

    commit_id: str | None
    deadline: float | None
    open_setups: tuple[SetupView, ...]


def open_setups(catalog: StoryCatalog, *, deadline: float | None = None) -> OpenSetupsView:
    """列出已建立但未回收的伏笔；deadline 为项目策略给的回收截止比例。"""
    positions = position_map(catalog)
    total = len(positions)
    established: dict[str, list[str]] = {}
    paid: set[str] = set()
    for entry in catalog.beats():
        beat = Beat.from_content(entry.content)
        for foreshadow_id in beat.setups:
            established.setdefault(foreshadow_id, []).append(entry.artifact_id)
        paid.update(beat.payoffs)

    cutoff_index = deadline * total - 1 if deadline is not None else None
    views: list[SetupView] = []
    for entry in catalog.by_kind(kinds.KIND_FORESHADOW):
        foreshadow_id = entry.artifact_id
        if foreshadow_id in paid or foreshadow_id not in established:
            continue  # 已回收，或从未被建立（那不是「未回收的 Setup」）
        foreshadow = Foreshadow.from_content(entry.content)
        setup_beats = tuple(
            sorted(set(established[foreshadow_id]), key=lambda b: positions.get(b, total))
        )
        first_position = positions.get(setup_beats[0]) if setup_beats else None
        overdue = (
            cutoff_index is not None
            and first_position is not None
            and first_position <= cutoff_index
        )
        views.append(
            SetupView(
                foreshadow_id=foreshadow_id,
                setup=foreshadow.setup,
                expected_payoff=foreshadow.expected_payoff,
                setup_beats=setup_beats,
                first_position=first_position,
                overdue=overdue,
            )
        )
    views.sort(key=lambda view: view.foreshadow_id)
    return OpenSetupsView(commit_id=catalog.commit_id, deadline=deadline, open_setups=tuple(views))
