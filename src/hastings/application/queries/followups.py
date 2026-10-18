"""操作之后的派生状态（V03-2.6 / 计划 §6.1、M08/M09）。

回答「这次操作之后，哪些后续工作是待处理的」，只做**确定性判断**，不启动
任何任务、不调用模型：

- ``summary_state``：本次受影响章节的摘要状态——没有任何摘要为 ``missing``，
  任一过期为 ``stale``，全部最新为 ``fresh``；无受影响章节时看全书摘要是否
  已存在（有则 ``fresh``，无则 ``missing``）。
- ``ledger_suggestions_state``：账本更新建议是否已整理。V03-4 之前不存在建议组，
  恒为 ``not_started``；这里是「尚未整理」的唯一事实来源，不靠前端猜测。

启动入口属于摘要与建议任务（V03-4.3/4.4）：本查询只报告状态，点击不产生
Job 与模型调用（I06）。
"""

from __future__ import annotations

from typing import Any

from hastings.application.services.commit_service import CommitService
from hastings.domain.manuscript.evidence import (
    KIND_CHAPTER_SUMMARY,
    SUMMARY_STATUS_FRESH,
    SUMMARY_STATUS_STALE,
)
from hastings.domain.repository.ids import ProjectId

__all__ = ["FollowupQueries"]

#: 账本建议组的 Artifact kind（V03-4 落地后才会出现）。
KIND_SUGGESTION_GROUP = "ledger.suggestion_group"

SUMMARY_MISSING = "missing"
SUGGESTIONS_NOT_STARTED = "not_started"


class FollowupQueries:
    """操作后的待处理状态（只读）。"""

    def __init__(self, repo: CommitService) -> None:
        self._repo = repo

    def states(
        self, project_id: str, changed_chapter_ids: tuple[str, ...]
    ) -> dict[str, Any]:
        pid = ProjectId(project_id)
        branch = self._repo.get_branch(pid, "main")
        head = branch.head_commit_id if branch else None
        if head is None:
            return {
                "summary_state": SUMMARY_MISSING,
                "ledger_suggestions_state": SUGGESTIONS_NOT_STARTED,
            }
        snapshot = self._repo.get_snapshot(pid, head)
        summaries: dict[str, str] = {}
        has_suggestions = False
        for view in snapshot.artifacts.values():
            if view.revision is None:
                continue
            if view.artifact.kind == KIND_CHAPTER_SUMMARY:
                chapter_id = str(view.revision.content.get("chapter_id", ""))
                if chapter_id:
                    summaries[chapter_id] = str(view.revision.content.get("status", ""))
            elif view.artifact.kind == KIND_SUGGESTION_GROUP:
                has_suggestions = True

        if changed_chapter_ids:
            statuses = [summaries.get(chapter_id) for chapter_id in changed_chapter_ids]
            if any(status is None for status in statuses):
                summary_state = SUMMARY_MISSING
            elif any(status == SUMMARY_STATUS_STALE for status in statuses):
                summary_state = SUMMARY_STATUS_STALE
            else:
                summary_state = SUMMARY_STATUS_FRESH
        else:
            summary_state = SUMMARY_STATUS_FRESH if summaries else SUMMARY_MISSING
        return {
            "summary_state": summary_state,
            "ledger_suggestions_state": (
                "available" if has_suggestions else SUGGESTIONS_NOT_STARTED
            ),
        }
