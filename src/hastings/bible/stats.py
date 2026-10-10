"""故事账本统计（P6 T7，计划 D7）：纯派生、无新存储。

stats 端点只做聚合：数据仍来自既有账本 YAML 与 chapters 目录——不引入
第二份账本数据源。风险派生规则（逾期/临近/正常）在此实现、后端算前端展示，
T8 伏笔表格的风险列直接复用 ``foreshadow_risk`` 纯函数。

泄压阀：账本损坏（ValidationError/LedgerFormatError/YAML 错）与 IO 故障
（OSError）原样上抛，由 REST 层映射 422/500——统计是派生视图，不静默吞错。
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, cast

import yaml
from pydantic import ValidationError

from hastings.bible.models import Clue, Foreshadow, Method, OutlineEntry
from hastings.bible.store import (
    LedgerFormatError,
    StoryWorkspace,
    list_characters,
    load_ledger,
    load_story_doc,
)

type ForeshadowRisk = Literal["overdue", "near", "normal"]

_CHAPTER_NUMBER_PATTERN = re.compile(r"ch(\d+)", re.IGNORECASE)


def parse_chapter_number(chapter_id: str) -> int | None:
    """从章节 id（如 ch03）提取章号；非 chNN 形态（序章等）返回 None。"""
    match = _CHAPTER_NUMBER_PATTERN.fullmatch(chapter_id.strip())
    return int(match.group(1)) if match else None


def latest_chapter_number(chapter_ids: Iterable[str]) -> int:
    """「当前最新章节」= 最大章号；无 chNN 章节（如只有序章）按 0 处理。"""
    numbers = (n for n in (parse_chapter_number(c) for c in chapter_ids) if n is not None)
    return max(numbers, default=0)


def foreshadow_risk(foreshadow: Foreshadow, latest_chapter: int) -> ForeshadowRisk:
    """伏笔风险派生（D7）：已回收一律正常；未回收按预计回收章判定。

    - expected ≤ 当前最新章节 → 逾期（红）
    - 距预计回收 ≤ 1 章 → 临近（黄）
    - 其余 / 预计回收不可解析 → 正常（绿）
    """
    if foreshadow.status == "resolved":
        return "normal"
    expected = parse_chapter_number(foreshadow.expected_resolve_chapter)
    if expected is None:
        return "normal"
    if expected <= latest_chapter:
        return "overdue"
    if expected - latest_chapter <= 1:
        return "near"
    return "normal"


@dataclass(frozen=True, slots=True)
class ForeshadowOverdue:
    """逾期伏笔条目：名字 + 埋设/预计回收两章锚点，喂前端逾期列表。"""

    name: str
    plant_chapter: str
    expected_resolve_chapter: str


@dataclass(frozen=True, slots=True)
class ForeshadowRiskItem:
    """逐条伏笔风险（T8 表格风险列数据源）：名字 + 派生风险（逾期/临近/正常）。"""

    name: str
    risk: ForeshadowRisk


@dataclass(frozen=True, slots=True)
class StoryStats:
    """全工作区统计（纯派生）；字段语义见 D7 与前端统计卡。"""

    chapters: int
    total_chars: int
    latest_chapter: int
    foreshadow_counts: dict[str, int]
    foreshadow_total: int
    foreshadow_resolved: int
    foreshadow_recovery_rate: float
    overdue_foreshadows: list[ForeshadowOverdue]
    foreshadow_risks: list[ForeshadowRiskItem]
    clue_counts: dict[str, int]
    clue_total: int
    clue_red_herring: int
    clue_red_herring_ratio: float


def _count_non_whitespace(text: str) -> int:
    """字数口径：去空白后的字符数（CJK 文案下约等于正文字数）。"""
    return len(re.sub(r"\s+", "", text))


def compute_story_stats(workspace: StoryWorkspace) -> StoryStats:
    """聚合全工作区统计；账本损坏/IO 故障原样上抛（REST 层映射 422/500）。

    chapters 目录只数顶层 ``*.md``（.revisions 归档目录不参与统计）；
    缺目录视为空正文（合法空结果）。
    """
    chapter_ids = (
        [path.stem for path in workspace.chapters_dir.glob("*.md")]
        if workspace.chapters_dir.exists()
        else []
    )
    latest = latest_chapter_number(chapter_ids)
    foreshadows = load_ledger(workspace.foreshadows_path, Foreshadow)
    clues = load_ledger(workspace.clues_path, Clue)

    foreshadow_counts = cast(dict[str, int], Counter(f.status for f in foreshadows))
    foreshadow_total = len(foreshadows)
    foreshadow_resolved = foreshadow_counts.get("resolved", 0)
    overdue = [
        ForeshadowOverdue(
            name=f.name,
            plant_chapter=f.plant_chapter,
            expected_resolve_chapter=f.expected_resolve_chapter,
        )
        for f in foreshadows
        if foreshadow_risk(f, latest) == "overdue"
    ]
    risks = [ForeshadowRiskItem(name=f.name, risk=foreshadow_risk(f, latest)) for f in foreshadows]
    clue_counts = cast(dict[str, int], Counter(c.status for c in clues))
    clue_total = len(clues)
    clue_red_herring = sum(1 for c in clues if c.is_red_herring)

    total_chars = 0
    for chapter_id in chapter_ids:
        total_chars += _count_non_whitespace(
            (workspace.chapters_dir / f"{chapter_id}.md").read_text(encoding="utf-8")
        )

    return StoryStats(
        chapters=len(chapter_ids),
        total_chars=total_chars,
        latest_chapter=latest,
        foreshadow_counts=foreshadow_counts,
        foreshadow_total=foreshadow_total,
        foreshadow_resolved=foreshadow_resolved,
        foreshadow_recovery_rate=(
            foreshadow_resolved / foreshadow_total if foreshadow_total else 0.0
        ),
        overdue_foreshadows=overdue,
        foreshadow_risks=risks,
        clue_counts=clue_counts,
        clue_total=clue_total,
        clue_red_herring=clue_red_herring,
        clue_red_herring_ratio=clue_red_herring / clue_total if clue_total else 0.0,
    )


def build_workspace_status(workspace: StoryWorkspace) -> str | None:
    """「当前工作区状态」注入节正文（P8 T5，D5）：只陈述事实，不解释怎么做事。

    数据源 = story.yaml + compute_story_stats + 角色/手法/章纲账本（单一
    数据源，P6 D7 延续）。协议归 system prompt 的工作流程节（D6），本函数
    只给快照——阶段、各账本计数、method 锁定态、逾期伏笔数、章节进度与
    章纲缺口（下一条未建章号）。

    泄压阀（P5 OSError 教训）：任一读取失败 → stderr 三要素告警并返回
    None，调用方跳过该节——状态注入失败不阻断写作流。
    """
    try:
        stats = compute_story_stats(workspace)
        stage = load_story_doc(workspace).stage
        characters = len(list_characters(workspace))
        methods = load_ledger(workspace.method_path, Method)
        outlines = load_ledger(workspace.outline_path, OutlineEntry)
    except (ValidationError, LedgerFormatError, yaml.YAMLError, OSError) as exc:
        print(
            f"[hastings] 工作区状态注入被跳过（{workspace.slug}）。"
            f"为什么错：{type(exc).__name__}: {exc}。"
            "怎样算对：修复对应 YAML/文件后，该节会在下一轮 prompt 自动恢复；"
            "写作流不受影响。",
            file=sys.stderr,
        )
        return None

    if not methods:
        method_label = "手法 0"
    elif all(m.status == "locked" for m in methods):
        method_label = f"手法 {len(methods)}（已锁定）"
    else:
        method_label = f"手法 {len(methods)}（未锁定）"

    gap = f"ch{max(o.chapter for o in outlines) + 1} 起未建" if outlines else "全部未建"
    return (
        f"阶段：{stage} ｜ 角色 {characters} · 线索 {stats.clue_total} · "
        f"伏笔 {stats.foreshadow_total}（逾期 {len(stats.overdue_foreshadows)}）· "
        f"{method_label} · 章纲 {len(outlines)} 条\n"
        f"已写章节：{stats.chapters} ｜ 章纲未覆盖：{gap}"
    )


__all__ = [
    "ForeshadowOverdue",
    "ForeshadowRiskItem",
    "ForeshadowRisk",
    "StoryStats",
    "build_workspace_status",
    "compute_story_stats",
    "foreshadow_risk",
    "latest_chapter_number",
    "parse_chapter_number",
]
