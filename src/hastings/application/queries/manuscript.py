"""Manuscript 查询入口（Phase 14 T14.2）：把 RepositoryReader 与目标 Commit 绑成正文视图。

与 ``StoryQueries`` 同构：按 ``(project_id, commit_id)`` 取快照、构造
``ManuscriptCatalog``，再把目录交给只读投影。**不做缓存**——调用方每次显式给出目标
Commit，head 前移后旧 Commit 的视图仍是旧 Commit 的视图，不会冒充最新。

查询视图是派生结果，本模块只读、无写入路径；写命令走 handlers 与 Commit Service。
"""

from __future__ import annotations

from hastings.application.ports.repository import RepositoryReader
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.manuscript.models import ChapterView, Manuscript
from hastings.domain.repository.ids import CommitId, ProjectId

__all__ = ["ManuscriptQueries"]


class ManuscriptQueries:
    """绑定只读端口的正文查询服务；所有视图都带 Commit ID。"""

    def __init__(self, reader: RepositoryReader) -> None:
        self._reader = reader

    def catalog(self, project_id: ProjectId, commit_id: CommitId) -> ManuscriptCatalog:
        """取指定 Commit 的正文目录（投影与查询的共同入口）。"""
        return ManuscriptCatalog.from_snapshot(self._reader.get_snapshot(project_id, commit_id))

    def manuscript(self, project_id: ProjectId, commit_id: CommitId) -> Manuscript:
        """整篇只读投影：章按 order_key 排序，段落按章的显式顺序。"""
        return self.catalog(project_id, commit_id).manuscript()

    def chapters(self, project_id: ProjectId, commit_id: CommitId) -> tuple[ChapterView, ...]:
        return self.manuscript(project_id, commit_id).chapters

    def chapter_text(self, project_id: ProjectId, commit_id: CommitId, chapter_id: str) -> str:
        """某章的整章文本（按当前顺序，空行分隔），用于导出与核对。"""
        return self.manuscript(project_id, commit_id).text_of(chapter_id)

    def chapter_base(self, project_id: ProjectId, commit_id: CommitId, chapter_id: str) -> str:
        """某章的乐观并发基础（T14.3）；客户端读到时记下，保存时回传比对。"""
        return self.catalog(project_id, commit_id).chapter_base(chapter_id)
