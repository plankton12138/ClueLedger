"""时间线投影（V03-7.6，计划 M12 时间线行）。

时间线只回显账本里已经记录的事件，不替作者推断顺序或精确时刻：

- **故事时间**：``TimelineEvent.time`` 是自由文本，原样显示；空文本报「时间未定」，
  不解析成时间戳、不把「晚上」补成「21:00」。区间文本（如「八点到九点」）原样保留。
- **叙述顺序**：只有角色认知记录带 ``at_beat_id``（获知事件），才按结构顺序算出
  位置下标；事件本身没有叙述位置，就显示「未记录」——**绝不用章节先后代替故事时间**。
- **真实事件 / 证词分轨**：``story.timeline_event`` 是实际发生的事件；``story.knowledge``
  是角色认知与证词（含 ``mode``：知道/怀疑/误信/不知道），保留原类型，不升级为真相。
  证词若指向某事实、事实又关联事件，只把事件作为「所指事件（真实时间另列）」，
  不把真实事件时间冒充证词时间。
- **分轨**：按参与者（角色）或地点分组；同一事件可出现在多条角色轨里，这是事实，
  不是重复记录。未记录地点/参与者的事件只报缺口，不猜归属。

``focus`` 只改变展示范围，不改事件内容与事实（筛选不改变事实）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from hastings.domain.story import error_codes as ec
from hastings.domain.story import kinds
from hastings.domain.story.canon.models import TimelineEvent
from hastings.domain.story.catalog import StoryCatalog, display_name
from hastings.domain.story.errors import violation
from hastings.domain.story.graph import (
    KNOWLEDGE_MODE_LABELS,
    LAYER_TRUTH,
    GraphEdge,
    GraphNode,
    graph_projection,
)
from hastings.domain.story.mystery.models import KnowledgeState
from hastings.domain.story.views import position_map

__all__ = [
    "GROUP_BY_CHARACTER",
    "GROUP_BY_PLACE",
    "ORDERING_NOTE",
    "TESTIMONY_TIME_NOTE",
    "TIMELINE_NO_EVENTS",
    "TIMELINE_PARTICIPANTS_NOT_RECORDED",
    "TIMELINE_PLACE_NOT_RECORDED",
    "TIMELINE_TESTIMONY_EVENT_NOT_RECORDED",
    "TIMELINE_TESTIMONY_FACT_UNRESOLVED",
    "TIMELINE_TESTIMONY_POSITION_UNRESOLVED",
    "TIMELINE_TIME_NOT_RECORDED",
    "TIME_RECORDED",
    "TIME_UNSPECIFIED",
    "TestimonyItem",
    "TestimonyTrack",
    "TimelineEventState",
    "TimelineGap",
    "TimelineGraph",
    "TimelineTrack",
    "timeline_projection",
]

GROUP_BY_CHARACTER = "character"
GROUP_BY_PLACE = "place"
GROUP_BYS: tuple[str, ...] = (GROUP_BY_CHARACTER, GROUP_BY_PLACE)

TIME_RECORDED = "recorded"
TIME_UNSPECIFIED = "unspecified"

TIMELINE_NO_EVENTS = "TIMELINE_NO_EVENTS"
TIMELINE_TIME_NOT_RECORDED = "TIMELINE_TIME_NOT_RECORDED"
TIMELINE_PLACE_NOT_RECORDED = "TIMELINE_PLACE_NOT_RECORDED"
TIMELINE_PARTICIPANTS_NOT_RECORDED = "TIMELINE_PARTICIPANTS_NOT_RECORDED"
TIMELINE_TESTIMONY_FACT_UNRESOLVED = "TIMELINE_TESTIMONY_FACT_UNRESOLVED"
TIMELINE_TESTIMONY_EVENT_NOT_RECORDED = "TIMELINE_TESTIMONY_EVENT_NOT_RECORDED"
TIMELINE_TESTIMONY_POSITION_UNRESOLVED = "TIMELINE_TESTIMONY_POSITION_UNRESOLVED"

ORDERING_NOTE = (
    "故事时间按账本原文显示（区间原样保留）；事件之间的先后顺序没有结构化记录，"
    "本图不排序，也不用章节先后代替故事时间。角色认知的叙述位置来自获知事件。"
)
TESTIMONY_TIME_NOTE = (
    "证词/认知保留原模式；所指事件的真实时间单独标注，不作为证词时间。"
)


@dataclass(frozen=True, slots=True)
class TimelineEventState:
    """一个真实事件：时间原文、地点、参与者与前后状态（全部原样回显）。"""

    event_id: str
    time_text: str
    time_status: str
    place_id: str
    participants: tuple[str, ...]
    before_state: str
    after_state: str


@dataclass(frozen=True, slots=True)
class TimelineTrack:
    """按角色或地点分的一条轨；``event_ids`` 保持账本顺序（不排序）。"""

    key: str
    kind: str
    id: str
    label: str
    event_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TestimonyItem:
    """一条证词/认知：角色、模式、备注原文、获知节拍与所指事件。"""

    knowledge_id: str
    character_id: str
    fact_id: str
    mode: str
    mode_label: str
    note: str
    at_beat_id: str | None
    position: int | None
    claimed_event_id: str | None
    claimed_event_time: str


@dataclass(frozen=True, slots=True)
class TestimonyTrack:
    character_id: str
    label: str
    items: tuple[TestimonyItem, ...]


@dataclass(frozen=True, slots=True)
class TimelineGap:
    code: str
    where: str
    why: str
    fix: str
    target_id: str | None = None


@dataclass(frozen=True, slots=True)
class TimelineGraph:
    """时间线视图：真实事件轨 + 证词轨 + 缺口（只读）。"""

    commit_id: str | None
    group_by: str
    events: tuple[TimelineEventState, ...]
    tracks: tuple[TimelineTrack, ...]
    testimonies: tuple[TestimonyTrack, ...]
    gaps: tuple[TimelineGap, ...]
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]


def _label(catalog: StoryCatalog, artifact_id: str) -> str:
    entry = catalog.get(artifact_id)
    return display_name(entry.kind, entry.content) if entry else artifact_id


def _as_ids(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item))
    return ()


def timeline_projection(
    catalog: StoryCatalog,
    *,
    group_by: str = GROUP_BY_CHARACTER,
    focus: str | None = None,
    evidence_status: Mapping[str, str] | None = None,
) -> TimelineGraph:
    """把账本事件与角色认知投影为分轨时间线；缺失只报缺口。"""
    if group_by not in GROUP_BYS:
        raise violation(
            ec.STORY_INVALID_COMMAND,
            "graphs.timeline.group_by",
            f"分轨方式只能是 {list(GROUP_BYS)}，收到 {group_by!r}",
            "按角色分轨用 character，按地点分轨用 place",
            group_by=group_by,
        )
    projection = graph_projection(catalog, evidence_status=evidence_status)
    node_ids = {node.id for node in projection.nodes}

    event_entries = sorted(
        catalog.by_kind(kinds.KIND_TIMELINE_EVENT), key=lambda item: item.artifact_id
    )
    events: list[TimelineEventState] = []
    gaps: list[TimelineGap] = []
    if not event_entries:
        gaps.append(
            TimelineGap(
                code=TIMELINE_NO_EVENTS,
                where="graphs.timeline",
                why="账本里还没有时间线事件",
                fix="在账本新建时间线事件，填写时间、地点与参与者；时间可以只写「时间未定」",
            )
        )
    for entry in event_entries:
        record = TimelineEvent.from_content(dict(entry.content))
        time_text = record.time.strip()
        if not time_text:
            gaps.append(
                TimelineGap(
                    code=TIMELINE_TIME_NOT_RECORDED,
                    where=f"timeline_event({entry.artifact_id}).time",
                    why="该事件没有记录时间",
                    fix="在账本事件里填写时间原文；不确定就写「时间未定」，不要猜精确时刻",
                    target_id=entry.artifact_id,
                )
            )
        if not record.place_id:
            gaps.append(
                TimelineGap(
                    code=TIMELINE_PLACE_NOT_RECORDED,
                    where=f"timeline_event({entry.artifact_id}).place_id",
                    why="该事件没有记录地点",
                    fix="在账本事件里选择地点；未记录时不会归入任何地点轨",
                    target_id=entry.artifact_id,
                )
            )
        if not record.participants:
            gaps.append(
                TimelineGap(
                    code=TIMELINE_PARTICIPANTS_NOT_RECORDED,
                    where=f"timeline_event({entry.artifact_id}).participants",
                    why="该事件没有记录参与者",
                    fix="在账本事件里补充参与者；未记录时不会归入任何角色轨",
                    target_id=entry.artifact_id,
                )
            )
        events.append(
            TimelineEventState(
                event_id=entry.artifact_id,
                time_text=time_text,
                time_status=TIME_RECORDED if time_text else TIME_UNSPECIFIED,
                place_id=record.place_id,
                participants=tuple(record.participants),
                before_state=record.before_state,
                after_state=record.after_state,
            )
        )

    tracks = _build_tracks(catalog, events, group_by)
    testimonies = _build_testimonies(catalog, gaps)

    selected_events = {event.event_id for event in events}
    selected_nodes = set(selected_events)
    for event in events:
        selected_nodes.update(event.participants)
        if event.place_id:
            selected_nodes.add(event.place_id)

    if focus is not None:
        if focus not in node_ids:
            raise violation(
                ec.STORY_UNKNOWN_ARTIFACT,
                "graphs.timeline.focus",
                f"聚焦对象 {focus!r} 不在当前快照中",
                "改用快照内存在的事件、人物或地点 ID",
                node_id=focus,
            )
        kind = _focus_kind(catalog, focus)
        if kind == kinds.KIND_TIMELINE_EVENT:
            tracks = tuple(track for track in tracks if focus in track.event_ids)
        elif kind in (kinds.KIND_CHARACTER, kinds.KIND_PLACE):
            tracks = tuple(track for track in tracks if track.id == focus)
        else:
            raise violation(
                ec.STORY_INVALID_COMMAND,
                "graphs.timeline.focus",
                f"对象 {focus!r} 不是时间线事件、人物或地点",
                "聚焦只能选事件、人物或地点；其他对象请用人物图/线索图/手法图",
                node_id=focus,
            )
        selected_events = {event_id for track in tracks for event_id in track.event_ids}
        selected_nodes = set(selected_events)
        for event in events:
            if event.event_id in selected_events:
                selected_nodes.update(event.participants)
                if event.place_id:
                    selected_nodes.add(event.place_id)
        selected_nodes.add(focus)
        testimonies = tuple(
            TestimonyTrack(
                character_id=track.character_id,
                label=track.label,
                items=tuple(
                    item
                    for item in track.items
                    if item.character_id in selected_nodes
                    or item.claimed_event_id in selected_events
                ),
            )
            for track in testimonies
            if track.character_id in selected_nodes
            or any(item.claimed_event_id in selected_events for item in track.items)
        )

    kept_events = [event for event in events if event.event_id in selected_events]
    edges = [
        edge
        for edge in projection.edges
        if edge.semantic_layer == LAYER_TRUTH
        and edge.source in selected_nodes
        and edge.target in selected_nodes
    ]
    nodes = tuple(node for node in projection.nodes if node.id in selected_nodes)
    return TimelineGraph(
        commit_id=projection.commit_id,
        group_by=group_by,
        events=tuple(kept_events),
        tracks=tracks,
        testimonies=testimonies,
        gaps=tuple(gaps),
        nodes=nodes,
        edges=tuple(edges),
    )


def _build_tracks(
    catalog: StoryCatalog,
    events: list[TimelineEventState],
    group_by: str,
) -> tuple[TimelineTrack, ...]:
    buckets: dict[str, list[str]] = {}
    for event in events:
        ids = event.participants if group_by == GROUP_BY_CHARACTER else (event.place_id,)
        for item in ids:
            if item:
                buckets.setdefault(item, []).append(event.event_id)
    tracks: list[TimelineTrack] = []
    for item in sorted(buckets):
        tracks.append(
            TimelineTrack(
                key=f"{group_by}:{item}",
                kind=group_by,
                id=item,
                label=_label(catalog, item),
                event_ids=tuple(buckets[item]),
            )
        )
    return tuple(tracks)


def _build_testimonies(
    catalog: StoryCatalog, gaps: list[TimelineGap]
) -> tuple[TestimonyTrack, ...]:
    positions = position_map(catalog)
    buckets: dict[str, list[TestimonyItem]] = {}
    for entry in sorted(catalog.by_kind(kinds.KIND_KNOWLEDGE), key=lambda item: item.artifact_id):
        knowledge = KnowledgeState.from_content(dict(entry.content))
        fact = catalog.get(knowledge.fact_id)
        claimed_event_id: str | None = None
        claimed_event_time = ""
        if fact is None:
            gaps.append(
                TimelineGap(
                    code=TIMELINE_TESTIMONY_FACT_UNRESOLVED,
                    where=f"knowledge({entry.artifact_id}).fact_id",
                    why=f"证词指向的事实 {knowledge.fact_id!r} 不在当前版本",
                    fix="先补该事实或修正引用；悬空证词不进入所指事件",
                    target_id=entry.artifact_id,
                )
            )
        else:
            claimed_event_id = str(fact.content.get("event_id") or "") or None
            if claimed_event_id is None:
                gaps.append(
                    TimelineGap(
                        code=TIMELINE_TESTIMONY_EVENT_NOT_RECORDED,
                        where=f"knowledge({entry.artifact_id}).fact_id",
                        why="这条证词指向的事实没有关联事件，无法落到时间线",
                        fix="在事实条目里关联事件；证词本身仍保留原认知类型",
                        target_id=entry.artifact_id,
                    )
                )
            else:
                event = catalog.get(claimed_event_id)
                if event is not None:
                    claimed_event_time = str(event.content.get("time") or "")
        position: int | None = None
        if knowledge.at_beat_id:
            position = positions.get(knowledge.at_beat_id)
            if position is None:
                gaps.append(
                    TimelineGap(
                        code=TIMELINE_TESTIMONY_POSITION_UNRESOLVED,
                        where=f"knowledge({entry.artifact_id}).at_beat_id",
                        why=f"获知节拍 {knowledge.at_beat_id!r} 不在叙述结构中",
                        fix="改选结构中存在的节拍；未记录获知事件时不推算叙述位置",
                        target_id=entry.artifact_id,
                    )
                )
        buckets.setdefault(knowledge.character_id, []).append(
            TestimonyItem(
                knowledge_id=entry.artifact_id,
                character_id=knowledge.character_id,
                fact_id=knowledge.fact_id,
                mode=knowledge.mode,
                mode_label=KNOWLEDGE_MODE_LABELS.get(knowledge.mode, knowledge.mode),
                note=knowledge.note,
                at_beat_id=knowledge.at_beat_id,
                position=position,
                claimed_event_id=claimed_event_id,
                claimed_event_time=claimed_event_time,
            )
        )
    return tuple(
        TestimonyTrack(
            character_id=character_id,
            label=_label(catalog, character_id),
            items=tuple(items),
        )
        for character_id, items in sorted(buckets.items())
    )


def _focus_kind(catalog: StoryCatalog, artifact_id: str) -> str:
    entry = catalog.get(artifact_id)
    return entry.kind if entry is not None else ""
