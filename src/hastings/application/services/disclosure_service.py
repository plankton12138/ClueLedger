"""披露边界查询（V03-4.7）：把领域计算接到某个版本的仓库上。

只读服务，两种用法：

- ``boundary()``：给前端和提示词用，回答「在这个位置，哪些事实读者已知 / 角色已知 /
  还不能说」；
- ``check()``：给采用与润色用，回答「新稿有没有把后文事实提前写出来」。

证据待核对的事实由 ``evidence.link`` 现状给出：只要某条事实的来源证据是
``needs_check``，本服务就把它降级为「不可断言」——旧记录不能证明已经获知（I09）。
"""

from __future__ import annotations

from typing import Any

from hastings.application.errors import ViolationError
from hastings.application.services.commit_service import CommitService
from hastings.domain.manuscript.evidence import (
    EVIDENCE_STATUS_NEEDS_CHECK,
    KIND_EVIDENCE_LINK,
)
from hastings.domain.repository.ids import CommitId, ProjectId
from hastings.domain.story import disclosure
from hastings.domain.story.catalog import StoryCatalog
from hastings.domain.story.errors import StoryViolation

__all__ = ["DisclosureService", "as_violation", "unverified_facts"]


def as_violation(exc: StoryViolation) -> ViolationError:
    """领域违规 → 应用信封；位置写错要有可行动的错误码，不能落到 500。"""
    return ViolationError(
        code=exc.code, where=exc.where, why=exc.why, fix=exc.fix, details=exc.details
    )


class DisclosureService:
    def __init__(self, repo: CommitService) -> None:
        self._repo = repo

    # -- 读取 ----------------------------------------------------------------

    def boundary(
        self,
        project_id: str,
        *,
        chapter_id: str | None = None,
        beat_id: str | None = None,
        commit_id: str | None = None,
    ) -> dict[str, Any]:
        """算边界并附带渲染文本：前端展示与提示词注入用同一份结果。"""
        catalog, unverified = self._load(project_id, commit_id)
        try:
            boundary = disclosure.boundary_at(
                catalog,
                chapter_id=chapter_id,
                beat_id=beat_id,
                unverified=unverified,
            )
        except StoryViolation as exc:
            raise as_violation(exc) from exc
        payload = boundary.to_wire()
        payload["text"] = boundary.render()
        return payload

    def check(
        self,
        project_id: str,
        *,
        original: str,
        candidate: str,
        chapter_id: str | None = None,
        beat_id: str | None = None,
        commit_id: str | None = None,
        authorized: bool = False,
    ) -> dict[str, Any]:
        """检测新稿是否扩大披露；``authorized`` 由作者显式授权改变揭示安排。"""
        catalog, unverified = self._load(project_id, commit_id)
        try:
            boundary = disclosure.boundary_at(
                catalog,
                chapter_id=chapter_id,
                beat_id=beat_id,
                unverified=unverified,
            )
        except StoryViolation as exc:
            raise as_violation(exc) from exc
        report = disclosure.check_expansion(
            boundary,
            original=original,
            candidate=candidate,
            authorized=authorized,
        )
        payload = report.to_wire()
        payload["boundary_hash"] = boundary.content_hash
        return payload

    # -- 内部 ----------------------------------------------------------------

    def _load(self, project_id: str, commit_id: str | None) -> tuple[StoryCatalog, dict[str, str]]:
        pid = ProjectId(project_id)
        if commit_id is not None:
            head: CommitId | None = CommitId(commit_id)
        else:
            branch = self._repo.get_branch(pid, "main")
            head = branch.head_commit_id if branch is not None else None
        if head is None:
            return StoryCatalog(commit_id=None, entries=()), {}
        snapshot = self._repo.get_snapshot(pid, head)
        return StoryCatalog.from_snapshot(snapshot), unverified_facts(snapshot)


def unverified_facts(snapshot: Any) -> dict[str, str]:
    """证据待核对的事实 → 人类可读原因；同一事实多条时合并来源章。"""
    chapters: dict[str, set[str]] = {}
    for view in snapshot.artifacts.values():
        if view.artifact.kind != KIND_EVIDENCE_LINK or view.revision is None:
            continue
        content = dict(view.revision.content)
        if str(content.get("status", "")) != EVIDENCE_STATUS_NEEDS_CHECK:
            continue
        target = content.get("target_id")
        if not isinstance(target, str) or not target:
            continue
        chapter = content.get("chapter_id")
        chapters.setdefault(target, set()).add(str(chapter) if chapter else "未记录来源章")
    return {
        target: "来源证据待核对：" + "、".join(sorted(where)) for target, where in chapters.items()
    }
