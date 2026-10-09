"""审计报告聚合（P7 T8，计划 D7）：纯派生、无新存储。

数据全部来自既有账本 YAML 与 stats 派生——不引入第二份账本数据源；
检查清单顺序固定：timeline_conflict → foreshadow_overdue → clue_planting
→ method_state。

口径诚实标注（写进响应与前端提示）：三指标是**结构代理**，非语义判断——
「人物一致性 96%」的准确读法是「时间线区间冲突密度低」；语义级检查移交
P9（§5）。泄压阀：账本损坏 / IO 故障原样上抛交 REST 映射 422/500；空账本
与无章节是合法空结果（清单空，不报错）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from hastings.bible.models import Clue, Foreshadow, Method, TimelineEntry
from hastings.bible.stats import (
    StoryStats,
    compute_story_stats,
    foreshadow_risk,
    parse_chapter_number,
)
from hastings.bible.store import StoryWorkspace, load_ledger
from hastings.bible.timeline_check import TimelineConflict, check_timeline

Severity = Literal["error", "warn"]


@dataclass(frozen=True, slots=True)
class AuditRatio:
    """二元比率指标（回收率 / 公平呈现率）：count 语义随指标（resolved/presented）。"""

    rate: float
    count: int
    total: int


@dataclass(frozen=True, slots=True)
class AuditConsistency:
    """人物一致性（结构代理）：1 - 冲突数 / 时间线条目数；空时间线为 None。"""

    rate: float
    conflicts: int
    entries: int


@dataclass(frozen=True, slots=True)
class AuditCheck:
    """一条检查结果：message 三要素，anchor_* 喂前端「定位问题章节」。"""

    rule: str
    severity: Severity
    message: str
    anchor_chapter: str | None = None
    anchor_name: str | None = None


@dataclass(frozen=True, slots=True)
class AuditReport:
    """聚合报告（D7 wire 底座）：三指标 + 固定顺序检查清单。"""

    chapter_id: str
    foreshadow_recovery: AuditRatio
    clue_fairness: AuditRatio
    character_consistency: AuditConsistency | None
    checks: list[AuditCheck]


def _timeline_conflict_check(
    conflict: TimelineConflict,
    chapter_of_entry: dict[str, str],
) -> AuditCheck:
    """R0（数据错误）与 R2（不在场矛盾）→ error；R1（分身重叠）→ warn。

    anchor：用冲突涉及的首个条目名查其叙述章（无则 None，前端不显示定位）。
    """
    severity: Severity = "warn" if conflict.rule == "R1" else "error"
    anchor_name = conflict.entries[0] if conflict.entries else None
    anchor_chapter = chapter_of_entry.get(anchor_name) if anchor_name is not None else None
    return AuditCheck(
        rule="timeline_conflict",
        severity=severity,
        message=conflict.message,
        anchor_chapter=anchor_chapter,
        anchor_name=anchor_name,
    )


def _foreshadow_overdue_checks(
    foreshadows: list[Foreshadow], stats: StoryStats
) -> list[AuditCheck]:
    """逾期伏笔逐条一条（stats 同款风险派生口径，含 content 供修文定位）。"""
    checks: list[AuditCheck] = []
    for f in foreshadows:
        if foreshadow_risk(f, stats.latest_chapter) != "overdue":
            continue
        checks.append(
            AuditCheck(
                rule="foreshadow_overdue",
                severity="error",
                message=(
                    f"伏笔「{f.name}」预计于 {f.expected_resolve_chapter or '（未填）'} 回收，"
                    f"当前已写至 ch{stats.latest_chapter:02d} 仍未回收"
                    f"（状态：{f.status}；内容：{f.content or '—'}）。"
                    "为什么错：超过计划回收章后悬念张力衰减，读者公平性受损。"
                    "怎样算对：在后续章节尽快呼应回收，或用 bible_upsert 把"
                    " expected_resolve_chapter 顺延并给出理由。"
                ),
                anchor_chapter=f.expected_resolve_chapter or f.plant_chapter or None,
                anchor_name=f.name,
            )
        )
    return checks


def _clue_planting_checks(clues: list[Clue], audit_chapter: int | None) -> list[AuditCheck]:
    """公平呈现检查：非红鲱鱼已揭晓（interpreted/resolved）但 chapter 空或
    章号 > 审计章 → 「揭晓前未公平呈现」（warn）。

    审计章不可解析（序章等）按 0 处理——任何已揭晓线索都算未呈现。
    """
    checks: list[AuditCheck] = []
    threshold = audit_chapter if audit_chapter is not None else 0
    for clue in clues:
        if clue.is_red_herring or clue.status not in ("interpreted", "resolved"):
            continue
        chapter_num = parse_chapter_number(clue.chapter)
        if chapter_num is not None and chapter_num <= threshold:
            continue
        where = f"ch{chapter_num:02d}" if chapter_num is not None else "（未填章节）"
        checks.append(
            AuditCheck(
                rule="clue_planting",
                severity="warn",
                message=(
                    f"线索「{clue.name}」已{clue.status}，但其公平呈现章为 {where}"
                    f"（审计章 ch{threshold:02d} 之后或缺失）；"
                    f"内容：{clue.content or '—'}。"
                    "为什么错：揭晓（解答）先于呈现（铺垫）违反公平性原则——"
                    "读者没有机会先看到线索。"
                    "怎样算对：把该线索的呈现写进审计章或更早章节，"
                    "或用 bible_upsert 修正 chapter 字段。"
                ),
                anchor_chapter=clue.chapter or None,
                anchor_name=clue.name,
            )
        )
    return checks


def _method_state_check(foreshadows: list[Foreshadow], methods: list[Method]) -> AuditCheck | None:
    """手法状态检查：任一伏笔 resolved 而手法全 drafted（或无手法登记）→ warn。"""
    if not any(f.status == "resolved" for f in foreshadows):
        return None
    if methods and any(m.status == "revealed" for m in methods):
        return None
    names = "、".join(m.name for m in methods) if methods else "（无手法登记）"
    return AuditCheck(
        rule="method_state",
        severity="warn",
        message=(
            f"谜底已揭晓（存在 resolved 伏笔），但手法状态未登记 revealed（{names}）。"
            "为什么错：手法账本与伏笔进度不同步，后续章节可能重复使用未声明手法。"
            "怎样算对：用 method_lock 登记（或把对应手法状态置为 revealed）。"
        ),
        anchor_name=methods[0].name if methods else None,
    )


def build_audit_report(workspace: StoryWorkspace, chapter_id: str) -> AuditReport:
    """聚合单章审计报告；账本损坏 / IO 故障原样上抛（REST 映射 422/500）。"""
    stats = compute_story_stats(workspace)
    clues = load_ledger(workspace.clues_path, Clue)
    timeline = load_ledger(workspace.timeline_path, TimelineEntry)
    methods = load_ledger(workspace.method_path, Method)
    foreshadows = load_ledger(workspace.foreshadows_path, Foreshadow)

    conflicts = check_timeline(timeline, methods)
    chapter_of_entry = {e.name: e.chapter for e in timeline}
    consistency = (
        AuditConsistency(
            rate=max(0.0, 1.0 - len(conflicts) / len(timeline)),
            conflicts=len(conflicts),
            entries=len(timeline),
        )
        if timeline
        else None
    )

    genuine_clues = [c for c in clues if not c.is_red_herring]
    presented = sum(1 for c in genuine_clues if c.status != "planned")

    audit_chapter = parse_chapter_number(chapter_id)
    checks: list[AuditCheck] = [_timeline_conflict_check(c, chapter_of_entry) for c in conflicts]
    checks.extend(_foreshadow_overdue_checks(foreshadows, stats))
    checks.extend(_clue_planting_checks(clues, audit_chapter))
    method_check = _method_state_check(foreshadows, methods)
    if method_check is not None:
        checks.append(method_check)

    return AuditReport(
        chapter_id=chapter_id,
        foreshadow_recovery=AuditRatio(
            rate=stats.foreshadow_recovery_rate,
            count=stats.foreshadow_resolved,
            total=stats.foreshadow_total,
        ),
        clue_fairness=AuditRatio(
            rate=(presented / len(genuine_clues)) if genuine_clues else 0.0,
            count=presented,
            total=len(genuine_clues),
        ),
        character_consistency=consistency,
        checks=checks,
    )


__all__ = [
    "AuditCheck",
    "AuditConsistency",
    "AuditRatio",
    "AuditReport",
    "build_audit_report",
]
