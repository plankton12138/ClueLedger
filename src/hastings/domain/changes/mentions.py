"""正文引用扫描（T15.3）：两阶段分析的语义补充第一块。

从正文 TextUnit 抽取对种子对象的**字面 mentions**，产生带 confidence 的
ImpactItem。设计约束（modules/change-impact.md §4.1）：

- **结构信号优先于语义信号**：确定性遍历已覆盖的正文不再重复报告
  （``covered_ids``）——显式 ``renders`` 边是 must_fix，语义观察不得
  在同一对象上叠床架屋；
- **mentions 永不 must_fix**：严重度由本模块的固定映射决定（不来自
  调用方规则），语义观察只能建议（should_fix）或存疑（review），
  结构必然性只属于显式边；
- **低置信降为 review 而非丢弃**（四透镜③）：单字别名等歧义来源
  标低置信度让人复核，不静默吞掉也不冒充确定。

本模块是纯函数：不做模型调用、不触达 Repository；文本与候选都由调用方
从 Snapshot 投影。无命中是合法空结果，返回空 items 与统计。
"""

from __future__ import annotations

from dataclasses import dataclass

from hastings.domain.changes.impact import (
    SEVERITY_REVIEW,
    SEVERITY_SHOULD_FIX,
    ImpactItem,
)

__all__ = [
    "CONFIDENCE_AMBIGUOUS",
    "CONFIDENCE_EXPLICIT",
    "REVIEW_BELOW",
    "MentionCandidate",
    "MentionScanResult",
    "MentionScanStats",
    "TextUnitRef",
    "scan_text_mentions",
]

# ≥2 字别名完整出现 = 显式引用；单字别名 = 歧义来源（中文无词边界，单字命中
# 极易误报），压到低置信由复核决定
CONFIDENCE_EXPLICIT = 0.9
CONFIDENCE_AMBIGUOUS = 0.5
REVIEW_BELOW = 0.8  # 低于该阈值为 review；mentions 任何置信度都不产生 must_fix


@dataclass(frozen=True, slots=True)
class MentionCandidate:
    """一个可被正文引用的候选对象：身份 + 名称别名（从 Canon 内容投影）。"""

    artifact_id: str
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TextUnitRef:
    """被扫描的正文段落：身份 + 文本（调用方保证只投活动段落）。"""

    text_unit_id: str
    text: str


@dataclass(frozen=True, slots=True)
class MentionScanStats:
    """扫描统计：覆盖了多少段落、跳过了多少已覆盖段落（可观测性）。"""

    unit_count: int = 0
    candidate_count: int = 0
    skipped_covered: int = 0
    hit_count: int = 0

    def to_content(self) -> dict[str, int]:
        return {
            "unit_count": self.unit_count,
            "candidate_count": self.candidate_count,
            "skipped_covered": self.skipped_covered,
            "hit_count": self.hit_count,
        }

    @classmethod
    def from_content(cls, content: dict[str, int]) -> MentionScanStats:
        return cls(
            unit_count=int(content.get("unit_count", 0)),
            candidate_count=int(content.get("candidate_count", 0)),
            skipped_covered=int(content.get("skipped_covered", 0)),
            hit_count=int(content.get("hit_count", 0)),
        )


@dataclass(frozen=True, slots=True)
class MentionScanResult:
    """扫描结果：语义补充项（追加在确定性报告之后，不替换既有项）+ 统计。"""

    items: tuple[ImpactItem, ...]
    stats: MentionScanStats


def scan_text_mentions(
    candidates: tuple[MentionCandidate, ...],
    units: tuple[TextUnitRef, ...],
    *,
    covered_ids: frozenset[str] = frozenset(),
) -> MentionScanResult:
    """扫描正文对候选对象的字面引用；每个（段落，候选）至多一项。

    多别名同时命中取**最长最确定**的一个作为依据；source 记在
    ``text_unit_id``，命中的别名与置信度记在 reason（展示与审计共用）。
    """
    items: list[ImpactItem] = []
    skipped = 0
    for unit in units:
        if unit.text_unit_id in covered_ids:
            skipped += 1  # 确定性阶段已报告：结构信号优先，不重复计项
            continue
        for _candidate_id, alias, confidence in _best_hits(unit.text, candidates):
            items.append(
                ImpactItem(
                    target_artifact_id=unit.text_unit_id,
                    severity=SEVERITY_SHOULD_FIX if confidence >= REVIEW_BELOW else SEVERITY_REVIEW,
                    impact_kind="wording",
                    path=(),  # 语义观察没有结构路径；来源即段落自身
                    reason=f"正文引用「{alias}」（置信 {confidence}）",
                    confidence=confidence,
                    text_unit_id=unit.text_unit_id,
                    mentioned_artifact_id=_candidate_id,
                )
            )
    items.sort(key=lambda i: (i.target_artifact_id, i.mentioned_artifact_id or ""))
    return MentionScanResult(
        items=tuple(items),
        stats=MentionScanStats(
            unit_count=len(units),
            candidate_count=len(candidates),
            skipped_covered=skipped,
            hit_count=len(items),
        ),
    )


def _best_hits(text: str, candidates: tuple[MentionCandidate, ...]) -> list[tuple[str, str, float]]:
    """每个候选至多一项：取置信度最高（并列取别名最长）的命中。"""
    hits: list[tuple[str, str, float]] = []
    for candidate in candidates:
        best: tuple[str, float] | None = None
        for alias in candidate.aliases:
            if not alias or alias not in text:
                continue
            confidence = CONFIDENCE_EXPLICIT if len(alias) >= 2 else CONFIDENCE_AMBIGUOUS
            if (
                best is None
                or confidence > best[1]
                or (confidence == best[1] and len(alias) > len(best[0]))
            ):
                best = (alias, confidence)
        if best is not None:
            hits.append((candidate.artifact_id, best[0], best[1]))
    hits.sort(key=lambda h: h[0])
    return hits
