"""Repository 结果对象（Phase 12 T12.1）。

no_change 是合法空结果（四透镜③）：不创建 Commit、不抛异常，
以正常返回值说明原因——把「没改动」污名化成故障会让调用方陷入重试循环。
"""

from __future__ import annotations

from dataclasses import dataclass

from hastings.domain.repository.ids import ArtifactId, CommitId

__all__ = ["CommitResult", "CommitStatus"]

COMMITTED = "committed"
NO_CHANGE = "no_change"
CommitStatus = str


@dataclass(frozen=True, slots=True)
class CommitResult:
    """提交结果。

    committed：携带 commit_id / new_head（提交时的分支 head）/ sequence / 变更摘要；
    no_change：携带 reason，其余字段为 None，不产生任何写入。
    幂等重试返回与首次完全相同的结果。
    """

    status: CommitStatus
    commit_id: CommitId | None = None
    new_head: CommitId | None = None
    sequence: int | None = None
    changed_artifact_ids: tuple[ArtifactId, ...] = ()
    no_change_reason: str | None = None

    @property
    def is_committed(self) -> bool:
        return self.status == COMMITTED

    @property
    def is_no_change(self) -> bool:
        return self.status == NO_CHANGE
