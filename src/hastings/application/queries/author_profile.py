"""作者模型查询入口（Phase 14 T14.6）：把 RepositoryReader 与目标 Commit 绑成作者模型视图。

与 ``ManuscriptQueries`` 同构：按 ``(project_id, commit_id)`` 取快照、构造
``AuthorModelCatalog``，再把目录交给只读投影。**不做缓存**——调用方每次显式给出目标
Commit，head 前移后旧 Commit 的视图仍是旧 Commit 的视图，不会冒充最新。

两类读者、两个入口：

- 作者模型页要看到**全部状态**（candidate / active / revoked + 证据 + 范围），
  走 ``preferences`` 与 ``preference_evidence``；
- 生成上下文只要**真正可用**的偏好，走 ``effective_preferences``——仅 active，
  按作用域具体度解析，撤销过的绝不出现（计划 §6 清单 13）。

查询视图是派生结果，本模块只读、无写入路径；写命令走 handlers 与 Commit Service。
"""

from __future__ import annotations

from hastings.application.ports.repository import RepositoryReader
from hastings.domain.author_model import rules
from hastings.domain.author_model.catalog import AuthorModelCatalog
from hastings.domain.author_model.models import (
    SCOPE_PROJECT,
    AuthorExample,
    AuthorObservation,
    PreferenceHypothesis,
)
from hastings.domain.repository.ids import CommitId, ProjectId

__all__ = ["AuthorProfileQueries"]


class AuthorProfileQueries:
    """绑定只读端口的作者模型查询服务；所有视图都带 Commit ID。"""

    def __init__(self, reader: RepositoryReader) -> None:
        self._reader = reader

    def catalog(self, project_id: ProjectId, commit_id: CommitId) -> AuthorModelCatalog:
        """取指定 Commit 的作者模型目录（投影与查询的共同入口）。"""
        return AuthorModelCatalog.from_snapshot(self._reader.get_snapshot(project_id, commit_id))

    def observations(
        self, project_id: ProjectId, commit_id: CommitId
    ) -> tuple[AuthorObservation, ...]:
        """全部已分类观察（作者模型页的「证据」区）。"""
        return self.catalog(project_id, commit_id).observations()

    def preferences(
        self, project_id: ProjectId, commit_id: CommitId
    ) -> tuple[PreferenceHypothesis, ...]:
        """全部偏好，含 candidate 与 revoked：作者模型页要展示完整状态与撤销历史。"""
        return self.catalog(project_id, commit_id).preferences()

    def effective_preferences(
        self,
        project_id: ProjectId,
        commit_id: CommitId,
        *,
        scope_kind: str = SCOPE_PROJECT,
        scope_target: str = "",
    ) -> tuple[PreferenceHypothesis, ...]:
        """某作用域下真正可用的偏好：仅 active，同类别取最具体作用域。

        revoked 与 candidate 都排除在生成上下文之外（撤销即排除；candidate 等更多证据）。
        """
        return rules.resolve_preferences(
            self.preferences(project_id, commit_id),
            scope_kind=scope_kind,
            scope_target=scope_target,
        )

    def preference_evidence(
        self, project_id: ProjectId, commit_id: CommitId, preference_id: str
    ) -> tuple[AuthorObservation, ...]:
        """反查支持某条偏好的观察：按证据 Commit + 类别对齐（计划 §6 清单 14）。

        每条 active 偏好都能回答「你为什么这么认为」——证据是 Commit ID，观察是
        可读的编辑前后文本与分类信号。
        """
        catalog = self.catalog(project_id, commit_id)
        preference = catalog.require_preference(
            preference_id, where="author_profile.preference_evidence"
        ).model()
        evidence = set(preference.evidence)
        return tuple(
            observation
            for observation in catalog.observations()
            if observation.category == preference.category and observation.commit_id in evidence
        )

    def examples(self, project_id: ProjectId, commit_id: CommitId) -> tuple[AuthorExample, ...]:
        """全部样例，含已删除的历史记录：作者模型页要展示撤回状态与引用 ID。"""
        return self.catalog(project_id, commit_id).examples()

    def effective_examples(
        self, project_id: ProjectId, commit_id: CommitId
    ) -> tuple[AuthorExample, ...]:
        """生成上下文可用的样例：仅 active，已删除的样例不再返回（T14.7）。"""
        return rules.effective_examples(self.examples(project_id, commit_id))
