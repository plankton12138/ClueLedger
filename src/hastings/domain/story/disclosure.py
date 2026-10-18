"""披露边界（V03-4.7）：某个叙事位置上「谁已经知道什么」。

产品设计 §2.1 的要求落到三个判断：

1. **读者披露按叙述顺序，角色知情按获知事件**。两者都从 ``StoryState`` 折叠结果取，
   不按章号比较——倒叙章位置靠后但读者先读到，只看章号会判错。
2. **后文真相是作者资料，不是此刻可写的断言**。位置之后才揭示的事实标成
   ``author_only``，渲染时明确写「不得在本章断言」，而不是笼统地混在背景里。
3. **证据待核对的事实不算已知**。``unverified`` 优先于一切：旧记录不能证明
   读者或角色已经获知（验收 A16 / I09）。

默认润色不扩大披露：``check_expansion`` 以**原文**为基线，只有原文没有、新稿却出现的
后文事实名才算扩大。这是字符串级的确定性检测，只覆盖可命名的短标签；长陈述的语义
越界由生成前的边界块约束，本模块不假装能审语义（计划明确「不新增独立模型一致性审计」）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from hastings.domain.repository.serialization import content_hash
from hastings.domain.story import error_codes as ec
from hastings.domain.story import kinds
from hastings.domain.story.catalog import StoryCatalog, StoryEntry, display_name
from hastings.domain.story.errors import violation
from hastings.domain.story.mystery.models import KNOWLEDGE_UNAWARE
from hastings.domain.story.narrative.order import ordered_beats
from hastings.domain.story.narrative.state import story_state_at_beat
from hastings.domain.story.views import CHANNEL_REVEAL, disclosure_timeline

__all__ = [
    "BOUNDARY_AUTHOR_ONLY",
    "BOUNDARY_CHARACTER",
    "BOUNDARY_READER",
    "BOUNDARY_UNVERIFIED",
    "BoundaryPosition",
    "DisclosureBoundary",
    "DisclosureReport",
    "ExpansionFinding",
    "FactBoundary",
    "POSITION_BEAT",
    "POSITION_CHAPTER",
    "POSITION_UNKNOWN",
    "boundary_at",
    "check_expansion",
    "resolve_position",
]

BOUNDARY_READER = "reader_known"
BOUNDARY_CHARACTER = "character_known"
BOUNDARY_AUTHOR_ONLY = "author_only"
BOUNDARY_UNVERIFIED = "unverified"

POSITION_CHAPTER = "chapter"
POSITION_BEAT = "beat"
POSITION_UNKNOWN = "unknown"

#: 可披露的事实类：只有这些种类的「是否已透露」有叙述意义。
#: 人物、地点、关系不进边界——角色出场不需要一条披露记录背书。
DISCLOSABLE_KINDS: Final[tuple[str, ...]] = (
    kinds.KIND_TRUTH_FACT,
    kinds.KIND_CLUE,
    kinds.KIND_FORESHADOW,
    kinds.KIND_MYSTERY_TRUTH,
    kinds.KIND_RED_HERRING,
    kinds.KIND_TIMELINE_EVENT,
)

#: 名称级检测的长度上限：超过这个长度的标签是陈述句，不是术语，不做子串匹配。
MAX_TERM_CHARS: Final[int] = 24

STATUS_LABELS: Final[dict[str, str]] = {
    BOUNDARY_READER: "读者已知",
    BOUNDARY_CHARACTER: "角色已知",
    BOUNDARY_AUTHOR_ONLY: "后文才揭示，不得在本章断言",
    BOUNDARY_UNVERIFIED: "证据待核对，不可断言",
}


# ---------------------------------------------------------------------------
# 位置
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BoundaryPosition:
    """边界截止位置：章或节拍；都没有时为 ``unknown``（保守处理）。"""

    kind: str
    position_id: str
    label: str
    cutoff_beat_id: str | None

    def to_wire(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "id": self.position_id,
            "label": self.label,
            "cutoff_beat_id": self.cutoff_beat_id or "",
        }


def _parent_map(catalog: StoryCatalog) -> dict[str, str]:
    parents: dict[str, str] = {}
    for entry in (*catalog.nodes(), *catalog.beats()):
        raw = entry.content.get("parent_id")
        if isinstance(raw, str) and raw:
            parents[entry.artifact_id] = raw
    return parents


def _within(catalog: StoryCatalog, beat_id: str, ancestor_id: str) -> bool:
    parents = _parent_map(catalog)
    cursor = parents.get(beat_id, "")
    seen: set[str] = set()
    while cursor and cursor not in seen:
        if cursor == ancestor_id:
            return True
        seen.add(cursor)
        cursor = parents.get(cursor, "")
    return False


def resolve_position(
    catalog: StoryCatalog,
    *,
    chapter_id: str | None = None,
    beat_id: str | None = None,
) -> BoundaryPosition:
    """把「哪一章」或「哪个节拍」解成截止位置；都不给则返回未知位置。"""
    if chapter_id and beat_id:
        raise violation(
            ec.STORY_INVALID_COMMAND,
            "disclosure.position",
            f"同时给了章 {chapter_id!r} 与节拍 {beat_id!r}",
            "只给一个位置：章用于整章润色，节拍用于精确到段落的写作",
        )
    beats = ordered_beats(catalog)
    if beat_id:
        if not any(entry.artifact_id == beat_id for entry in beats):
            raise violation(
                ec.STORY_UNKNOWN_ARTIFACT,
                "disclosure.position.beat_id",
                f"节拍 {beat_id!r} 不在结构顺序中",
                "改用结构中存在的节拍 ID",
                beat_id=beat_id,
            )
        entry = catalog.get(beat_id)
        label = display_name(entry.kind, entry.content) if entry is not None else ""
        return BoundaryPosition(POSITION_BEAT, beat_id, label or beat_id, beat_id)
    if chapter_id:
        entry = catalog.get(chapter_id)
        if entry is None or entry.kind not in kinds.NARRATIVE_NODE_KINDS:
            raise violation(
                ec.STORY_UNKNOWN_ARTIFACT,
                "disclosure.position.chapter_id",
                f"{chapter_id!r} 不是叙事结构中的章",
                "先用结构树查询章 ID；稿件章 ID 与叙事章 ID 不是同一套",
                chapter_id=chapter_id,
            )
        label = display_name(entry.kind, entry.content) or chapter_id
        inside = [b.artifact_id for b in beats if _within(catalog, b.artifact_id, chapter_id)]
        return BoundaryPosition(POSITION_CHAPTER, chapter_id, label, inside[-1] if inside else None)
    return BoundaryPosition(POSITION_UNKNOWN, "", "", None)


# ---------------------------------------------------------------------------
# 事实边界
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FactBoundary:
    """一条事实在本位置的可见性。"""

    fact_id: str
    label: str
    status: str
    interpretation: str = ""  # 读者通道：true / false / partial
    characters: tuple[str, ...] = ()  # 角色通道：已获知（或误信）的角色
    first_reveal_at: str = ""  # 读者最早在哪一章看到（全书范围）
    reason: str = ""

    @property
    def term(self) -> str:
        """可用于名称级检测的术语；陈述句与空标签返回空串。"""
        text = " ".join(self.label.split())
        if not text or len(text) > MAX_TERM_CHARS:
            return ""
        return text

    def to_wire(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "fact_id": self.fact_id,
            "label": self.label,
            "status": self.status,
        }
        if self.interpretation:
            payload["interpretation"] = self.interpretation
        if self.characters:
            payload["characters"] = list(self.characters)
        if self.first_reveal_at:
            payload["first_reveal_at"] = self.first_reveal_at
        if self.reason:
            payload["reason"] = self.reason
        return payload


@dataclass(frozen=True, slots=True)
class DisclosureBoundary:
    commit_id: str | None
    position: BoundaryPosition
    facts: tuple[FactBoundary, ...] = ()
    warnings: tuple[str, ...] = ()
    content_hash: str = ""

    def of_status(self, status: str) -> tuple[FactBoundary, ...]:
        return tuple(fact for fact in self.facts if fact.status == status)

    def to_wire(self) -> dict[str, object]:
        return {
            "commit_id": self.commit_id,
            "position": self.position.to_wire(),
            "facts": [fact.to_wire() for fact in self.facts],
            "reader_known": [f.fact_id for f in self.of_status(BOUNDARY_READER)],
            "character_known": [f.fact_id for f in self.of_status(BOUNDARY_CHARACTER)],
            "author_only": [f.fact_id for f in self.of_status(BOUNDARY_AUTHOR_ONLY)],
            "unverified": [f.fact_id for f in self.of_status(BOUNDARY_UNVERIFIED)],
            "warnings": list(self.warnings),
            "content_hash": self.content_hash,
        }

    def render(self) -> str:
        """提示词块：只写约束，不复述整份账本。同一份边界永远渲染成同一段。"""
        head = (
            f"本节点：{self.position.label}"
            if self.position.kind != POSITION_UNKNOWN
            else "未指定叙事位置（按保守边界处理）"
        )
        lines = [head]
        reader = self.of_status(BOUNDARY_READER)
        if reader:
            parts = []
            for fact in reader:
                mark = "" if fact.interpretation in ("", "true") else f"（{fact.interpretation}）"
                parts.append(f"{fact.label}{mark}")
            lines.append("- 读者已知：" + "、".join(parts))
        characters = self.of_status(BOUNDARY_CHARACTER)
        if characters:
            parts = []
            for fact in characters:
                who = "、".join(fact.characters) if fact.characters else "角色"
                parts.append(f"{fact.label}（{who}）")
            lines.append("- 角色已知：" + "、".join(parts))
        hidden = self.of_status(BOUNDARY_AUTHOR_ONLY)
        if hidden:
            parts = []
            for fact in hidden:
                where = f"，首次揭示于{fact.first_reveal_at}" if fact.first_reveal_at else ""
                parts.append(f"{fact.label}{where}")
            lines.append("- 作者资料，不得在本章让角色说出或让读者看出：" + "、".join(parts))
        unverified = self.of_status(BOUNDARY_UNVERIFIED)
        if unverified:
            parts = [f"{fact.label}（{fact.reason or '证据待核对'}）" for fact in unverified]
            lines.append("- 证据待核对，不可当作已获知：" + "、".join(parts))
        if self.warnings:
            lines.append("- 注意：" + "；".join(self.warnings))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 计算
# ---------------------------------------------------------------------------


def _chapter_label_of_beat(catalog: StoryCatalog) -> dict[str, str]:
    """节拍 → 所属章的显示名；章本身没有名字时退到章 ID。"""
    parents = _parent_map(catalog)
    labels: dict[str, str] = {}
    for entry in (*catalog.nodes(), *catalog.beats()):
        if entry.kind != kinds.KIND_CHAPTER:
            continue
        name = display_name(entry.kind, entry.content)
        labels[entry.artifact_id] = name or entry.artifact_id
    result: dict[str, str] = {}
    for beat in catalog.beats():
        cursor = beat.artifact_id
        seen: set[str] = set()
        while cursor and cursor not in seen:
            if cursor in labels:
                result[beat.artifact_id] = labels[cursor]
                break
            seen.add(cursor)
            cursor = parents.get(cursor, "")
    return result


def _first_reveal_labels(catalog: StoryCatalog) -> dict[str, str]:
    """事实 → 读者最早在哪个位置看到；只看读者通道，取最小结构位置。"""
    chapter_of = _chapter_label_of_beat(catalog)
    earliest: dict[str, tuple[int, str]] = {}
    for event in disclosure_timeline(catalog).events:
        if event.channel != CHANNEL_REVEAL or event.position is None:
            continue
        label = chapter_of.get(event.at_beat_id or "", "")
        if not label:
            continue
        current = earliest.get(event.fact_id)
        if current is None or event.position < current[0]:
            earliest[event.fact_id] = (event.position, label)
    return {fact_id: label for fact_id, (_, label) in earliest.items()}


def _disclosable(catalog: StoryCatalog) -> list[StoryEntry]:
    order = {kind: index for index, kind in enumerate(DISCLOSABLE_KINDS)}
    rows = [entry for entry in catalog.canon() if entry.kind in order]
    rows.sort(key=lambda e: (order[e.kind], e.artifact_id))
    return rows


def boundary_at(
    catalog: StoryCatalog,
    *,
    chapter_id: str | None = None,
    beat_id: str | None = None,
    unverified: dict[str, str] | None = None,
) -> DisclosureBoundary:
    """算某个位置的披露边界。

    ``unverified`` 是「证据待核对」的事实 → 原因；由调用方按 ``evidence.link`` 现状给出，
    这样领域层不必知道证据存在哪张表。位置未知时不做任何「已经披露」的推断。
    """
    position = resolve_position(catalog, chapter_id=chapter_id, beat_id=beat_id)
    warnings: list[str] = []
    reader: dict[str, tuple[str, str]] = {}
    known: dict[str, dict[str, str]] = {}

    if position.kind == POSITION_UNKNOWN:
        warnings.append("未指定叙事位置：只给出保守边界，不声称任何事实已经披露")
    elif position.cutoff_beat_id is None:
        warnings.append(f"{position.label} 下没有节拍：无法确定披露截止点，按保守边界处理")
    else:
        state = story_state_at_beat(catalog, position.cutoff_beat_id, after=True)
        for item in state.disclosures:
            reader[item.fact_id] = (item.interpretation, item.at_beat_id or "")
        for cognition in state.knowledge:
            if cognition.mode == KNOWLEDGE_UNAWARE:
                continue
            known.setdefault(cognition.fact_id, {})[cognition.character_id] = cognition.mode
        for warning in state.warnings:
            warnings.append(f"{warning.where}：{warning.why}")

    first_reveal = _first_reveal_labels(catalog)
    stale = dict(unverified or {})
    facts: list[FactBoundary] = []
    for entry in _disclosable(catalog):
        label = display_name(entry.kind, entry.content) or entry.artifact_id
        if entry.artifact_id in stale:
            facts.append(
                FactBoundary(
                    fact_id=entry.artifact_id,
                    label=label,
                    status=BOUNDARY_UNVERIFIED,
                    first_reveal_at=first_reveal.get(entry.artifact_id, ""),
                    reason=stale[entry.artifact_id],
                )
            )
            continue
        if entry.artifact_id in reader:
            interpretation, at_beat = reader[entry.artifact_id]
            facts.append(
                FactBoundary(
                    fact_id=entry.artifact_id,
                    label=label,
                    status=BOUNDARY_READER,
                    interpretation=interpretation,
                    first_reveal_at=first_reveal.get(entry.artifact_id, ""),
                    reason=f"披露于 {at_beat}" if at_beat else "",
                )
            )
            continue
        characters = known.get(entry.artifact_id)
        if characters:
            facts.append(
                FactBoundary(
                    fact_id=entry.artifact_id,
                    label=label,
                    status=BOUNDARY_CHARACTER,
                    characters=tuple(sorted(characters)),
                    first_reveal_at=first_reveal.get(entry.artifact_id, ""),
                )
            )
            continue
        facts.append(
            FactBoundary(
                fact_id=entry.artifact_id,
                label=label,
                status=BOUNDARY_AUTHOR_ONLY,
                first_reveal_at=first_reveal.get(entry.artifact_id, ""),
            )
        )

    wire = {
        "commit_id": catalog.commit_id,
        "position": position.to_wire(),
        "facts": [fact.to_wire() for fact in facts],
        "warnings": warnings,
    }
    return DisclosureBoundary(
        commit_id=catalog.commit_id,
        position=position,
        facts=tuple(facts),
        warnings=tuple(warnings),
        content_hash=content_hash(wire),
    )


# ---------------------------------------------------------------------------
# 扩大披露检测
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExpansionFinding:
    fact_id: str
    label: str
    status: str
    first_reveal_at: str = ""

    def to_wire(self) -> dict[str, str]:
        return {
            "fact_id": self.fact_id,
            "label": self.label,
            "status": self.status,
            "first_reveal_at": self.first_reveal_at,
        }


@dataclass(frozen=True, slots=True)
class DisclosureReport:
    """一次「是否扩大披露」的检测结果；``blocked`` 才是采用门槛。"""

    position: BoundaryPosition
    findings: tuple[ExpansionFinding, ...] = ()
    checked_terms: int = 0
    skipped_terms: int = 0
    authorized: bool = False
    content_hash: str = ""

    @property
    def expands(self) -> bool:
        return bool(self.findings)

    @property
    def position_known(self) -> bool:
        return self.position.kind != POSITION_UNKNOWN

    @property
    def blocked(self) -> bool:
        """位置已知才拦：没有叙事位置时只能提示，不能替作者裁决。"""
        return self.position_known and bool(self.findings) and not self.authorized

    def to_wire(self) -> dict[str, object]:
        return {
            "position": self.position.to_wire(),
            "position_known": self.position_known,
            "expands": self.expands,
            "blocked": self.blocked,
            "authorized": self.authorized,
            "findings": [finding.to_wire() for finding in self.findings],
            "checked_terms": self.checked_terms,
            "skipped_terms": self.skipped_terms,
            "content_hash": self.content_hash,
        }


def check_expansion(
    boundary: DisclosureBoundary,
    *,
    original: str,
    candidate: str,
    authorized: bool = False,
) -> DisclosureReport:
    """默认润色不扩大披露：原文没有、新稿出现的后文事实名即视为扩大。

    基线是**原文**而不是边界本身：作者原来就在这一章写过某个词，说明它已在本章
    的披露范围内（哪怕记录里没登记），此时不该把润色判成越界。
    """
    findings: list[ExpansionFinding] = []
    checked = 0
    skipped = 0
    for fact in boundary.facts:
        if fact.status not in (BOUNDARY_AUTHOR_ONLY, BOUNDARY_UNVERIFIED):
            continue
        term = fact.term
        if not term:
            skipped += 1
            continue
        checked += 1
        if term in original:
            continue
        if term in candidate:
            findings.append(
                ExpansionFinding(
                    fact_id=fact.fact_id,
                    label=fact.label,
                    status=fact.status,
                    first_reveal_at=fact.first_reveal_at,
                )
            )
    findings.sort(key=lambda item: item.fact_id)
    wire = {
        "position": boundary.position.to_wire(),
        "original": content_hash(original),
        "candidate": content_hash(candidate),
        "authorized": authorized,
        "findings": [item.to_wire() for item in findings],
    }
    return DisclosureReport(
        position=boundary.position,
        findings=tuple(findings),
        checked_terms=checked,
        skipped_terms=skipped,
        authorized=authorized,
        content_hash=content_hash(wire),
    )
