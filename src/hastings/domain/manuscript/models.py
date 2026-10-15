"""正文领域模型（Phase 14 T14.1）：Chapter、TextUnit 与读投影 / 变更结果。

段落（TextUnit）是版本化的最小正文单位，携带谱系（`derived_from`）与状态
（active/deleted）。顺序由所属章的 `text_unit_ids` 显式持有——结构性编辑只
改写章的这一个字段，避免整章所有段落因顺序变化被反复改写。

删除不物理移除 Artifact：段落被置为 `deleted` 并保留在历史中（Repository
的不变量 1：Revision 创建后不可删除）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hastings.domain.manuscript import error_codes as ec
from hastings.domain.manuscript.errors import violation
from hastings.domain.manuscript.operations import Hunk
from hastings.domain.manuscript.schema import (
    SCHEMA_VERSION,
    require_str,
    split_legacy,
    tuple_of_str,
)
from hastings.domain.repository.requests import ArtifactChange

__all__ = [
    "EVENT_CHAPTER_COMMITTED",
    "HUNK_ACCEPTED",
    "HUNK_PENDING",
    "HUNK_REJECTED",
    "HUNK_STATUSES",
    "ORIGIN_EDIT",
    "ORIGIN_IMPORT",
    "PROPOSAL_ACCEPTED",
    "PROPOSAL_PARTIAL",
    "PROPOSAL_PENDING",
    "PROPOSAL_REJECTED",
    "STATE_ACTIVE",
    "STATE_DELETED",
    "Chapter",
    "ChapterView",
    "Manuscript",
    "ManuscriptChange",
    "ManuscriptPatchProposal",
    "PatchHunk",
    "TextUnit",
]

STATE_ACTIVE = "active"
STATE_DELETED = "deleted"
UNIT_STATES = frozenset((STATE_ACTIVE, STATE_DELETED))

ORIGIN_IMPORT = "import"
ORIGIN_EDIT = "edit"

# Agent Patch（T14.4）：hunk 与提案状态
HUNK_PENDING = "pending"
HUNK_ACCEPTED = "accepted"
HUNK_REJECTED = "rejected"
HUNK_STATUSES = frozenset((HUNK_PENDING, HUNK_ACCEPTED, HUNK_REJECTED))

PROPOSAL_PENDING = "pending"  # 仍有未决 hunk
PROPOSAL_PARTIAL = "partial"  # 既有接受也有拒绝
PROPOSAL_ACCEPTED = "accepted"  # 全部接受
PROPOSAL_REJECTED = "rejected"  # 全部拒绝

PATCH_SOURCE_AGENT = "agent"
PATCH_SOURCE_HUMAN = "human"
_PATCH_SOURCES = frozenset((PATCH_SOURCE_AGENT, PATCH_SOURCE_HUMAN))

# 业务事件类型（领域契约）：章正文发生一次提交时发出。事件由 Commit Service 与
# Commit 同事务写入 Outbox（ADR-007），消费者据此独立调度下游分析——保存不再等待
# 任务创建，故障补偿与去重交给持久化的 Outbox（四透镜④：可观测性是事件契约的一部分）。
EVENT_CHAPTER_COMMITTED = "manuscript.chapter.committed"

_CHAPTER_KEYS = frozenset(
    {
        "schema_version",
        "chapter_id",
        "title",
        "order_key",
        "text_unit_ids",
        "narrative_chapter_id",
    }
)
_UNIT_KEYS = frozenset(
    {
        "schema_version",
        "unit_id",
        "chapter_id",
        "text",
        "state",
        "origin",
        "scene_id",
        "derived_from",
    }
)


@dataclass(frozen=True, slots=True)
class Chapter:
    """章：正文的容器与顺序的唯一持有者（kind `manuscript.chapter`）。"""

    chapter_id: str
    title: str
    order_key: str = ""
    text_unit_ids: tuple[str, ...] = ()
    narrative_chapter_id: str | None = None
    schema_version: int = SCHEMA_VERSION
    legacy_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "text_unit_ids", tuple(self.text_unit_ids))
        object.__setattr__(self, "legacy_metadata", dict(self.legacy_metadata))

    def to_content(self) -> dict[str, Any]:
        return {
            **self.legacy_metadata,
            "schema_version": self.schema_version,
            "chapter_id": self.chapter_id,
            "title": self.title,
            "order_key": self.order_key,
            "text_unit_ids": list(self.text_unit_ids),
            "narrative_chapter_id": self.narrative_chapter_id,
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> Chapter:
        where = "manuscript.chapter"
        return cls(
            chapter_id=require_str(content.get("chapter_id"), field="chapter_id", where=where),
            title=str(content.get("title", "")),
            order_key=str(content.get("order_key", "")),
            text_unit_ids=tuple_of_str(content.get("text_unit_ids")),
            narrative_chapter_id=content.get("narrative_chapter_id"),
            schema_version=int(content.get("schema_version", SCHEMA_VERSION)),
            legacy_metadata=split_legacy(content, _CHAPTER_KEYS),
        )


@dataclass(frozen=True, slots=True)
class TextUnit:
    """段落：版本化正文单位（kind `manuscript.text_unit`），带谱系与状态。"""

    unit_id: str
    chapter_id: str
    text: str = ""
    state: str = STATE_ACTIVE
    origin: str = ORIGIN_IMPORT
    scene_id: str | None = None
    derived_from: tuple[str, ...] = ()
    schema_version: int = SCHEMA_VERSION
    legacy_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "derived_from", tuple(self.derived_from))
        object.__setattr__(self, "legacy_metadata", dict(self.legacy_metadata))

    @property
    def is_active(self) -> bool:
        return self.state == STATE_ACTIVE

    def to_content(self) -> dict[str, Any]:
        return {
            **self.legacy_metadata,
            "schema_version": self.schema_version,
            "unit_id": self.unit_id,
            "chapter_id": self.chapter_id,
            "text": self.text,
            "state": self.state,
            "origin": self.origin,
            "scene_id": self.scene_id,
            "derived_from": list(self.derived_from),
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> TextUnit:
        where = "manuscript.text_unit"
        state = str(content.get("state", STATE_ACTIVE))
        if state not in UNIT_STATES:
            raise violation(
                ec.MANUSCRIPT_INVALID_OPERATION,
                where,
                f"state 只能是 {sorted(UNIT_STATES)}，收到 {state!r}",
                "把 state 改为 active 或 deleted",
                state=state,
            )
        return cls(
            unit_id=require_str(content.get("unit_id"), field="unit_id", where=where),
            chapter_id=require_str(content.get("chapter_id"), field="chapter_id", where=where),
            text=str(content.get("text", "")),
            state=state,
            origin=str(content.get("origin", ORIGIN_EDIT)),
            scene_id=content.get("scene_id"),
            derived_from=tuple_of_str(content.get("derived_from")),
            schema_version=int(content.get("schema_version", SCHEMA_VERSION)),
            legacy_metadata=split_legacy(content, _UNIT_KEYS),
        )


_PROPOSAL_KEYS = frozenset(
    {"schema_version", "proposal_id", "chapter_id", "base", "applied_base", "hunks", "source"}
)
_HUNK_KEYS = frozenset({"hunk_id", "operation", "status", "note"})


@dataclass(frozen=True, slots=True)
class ChapterView:
    """某章在某 Commit 的有序活动段落（只读投影）。"""

    chapter: Chapter
    units: tuple[TextUnit, ...]


@dataclass(frozen=True, slots=True)
class Manuscript:
    """整篇正文的只读投影；章按 order_key 排序，段落按章的显式顺序。"""

    commit_id: str | None
    chapters: tuple[ChapterView, ...]

    def text_of(self, chapter_id: str) -> str:
        """按当前顺序把某章还原为整章文本（空行分隔），用于导出与核对。"""
        for view in self.chapters:
            if view.chapter.chapter_id == chapter_id:
                return "\n\n".join(unit.text for unit in view.units)
        return ""


@dataclass(frozen=True, slots=True)
class ManuscriptChange:
    """命令结果：要提交的 Artifact 变化 + 规范化 hunk + 摘要。

    `hunks` 只包含**实际生效**的操作块（no-op 不产生 hunk），供 T14.4 Patch
    逐块接受/拒绝追踪。`is_empty()` 为真表示合法空结果，handler 据此跳过提交。

    `events` 是**随本次提交落盘**的业务事件（类型 + payload）；handler 把它透传进
    ``CommitRequest.events``，由 Commit Service 与 Commit 同事务写入 Outbox——因此
    「正文已变」与「下游该分析」之间没有双写窗口（ADR-007）。

    `chapter_base_after` 是本次变化落库后目标章的基础（内容基础，见
    ``ManuscriptCatalog.chapter_base``）：命令层在应用前就能算出「应用后」的基础，
    因此逐 hunk 接受能把**本提案自己造成的移动**记进提案，而不必等到提交后才知道
    （评审 #4）。只有改动章正文的命令会设置它，其余保持空串。
    """

    artifact_changes: tuple[ArtifactChange, ...] = ()
    hunks: tuple[Hunk, ...] = ()
    warnings: tuple[str, ...] = ()
    events: tuple[tuple[str, dict[str, Any]], ...] = ()
    summary: str = ""
    chapter_base_after: str = ""

    def is_empty(self) -> bool:
        """无内容变化 = 合法空结果（no-op），handler 据此跳过提交。"""
        return not self.artifact_changes


@dataclass(frozen=True, slots=True)
class PatchHunk:
    """Agent Patch 的单个编辑块：稳定身份 + 序列化操作 + 处理状态（T14.4）。

    状态随提案 Artifact 版本化：未决（pending）→ 接受（accepted）或拒绝
    （rejected）。未接受的 hunk 在后续提交里仍可追踪（提案内容带全部 hunk）。
    """

    hunk_id: str
    operation: dict[str, Any]
    status: str = HUNK_PENDING
    note: str = ""

    @property
    def is_pending(self) -> bool:
        return self.status == HUNK_PENDING

    def to_content(self) -> dict[str, Any]:
        return {
            "hunk_id": self.hunk_id,
            "operation": dict(self.operation),
            "status": self.status,
            "note": self.note,
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> PatchHunk:
        where = "manuscript.patch_hunk"
        status = str(content.get("status", HUNK_PENDING))
        if status not in HUNK_STATUSES:
            raise violation(
                ec.MANUSCRIPT_INVALID_OPERATION,
                where,
                f"hunk 状态只能是 {sorted(HUNK_STATUSES)}，收到 {status!r}",
                "把状态改为 pending/accepted/rejected",
                status=status,
            )
        return cls(
            hunk_id=require_str(content.get("hunk_id"), field="hunk_id", where=where),
            operation=dict(content.get("operation") or {}),
            status=status,
            note=str(content.get("note", "")),
        )


@dataclass(frozen=True, slots=True)
class ManuscriptPatchProposal:
    """Agent Patch 提案（kind ``manuscript.patch_proposal``）：候选编辑集合。

    提案本身**不是提交**：它只登记 hunk 与状态，不改变正文。正文变化只能经作者
    显式接受的 ``accept_patch_hunks`` 发生（T14.4「Agent Patch 无法直接提交」）。
    ``base`` 记下提案登记时章的基础；``applied_base`` 记下**本提案自己已经接受**
    的变化之后章的基础（每次接受后回写，未接受过则保持空串）。接受时的乐观并发
    检查用 ``applied_base or base``——因此剩余 hunk 不会因本提案自己造成的移动
    被误判为外部冲突，而外部编辑仍会移动基础并显式冲突（评审 #4）。
    """

    proposal_id: str
    chapter_id: str
    base: str
    applied_base: str = ""
    hunks: tuple[PatchHunk, ...] = ()
    source: str = PATCH_SOURCE_AGENT
    schema_version: int = SCHEMA_VERSION
    legacy_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "hunks", tuple(self.hunks))
        object.__setattr__(self, "legacy_metadata", dict(self.legacy_metadata))

    def pending_hunks(self) -> tuple[PatchHunk, ...]:
        return tuple(h for h in self.hunks if h.is_pending)

    def status(self) -> str:
        """提案状态由 hunk 状态派生（不落库，避免双写不一致）。"""
        if any(h.is_pending for h in self.hunks):
            return PROPOSAL_PENDING
        accepted = any(h.status == HUNK_ACCEPTED for h in self.hunks)
        rejected = any(h.status == HUNK_REJECTED for h in self.hunks)
        if accepted and rejected:
            return PROPOSAL_PARTIAL
        if accepted:
            return PROPOSAL_ACCEPTED
        return PROPOSAL_REJECTED

    def to_content(self) -> dict[str, Any]:
        return {
            **self.legacy_metadata,
            "schema_version": self.schema_version,
            "proposal_id": self.proposal_id,
            "chapter_id": self.chapter_id,
            "base": self.base,
            "applied_base": self.applied_base,
            "hunks": [h.to_content() for h in self.hunks],
            "source": self.source,
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> ManuscriptPatchProposal:
        where = "manuscript.patch_proposal"
        source = str(content.get("source", PATCH_SOURCE_AGENT))
        if source not in _PATCH_SOURCES:
            raise violation(
                ec.MANUSCRIPT_INVALID_OPERATION,
                where,
                f"提案来源只能是 {sorted(_PATCH_SOURCES)}，收到 {source!r}",
                "把 source 改为 agent 或 human",
                source=source,
            )
        return cls(
            proposal_id=require_str(content.get("proposal_id"), field="proposal_id", where=where),
            chapter_id=require_str(content.get("chapter_id"), field="chapter_id", where=where),
            base=str(content.get("base", "")),
            applied_base=str(content.get("applied_base", "")),
            hunks=tuple(PatchHunk.from_content(h) for h in (content.get("hunks") or [])),
            source=source,
            schema_version=int(content.get("schema_version", SCHEMA_VERSION)),
            legacy_metadata=split_legacy(content, _PROPOSAL_KEYS),
        )
