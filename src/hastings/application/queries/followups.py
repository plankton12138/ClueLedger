"""操作之后的派生状态（V03-2.6 / 计划 §6.1、M08/M09）。

回答「这次操作之后，哪些后续工作是待处理的」，只做**确定性判断**，不启动
任何任务、不调用模型：

- ``summary_state``：本次受影响章节的摘要状态——没有任何摘要为 ``missing``，
  任一过期为 ``stale``，全部最新为 ``fresh``；无受影响章节时看全书摘要是否
  已存在（有则 ``fresh``，无则 ``missing``）。
- ``ledger_suggestions_state``：账本更新建议的整理状态。建议实际存放在
  **候选组的辅助表**里（``ledger_staged`` 桶，V03-4.4），因此这里直接读那份
  数据，不查版本对象：没有候选组为 ``not_started``，有 pending 条目为
  ``available``，组存在但条目都已决定为 ``handled``。这里是该状态的唯一事实
  来源，不靠前端猜测。

启动入口属于摘要与建议任务（V03-4.3/4.4）：本查询只报告状态，点击不产生
Job 与模型调用（I06）。
"""

from __future__ import annotations

from typing import Any

from hastings.application.services.commit_service import CommitService

# 候选组的数据契约（桶名/键/条目状态）只有一处定义，从这里导入避免两处漂移。
from hastings.application.services.ledger_candidate_service import (
    CANDIDATE_BUCKET,
    GROUP_KEY,
    PENDING,
)
from hastings.domain.manuscript.evidence import (
    KIND_CHAPTER_SUMMARY,
    SUMMARY_STATUS_FRESH,
    SUMMARY_STATUS_STALE,
)
from hastings.domain.repository.ids import ProjectId

__all__ = ["FollowupQueries"]

SUMMARY_MISSING = "missing"
SUGGESTIONS_NOT_STARTED = "not_started"
SUGGESTIONS_AVAILABLE = "available"
SUGGESTIONS_HANDLED = "handled"


class FollowupQueries:
    """操作后的待处理状态（只读）。"""

    def __init__(self, repo: CommitService) -> None:
        self._repo = repo

    def states(self, project_id: str, changed_chapter_ids: tuple[str, ...]) -> dict[str, Any]:
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
        for view in snapshot.artifacts.values():
            if view.revision is None:
                continue
            if view.artifact.kind == KIND_CHAPTER_SUMMARY:
                chapter_id = str(view.revision.content.get("chapter_id", ""))
                if chapter_id:
                    summaries[chapter_id] = str(view.revision.content.get("status", ""))

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
            "ledger_suggestions_state": self._suggestions_state(pid),
        }

    def _suggestions_state(self, pid: ProjectId) -> str:
        """建议状态读候选组本身（V03-4.4 存辅助表，不产生版本对象）。

        曾经这里按 ``ledger.suggestion_group`` Artifact 判断，而建议从来只写
        辅助表——于是「已经整理出四条建议」仍回报 ``not_started``，作者会重复
        启动整理。按 pending 条目数区分 available / handled：全部决定之后不再
        提示「可查看」，但也不谎称「尚未整理」。
        """
        group = self._repo.aux_read(pid, CANDIDATE_BUCKET, GROUP_KEY)
        if group is None:
            return SUGGESTIONS_NOT_STARTED
        items = group.get("items") or []
        pending = sum(1 for item in items if item.get("status") == PENDING)
        return SUGGESTIONS_AVAILABLE if pending else SUGGESTIONS_HANDLED
