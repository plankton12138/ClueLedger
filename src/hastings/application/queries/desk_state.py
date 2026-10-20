"""工作台共享状态（V03-4.5）：侧栏与主区读同一份计数与引用。

产品设计 §5.1/§5.2 要求「建议在本次结果对应的对话中成组呈现，右侧只提供数量和
定位入口」，§6 要求「不重复建议列表事实源」。所以这个查询只返回**计数与 ID**：

- 计数告诉侧栏「有几条待确认」，不给条目正文；
- ID 指向唯一权威存储（候选组、副本、约定快照），侧栏拿着 ID 去读详情，
  不在前端再存一份可以独立漂移的列表。

状态全部来自当前 Commit 的投影 + 辅助表，只读、无模型、不创建任务。
``content_hash`` 让前端能判断「要不要重取」，而不是靠轮询比对每个字段。
"""

from __future__ import annotations

from typing import Any

from hastings.application.queries.ledger import KIND_LABELS
from hastings.application.services.commit_service import CommitService
from hastings.application.services.edit_session_service import EditSessionService
from hastings.application.services.ledger_candidate_service import LedgerCandidateService
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.manuscript.evidence import (
    KIND_CHAPTER_SUMMARY,
    SUMMARY_STATUS_FRESH,
    SUMMARY_STATUS_STALE,
)
from hastings.domain.manuscript.operation_record import OPERATION_RECORD_KIND
from hastings.domain.repository.ids import ProjectId
from hastings.domain.repository.serialization import content_hash
from hastings.domain.story import agreements as story_agreements
from hastings.domain.story.catalog import StoryCatalog

__all__ = ["DeskStateQueries"]

SUMMARY_MISSING = "missing"

#: 与账本清单同一口径：只有这些种类算「已确认设定」。
_LEDGER_KINDS = frozenset(KIND_LABELS)

#: 侧栏最多回看多少条最近结果：够回答「刚才那次改了什么」，不变成历史列表。
RECENT_OPERATIONS_LIMIT = 8


def _recent_operations(snapshot: Any) -> list[dict[str, Any]]:
    """按修订时间倒序取最近的操作记录：重启后仍能看到「上次改了什么」。

    记录本身就是权威存储，这里只做投影；不读对话、不判断投影是否完成——
    投影状态由 ``GET /operations/{id}`` 单独回答。
    """
    rows: list[tuple[str, dict[str, Any]]] = []
    for view in snapshot.artifacts.values():
        if view.artifact.kind != OPERATION_RECORD_KIND or view.revision is None:
            continue
        content = dict(view.revision.content)
        operation_id = content.get("operation_id")
        if not isinstance(operation_id, str) or not operation_id:
            continue
        rows.append(
            (
                view.revision.created_at.isoformat(),
                {
                    "operation_id": operation_id,
                    "kind": str(content.get("kind", "")),
                    "status": str(content.get("status", "")),
                    "conversation_id": content.get("conversation_id"),
                    "changed_chapter_ids": list(content.get("changed_chapter_ids") or []),
                    "created_at": view.revision.created_at.isoformat(),
                },
            )
        )
    rows.sort(key=lambda row: (row[0], row[1]["operation_id"]), reverse=True)
    return [row[1] for row in rows[:RECENT_OPERATIONS_LIMIT]]


def _summary_buckets(snapshot: Any, manuscripts: ManuscriptCatalog) -> dict[str, int]:
    """按章统计摘要状态；没有摘要的章计入 missing。"""
    buckets = {SUMMARY_STATUS_FRESH: 0, SUMMARY_STATUS_STALE: 0, SUMMARY_MISSING: 0}
    states: dict[str, str] = {}
    for view in snapshot.artifacts.values():
        if view.artifact.kind != KIND_CHAPTER_SUMMARY or view.revision is None:
            continue
        content = dict(view.revision.content)
        chapter_id = content.get("chapter_id")
        if isinstance(chapter_id, str) and chapter_id:
            states[chapter_id] = str(content.get("status") or SUMMARY_MISSING)
    for chapter in manuscripts.chapters():
        status = states.get(chapter.artifact_id, SUMMARY_MISSING)
        buckets[status if status in buckets else SUMMARY_MISSING] += 1
    return buckets


class DeskStateQueries:
    """工作台共享状态：计数 + 权威 ID，不给可漂移的副本。"""

    def __init__(
        self,
        repo: CommitService,
        candidates: LedgerCandidateService,
        edit_sessions: EditSessionService | None = None,
    ) -> None:
        self._repo = repo
        self._candidates = candidates
        self._edits = edit_sessions

    def states(self, project_id: str) -> dict[str, Any]:
        pid = ProjectId(project_id)
        branch = self._repo.get_branch(pid, "main")
        head = branch.head_commit_id if branch is not None else None

        ledger_entries = 0
        agreements_count = 0
        agreements_hash = ""
        summaries = {SUMMARY_STATUS_FRESH: 0, SUMMARY_STATUS_STALE: 0, SUMMARY_MISSING: 0}
        recent: list[dict[str, Any]] = []
        if head is not None:
            snapshot = self._repo.get_snapshot(pid, head)
            story = StoryCatalog.from_snapshot(snapshot)
            manuscripts = ManuscriptCatalog.from_snapshot(snapshot)
            ledger_entries = sum(1 for entry in story.entries if entry.kind in _LEDGER_KINDS)
            agreement_snapshot = story_agreements.snapshot(story)
            agreements_count = len(agreement_snapshot.items)
            agreements_hash = agreement_snapshot.content_hash
            summaries = _summary_buckets(snapshot, manuscripts)
            recent = _recent_operations(snapshot)

        candidates = self._candidates.view(project_id)
        edit = self._edits.current(project_id) if self._edits is not None else None
        session = (edit or {}).get("session") or {}
        open_chapters = sorted(str(draft["chapter_id"]) for draft in (edit or {}).get("drafts", []))

        payload: dict[str, Any] = {
            "project_id": project_id,
            "commit_id": head.value if head is not None else None,
            "counts": {
                "ledger_entries": ledger_entries,
                "agreements": agreements_count,
                "summaries": summaries,
                # 待确认只给数量：条目正文由候选组接口给，避免两处列表。
                "pending_candidates": int(candidates.get("pending_count", 0)),
                "hypothesis_candidates": int(candidates.get("hypothesis_count", 0)),
                "open_edit_chapters": len(open_chapters),
            },
            "recent_operations": recent,
            "refs": {
                "candidate_group_id": candidates.get("group_id"),
                "candidate_revision": candidates.get("revision"),
                "agreements_hash": agreements_hash,
                "edit_session_id": session.get("session_id") if session else None,
                "edit_chapter_ids": open_chapters,
            },
        }
        payload["content_hash"] = content_hash(payload)
        return payload
