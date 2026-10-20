"""账本更新建议（V03-4.4）：显式启动、带来源暂存、采用时核对来源。

为什么复用候选组而不是另建一套：建议与手工候选的确认规则必须一致——类型化字段、
引用闭合、假设拦截、一次提交。差别只在**来源**：建议每项带正文原句与生成时的章基础
版本，采用前会核对；手工候选没有来源，不受这条约束。

边界（M09）：

- 只由作者显式启动；保存正文不会调用这里，也不会自动整理。
- 零建议是合法结果：不建空卡片，也不改动已有候选组。
- 部分采用由候选组的 accept 完成：选中的一次提交，未选中的继续 pending；
  依赖未闭合时拒绝，不产生悬空关系。
- 建议不改正文：采用只写账本。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from hastings.application.errors import ViolationError
from hastings.application.ports.clock import Clock
from hastings.application.queries.ledger import KIND_LABELS, entry_view
from hastings.application.services.commit_service import CommitService
from hastings.application.services.ledger_candidate_service import LedgerCandidateService
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.repository.ids import ProjectId
from hastings.domain.story import suggestions as domain
from hastings.domain.story.catalog import StoryCatalog
from hastings.domain.story.errors import StoryViolation

__all__ = ["SuggestionService", "SuggestionWriterFn"]

#: 提议器返回 (原始输出, 实际模型)：模型标识要写进来源，便于回看是谁给的这条建议。
SuggestionWriterFn = Callable[..., Awaitable[tuple[str, str]]]


def _violation(code: str, where: str, why: str, fix: str, **details: Any) -> ViolationError:
    return ViolationError(code=code, where=where, why=why, fix=fix, details=details)


def _as_violation(exc: StoryViolation) -> ViolationError:
    return ViolationError(
        code=exc.code, where=exc.where, why=exc.why, fix=exc.fix, details=exc.details
    )


class SuggestionService:
    def __init__(
        self,
        repo: CommitService,
        candidates: LedgerCandidateService,
        clock: Clock,
        proposer: SuggestionWriterFn | None = None,
    ) -> None:
        self._repo = repo
        self._candidates = candidates
        self._clock = clock
        self._proposer = proposer

    # -- 读 -----------------------------------------------------------------
    def view(self, project_id: str) -> dict[str, Any]:
        """候选组 + 生成来源：右侧「待处理」与详情页读同一份数据。"""
        return self._candidates.view(project_id)

    # -- 写（显式启动） ------------------------------------------------------
    async def start(
        self,
        project_id: str,
        chapter_id: str,
        *,
        request_id: str,
        guidance: str = "",
    ) -> dict[str, Any]:
        pid = ProjectId(project_id)
        branch = self._repo.get_branch(pid, "main")
        head = branch.head_commit_id if branch is not None else None
        if head is None:
            raise _violation(
                "SUGGESTION_CHAPTER_NOT_FOUND",
                f"suggestions.start({pid.value})",
                "作品还没有正文，无法整理账本建议",
                "先导入或试写一章，再整理建议",
                project_id=pid.value,
                chapter_id=chapter_id,
            )
        snapshot = self._repo.get_snapshot(pid, head)
        manuscripts = ManuscriptCatalog.from_snapshot(snapshot)
        chapter = manuscripts.chapter_entry(chapter_id)
        if chapter is None:
            raise _violation(
                "SUGGESTION_CHAPTER_NOT_FOUND",
                f"suggestions.start({pid.value})",
                f"作品 {pid.value} 没有章节 {chapter_id!r}",
                "刷新章节目录后重新选择；建议只针对现有章节",
                project_id=pid.value,
                chapter_id=chapter_id,
            )
        if self._proposer is None:
            raise _violation(
                "SUGGESTION_NOT_AVAILABLE",
                f"suggestions.start({pid.value})",
                "当前部署没有接通建议整理服务",
                "在设置→模型接入里连接模型后重试；没有模型时账本仍可手工维护",
            )

        chapter_base = manuscripts.chapter_base(chapter_id)
        text = "\n".join(unit.model().text for unit in manuscripts.units_of(chapter_id))
        story = StoryCatalog.from_snapshot(snapshot)
        ledger_lines = [
            f"{view['kind_label']} {view['title']}：{view['summary']}"
            for view in (entry_view(entry) for entry in story.entries)
            if view["kind"] in KIND_LABELS
        ]

        raw, model = await self._proposer(
            system=domain.SUGGESTION_SYSTEM_PROMPT,
            prompt=domain.render_suggestion_prompt(
                chapter_title=str(chapter.model().title),
                text=text,
                ledger_lines=ledger_lines,
                guidance=guidance,
            ),
        )
        try:
            proposals = domain.parse_proposals(raw)
        except StoryViolation as exc:
            raise _as_violation(exc) from exc

        if not proposals:
            # 零建议是合法空结果：不建空卡片，也不动已有候选组。
            return {
                "status": "empty",
                "staged": 0,
                "source": {"chapter_id": chapter_id, "chapter_base": chapter_base},
                "group": self.view(project_id),
            }

        origin = {
            "chapter_id": chapter_id,
            "chapter_base": chapter_base,
            "model": model,
            "task_id": f"suggestions:{pid.value}:{request_id}",
            "created_at": self._clock.now().isoformat(),
        }
        items = [
            {
                "kind": proposal.kind,
                "fields": proposal.fields,
                "hypothesis": proposal.hypothesis,
                "source": {
                    "chapter_id": chapter_id,
                    "chapter_base": chapter_base,
                    "quote": proposal.quote,
                },
            }
            for proposal in proposals
        ]
        group = self._candidates.stage_suggestions(
            project_id, origin=origin, items=items, request_id=request_id
        )
        return {
            "status": "staged",
            "staged": len(items),
            "source": {"chapter_id": chapter_id, "chapter_base": chapter_base},
            "group": group,
        }
