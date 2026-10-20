"""背景材料收集（V03-4.2）：把某个 Commit 读成装配器要的输入。

职责边界刻意收窄：**只读、只收集、不判断**。选择与裁剪全在
``contexts.background.assemble_background`` 里，那部分是纯函数，能脱离仓库测试；
这里只负责把仓库里已有的东西按固定版本取出来，避免「一边读库一边裁剪」
导致同一次装配前后看到不同版本。

摘要内容由 V03-4.3 产出；本模块只读现有的 ``summary.chapter`` 状态。缺失或过期时，
调用方可以选择是否按当前版本读原文（``include_temporary_text``）——读了也只是
本次临时背景，不会写回正式摘要。
"""

from __future__ import annotations

from typing import Any

from hastings.application.contexts.background import (
    SUMMARY_MISSING,
    AgreementInput,
    BackgroundPackage,
    BackgroundRequest,
    BoundaryInput,
    ChapterInput,
    LedgerInput,
    assemble_background,
)
from hastings.application.queries.ledger import KIND_LABELS, entry_view
from hastings.application.services.commit_service import CommitService
from hastings.application.services.disclosure_service import as_violation, unverified_facts
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.manuscript.evidence import KIND_CHAPTER_SUMMARY
from hastings.domain.repository.ids import CommitId, ProjectId
from hastings.domain.story import agreements as story_agreements
from hastings.domain.story import disclosure as story_disclosure
from hastings.domain.story.catalog import StoryCatalog, StoryEntry
from hastings.domain.story.errors import StoryViolation

__all__ = ["DEFAULT_BUDGET_TOKENS", "MAX_BUDGET_TOKENS", "BackgroundService"]

#: 默认背景预算：与 ContextBuilder 同一量级（计划 §9.6 的初始值，未做样本测量）。
DEFAULT_BUDGET_TOKENS = 12_000

#: 单次装配上限：再大就该拆任务，而不是把整本书塞进背景。
MAX_BUDGET_TOKENS = 64_000

#: 与账本清单同一口径：只有登记过的种类才进背景，节拍/作者模型不在其中。
_LEDGER_KINDS = frozenset(KIND_LABELS)


def _ledger_input(entry: StoryEntry) -> LedgerInput:
    view = entry_view(entry)
    return LedgerInput(
        artifact_id=entry.artifact_id,
        revision_id=entry.revision_id,
        kind=entry.kind,
        kind_label=view["kind_label"],
        title=view["title"],
        summary=view["summary"],
    )


def _summary_states(snapshot: Any) -> dict[str, dict[str, Any]]:
    """按 chapter_id 索引现有摘要状态；只认 ``summary.chapter``。"""
    states: dict[str, dict[str, Any]] = {}
    for view in snapshot.artifacts.values():
        if view.artifact.kind != KIND_CHAPTER_SUMMARY or view.revision is None:
            continue
        content = dict(view.revision.content)
        chapter_id = content.get("chapter_id")
        if isinstance(chapter_id, str) and chapter_id:
            states[chapter_id] = content
    return states


