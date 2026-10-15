"""Story 查询入口（Phase 13 T13.7）：把 RepositoryReader 与目标 Commit 绑成视图。

领域层的投影是纯函数；本模块只做两件事：按 `(project_id, commit_id)` 取快照、
构造 `StoryCatalog`，再把目录交给投影。**不做缓存**——调用方每次都显式给出
目标 Commit，head 前移后旧 Commit 的视图仍是旧 Commit 的视图，不会冒充最新
（计划 T13.7「缓存必须带 Commit ID，head 改变后不能返回旧结果」）。

查询视图是派生结果，本模块只读、无写入路径；写命令走 handlers 与 Commit Service。
"""

from __future__ import annotations

from hastings.application.ports.repository import RepositoryReader
from hastings.domain.repository.ids import CommitId, ProjectId
from hastings.domain.story.catalog import StoryCatalog
from hastings.domain.story.narrative.state import (
    StoryState,
    story_state_at_beat,
    story_state_final,
)
from hastings.domain.story.views import (
    CanonSnapshot,
    DisclosureTimeline,
    MysteryFairnessView,
    NarrativeTree,
    OpenSetupsView,
    canon_snapshot,
    disclosure_timeline,
    mystery_fairness,
    narrative_tree,
    open_setups,
)

__all__ = ["StoryQueries"]


class StoryQueries:
    """绑定只读端口的 Story 查询服务；所有视图都带 Commit ID。"""

    def __init__(self, reader: RepositoryReader) -> None:
        self._reader = reader

    def catalog(self, project_id: ProjectId, commit_id: CommitId) -> StoryCatalog:
        """取指定 Commit 的故事目录（投影与查询的共同入口）。"""
        return StoryCatalog.from_snapshot(self._reader.get_snapshot(project_id, commit_id))

    def branch_head(self, project_id: ProjectId, branch_name: str = "main") -> CommitId | None:
        """main（或指定分支）的 head；项目/分支不存在或为空返回 None。"""
        branch = self._reader.get_branch(project_id, branch_name)
        return branch.head_commit_id if branch is not None else None

    def canon_snapshot(self, project_id: ProjectId, commit_id: CommitId) -> CanonSnapshot:
        return canon_snapshot(self.catalog(project_id, commit_id))

    def narrative_tree(self, project_id: ProjectId, commit_id: CommitId) -> NarrativeTree:
        return narrative_tree(self.catalog(project_id, commit_id))

    def disclosure_timeline(self, project_id: ProjectId, commit_id: CommitId) -> DisclosureTimeline:
        return disclosure_timeline(self.catalog(project_id, commit_id))

    def mystery_fairness(
        self,
        project_id: ProjectId,
        commit_id: CommitId,
        *,
        fairness_cutoff: float | None = None,
    ) -> MysteryFairnessView:
        return mystery_fairness(
            self.catalog(project_id, commit_id), fairness_cutoff=fairness_cutoff
        )

    def open_setups(
        self,
        project_id: ProjectId,
        commit_id: CommitId,
        *,
        deadline: float | None = None,
    ) -> OpenSetupsView:
        return open_setups(self.catalog(project_id, commit_id), deadline=deadline)

    def story_state(
        self,
        project_id: ProjectId,
        commit_id: CommitId,
        *,
        beat_id: str | None = None,
        after: bool = True,
    ) -> StoryState:
        """某 Beat 前后（默认之后）的状态；beat_id=None 表示最终状态。"""
        catalog = self.catalog(project_id, commit_id)
        if beat_id is None:
            return story_state_final(catalog)
        return story_state_at_beat(catalog, beat_id, after=after)
