"""Repository 端口：只读查询与原子提交（Phase 11 冻结名称与语义，T11.5）。

本阶段只冻结端口名、方法名与调用语义；载荷类型暂用 ``Mapping[str, object]``。
Phase 12 将以领域值对象（``ProjectId``/``CommitRequest``/``Snapshot`` 等，
见 repository-kernel.md §3 与计划 T12.1）收窄签名——收窄只允许变具体，
不允许改名、删除版本条件（``expected_head``）或把读端口变成写端口。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

__all__ = ["CommitRepository", "RepositoryReader"]


@runtime_checkable
class RepositoryReader(Protocol):
    """只读端口：Snapshot、Artifact 与 Diff 查询（ADR-002）。"""

    def get_snapshot(self, project_id: str, commit_id: str) -> Mapping[str, object]:
        """读取某 Commit 下的项目快照。"""

    def get_artifact(self, artifact_id: str, at_commit_id: str) -> Mapping[str, object]:
        """读取某 Artifact 在指定 Commit 的版本视图。"""

    def diff(self, before_commit_id: str, after_commit_id: str) -> Mapping[str, object]:
        """比较两个 Commit 间的 Artifact 与关系差异。"""


@runtime_checkable
class CommitRepository(Protocol):
    """写端口：原子提交、分支创建与移动（乐观版本检查）。

    agents 层禁止 import 本端口（依赖规则：工作者只返回 Proposal，ADR-003）。
    """

    def commit(self, request: Mapping[str, object]) -> Mapping[str, object]:
        """按 CommitRequest 原子提交；``expected_head`` 不匹配时整体失败（ADR-002/007）。"""

    def create_branch(self, project_id: str, name: str, at_commit_id: str) -> Mapping[str, object]:
        """从指定 Commit 创建分支。"""

    def move_branch(
        self, branch_id: str, new_head_commit_id: str, expected_version: int
    ) -> Mapping[str, object]:
        """移动分支 head；``expected_version`` 用于乐观并发检查。"""