class BackgroundService:
    def __init__(self, repo: CommitService) -> None:
        self._repo = repo

    def build(
        self,
        project_id: str,
        *,
        scope: str,
        target_chapter_ids: list[str] | None = None,
        budget_tokens: int = DEFAULT_BUDGET_TOKENS,
        include_temporary_text: bool = False,
        commit_id: str | None = None,
        story_chapter_id: str | None = None,
    ) -> dict[str, Any]:
        """装配并返回 wire 形状的背景包；空作品是合法输入。"""
        return self.build_package(
            project_id,
            scope=scope,
            target_chapter_ids=target_chapter_ids,
            budget_tokens=budget_tokens,
            include_temporary_text=include_temporary_text,
            commit_id=commit_id,
            story_chapter_id=story_chapter_id,
        ).to_wire()

    def render(
        self,
        project_id: str,
        *,
        scope: str,
        target_chapter_ids: list[str] | None = None,
        budget_tokens: int = DEFAULT_BUDGET_TOKENS,
        include_temporary_text: bool = False,
        commit_id: str | None = None,
        story_chapter_id: str | None = None,
    ) -> str:
        """渲染成提示词用文本：同一部作品、同一版本，渲染结果完全一致。"""
        return self.build_package(
            project_id,
            scope=scope,
            target_chapter_ids=target_chapter_ids,
            budget_tokens=budget_tokens,
            include_temporary_text=include_temporary_text,
            commit_id=commit_id,
            story_chapter_id=story_chapter_id,
        ).render()

    def build_package(
        self,
        project_id: str,
        *,
        scope: str,
        target_chapter_ids: list[str] | None = None,
        budget_tokens: int = DEFAULT_BUDGET_TOKENS,
        include_temporary_text: bool = False,
        commit_id: str | None = None,
        story_chapter_id: str | None = None,
    ) -> BackgroundPackage:
        """按指定版本（缺省为当前 head）装配；任务侧传自己的 ``base_head``。

        传 ``commit_id`` 是 M07 的硬要求：任务开始后作者改约定或正文，不能偷换
        已经启动任务的输入。调用方负责给出那个固定的版本。
        """
        pid = ProjectId(project_id)
        targets = tuple(target_chapter_ids or ())
        if commit_id is not None:
            head: CommitId | None = CommitId(commit_id)
        else:
            branch = self._repo.get_branch(pid, "main")
            head = branch.head_commit_id if branch is not None else None
        if head is None:
            return assemble_background(
                BackgroundRequest(
                    project_id=project_id,
                    commit_id=None,
                    scope=scope,
                    budget_tokens=budget_tokens,
                    target_chapter_ids=targets,
                )
            )

        snapshot = self._repo.get_snapshot(pid, head)
        story = StoryCatalog.from_snapshot(snapshot)
        manuscripts = ManuscriptCatalog.from_snapshot(snapshot)
        summaries = _summary_states(snapshot)
        boundary = self._boundary(story, snapshot, story_chapter_id)

        agreements = tuple(
            AgreementInput(
                agreement_id=ref.agreement_id,
                revision_id=ref.revision_id,
                topic=ref.topic,
                scope=ref.scope,
                statement=ref.statement,
            )
            for ref in story_agreements.snapshot(story).items
        )
        ledger = tuple(
            _ledger_input(entry) for entry in story.entries if entry.kind in _LEDGER_KINDS
        )
        chapters = tuple(
            self._chapter_input(
                manuscripts,
                entry,
                summaries.get(entry.artifact_id),
                include_temporary_text=include_temporary_text,
                targets=targets,
            )
            for entry in manuscripts.chapters()
        )
        return assemble_background(
            BackgroundRequest(
                project_id=project_id,
                commit_id=head.value,
                scope=scope,
                budget_tokens=budget_tokens,
                agreements=agreements,
                ledger=ledger,
                chapters=chapters,
                target_chapter_ids=targets,
                boundary=boundary,
            )
        )

    @staticmethod
    def _boundary(
        story: StoryCatalog, snapshot: Any, story_chapter_id: str | None
    ) -> BoundaryInput | None:
        """只在调用方给了叙事位置时算边界。

        没有位置就不猜：编一个边界比不给更糟——它会让「不得断言」的清单看起
        像权威结论，实际只是按章号臆测的结果。
        """
        if not story_chapter_id:
            return None
        try:
            computed = story_disclosure.boundary_at(
                story,
                chapter_id=story_chapter_id,
                unverified=unverified_facts(snapshot),
            )
        except StoryViolation as exc:
            # 位置写错要报错，不能悄悄少一块约束：模型会以为「本章没有限制」。
            raise as_violation(exc) from exc
        return BoundaryInput(
            position_label=computed.position.label or "本节点",
            text=computed.render(),
            revision_id=computed.content_hash,
            content_hash=computed.content_hash,
        )

    def _chapter_input(
        self,
        manuscripts: ManuscriptCatalog,
        entry: Any,
        summary: dict[str, Any] | None,
        *,
        include_temporary_text: bool,
        targets: tuple[str, ...],
    ) -> ChapterInput:
        chapter = entry.model()
        status = SUMMARY_MISSING
        summary_id = ""
        summary_text = ""
        source_hash = ""
        if summary is not None:
            summary_id = str(summary.get("summary_id", ""))
            status = str(summary.get("status") or SUMMARY_MISSING)
            summary_text = str(summary.get("text", ""))
            source_hash = str(summary.get("source_hash", ""))
        temporary = ""
        if include_temporary_text and entry.artifact_id in targets:
            temporary = "\n".join(
                unit.model().text for unit in manuscripts.units_of(entry.artifact_id)
            )
        return ChapterInput(
            chapter_id=entry.artifact_id,
            revision_id=entry.revision_id,
            title=str(chapter.title),
            order_key=str(chapter.order_key),
            summary_id=summary_id,
            summary_status=status,
            summary_text=summary_text,
            summary_source_hash=source_hash,
            temporary_text=temporary,
        )
