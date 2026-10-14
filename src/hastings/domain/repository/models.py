"""Repository 领域模型（modules/repository-kernel.md §2 核心对象）。

全部 frozen dataclass：Revision/Commit 创建后不可修改或删除（不变量 1），
可变性只存在于 Branch head 与 Lease/Outbox 状态，由存储层在事务内更新。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from hastings.domain.repository.ids import (
    Actor,
    ArtifactId,
    BranchId,
    CommitId,
    ContentHash,
    JobId,
    ProjectId,
    RevisionId,
)

__all__ = [
    "Artifact",
    "ArtifactView",
    "ArtifactDiffEntry",
    "Branch",
    "Commit",
    "CommitEntry",
    "Lease",
    "OutboxEvent",
    "Project",
    "RelationRevision",
    "RepositoryDiff",
    "Revision",
    "Snapshot",
    "lease_resource_key",
]

RELATION_ACTION_ADD = "add"
RELATION_ACTION_REMOVE = "remove"


@dataclass(frozen=True, slots=True)
class Project:
    """小说项目边界。"""

    id: ProjectId
    name: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Artifact:
    """可版本化对象身份；kind 创建后不可变。"""

    id: ArtifactId
    project_id: ProjectId
    kind: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Revision:
    """不可变内容版本。content 为经领域 Schema 验证的 JSON 兼容数据。"""

    id: RevisionId
    artifact_id: ArtifactId
    parent_id: RevisionId | None
    content: Any  # JSON 兼容结构；哈希已由 content_hash 固定
    content_hash: ContentHash
    author: Actor
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Commit:
    """一组原子生效的变化；sequence 项目内严格递增且唯一。"""

    id: CommitId
    project_id: ProjectId
    parent_ids: tuple[CommitId, ...]
    message: str
    actor: Actor
    created_at: datetime
    sequence: int


@dataclass(frozen=True, slots=True)
class CommitEntry:
    """Commit 中的对象变化；old 必须等于基础快照中的有效 revision（不变量 5）。"""

    commit_id: CommitId
    artifact_id: ArtifactId
    old_revision_id: RevisionId | None
    new_revision_id: RevisionId


@dataclass(frozen=True, slots=True)
class Branch:
    """可移动引用；version 用于乐观并发（不变量 2）。"""

    id: BranchId
    project_id: ProjectId
    name: str
    head_commit_id: CommitId | None
    version: int


@dataclass(frozen=True, slots=True)
class RelationRevision:
    """版本化关系边；由哪个 Commit 以 add/remove 写入，快照按序折叠。"""

    id: str
    commit_id: CommitId
    action: str
    source: ArtifactId
    relation_type: str
    target: ArtifactId
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.action not in (RELATION_ACTION_ADD, RELATION_ACTION_REMOVE):
            raise ValueError(
                f"RelationRevision.action 非法（哪里错：得到 {self.action!r}；"
                f"为什么错：只接受 add/remove；怎样算对：新增边用 add，撤销边用 remove）"
            )
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    """与 Commit 同事务写入的业务事件（ADR-007）；published_at 为空表示待发布。"""

    id: str
    project_id: ProjectId
    commit_id: CommitId | None
    sequence: int
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    published_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", dict(self.payload))


@dataclass(frozen=True, slots=True)
class Lease:
    """跨会话的短期执行互斥；续租与提交必须验证 owner+version（不变量 9）。"""

    resource_key: str
    owner_job_id: JobId
    expires_at: datetime
    version: int

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at


def lease_resource_key(project_id: ProjectId, artifact_id: ArtifactId) -> str:
    """资源 key 的规范形式：project:{id}:artifact:{id}。"""
    return f"project:{project_id.value}:artifact:{artifact_id.value}"


@dataclass(frozen=True, slots=True)
class ArtifactView:
    """某 Commit 下 Artifact 的版本视图；revision 为 None 表示该 Commit 尚未引入此对象。"""

    artifact: Artifact
    revision: Revision | None


@dataclass(frozen=True, slots=True)
class Snapshot:
    """项目在某 Commit 的完整快照。"""

    project_id: ProjectId
    commit_id: CommitId
    artifacts: dict[ArtifactId, ArtifactView]
    relations: tuple[RelationRevision, ...]


@dataclass(frozen=True, slots=True)
class ArtifactDiffEntry:
    """两 Commit 间单个 Artifact 的差异；任一侧为 None 表示新增/删除。"""

    artifact_id: ArtifactId
    kind: str
    old_revision_id: RevisionId | None
    new_revision_id: RevisionId | None


@dataclass(frozen=True, slots=True)
class RepositoryDiff:
    """两 Commit 间的 Artifact 与关系差异。"""

    before_commit_id: CommitId
    after_commit_id: CommitId
    artifact_changes: tuple[ArtifactDiffEntry, ...]
    relations_added: tuple[RelationRevision, ...]
    relations_removed: tuple[RelationRevision, ...]
