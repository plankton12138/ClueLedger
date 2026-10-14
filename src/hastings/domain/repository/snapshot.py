"""快照折叠纯函数（Phase 12）：把 Commit 链折叠成某时刻的有效状态。

输入约定：commits 按 root→head 拓扑序排列；entries/relation 行已按同序展开。
折叠规则是「后写的赢」——这与 Git 的最后写入语义一致，也与不变量 6 的
sequence 严格递增配合：同一 Artifact 在链上最后一次写入即当前有效版本。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from hastings.domain.repository.ids import ArtifactId, CommitId, RevisionId
from hastings.domain.repository.models import (
    RELATION_ACTION_ADD,
    RELATION_ACTION_REMOVE,
    Commit,
    CommitEntry,
    RelationRevision,
)

__all__ = ["fold_effective_relations", "fold_effective_revisions"]


def fold_effective_revisions(
    commits: Sequence[Commit],
    entries_by_commit: Mapping[CommitId, Sequence[CommitEntry]],
) -> dict[ArtifactId, RevisionId]:
    """按 root→head 顺序折叠出每个 Artifact 的有效 Revision（无历史的对象不在结果中）。"""
    effective: dict[ArtifactId, RevisionId] = {}
    for commit in commits:
        for entry in entries_by_commit.get(commit.id, ()):
            effective[entry.artifact_id] = entry.new_revision_id
    return effective


def fold_effective_relations(
    relation_rows: Sequence[RelationRevision],
) -> tuple[RelationRevision, ...]:
    """折叠关系边：add 建边、remove 撤边；输出保留 add 的先后顺序。"""
    edges: dict[tuple[ArtifactId, str, ArtifactId], RelationRevision] = {}
    for row in relation_rows:
        key = (row.source, row.relation_type, row.target)
        if row.action == RELATION_ACTION_ADD:
            edges[key] = row
        elif row.action == RELATION_ACTION_REMOVE:
            edges.pop(key, None)
    return tuple(edges.values())
