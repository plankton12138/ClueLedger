"""Repository 请求对象（Phase 12 T12.1）。

CommitRequest 携带模块文档 §3 要求的全部字段：project/branch/expected_head、
变更条目、关系变化、actor、幂等键、用户可读摘要与待发布事件。
版本条件（expected_head、old_revision_id、expected_version）只允许保留，不允许删除。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hastings.domain.repository.ids import (
    Actor,
    ArtifactId,
    BranchId,
    CommitId,
    ProjectId,
    RevisionId,
)

__all__ = [
    "ArtifactChange",
    "ArtifactQuery",
    "CommitRequest",
    "CreateBranchRequest",
    "MoveBranchRequest",
    "RelationChange",
]


@dataclass(frozen=True, slots=True)
class ArtifactChange:
    """单个 Artifact 的内容变化。

    kind 仅在创建新 Artifact 时必填；old_revision_id 是期望的基础版本
    （不变量 5），新 Artifact 必须为 None。
    """

    artifact_id: ArtifactId
    content: Any
    old_revision_id: RevisionId | None = None
    kind: str | None = None


@dataclass(frozen=True, slots=True)
class RelationChange:
    """关系变化：add 建边（可带 metadata），remove 撤边。"""

    action: str
    source: ArtifactId
    relation_type: str
    target: ArtifactId
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CommitRequest:
    """原子提交请求；expected_head 不匹配时整体失败（不变量 3）。"""

    project_id: ProjectId
    branch_id: BranchId
    expected_head: CommitId | None
    actor: Actor
    idempotency_key: str
    summary: str
    changes: tuple[ArtifactChange, ...] = ()
    relations: tuple[RelationChange, ...] = ()
    events: tuple[tuple[str, dict[str, Any]], ...] = ()
    required_leases: tuple[tuple[str, str, int], ...] = ()

    def __post_init__(self) -> None:
        if not self.idempotency_key or not self.idempotency_key.strip():
            raise ValueError(
                "CommitRequest.idempotency_key 不能为空（为什么错：幂等重试依赖稳定键；"
                "怎样算对：同一逻辑操作的所有重试使用同一个键）"
            )
        if not self.summary:
            raise ValueError(
                "CommitRequest.summary 不能为空（为什么错：Commit 是给人读的历史，"
                "无摘要无法在事件与界面中解释本次变化）"
            )


@dataclass(frozen=True, slots=True)
class CreateBranchRequest:
    """从指定 Commit 创建分支。"""

    project_id: ProjectId
    name: str
    at_commit_id: CommitId


@dataclass(frozen=True, slots=True)
class MoveBranchRequest:
    """移动分支 head；expected_version 用于乐观并发检查。"""

    branch_id: BranchId
    new_head_commit_id: CommitId
    expected_version: int


@dataclass(frozen=True, slots=True)
class ArtifactQuery:
    """Artifact 列表过滤；kind 为 None 表示不过滤。"""

    kind: str | None = None
