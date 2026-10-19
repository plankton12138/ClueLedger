"""原文覆盖台账：逐章记录整体任务里「哪些章被真正读过」（V03-7.2，计划 M10 第 5 条）。

一次整体调整任务不是逐章审批，而是连续把作者授权范围内的章读一遍、改需要改的章。
为了让作者事后能查「哪些章真读过、哪些还没覆盖」，任务内部维护一份覆盖台账：每章
带一个状态与**读取区间**，状态取五值之一：

- ``unread``：还没读；
- ``read``：读过（全文、部分区间或只读了目录/摘要），但还没确认「无需修改」；
- ``checked_unchanged``：**全文检查后确认无需修改**；
- ``changed``：本次任务按授权改过该章（改动本身就是覆盖）；
- ``blocked``：想读但被具体条件挡住（如未结束副本、环境故障），带未覆盖原因。

硬约束（本模块的核心价值，四透镜②）：**只读摘要/目录不能记 ``checked_unchanged``**。
「读摘要」与「读全文」在这里是两个不同的 ``read_kind``，而确认无需修改要求
``read_kind == full`` 且读取区间**覆盖整章**；否则一律拒绝并给出三要素指引，
让调用方（模型/工作流）回去把整章读完，而不是把「目录里看过一眼」当成「全文检查过」。

本模块是纯函数：不读库、不调模型、不写任何东西，全部是 ``frozen`` 值对象与
返回新对象的迁移方法。持久化在 ``application/services/revision_work_service.py``。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from hastings.domain.revision import error_codes as ec
from hastings.domain.revision.errors import violation

__all__ = [
    "COVERAGE_STATUSES",
    "COVERED_STATUSES",
    "ChapterCoverage",
    "CoverageLedger",
    "READ_KINDS",
    "READ_KIND_FULL",
    "READ_KIND_SUMMARY",
    "ReadRange",
    "STATUS_BLOCKED",
    "STATUS_CHANGED",
    "STATUS_CHECKED_UNCHANGED",
    "STATUS_READ",
    "STATUS_UNREAD",
    "uncovered_reason",
]

# ---- 覆盖状态 -------------------------------------------------------------
STATUS_UNREAD = "unread"
STATUS_READ = "read"
STATUS_CHECKED_UNCHANGED = "checked_unchanged"
STATUS_CHANGED = "changed"
STATUS_BLOCKED = "blocked"

COVERAGE_STATUSES: tuple[str, ...] = (
    STATUS_UNREAD,
    STATUS_READ,
    STATUS_CHECKED_UNCHANGED,
    STATUS_CHANGED,
    STATUS_BLOCKED,
)

#: 「已覆盖」状态：确认无需修改，或已按授权改过。其余（未读/已读/受阻）仍算未覆盖。
COVERED_STATUSES: frozenset[str] = frozenset((STATUS_CHECKED_UNCHANGED, STATUS_CHANGED))

# ---- 读取种类 -------------------------------------------------------------
#: 只读了目录/章节摘要：给出了标题/字数等概览，**不含全文**。
READ_KIND_SUMMARY = "summary"
#: 读了整章正文（可能分段读取，但要能覆盖整章区间才算全文检查）。
READ_KIND_FULL = "full"
READ_KINDS: tuple[str, ...] = (READ_KIND_SUMMARY, READ_KIND_FULL)


@dataclass(frozen=True, slots=True)
class ReadRange:
    """一次读取覆盖的字符区间；空章用 ``(0, 0)`` 表示「整章已读」。"""

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise violation(
                ec.COVERAGE_INVALID_TRANSITION,
                where="coverage.range",
                why=f"读取区间非法：start={self.start}, end={self.end}",
                fix="给出满足 0 ≤ start ≤ end 的字符区间（对照整章正文字符长度）",
                start=self.start,
                end=self.end,
            )

    def covers(self, length: int) -> bool:
        """该单一区间是否已覆盖 ``[0, length)``（整章）。"""
        return self.start <= 0 and self.end >= length

    def to_wire(self) -> dict[str, int]:
        return {"start": self.start, "end": self.end}

    @classmethod
    def from_wire(cls, payload: Any) -> ReadRange:
        if not isinstance(payload, Mapping):
            raise violation(
                ec.COVERAGE_INVALID_TRANSITION,
                where="coverage.range",
                why="读取区间不是对象",
                fix='给出 {"start": 0, "end": 字数} 的区间对象',
            )
        return cls(start=int(payload.get("start", 0)), end=int(payload.get("end", 0)))


def _merge_ranges(ranges: Iterable[ReadRange]) -> tuple[ReadRange, ...]:
    """按起点升序合并重叠/相邻区间，得到规范化、最小的等价区间列表。"""
    ordered = sorted(ranges, key=lambda r: (r.start, r.end))
    merged: list[ReadRange] = []
    for current in ordered:
        if merged and current.start <= merged[-1].end:
            last = merged[-1]
            merged[-1] = ReadRange(last.start, max(last.end, current.end))
        else:
            merged.append(current)
    return tuple(merged)


def _covers_whole(ranges: tuple[ReadRange, ...], length: int) -> bool:
    """规范化区间是否**拼接覆盖整章** ``[0, length)``。空章（length ≤ 0）直接算覆盖。"""
    if length <= 0:
        return True
    merged = _merge_ranges(ranges)
    return bool(merged) and merged[0].start <= 0 and merged[0].end >= length


@dataclass(frozen=True, slots=True)
class ChapterCoverage:
    """一章的覆盖记录：状态 + 来源版本 + 读取区间 + 未覆盖原因。"""

    chapter_id: str
    status: str = STATUS_UNREAD
    source_revision_id: str = ""
    text_length: int = 0
    read_kind: str = ""
    ranges: tuple[ReadRange, ...] = ()
    reason: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "ranges", _merge_ranges(self.ranges))
        if self.status not in COVERAGE_STATUSES:
            raise violation(
                ec.COVERAGE_INVALID_TRANSITION,
                where="coverage.entry",
                why=f"覆盖状态只能是 {sorted(COVERAGE_STATUSES)}，收到 {self.status!r}",
                fix="把状态改为 unread/read/checked_unchanged/changed/blocked",
            )
        if self.read_kind not in ("", *READ_KINDS):
            raise violation(
                ec.COVERAGE_INVALID_TRANSITION,
                where="coverage.entry",
                why=f"读取种类只能是 '' 或 {sorted(READ_KINDS)}，收到 {self.read_kind!r}",
                fix="把 read_kind 改为 summary 或 full（未读过留空）",
            )

    @property
    def fully_read(self) -> bool:
        """是否做了**全文检查**：整章区间被覆盖，且读的是全文而不是摘要。"""
        return self.read_kind == READ_KIND_FULL and _covers_whole(self.ranges, self.text_length)

    @property
    def covered(self) -> bool:
        return self.status in COVERED_STATUSES

    def to_wire(self) -> dict[str, Any]:
        return {
            "chapter_id": self.chapter_id,
            "status": self.status,
            "source_revision_id": self.source_revision_id,
            "text_length": self.text_length,
            "read_kind": self.read_kind,
            "ranges": [r.to_wire() for r in self.ranges],
            "reason": self.reason,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> ChapterCoverage:
        return cls(
            chapter_id=str(payload.get("chapter_id", "")),
            status=str(payload.get("status", STATUS_UNREAD)),
            source_revision_id=str(payload.get("source_revision_id", "")),
            text_length=int(payload.get("text_length", 0)),
            read_kind=str(payload.get("read_kind", "")),
            ranges=tuple(ReadRange.from_wire(r) for r in (payload.get("ranges") or [])),
            reason=str(payload.get("reason", "")),
            updated_at=str(payload.get("updated_at", "")),
        )


def uncovered_reason(entry: ChapterCoverage) -> str:
    """未覆盖的可读原因（合法空结果不报错，但要能解释「为什么还没覆盖」）。"""
    if entry.status == STATUS_UNREAD:
        return "尚未读取该章"
    if entry.status == STATUS_BLOCKED:
        return entry.reason or "读取受阻，原因未记录"
    if entry.status == STATUS_READ:
        if entry.read_kind == READ_KIND_SUMMARY:
            return "只读了目录/摘要，未做全文检查"
        if entry.ranges and not entry.fully_read:
            covered = sum(r.end - r.start for r in entry.ranges)
            return f"只读了部分区间（约 {covered}/{entry.text_length} 字），未覆盖整章"
        return "已读全文但尚未确认无需修改"
    return ""


@dataclass(frozen=True, slots=True)
class CoverageLedger:
    """一次整体任务的原文覆盖台账：声明章节集合 + 逐章覆盖记录。"""

    work_id: str
    project_id: str
    base_commit_id: str
    chapter_ids: tuple[str, ...] = ()
    entries: tuple[ChapterCoverage, ...] = ()
    updated_at: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "chapter_ids", tuple(self.chapter_ids))
        object.__setattr__(self, "entries", tuple(self.entries))

    @classmethod
    def create(
        cls,
        work_id: str,
        project_id: str,
        base_commit_id: str,
        chapter_ids: Iterable[str],
        *,
        at: str = "",
    ) -> CoverageLedger:
        """开账：声明章节集合，全部初始为 ``unread``。"""
        ordered = tuple(dict.fromkeys(chapter_ids))
        return cls(
            work_id=work_id,
            project_id=project_id,
            base_commit_id=base_commit_id,
            chapter_ids=ordered,
            entries=tuple(ChapterCoverage(chapter_id=cid) for cid in ordered),
            updated_at=at,
        )

    # ---- 查询 -------------------------------------------------------------
    def entry(self, chapter_id: str) -> ChapterCoverage | None:
        return next((e for e in self.entries if e.chapter_id == chapter_id), None)

    def require_entry(self, chapter_id: str) -> ChapterCoverage:
        """定位声明集合内的一章；不在集合内即三要素违规（应用层复用此定位）。"""
        entry = self.entry(chapter_id)
        if entry is None:
            raise violation(
                ec.COVERAGE_UNKNOWN_CHAPTER,
                where=f"coverage({chapter_id})",
                why=f"章 {chapter_id!r} 不在本任务声明的章节集合内",
                fix="只在任务的章节清单内记录读取；新章先随发布进入正文再纳入清单",
                chapter_id=chapter_id,
                chapter_ids=list(self.chapter_ids),
            )
        return entry

    @property
    def covered(self) -> tuple[ChapterCoverage, ...]:
        return tuple(e for e in self.entries if e.covered)

    def uncovered(self) -> tuple[ChapterCoverage, ...]:
        """尚未覆盖的章（未读/已读未确认/受阻），逐章带可读原因。"""
        return tuple(e for e in self.entries if not e.covered)

    @property
    def complete(self) -> bool:
        """是否覆盖全部章——到这一步才允许进入「提交前检查」与发布。"""
        return not self.uncovered()

    def _with(self, updated: ChapterCoverage, at: str) -> CoverageLedger:
        entries = tuple(updated if e.chapter_id == updated.chapter_id else e for e in self.entries)
        return replace(self, entries=entries, updated_at=at)

    # ---- 迁移（返回新台账，不原地改） --------------------------------------
    def with_full_read(
        self,
        chapter_id: str,
        *,
        source_revision_id: str,
        text_length: int,
        start: int,
        end: int,
        at: str = "",
    ) -> CoverageLedger:
        """记录一次全文读取的字符区间；区间累积，来源变化则重置既往读取。"""
        entry = self.require_entry(chapter_id)
        source_changed = source_revision_id != entry.source_revision_id
        ranges = () if source_changed else entry.ranges
        status = entry.status
        if status != STATUS_CHANGED and (source_changed or status != STATUS_CHECKED_UNCHANGED):
            # 来源变了或原本不是「已确认」：退回到「已读」，重新判定。
            status = STATUS_READ
        updated = replace(
            entry,
            status=status,
            source_revision_id=source_revision_id,
            text_length=text_length,
            read_kind=READ_KIND_FULL,
            ranges=_merge_ranges((*ranges, ReadRange(start, end))),
            reason="",
            updated_at=at,
        )
        return self._with(updated, at)

    def with_summary_read(
        self,
        chapter_id: str,
        *,
        source_revision_id: str,
        text_length: int,
        at: str = "",
    ) -> CoverageLedger:
        """记录一次目录/摘要读取：**永不产生 checked_unchanged**（V03-7.2 硬约束）。"""
        entry = self.require_entry(chapter_id)
        source_changed = source_revision_id != entry.source_revision_id
        ranges = () if source_changed else entry.ranges
        # 摘要不覆盖既有全文读取，也不把 full 降级为 summary。
        read_kind = entry.read_kind if entry.read_kind else READ_KIND_SUMMARY
        status = entry.status
        if status != STATUS_CHANGED and (source_changed or status != STATUS_CHECKED_UNCHANGED):
            status = STATUS_READ
        updated = replace(
            entry,
            status=status,
            source_revision_id=source_revision_id,
            text_length=text_length,
            read_kind=read_kind,
            ranges=ranges,
            reason="",
            updated_at=at,
        )
        return self._with(updated, at)

    def with_confirmed_unchanged(self, chapter_id: str, *, at: str = "") -> CoverageLedger:
        """确认该章无需修改——**要求先做过全文检查**（硬约束，四透镜②）。

        只读摘要/目录、或全文区间未覆盖整章时一律拒绝，错误消息指明「怎样算对」，
        避免调用方把「目录里看过一眼」当成「全文检查过」（计划 M10 第 5 条）。
        """
        entry = self.require_entry(chapter_id)
        if entry.status == STATUS_CHANGED:
            raise violation(
                ec.COVERAGE_INVALID_TRANSITION,
                where=f"coverage.confirm({chapter_id})",
                why="该章已在本次任务中按授权改过，不能再标为「无需修改」",
                fix="改过的章记 changed 即可；无需修改的判断只用于未改动的章",
                chapter_id=chapter_id,
            )
        if not entry.fully_read:
            raise violation(
                ec.COVERAGE_NOT_FULLY_READ,
                where=f"coverage.confirm({chapter_id})",
                why=_not_full_reason(entry),
                fix="先读该章完整正文（读取区间覆盖整章），再做「无需修改」判断；"
                "目录/章节摘要读取不算全文检查",
                chapter_id=chapter_id,
                status=entry.status,
                read_kind=entry.read_kind,
                text_length=entry.text_length,
            )
        updated = replace(entry, status=STATUS_CHECKED_UNCHANGED, reason="", updated_at=at)
        return self._with(updated, at)

    def with_change(
        self,
        chapter_id: str,
        *,
        source_revision_id: str = "",
        text_length: int | None = None,
        at: str = "",
    ) -> CoverageLedger:
        """记录该章已被本次任务改过；改动即覆盖，既往读取区间随来源变化清空。"""
        entry = self.require_entry(chapter_id)
        updated = replace(
            entry,
            status=STATUS_CHANGED,
            source_revision_id=source_revision_id or entry.source_revision_id,
            text_length=entry.text_length if text_length is None else text_length,
            read_kind="",
            ranges=(),
            reason="",
            updated_at=at,
        )
        return self._with(updated, at)

    def with_blocked(self, chapter_id: str, *, reason: str, at: str = "") -> CoverageLedger:
        """记录该章读取受阻（未结束副本、环境故障等），带未覆盖原因。"""
        if not reason.strip():
            raise violation(
                ec.COVERAGE_INVALID_TRANSITION,
                where=f"coverage.blocked({chapter_id})",
                why="受阻必须给具体原因，否则与「还没读」无从区分",
                fix="说明挡住读取的条件（如未结束副本、来源不可读、环境故障）",
                chapter_id=chapter_id,
            )
        entry = self.require_entry(chapter_id)
        updated = replace(entry, status=STATUS_BLOCKED, reason=reason, updated_at=at)
        return self._with(updated, at)

    # ---- 序列化 -----------------------------------------------------------
    def to_wire(self) -> dict[str, Any]:
        return {
            "work_id": self.work_id,
            "project_id": self.project_id,
            "base_commit_id": self.base_commit_id,
            "chapter_ids": list(self.chapter_ids),
            "entries": [e.to_wire() for e in self.entries],
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> CoverageLedger:
        return cls(
            work_id=str(payload.get("work_id", "")),
            project_id=str(payload.get("project_id", "")),
            base_commit_id=str(payload.get("base_commit_id", "")),
            chapter_ids=tuple(str(c) for c in (payload.get("chapter_ids") or [])),
            entries=tuple(ChapterCoverage.from_wire(e) for e in (payload.get("entries") or [])),
            updated_at=str(payload.get("updated_at", "")),
        )


def _not_full_reason(entry: ChapterCoverage) -> str:
    if entry.read_kind == READ_KIND_SUMMARY:
        return "只读到目录/摘要，未做全文检查"
    if entry.read_kind == READ_KIND_FULL:
        covered = sum(r.end - r.start for r in entry.ranges)
        return f"全文读取区间未覆盖整章（约 {covered}/{entry.text_length} 字）"
    return "该章尚未读取，无从判断是否无需修改"
