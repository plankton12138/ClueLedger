"""时间线冲突检测：确定性纯函数规则集（P2 T5，计划 D2/D3）。

账本快照进，冲突列表出——不读盘、不碰 workspace、不依赖 LLM，
冲突判定是纯区间运算（D2）。三条规则：

- R0 数据自检：day<1、时刻非 HH:MM、t_end<t_start（不支持跨零点）。
  数据错误回喂而非环境故障；含 R0 的条目区间不可信，不参与 R1/R2；
- R1 人物分身：同角色同 day 事件区间重叠（半开区间，相切不算）。
  缺时刻按全天 [00:00,24:00) 处理——迫使模型补全时刻消歧；
- R2 不在场破绽（D3 方案 A）：alibi_claim 与该人物同 day 真实事件区间相交。

输出顺序确定：条目 R0（输入序）→ 主张 R0 → R1（人名字典序）→ R2（方法序）。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

from hastings.bible.models import AlibiClaim, Method, TimelineEntry

type Rule = Literal["R0", "R1", "R2"]

_MINUTES_PER_DAY: Final[int] = 24 * 60


@dataclass(frozen=True, slots=True)
class TimelineConflict:
    """一条冲突/数据错误：message 三要素直接喂回模型，结构化字段进 details。"""

    rule: Rule
    message: str
    day: int
    persons: tuple[str, ...]
    entries: tuple[str, ...]


def _parse_hhmm(value: str) -> int | None:
    """'HH:MM' → 当天分钟数；格式非法返回 None（24 小时制，00:00-23:59）。"""
    parts = value.split(":")
    if len(parts) != 2 or len(parts[0]) != 2 or len(parts[1]) != 2:
        return None
    hh, mm = parts
    if not (hh.isdigit() and mm.isdigit()):
        return None
    hours, minutes = int(hh), int(mm)
    if hours > 23 or minutes > 59:
        return None
    return hours * 60 + minutes


def _interval(t_start: str | None, t_end: str | None) -> tuple[int, int] | None:
    """有效区间（半开）；缺省端补全天边界，格式非法返回 None。"""
    start = _parse_hhmm(t_start) if t_start is not None else 0
    end = _parse_hhmm(t_end) if t_end is not None else _MINUTES_PER_DAY
    if start is None or end is None:
        return None
    return (start, end)


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """半开区间相交判定：相切（端点相等）不算重叠。"""
    return a[0] < b[1] and b[0] < a[1]


def _fmt_interval(t_start: str | None, t_end: str | None) -> str:
    minutes = _interval(t_start, t_end)
    if minutes is None:
        return "?"
    start, end = minutes
    if start == 0 and end == _MINUTES_PER_DAY:
        return "全天"
    hh = f"{start // 60:02d}:{start % 60:02d}"
    mm = f"{end // 60:02d}:{end % 60:02d}"
    return f"{hh}-{mm}" if t_start is not None or t_end is not None else "全天"


def _entry_data_conflicts(entry: TimelineEntry) -> list[TimelineConflict]:
    """R0：单条时间线事件的数据自检（day / 时刻格式 / 跨零点）。"""
    conflicts: list[TimelineConflict] = []
    if entry.day < 1:
        conflicts.append(
            TimelineConflict(
                rule="R0",
                message=(
                    f"时间线事件「{entry.name}」数据非法：day={entry.day}。"
                    "为什么错：day 从 1 起计数。"
                    "怎样算对：改为 ≥1 的整数；相对时间（如“三天后”）先换算成绝对 day，"
                    "原文写进 note 字段。"
                ),
                day=entry.day,
                persons=(),
                entries=(entry.name,),
            )
        )
    for field in ("t_start", "t_end"):
        value: str | None = getattr(entry, field)
        if value is not None and _parse_hhmm(value) is None:
            conflicts.append(
                TimelineConflict(
                    rule="R0",
                    message=(
                        f"时间线事件「{entry.name}」数据非法：{field}={value!r} 不是 HH:MM。"
                        "为什么错：时刻必须是 24 小时制 HH:MM（00:00-23:59）。"
                        '怎样算对：改为形如 "09:00"；跨零点区间拆成两天两条事件。'
                    ),
                    day=entry.day,
                    persons=(),
                    entries=(entry.name,),
                )
            )
    start = _parse_hhmm(entry.t_start) if entry.t_start is not None else None
    end = _parse_hhmm(entry.t_end) if entry.t_end is not None else None
    if start is not None and end is not None and end < start:
        conflicts.append(
            TimelineConflict(
                rule="R0",
                message=(
                    f"时间线事件「{entry.name}」数据非法：t_end({entry.t_end}) 早于 "
                    f"t_start({entry.t_start})。为什么错：不支持跨零点区间。"
                    "怎样算对：拆成两天两条事件（如 23:00-23:59 与次日 00:00-01:00）。"
                ),
                day=entry.day,
                persons=(),
                entries=(entry.name,),
            )
        )
    return conflicts


def _claim_data_conflicts(method: Method, claim: AlibiClaim) -> list[TimelineConflict]:
    """R0：不在场主张的数据自检（day / 时刻格式）。"""
    conflicts: list[TimelineConflict] = []
    who = f"手法「{method.name}」的不在场主张（{claim.person}）"
    if claim.day < 1:
        conflicts.append(
            TimelineConflict(
                rule="R0",
                message=(
                    f"{who}数据非法：day={claim.day}。"
                    "为什么错：day 从 1 起计数。"
                    "怎样算对：改为 ≥1 的整数，与真实时间线同一坐标系。"
                ),
                day=claim.day,
                persons=(claim.person,),
                entries=(method.name,),
            )
        )
    for field in ("t_start", "t_end"):
        value: str | None = getattr(claim, field)
        if value is not None and _parse_hhmm(value) is None:
            conflicts.append(
                TimelineConflict(
                    rule="R0",
                    message=(
                        f"{who}数据非法：{field}={value!r} 不是 HH:MM。"
                        "为什么错：时刻必须是 24 小时制 HH:MM（00:00-23:59）。"
                        '怎样算对：改为形如 "09:00"；跨零点主张拆成两条。'
                    ),
                    day=claim.day,
                    persons=(claim.person,),
                    entries=(method.name,),
                )
            )
    return conflicts


def _check_doppelganger(entries: Sequence[TimelineEntry]) -> list[TimelineConflict]:
    """R1：同角色同 day 的有效区间两两求交（不同事件之间）。"""
    grouped: dict[tuple[str, int], list[tuple[TimelineEntry, tuple[int, int]]]] = {}
    for entry in entries:
        interval = _interval(entry.t_start, entry.t_end)
        if interval is None:  # R0 已上报，区间不可信
            continue
        for person in dict.fromkeys(entry.participants):
            grouped.setdefault((person, entry.day), []).append((entry, interval))

    conflicts: list[TimelineConflict] = []
    for (person, day), items in sorted(grouped.items()):
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                first, second = items[i], items[j]
                first_entry, second_entry = first[0], second[0]
                if not _overlaps(first[1], second[1]):
                    continue
                first_iv = _fmt_interval(first_entry.t_start, first_entry.t_end)
                second_iv = _fmt_interval(second_entry.t_start, second_entry.t_end)
                conflicts.append(
                    TimelineConflict(
                        rule="R1",
                        message=(
                            f"人物分身：「{person}」在第 {day} 天的两个事件时间重叠——"
                            f"「{first_entry.name}」({first_iv}) 与"
                            f"「{second_entry.name}」({second_iv})。"
                            "为什么错：同一角色同一时段不能出现在两处。"
                            "怎样算对：调整任一事件的 day 或时刻，"
                            f"或把 {person} 从其中一处 participants 移除。"
                        ),
                        day=day,
                        persons=(person,),
                        entries=(first_entry.name, second_entry.name),
                    )
                )
    return conflicts


def _check_alibi(
    entries: Sequence[TimelineEntry], methods: Sequence[Method]
) -> list[TimelineConflict]:
    """R2（D3 方案 A）：不在场主张与该人物同 day 真实事件区间相交。"""
    real: dict[tuple[str, int], list[tuple[TimelineEntry, tuple[int, int]]]] = {}
    for entry in entries:
        interval = _interval(entry.t_start, entry.t_end)
        if interval is None:
            continue
        for person in dict.fromkeys(entry.participants):
            real.setdefault((person, entry.day), []).append((entry, interval))

    conflicts: list[TimelineConflict] = []
    for method in methods:
        for claim in method.alibi_claims:
            claim_interval = _interval(claim.t_start, claim.t_end)
            if claim_interval is None:  # 主张数据错误已在 R0 上报
                continue
            for event, event_interval in real.get((claim.person, claim.day), []):
                if not _overlaps(claim_interval, event_interval):
                    continue
                conflicts.append(
                    TimelineConflict(
                        rule="R2",
                        message=(
                            f"不在场破绽：「{claim.person}」主张第 {claim.day} 天 "
                            f"{_fmt_interval(claim.t_start, claim.t_end)} 不在现场"
                            f"（手法「{method.name}」：{claim.claim or '未填写主张'}），"
                            f"但真实时间线显示其参与了「{event.name}」"
                            f"({_fmt_interval(event.t_start, event.t_end)})，区间相交。"
                            "为什么错：不在场主张与已登记事件矛盾。"
                            "怎样算对：修改主张的 day/时刻/内容，或调整事件时间——"
                            "诡计依赖的不在场必须与时间线自洽。"
                        ),
                        day=claim.day,
                        persons=(claim.person,),
                        entries=(method.name, event.name),
                    )
                )
    return conflicts


def check_timeline(
    entries: Sequence[TimelineEntry], methods: Sequence[Method] = ()
) -> list[TimelineConflict]:
    """对账本快照做全量冲突检测；干净时间线返回空列表。"""
    conflicts: list[TimelineConflict] = []
    conflicts.extend(c for e in entries for c in _entry_data_conflicts(e))
    conflicts.extend(
        c for m in methods for claim in m.alibi_claims for c in _claim_data_conflicts(m, claim)
    )
    conflicts.extend(_check_doppelganger(entries))
    conflicts.extend(_check_alibi(entries, methods))
    return conflicts


__all__ = ["TimelineConflict", "check_timeline"]
