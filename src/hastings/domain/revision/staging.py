"""内部工作版本的暂存区（V03-7.4，计划 M10 第 9/10 条）。

整体调整在执行期间先改**内部工作版本**，不碰正式正文与账本。这里存的就是
「准备发布什么」：

- 逐章的完整新文本 + 它依据的章基础版本（``base_revision_id``）；
- 待新建/修订的账本条目（修订必须带条目当前 ``revision_id``）。

暂存不是提交：它没有 Commit、没有正式版本，也不改覆盖台账。发布时由
``application/services/revision_publish_service.py`` 把全部暂存内容放进**同一个
Commit**；任一对象的基础已变则整体不发布（不出现「正文更新了、账本没更新」的半套）。

本模块是纯函数：不读库、不调模型。``base_revision_id`` 是准备时读到的章基础，
发布时用它与正式版逐章比对，作为「基础重验」的输入。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from hastings.domain.revision import error_codes as ec
from hastings.domain.revision.errors import violation

__all__ = [
    "RevisionStaging",
    "StagedChapter",
    "StagedLedgerChange",
]


@dataclass(frozen=True, slots=True)
class StagedChapter:
    """一章的暂存全文：发布时按 ``base_revision_id`` 做逐对象 CAS。"""

    chapter_id: str
    base_revision_id: str
    text: str
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not self.chapter_id:
            raise violation(
                ec.REVISION_STAGE_INVALID,
                where="revision_stage.chapter",
                why="暂存章缺少 chapter_id",
                fix="给出要修改的章 ID（必须在本轮工作版本的章节清单内）",
            )
        if not self.base_revision_id:
            raise violation(
                ec.REVISION_STAGE_INVALID,
                where=f"revision_stage.chapter({self.chapter_id})",
                why="暂存章缺少 base_revision_id",
                fix="先读取该章当前正文并带上它的章基础版本；没有基础的暂存无法安全发布",
                chapter_id=self.chapter_id,
            )
        if not self.text.strip():
            raise violation(
                ec.REVISION_STAGE_INVALID,
                where=f"revision_stage.chapter({self.chapter_id})",
                why="暂存正文为空",
                fix="写入要发布的完整章节正文；本版不支持用空文本删除整章",
                chapter_id=self.chapter_id,
            )

    def to_wire(self) -> dict[str, Any]:
        return {
            "chapter_id": self.chapter_id,
            "base_revision_id": self.base_revision_id,
            "text": self.text,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> StagedChapter:
        return cls(
            chapter_id=str(payload.get("chapter_id", "")),
            base_revision_id=str(payload.get("base_revision_id", "")),
            text=str(payload.get("text", "")),
            updated_at=str(payload.get("updated_at", "")),
        )


@dataclass(frozen=True, slots=True)
class StagedLedgerChange:
    """一条待发布账本变化：``entry_id=None`` 为新建，否则为修订。

    修订要求 ``expected_revision``：发布时按它做 revision CAS，作者/模型在暂存
    之后又改过该条目就整体拒绝，不会用旧内容覆盖新内容。
    """

    stage_id: str
    fields: dict[str, Any]
    entry_id: str | None = None
    kind: str = ""
    expected_revision: str | None = None
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not self.stage_id:
            raise violation(
                ec.REVISION_STAGE_INVALID,
                where="revision_stage.ledger",
                why="暂存账本变化缺少 stage_id",
                fix="给一个稳定 stage_id（修订默认用 entry_id），便于覆盖与撤销暂存",
            )
        if self.entry_id is None and not self.kind:
            raise violation(
                ec.REVISION_STAGE_INVALID,
                where=f"revision_stage.ledger({self.stage_id})",
                why="新建账本条目缺少 kind",
                fix="新建时给出条目种类（人物/地点/线索/真相/关系等）",
                stage_id=self.stage_id,
            )
        if self.entry_id is not None and not self.expected_revision:
            raise violation(
                ec.REVISION_STAGE_INVALID,
                where=f"revision_stage.ledger({self.stage_id})",
                why="修订账本条目缺少 expected_revision",
                fix="先读取条目当前 revision_id 再暂存；否则发布时会盲覆盖别人的修改",
                stage_id=self.stage_id,
                entry_id=self.entry_id,
            )

    def to_wire(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "entry_id": self.entry_id,
            "kind": self.kind,
            "fields": dict(self.fields),
            "expected_revision": self.expected_revision,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> StagedLedgerChange:
        return cls(
            stage_id=str(payload.get("stage_id", "")),
            fields=dict(payload.get("fields") or {}),
            entry_id=None if payload.get("entry_id") is None else str(payload["entry_id"]),
            kind=str(payload.get("kind", "")),
            expected_revision=(
                None
                if payload.get("expected_revision") is None
                else str(payload["expected_revision"])
            ),
            updated_at=str(payload.get("updated_at", "")),
        )


@dataclass(frozen=True, slots=True)
class RevisionStaging:
    """一次任务的暂存内容：章节按 chapter_id 唯一，账本按 stage_id 唯一。"""

    task_id: str
    project_id: str
    chapters: tuple[StagedChapter, ...] = ()
    ledger: tuple[StagedLedgerChange, ...] = ()
    updated_at: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "chapters", tuple(self.chapters))
        object.__setattr__(self, "ledger", tuple(self.ledger))

    @classmethod
    def empty(cls, *, task_id: str, project_id: str) -> RevisionStaging:
        return cls(task_id=task_id, project_id=project_id)

    @property
    def is_empty(self) -> bool:
        return not self.chapters and not self.ledger

    def chapter(self, chapter_id: str) -> StagedChapter | None:
        return next((c for c in self.chapters if c.chapter_id == chapter_id), None)

    def with_chapter(self, staged: StagedChapter) -> RevisionStaging:
        chapters = tuple(staged if c.chapter_id == staged.chapter_id else c for c in self.chapters)
        if self.chapter(staged.chapter_id) is None:
            chapters = (*self.chapters, staged)
        return replace(self, chapters=chapters, updated_at=staged.updated_at)

    def without_chapter(self, chapter_id: str, *, at: str = "") -> RevisionStaging:
        return replace(
            self,
            chapters=tuple(c for c in self.chapters if c.chapter_id != chapter_id),
            updated_at=at,
        )

    def with_ledger(self, staged: StagedLedgerChange) -> RevisionStaging:
        ledger = tuple(item if item.stage_id != staged.stage_id else staged for item in self.ledger)
        if not any(item.stage_id == staged.stage_id for item in self.ledger):
            ledger = (*self.ledger, staged)
        return replace(self, ledger=ledger, updated_at=staged.updated_at)

    def without_ledger(self, stage_id: str, *, at: str = "") -> RevisionStaging:
        return replace(
            self,
            ledger=tuple(item for item in self.ledger if item.stage_id != stage_id),
            updated_at=at,
        )

    def to_wire(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "chapters": [c.to_wire() for c in self.chapters],
            "ledger": [item.to_wire() for item in self.ledger],
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> RevisionStaging:
        return cls(
            task_id=str(payload.get("task_id", "")),
            project_id=str(payload.get("project_id", "")),
            chapters=tuple(StagedChapter.from_wire(c) for c in (payload.get("chapters") or [])),
            ledger=tuple(
                StagedLedgerChange.from_wire(item) for item in (payload.get("ledger") or [])
            ),
            updated_at=str(payload.get("updated_at", "")),
        )
