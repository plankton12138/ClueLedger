"""背景装配（V03-4.2）：一份背景，对话与创作共用。

计划 M07 的要求落成三个决定：

1. **同一部作品只有一种背景**。普通讨论与试写/润色都从这里取，差别只在
   ``scope``（约定按适用范围过滤）与 ``target_chapter_ids``（本次目标），
   不在各自的代码里另拼一套提示词。
2. **裁剪顺序固定、可解释**：约定 → 故事核心 → 章节概览 → 目录 → 目标展开。
   预算不够时从后往前丢，丢掉的每一项都进 ``omissions``（可按版本读回），
   不会被静默省略成「模型不知道」。
3. **来源与版本随内容一起走**：每块内容记录来自哪个 revision / 哪条摘要状态；
   过期或缺失摘要时用调用方读到的原文构建**本次临时**背景（``temporary=True``），
   不写回正式摘要，也不会把未读章节标成已检查。

本模块是纯函数：不读库、不调模型、不写任何东西。材料由调用方按固定版本取好后
传入，因此同样的输入必然得到同样的包与同一个 ``content_hash``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from hastings.application.contexts.tokens import estimate_tokens
from hastings.domain.repository.serialization import content_hash

__all__ = [
    "AgreementInput",
    "BackgroundItem",
    "BackgroundOmission",
    "BackgroundPackage",
    "BackgroundRequest",
    "ChapterInput",
    "CoverageEntry",
    "LedgerInput",
    "MODE_OMITTED",
    "MODE_SUMMARY",
    "MODE_TEMPORARY",
    "SUMMARY_MISSING",
    "TIER_AGREEMENTS",
    "TIER_CORE",
    "TIER_INDEX",
    "TIER_OVERVIEW",
    "TIER_PRIORITY",
    "TIER_TARGET",
    "assemble_background",
]

# ---------------------------------------------------------------------------
# 层级：顺序即优先级，裁剪从后往前
# ---------------------------------------------------------------------------

TIER_AGREEMENTS = "agreements"
TIER_CORE = "core"
TIER_OVERVIEW = "overview"
TIER_INDEX = "index"
TIER_TARGET = "target"

TIER_PRIORITY: Final[tuple[str, ...]] = (
    TIER_AGREEMENTS,
    TIER_CORE,
    TIER_OVERVIEW,
    TIER_INDEX,
    TIER_TARGET,
)

#: 必留层级：装不下说明预算本身不成立，明确报错而不是悄悄丢事实。
MANDATORY_TIERS: Final[frozenset[str]] = frozenset({TIER_AGREEMENTS, TIER_CORE})

TIER_LABELS: Final[dict[str, str]] = {
    TIER_AGREEMENTS: "本书约定",
    TIER_CORE: "故事核心",
    TIER_OVERVIEW: "章节概览",
    TIER_INDEX: "目录",
    TIER_TARGET: "本次目标",
}

#: 故事核心：作品的地基与全局作者资料。悬疑真相属于「作者知道、读者未必知道」，
#: 渲染时显式标注，避免 4.7 的披露边界在润色前章时被悄悄突破。
CORE_KINDS: Final[frozenset[str]] = frozenset(
    {"story.premise", "story.world_rule", "story.mystery_truth"}
)
AUTHOR_TRUTH_KINDS: Final[frozenset[str]] = frozenset({"story.mystery_truth"})

SUMMARY_MISSING = "missing"
MODE_SUMMARY = "summary"
MODE_TEMPORARY = "temporary_text"
MODE_OMITTED = "omitted"

#: 目录里每条摘要的截断长度：目录是索引，不是内容。
INDEX_SUMMARY_CHARS = 60
#: 每条内容之间的分隔开销（换行、标记），用于预算预留。
_ITEM_OVERHEAD = 8
#: 包级固定开销（版本索引、覆盖记录、裁剪说明）。
_PACKAGE_OVERHEAD = 96


# ---------------------------------------------------------------------------
# 输入
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgreementInput:
    """一条生效约定（来自 ``AgreementSnapshot``）。"""

    agreement_id: str
    revision_id: str
    topic: str
    scope: str
    statement: str

    def applies_to(self, scope: str) -> bool:
        return self.scope == "all" or self.scope == scope


@dataclass(frozen=True, slots=True)
class LedgerInput:
    """一条可引用账本条目（来自 ``LedgerQueries``）。"""

    artifact_id: str
    revision_id: str
    kind: str
    kind_label: str
    title: str
    summary: str


@dataclass(frozen=True, slots=True)
class ChapterInput:
    """一章的目录信息与可用背景。

    ``summary_status`` 取 ``fresh`` / ``stale`` / ``missing``：
    - fresh：直接用摘要；
    - stale 或 missing：用 ``temporary_text``（调用方按当前版本读到的原文）
      构建临时背景；没有原文则如实省略并记入覆盖。
    """

    chapter_id: str
    revision_id: str
    title: str
    order_key: str
    summary_id: str = ""
    summary_status: str = SUMMARY_MISSING
    summary_text: str = ""
    summary_source_hash: str = ""
    temporary_text: str = ""


@dataclass(frozen=True, slots=True)
class BackgroundRequest:
    """一次装配请求：材料都已按某个 Commit 取好，装配器只做选择与裁剪。"""

    project_id: str
    commit_id: str | None
    scope: str
    budget_tokens: int
    agreements: tuple[AgreementInput, ...] = ()
    ledger: tuple[LedgerInput, ...] = ()
    chapters: tuple[ChapterInput, ...] = ()
    target_chapter_ids: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BackgroundItem:
    tier: str
    kind: str
    ref_id: str
    revision_id: str
    label: str
    text: str
    temporary: bool = False

    def to_wire(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "tier": self.tier,
            "kind": self.kind,
            "ref_id": self.ref_id,
            "revision_id": self.revision_id,
            "label": self.label,
            "text": self.text,
        }
        if self.temporary:
            payload["temporary"] = True
        return payload


@dataclass(frozen=True, slots=True)
class BackgroundOmission:
    """被裁掉的一块：说清是哪层、哪一项、为什么，便于按版本读回。"""

    tier: str
    ref_id: str
    label: str
    reason: str

    def to_wire(self) -> dict[str, str]:
        return {
            "tier": self.tier,
            "ref_id": self.ref_id,
            "label": self.label,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class CoverageEntry:
    """一章实际拿到什么背景：摘要、临时原文，还是没拿到。"""

    chapter_id: str
    mode: str
    source: str

    def to_wire(self) -> dict[str, str]:
        return {"chapter_id": self.chapter_id, "mode": self.mode, "source": self.source}


@dataclass(frozen=True, slots=True)
class BackgroundPackage:
    project_id: str
    commit_id: str | None
    scope: str
    items: tuple[BackgroundItem, ...] = ()
    omissions: tuple[BackgroundOmission, ...] = ()
    coverage: tuple[CoverageEntry, ...] = ()
    versions: dict[str, Any] = field(default_factory=dict)
    budget_tokens: int = 0
    estimated_tokens: int = 0
    content_hash: str = ""

    # -- 派生查询 -----------------------------------------------------------
    def of_tier(self, tier: str) -> tuple[BackgroundItem, ...]:
        return tuple(item for item in self.items if item.tier == tier)

    def coverage_of(self, chapter_id: str) -> CoverageEntry | None:
        return next((c for c in self.coverage if c.chapter_id == chapter_id), None)

    # -- wire / 渲染 --------------------------------------------------------
    def to_wire(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "commit_id": self.commit_id,
            "scope": self.scope,
            "items": [item.to_wire() for item in self.items],
            "omissions": [o.to_wire() for o in self.omissions],
            "coverage": [c.to_wire() for c in self.coverage],
            "versions": dict(self.versions),
            "budget_tokens": self.budget_tokens,
            "estimated_tokens": self.estimated_tokens,
            "content_hash": self.content_hash,
        }

    def render(self) -> str:
        """确定性文本：同一包永远渲染成同一段，便于比对与留证。

        刻意不写「以下是背景」这类客套话——提示词模板负责措辞，这里只负责
        事实与来源；临时读取的原文单独标注，避免被当成正式摘要。
        """
        blocks: list[str] = []
        for tier in TIER_PRIORITY:
            items = self.of_tier(tier)
            if not items:
                continue
            lines = [f"## {TIER_LABELS[tier]}"]
            for item in items:
                mark = "（本次临时读取，未写入正式摘要）" if item.temporary else ""
                lines.append(f"- [{item.label}]{mark} {item.text}")
            blocks.append("\n".join(lines))
        if self.omissions:
            note = "、".join(f"{o.label}" for o in self.omissions)
            blocks.append(f"## 未载入\n{note}（超出本次预算，可按版本读回）")
        return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# 装配
# ---------------------------------------------------------------------------


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else f"{text[:limit]}…"


def _agreement_items(request: BackgroundRequest) -> list[BackgroundItem]:
    chosen = [a for a in request.agreements if a.applies_to(request.scope)]
    chosen.sort(key=lambda a: (a.topic, a.agreement_id))
    return [
        BackgroundItem(
            tier=TIER_AGREEMENTS,
            kind="agreement",
            ref_id=a.agreement_id,
            revision_id=a.revision_id,
            label=a.topic,
            text=a.statement,
        )
        for a in chosen
    ]


def _core_items(request: BackgroundRequest) -> list[BackgroundItem]:
    """故事核心：前提、世界规则、全局真相。按种类的固定顺序，不按字母碰运气。"""
    order = {"story.premise": 0, "story.world_rule": 1, "story.mystery_truth": 2}
    chosen = [e for e in request.ledger if e.kind in CORE_KINDS]
    chosen.sort(key=lambda e: (order[e.kind], e.title, e.artifact_id))
    items = []
    for entry in chosen:
        label = entry.kind_label
        if entry.kind in AUTHOR_TRUTH_KINDS:
            label = f"{entry.kind_label}（作者资料，勿提前披露）"
        items.append(
            BackgroundItem(
                tier=TIER_CORE,
                kind="ledger",
                ref_id=entry.artifact_id,
                revision_id=entry.revision_id,
                label=f"{label} {entry.title}",
                text=entry.summary,
            )
        )
    return items


def _overview_items(request: BackgroundRequest, targets: set[str]) -> list[BackgroundItem]:
    """非目标章的概览：只用可用（fresh）摘要，过期内容不进概览。"""
    items = []
    for chapter in sorted(request.chapters, key=lambda c: (c.order_key, c.chapter_id)):
        if chapter.chapter_id in targets:
            continue
        if chapter.summary_status != "fresh" or not chapter.summary_text.strip():
            continue
        items.append(
            BackgroundItem(
                tier=TIER_OVERVIEW,
                kind="chapter_summary",
                ref_id=chapter.summary_id or chapter.chapter_id,
                revision_id=chapter.revision_id,
                label=chapter.title,
                text=chapter.summary_text.strip(),
            )
        )
    return items


def _index_items(request: BackgroundRequest, targets: set[str]) -> list[BackgroundItem]:
    """目录：账本（人物/线索/地点等）与章节。目录永远比正文便宜，优先保留。"""
    items: list[BackgroundItem] = []
    others = [e for e in request.ledger if e.kind not in CORE_KINDS]
    if others:
        others.sort(key=lambda e: (e.kind_label, e.title, e.artifact_id))
        lines = [
            f"{e.kind_label} {e.title}：{_clip(e.summary, INDEX_SUMMARY_CHARS)}" for e in others
        ]
        items.append(
            BackgroundItem(
                tier=TIER_INDEX,
                kind="ledger_index",
                ref_id="ledger",
                revision_id=str(request.commit_id or ""),
                label=f"设定目录（{len(others)} 项）",
                text="\n".join(lines),
            )
        )
    if request.chapters:
        ordered = sorted(request.chapters, key=lambda c: (c.order_key, c.chapter_id))
        lines = [
            f"{index + 1}. {c.title}" + ("（本次目标）" if c.chapter_id in targets else "")
            for index, c in enumerate(ordered)
        ]
        items.append(
            BackgroundItem(
                tier=TIER_INDEX,
                kind="chapter_index",
                ref_id="chapters",
                revision_id=str(request.commit_id or ""),
                label=f"章节目录（{len(ordered)} 章）",
                text="\n".join(lines),
            )
        )
    return items


def _target_items(
    request: BackgroundRequest, targets: list[str], coverage: dict[str, CoverageEntry]
) -> list[BackgroundItem]:
    """目标章展开：优先正式摘要，没有就用本次临时原文；都没有则如实缺省。"""
    by_id = {c.chapter_id: c for c in request.chapters}
    items: list[BackgroundItem] = []
    for chapter_id in targets:
        chapter = by_id.get(chapter_id)
        if chapter is None:
            coverage[chapter_id] = CoverageEntry(
                chapter_id=chapter_id, mode=MODE_OMITTED, source="目标章节不在当前目录"
            )
            continue
        if chapter.summary_status == "fresh" and chapter.summary_text.strip():
            items.append(
                BackgroundItem(
                    tier=TIER_TARGET,
                    kind="chapter_summary",
                    ref_id=chapter.summary_id or chapter.chapter_id,
                    revision_id=chapter.revision_id,
                    label=chapter.title,
                    text=chapter.summary_text.strip(),
                )
            )
            coverage[chapter_id] = CoverageEntry(
                chapter_id=chapter_id,
                mode=MODE_SUMMARY,
                source=chapter.summary_id or chapter.chapter_id,
            )
            continue
        if chapter.temporary_text.strip():
            items.append(
                BackgroundItem(
                    tier=TIER_TARGET,
                    kind="chapter_text",
                    ref_id=chapter.chapter_id,
                    revision_id=chapter.revision_id,
                    label=chapter.title,
                    text=chapter.temporary_text.strip(),
                    temporary=True,
                )
            )
            coverage[chapter_id] = CoverageEntry(
                chapter_id=chapter_id, mode=MODE_TEMPORARY, source=chapter.revision_id
            )
            continue
        coverage[chapter_id] = CoverageEntry(
            chapter_id=chapter_id,
            mode=MODE_OMITTED,
            source=f"摘要状态 {chapter.summary_status}，且没有本次原文",
        )
    return items


def _versions(request: BackgroundRequest) -> dict[str, Any]:
    agreements = [
        {"agreement_id": a.agreement_id, "revision_id": a.revision_id}
        for a in sorted(request.agreements, key=lambda a: a.agreement_id)
    ]
    summaries = {
        c.chapter_id: {
            "summary_id": c.summary_id,
            "status": c.summary_status,
            "source_hash": c.summary_source_hash,
        }
        for c in sorted(request.chapters, key=lambda c: c.chapter_id)
        if c.summary_id
    }
    return {
        "commit_id": request.commit_id,
        "agreements": agreements,
        "summaries": summaries,
    }


def _trim(
    candidates: list[BackgroundItem], budget: int
) -> tuple[list[BackgroundItem], list[BackgroundOmission], int]:
    """按优先级贪心装入；装不下的进 omissions，必留层级装不下直接报错。"""
    kept: list[BackgroundItem] = []
    dropped: list[BackgroundOmission] = []
    used = 0
    for item in candidates:
        cost = estimate_tokens(item.to_wire()) + _ITEM_OVERHEAD
        if used + cost <= budget:
            kept.append(item)
            used += cost
            continue
        if item.tier in MANDATORY_TIERS:
            raise ValueError(
                f"背景预算 {budget} 不足以保留{TIER_LABELS.get(item.tier, item.tier)}："
                f"「{item.label}」需要 {cost}，已用 {used}。增大预算或减少约定/核心设定"
            )
        dropped.append(
            BackgroundOmission(
                tier=item.tier,
                ref_id=item.ref_id,
                label=item.label,
                reason="超出背景预算，可按版本读回",
            )
        )
    return kept, dropped, used


def assemble_background(request: BackgroundRequest) -> BackgroundPackage:
    """装配一份背景包；调用方负责提供同一 Commit 的材料。"""
    if request.budget_tokens <= 0:
        raise ValueError("背景预算必须为正数；预算为 0 时不要调用装配器")
    targets = list(dict.fromkeys(request.target_chapter_ids))
    target_set = set(targets)
    coverage: dict[str, CoverageEntry] = {}

    candidates: list[BackgroundItem] = []
    candidates.extend(_agreement_items(request))
    candidates.extend(_core_items(request))
    candidates.extend(_overview_items(request, target_set))
    candidates.extend(_index_items(request, target_set))
    candidates.extend(_target_items(request, targets, coverage))

    kept, dropped, used = _trim(candidates, request.budget_tokens)

    # 概览里的章也算「拿到了摘要」，目标章以更具体的模式覆盖。
    for item in kept:
        if item.tier == TIER_OVERVIEW and item.ref_id not in coverage:
            chapter = next(
                (c for c in request.chapters if (c.summary_id or c.chapter_id) == item.ref_id),
                None,
            )
            if chapter is not None:
                coverage[chapter.chapter_id] = CoverageEntry(
                    chapter_id=chapter.chapter_id,
                    mode=MODE_SUMMARY,
                    source=chapter.summary_id or chapter.chapter_id,
                )
    dropped_refs = {o.ref_id for o in dropped}
    for chapter in request.chapters:
        if chapter.chapter_id in coverage:
            continue
        source = chapter.summary_id or chapter.chapter_id
        if chapter.chapter_id in dropped_refs or source in dropped_refs:
            coverage[chapter.chapter_id] = CoverageEntry(
                chapter_id=chapter.chapter_id, mode=MODE_OMITTED, source="超出背景预算"
            )
        elif chapter.chapter_id in target_set:
            continue  # 已在 _target_items 里写明缺省原因
        elif chapter.summary_status == "fresh" and chapter.summary_text.strip():
            coverage[chapter.chapter_id] = CoverageEntry(
                chapter_id=chapter.chapter_id, mode=MODE_OMITTED, source="超出背景预算"
            )
        else:
            coverage[chapter.chapter_id] = CoverageEntry(
                chapter_id=chapter.chapter_id,
                mode=MODE_OMITTED,
                source=f"摘要状态 {chapter.summary_status}",
            )

    estimated = used + _PACKAGE_OVERHEAD
    if estimated > request.budget_tokens:
        raise ValueError(
            f"背景预算 {request.budget_tokens} 不足以容纳版本索引与裁剪记录（需要 {estimated}）"
        )
    versions = _versions(request)
    wire_for_hash = {
        "project_id": request.project_id,
        "commit_id": request.commit_id,
        "scope": request.scope,
        "items": [item.to_wire() for item in kept],
        "omissions": [o.to_wire() for o in dropped],
        "coverage": [c.to_wire() for c in sorted(coverage.values(), key=lambda c: c.chapter_id)],
    }
    return BackgroundPackage(
        project_id=request.project_id,
        commit_id=request.commit_id,
        scope=request.scope,
        items=tuple(kept),
        omissions=tuple(dropped),
        coverage=tuple(sorted(coverage.values(), key=lambda c: c.chapter_id)),
        versions=versions,
        budget_tokens=request.budget_tokens,
        estimated_tokens=estimated,
        content_hash=content_hash(wire_for_hash),
    )
