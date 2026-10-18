"""章版本历史查询（V03-2.5 / M11 的只读前置）。

回答「这一章有过哪些正式版本、当前是哪一个」：沿 main 分支的提交链从新到旧
扫描，按章基础（``chapter_base``，活动段落的顺序与文本哈希）判重——同一基础
的连续提交（例如改了别的章）不算新版本。

性能说明：每个候选提交都读一次快照，按 ``limit`` 截断。长篇历史（数百提交）
下这是 O(提交数) 次快照读取，属于当前可接受的简单实现；压测与增量索引留到
V03-8，不在这里提前优化。
"""

from __future__ import annotations

from typing import Any

from hastings.application.services.commit_service import CommitService
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.repository.ids import ProjectId

__all__ = ["ChapterHistoryQueries"]


class ChapterHistoryQueries:
    """只读：章版本列表（不含写路径）。"""

    def __init__(self, repo: CommitService) -> None:
        self._repo = repo

    def versions(
        self, project_id: str, chapter_id: str, *, limit: int = 20
    ) -> dict[str, Any]:
        pid = ProjectId(project_id)
        branch = self._repo.get_branch(pid, "main")
        head = branch.head_commit_id if branch else None
        if head is None:
            return {
                "chapter_id": chapter_id,
                "head_commit_id": None,
                "versions": [],
            }
        chain = self._repo.commit_history(pid, head)
        versions: list[dict[str, Any]] = []
        previous_base: str | None = None
        for commit in reversed(chain):
            catalog = ManuscriptCatalog.from_snapshot(self._repo.get_snapshot(pid, commit.id))
            entry = catalog.chapter_entry(chapter_id)
            if entry is None:
                continue
            base = catalog.chapter_base(chapter_id)
            if base == previous_base:
                continue
            text = "\n\n".join(unit.model().text for unit in catalog.units_of(chapter_id))
            versions.append(
                {
                    "commit_id": commit.id.value,
                    "created_at": commit.created_at.isoformat(),
                    "summary": commit.message,
                    "actor": commit.actor.id,
                    "chapter_base": base,
                    "title": entry.model().title,
                    "preview": text[:200],
                    "char_count": len(text),
                    "is_current": len(versions) == 0,
                }
            )
            previous_base = base
            if len(versions) >= max(1, limit):
                break
        return {
            "chapter_id": chapter_id,
            "head_commit_id": head.value,
            "versions": versions,
        }
