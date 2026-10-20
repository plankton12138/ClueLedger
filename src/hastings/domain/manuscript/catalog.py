"""ManuscriptCatalog：把 Repository Snapshot 投影为正文目录（T14.1）。

领域命令不直接访问存储：application handler 先取 Snapshot、构造
ManuscriptCatalog，再交给命令函数。目录提供按 ID 精确定位、按章的显式顺序
取活动段落、以及整篇只读投影 Manuscript。

本模块是「读」抽象，不产生写入；写路径只经 ManuscriptChange 返回。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from hastings.domain.manuscript import error_codes as ec
from hastings.domain.manuscript import kinds
from hastings.domain.manuscript.errors import violation
from hastings.domain.manuscript.models import (
    Chapter,
    ChapterView,
    Manuscript,
    ManuscriptPatchProposal,
    TextUnit,
)
from hastings.domain.repository.models import Snapshot
from hastings.domain.repository.serialization import content_hash

__all__ = ["ManuscriptCatalog", "ManuscriptEntry", "content_base", "decode"]

Decoder = Callable[[dict[str, Any]], Any]


def content_base(chapter_id: str, pairs: Iterable[tuple[str, str]]) -> str:
    """章基础身份的统一计算：有序 ``(unit_id, 文本)`` 对的内容哈希。

    目录读投影（``ManuscriptCatalog.chapter_base``）与命令层应用后的状态
    （``commands._apply``）都必须经同一个函数得到基础，否则逐 hunk 接受记回
    提案的 applied_base 与下次读到的真实基础对不上（评审 #4）。
    """
    return content_hash(
        {"chapter_id": chapter_id, "units": [[unit_id, text] for unit_id, text in pairs]}
    )


_DECODERS: dict[str, Decoder] = {
    kinds.KIND_CHAPTER: Chapter.from_content,
    kinds.KIND_TEXT_UNIT: TextUnit.from_content,
    kinds.KIND_PATCH_PROPOSAL: ManuscriptPatchProposal.from_content,
}


def decode(kind: str, content: dict[str, Any]) -> Any:
    """按 kind 解码为领域模型；未注册 kind 返回原始 content。"""
    decoder = _DECODERS.get(kind)
    if decoder is None:
        return content
    return decoder(content)


@dataclass(frozen=True, slots=True)
class ManuscriptEntry:
    """Snapshot 中单个正文对象：身份 + 有效 Revision + 内容。"""

    artifact_id: str
    kind: str
    revision_id: str
    content: dict[str, Any]

    def model(self) -> Any:
        return decode(self.kind, self.content)


def next_order_keys(catalog: ManuscriptCatalog, count: int) -> list[str]:
    """追加章的顺序键：从现有最大数字 +1 起四位补零；非数字键忽略。

    单章导入不传顺序键时用它接着排，避免每章都落到 ``0001`` 而退回按 ID 排序。
    """
    highest = 0
    for entry in catalog.chapters():
        try:
            highest = max(highest, int(entry.model().order_key))
        except (TypeError, ValueError):
            continue
    return [f"{highest + index + 1:04d}" for index in range(max(0, count))]


@dataclass(frozen=True, slots=True)
class ManuscriptCatalog:
    """某 Commit 的正文目录（只读）。commit_id 为 None 表示空目录。"""

    commit_id: str | None
    entries: tuple[ManuscriptEntry, ...]

    @classmethod
    def from_snapshot(cls, snapshot: Snapshot) -> ManuscriptCatalog:
        entries: list[ManuscriptEntry] = []
        for view in snapshot.artifacts.values():
            if view.revision is None:
                continue
            if not kinds.is_manuscript_kind(view.artifact.kind):
                continue
            entries.append(
                ManuscriptEntry(
                    artifact_id=view.artifact.id.value,
                    kind=view.artifact.kind,
                    revision_id=view.revision.id.value,
                    content=dict(view.revision.content),
                )
            )
        entries.sort(key=lambda e: e.artifact_id)
        return cls(commit_id=snapshot.commit_id.value, entries=tuple(entries))

    # ------------------------------------------------------------------
    # 定位
    # ------------------------------------------------------------------
    def get(self, artifact_id: str) -> ManuscriptEntry | None:
        return next((e for e in self.entries if e.artifact_id == artifact_id), None)

    def by_kind(self, kind: str) -> tuple[ManuscriptEntry, ...]:
        return tuple(e for e in self.entries if e.kind == kind)

    def chapter_entry(self, chapter_id: str) -> ManuscriptEntry | None:
        entry = self.get(chapter_id)
        return entry if entry is not None and entry.kind == kinds.KIND_CHAPTER else None

    def require_chapter(self, chapter_id: str, *, where: str) -> ManuscriptEntry:
        entry = self.get(chapter_id)
        if entry is None:
            raise violation(
                ec.MANUSCRIPT_UNKNOWN_CHAPTER,
                where,
                f"章 {chapter_id!r} 在当前快照中不存在",
                "改用快照内存在的章 ID，或先导入该章",
                chapter_id=chapter_id,
            )
        if entry.kind != kinds.KIND_CHAPTER:
            raise violation(
                ec.MANUSCRIPT_INVALID_OPERATION,
                where,
                f"{chapter_id!r} 的类型是 {entry.kind!r}，不是章",
                f"章引用必须指向 {kinds.KIND_CHAPTER} 对象",
                chapter_id=chapter_id,
                kind=entry.kind,
            )
        return entry

    def unit_entry(self, unit_id: str) -> ManuscriptEntry | None:
        entry = self.get(unit_id)
        return entry if entry is not None and entry.kind == kinds.KIND_TEXT_UNIT else None

    def require_unit(self, unit_id: str, *, where: str) -> ManuscriptEntry:
        entry = self.get(unit_id)
        if entry is None:
            raise violation(
                ec.MANUSCRIPT_UNKNOWN_TEXT_UNIT,
                where,
                f"段落 {unit_id!r} 在当前快照中不存在",
                "改用快照内存在的段落 ID",
                unit_id=unit_id,
            )
        if entry.kind != kinds.KIND_TEXT_UNIT:
            raise violation(
                ec.MANUSCRIPT_INVALID_OPERATION,
                where,
                f"{unit_id!r} 的类型是 {entry.kind!r}，不是段落",
                f"段落引用必须指向 {kinds.KIND_TEXT_UNIT} 对象",
                unit_id=unit_id,
                kind=entry.kind,
            )
        return entry

    def proposal_entry(self, proposal_id: str) -> ManuscriptEntry | None:
        entry = self.get(proposal_id)
        return entry if entry is not None and entry.kind == kinds.KIND_PATCH_PROPOSAL else None

    def require_proposal(self, proposal_id: str, *, where: str) -> ManuscriptEntry:
        """定位 Agent Patch 提案；提案只是候选编辑的登记处，不是正文。"""
        entry = self.get(proposal_id)
        if entry is None:
            raise violation(
                ec.MANUSCRIPT_UNKNOWN_PROPOSAL,
                where,
                f"提案 {proposal_id!r} 在当前快照中不存在",
                "先 propose_patch 登记提案，再用它返回的 proposal_id 接受或拒绝",
                proposal_id=proposal_id,
            )
        if entry.kind != kinds.KIND_PATCH_PROPOSAL:
            raise violation(
                ec.MANUSCRIPT_INVALID_OPERATION,
                where,
                f"{proposal_id!r} 的类型是 {entry.kind!r}，不是 Patch 提案",
                f"提案引用必须指向 {kinds.KIND_PATCH_PROPOSAL} 对象",
                proposal_id=proposal_id,
                kind=entry.kind,
            )
        return entry

    # ------------------------------------------------------------------
    # 投影
    # ------------------------------------------------------------------
    def chapters(self) -> tuple[ManuscriptEntry, ...]:
        """按 order_key（再按 ID）排序的全部章。"""
        rows = self.by_kind(kinds.KIND_CHAPTER)
        return tuple(sorted(rows, key=lambda e: (e.model().order_key, e.artifact_id)))

    def units_of(self, chapter_id: str) -> tuple[ManuscriptEntry, ...]:
        """某章的有序**活动**段落（顺序以章的 text_unit_ids 为准）。"""
        chapter: Chapter = self.require_chapter(chapter_id, where="manuscript.units_of").model()
        result: list[ManuscriptEntry] = []
        for unit_id in chapter.text_unit_ids:
            entry = self.unit_entry(unit_id)
            if entry is None:
                continue
            if entry.model().is_active:
                result.append(entry)
        return tuple(result)

    def manuscript(self) -> Manuscript:
        """整篇只读投影：章按顺序，段落按章的显式顺序。"""
        chapters = tuple(
            ChapterView(
                chapter=entry.model(),
                units=tuple(u.model() for u in self.units_of(entry.artifact_id)),
            )
            for entry in self.chapters()
        )
        return Manuscript(commit_id=self.commit_id, chapters=chapters)

    # ------------------------------------------------------------------
    # 乐观并发基础（T14.3）
    # ------------------------------------------------------------------
    def chapter_base(self, chapter_id: str) -> str:
        """章的基础身份：活动段落有序 ``(unit_id, 文本)`` 对的哈希。

        任何段落文本改动或结构改动（增删拆分合并移动）都会移动它；其他章的改动
        不影响——因此它是「章节范围」的乐观并发基础，比 Commit 级 base 更精准
        （纯文本替换不移动章 Artifact 自身的 Revision，不能用它做 base）。

        基于内容而非 Revision 身份：命令是纯函数，拿不到提交时才分配的 Revision，
        但拿得到「应用本批操作后」的全部段落文本——见 ``content_base``，逐 hunk
        接受靠它在提交前算出应用后的基础并记回提案（评审 #4）。no-op（文本不变）
        不移动基础，任何实际文本/结构变化都移动，检测能力与 Revision 语义等价。
        """
        chapter: Chapter = self.require_chapter(chapter_id, where="manuscript.chapter_base").model()
        pairs: list[tuple[str, str]] = []
        for unit_id in chapter.text_unit_ids:
            entry = self.unit_entry(unit_id)
            if entry is None or not entry.model().is_active:
                continue
            pairs.append((unit_id, entry.model().text))
        return content_base(chapter_id, pairs)
