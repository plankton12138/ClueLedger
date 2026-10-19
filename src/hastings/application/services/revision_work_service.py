"""内部工作版本与原文覆盖台账服务（V03-7.2，计划 M10 第 5–6 条）。

一次整体调整任务在正式发布前有个**内部工作版本**：它绑定一个正式基础 Commit，
声明本次要覆盖的章节集合，并逐章记录「实际读到哪、是全文还是摘要、是否已确认无需
修改、是否已按授权改过、是否受阻」。这份记录就是**原文覆盖台账**——作者事后可查
「哪些章真读过、哪些还没覆盖」，也是「提交前检查」的依据（未覆盖不许当作完成）。

存储沿用仓库辅助表（V03-0.5 端口）：整份台账作为一条记录原子读-改-写，与既有的
手工修改副本同构。此处**只读摘要**与**读全文**是两个不同的读取种类，只读摘要**永远
不能**把一章记成 ``checked_unchanged``——这条硬约束写在领域层（
``domain/revision/coverage.py`` 的 ``with_confirmed_unchanged``），服务层只负责把
真实的章来源与字数喂给领域。

``global_context`` 是本任务可回读的**全局上下文**：正式基础、逐章目录与来源版本、
覆盖台账与未覆盖清单。它是从工作版本记录 + 仓库按版本推导的**只读投影**，不另存
第二份事实（I08/I09）。把它堆进 ``ContextSnapshot`` 供模型使用属 V03-7.3 的执行
循环；本任务先把「读得到、查得到」这条落稳。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hastings.application.errors import ViolationError
from hastings.application.ports.ids import IdGenerator
from hastings.application.services.commit_service import CommitService
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.repository.ids import CommitId, ProjectId
from hastings.domain.revision import error_codes as ec
from hastings.domain.revision.coverage import (
    READ_KIND_FULL,
    READ_KIND_SUMMARY,
    CoverageLedger,
    uncovered_reason,
)

__all__ = ["RevisionWorkService", "WORK_BUCKET"]

#: 内部工作版本记录（当前唯一；key 固定为 ``current``）。
WORK_BUCKET = "revision_work"

_CURRENT_KEY = "current"

_MAIN_BRANCH = "main"


@dataclass(frozen=True, slots=True)
class _ChapterFact:
    """某章在当前正式版本的来源事实：内容基础哈希与整章正文字数。"""

    chapter_id: str
    title: str
    order_key: str
    chapter_base: str
    text_length: int


class RevisionWorkService:
    """打开/恢复内部工作版本，逐章记录读取与覆盖，回读覆盖台账与全局上下文。"""

    def __init__(self, repo: CommitService, ids: IdGenerator, clock: Any) -> None:
        self._repo = repo
        self._ids = ids
        self._clock: Any = clock

    # -- 打开与回读 -----------------------------------------------------------
    def open(self, project_id: str) -> dict[str, Any]:
        """打开（或恢复）当前工作版本：绑定正式 head，声明章节集合为全书各章。

        幂等：已有未结束的工作版本则原样返回，不重建、不改基础——后到的请求
        不会悄悄把正在进行的一轮重置。空作品（无提交）明确拒绝并给出下一步。
        """
        pid = ProjectId(project_id)
        existing = self._repo.aux_read(pid, WORK_BUCKET, _CURRENT_KEY)
        if existing is not None:
            return self._view(CoverageLedger.from_wire(existing))
        head = self._head(pid)
        catalog = ManuscriptCatalog.from_snapshot(self._repo.get_snapshot(pid, CommitId(head)))
        ledger = CoverageLedger.create(
            work_id=self._ids.new_id("rwk"),
            project_id=project_id,
            base_commit_id=head,
            chapter_ids=[e.artifact_id for e in catalog.chapters()],
            at=self._now(),
        )
        self._repo.aux_mutate(pid, WORK_BUCKET, _CURRENT_KEY, lambda _current: ledger.to_wire())
        return self._view(ledger)

    def current(self, project_id: str) -> dict[str, Any] | None:
        pid = ProjectId(project_id)
        payload = self._repo.aux_read(pid, WORK_BUCKET, _CURRENT_KEY)
        if payload is None:
            return None
        return self._view(CoverageLedger.from_wire(payload))

    # -- 记录读取与覆盖 -------------------------------------------------------
    def record_read(
        self,
        project_id: str,
        chapter_id: str,
        *,
        kind: str,
        start: int | None = None,
        end: int | None = None,
    ) -> dict[str, Any]:
        """记录一次真实读取：``kind=full`` 记字符区间，``kind=summary`` 记目录/摘要读取。

        服务只负责把该章在正式基础里的**来源版本与整章字数**喂给领域；全文与摘要的
        区别、以及「摘要不得记 checked_unchanged」由领域硬约束判定。全文读取未给
        区间时默认整章 ``(0, 字数)``。
        """
        pid, ledger = self._load(project_id)
        if kind not in (READ_KIND_SUMMARY, READ_KIND_FULL):
            raise self._invalid_kind(kind)
        fact = self._chapter_fact(pid, ledger, chapter_id)
        if kind == READ_KIND_SUMMARY:
            updated = ledger.with_summary_read(
                chapter_id,
                source_revision_id=fact.chapter_base,
                text_length=fact.text_length,
                at=self._now(),
            )
        else:
            lo = 0 if start is None else max(0, start)
            hi = fact.text_length if end is None else max(lo, end)
            updated = ledger.with_full_read(
                chapter_id,
                source_revision_id=fact.chapter_base,
                text_length=fact.text_length,
                start=lo,
                end=hi,
                at=self._now(),
            )
        return self._save_view(pid, updated)

    def confirm_unchanged(self, project_id: str, chapter_id: str) -> dict[str, Any]:
        """确认该章无需修改；未做全文检查会被领域拒绝（三要素错误）。"""
        pid, ledger = self._load(project_id)
        self._chapter_fact(pid, ledger, chapter_id)  # 章不存在时在领域之前先给定位
        updated = ledger.with_confirmed_unchanged(chapter_id, at=self._now())
        return self._save_view(pid, updated)

    def mark_changed(self, project_id: str, chapter_id: str) -> dict[str, Any]:
        """记录该章已在本次任务中按授权改过（改动即覆盖）。"""
        pid, ledger = self._load(project_id)
        fact = self._chapter_fact(pid, ledger, chapter_id)
        updated = ledger.with_change(
            chapter_id,
            source_revision_id=fact.chapter_base,
            text_length=fact.text_length,
            at=self._now(),
        )
        return self._save_view(pid, updated)

    def mark_blocked(self, project_id: str, chapter_id: str, *, reason: str) -> dict[str, Any]:
        """记录该章读取受阻，带未覆盖原因。"""
        pid, ledger = self._load(project_id)
        self._chapter_fact(pid, ledger, chapter_id)
        updated = ledger.with_blocked(chapter_id, reason=reason, at=self._now())
        return self._save_view(pid, updated)

    # -- 内部 ----------------------------------------------------------------
    def _head(self, pid: ProjectId) -> str:
        branch = self._repo.get_branch(pid, _MAIN_BRANCH)
        if branch is None or branch.head_commit_id is None:
            raise ViolationError(
                code=ec.REVISION_WORK_EMPTY_PROJECT,
                where=f"revision-work.open({pid.value})",
                why="作品还没有任何提交，没有可绑定的正式基础",
                fix="先导入或新建正文（产生首个 Commit），再打开整体调整工作版本",
                details={"project_id": pid.value},
            )
        return branch.head_commit_id.value

    def _load(self, project_id: str) -> tuple[ProjectId, CoverageLedger]:
        pid = ProjectId(project_id)
        payload = self._repo.aux_read(pid, WORK_BUCKET, _CURRENT_KEY)
        if payload is None:
            raise ViolationError(
                code=ec.REVISION_WORK_NOT_FOUND,
                where=f"revision-work({project_id})",
                why="当前没有进行中的整体调整工作版本",
                fix="先 POST /revision-work 打开整体调整（绑定正式基础并声明章节集合）",
                details={"project_id": project_id},
            )
        return pid, CoverageLedger.from_wire(payload)

    def _chapter_fact(
        self, pid: ProjectId, ledger: CoverageLedger, chapter_id: str
    ) -> _ChapterFact:
        """取章在当前正式基础里的来源与整章字数；不在清单/快照中即违规。"""
        ledger.require_entry(chapter_id)  # 复用领域的三要素定位错误
        catalog = ManuscriptCatalog.from_snapshot(
            self._repo.get_snapshot(pid, CommitId(ledger.base_commit_id))
        )
        entry = catalog.chapter_entry(chapter_id)
        if entry is None:
            raise ViolationError(
                code=ec.COVERAGE_UNKNOWN_CHAPTER,
                where=f"revision-work.chapter({chapter_id})",
                why=f"章 {chapter_id!r} 在正式基础版本里不存在",
                fix="只在正式正文里存在的章上记录覆盖；新章先随发布进入正文",
                details={"chapter_id": chapter_id, "base_commit_id": ledger.base_commit_id},
            )
        chapter = entry.model()
        text = "\n\n".join(u.model().text for u in catalog.units_of(chapter_id))
        return _ChapterFact(
            chapter_id=chapter_id,
            title=chapter.title,
            order_key=chapter.order_key,
            chapter_base=catalog.chapter_base(chapter_id),
            text_length=len(text),
        )

    def _invalid_kind(self, kind: str) -> ViolationError:
        return ViolationError(
            code=ec.COVERAGE_INVALID_TRANSITION,
            where="revision-work.read.kind",
            why=f"读取种类只能是 summary 或 full，收到 {kind!r}",
            fix="目录/摘要读取用 summary；整章正文用 full（可给区间）",
            details={"kind": kind},
        )

    def _save_view(self, pid: ProjectId, ledger: CoverageLedger) -> dict[str, Any]:
        self._repo.aux_mutate(pid, WORK_BUCKET, _CURRENT_KEY, lambda _current: ledger.to_wire())
        return self._view(ledger)

    def _view(self, ledger: CoverageLedger) -> dict[str, Any]:
        pid = ProjectId(ledger.project_id)
        catalog = ManuscriptCatalog.from_snapshot(
            self._repo.get_snapshot(pid, CommitId(ledger.base_commit_id))
        )
        chapters: list[dict[str, Any]] = []
        for entry in catalog.chapters():
            coverage = ledger.entry(entry.artifact_id)
            chapters.append(
                {
                    "chapter_id": entry.artifact_id,
                    "title": entry.model().title,
                    "order_key": entry.model().order_key,
                    "chapter_base": catalog.chapter_base(entry.artifact_id),
                    "covered": bool(coverage and coverage.covered),
                }
            )
        uncovered = [
            {
                "chapter_id": e.chapter_id,
                "status": e.status,
                "reason": uncovered_reason(e),
            }
            for e in ledger.uncovered()
        ]
        return {
            "work_id": ledger.work_id,
            "project_id": ledger.project_id,
            "base_commit_id": ledger.base_commit_id,
            "updated_at": ledger.updated_at,
            "coverage": [e.to_wire() for e in ledger.entries],
            "uncovered": uncovered,
            "complete": ledger.complete,
            "covered_count": len(ledger.covered),
            "total_count": len(ledger.entries),
            # 全局上下文：正式基础 + 逐章目录/来源版本 + 覆盖台账（只读投影）。
            "context": {
                "base_commit_id": ledger.base_commit_id,
                "chapters": chapters,
                "coverage": [e.to_wire() for e in ledger.entries],
            },
        }

    def _now(self) -> str:
        return str(self._clock.now().isoformat())
