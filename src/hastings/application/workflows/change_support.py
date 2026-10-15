"""变更工作流共用的版本读取与错误边界；写入仍由各用例构造 CommitRequest。"""

from __future__ import annotations

from typing import Any

from hastings.application.errors import ViolationError
from hastings.application.ports.repository import RepositoryReader
from hastings.domain.repository.ids import Actor, ArtifactId, CommitId, ProjectId
from hastings.domain.repository.models import Branch, Snapshot


def reject(code: str, where: str, why: str, fix: str) -> None:
    raise ViolationError(code=code, where=where, why=why, fix=fix)


def require_author(actor: Actor) -> None:
    if actor.kind != "human":
        reject(
            "CHANGES_APPROVAL_REQUIRED",
            "changes.author",
            "该操作需要作者决策",
            "由作者审阅具体范围后执行；Worker 只能提出补丁",
        )


def branch_snapshot(
    reader: RepositoryReader, project: ProjectId, name: str = "main"
) -> tuple[Branch, Snapshot]:
    branch = reader.get_branch(project, name)
    if branch is None or branch.head_commit_id is None:
        raise ViolationError(
            code="REPO_BRANCH_NOT_FOUND",
            where="changes.branch",
            why=f"分支 {name!r} 不存在或为空",
            fix="选择已有提交的项目和分支",
        )
    return branch, reader.get_snapshot(project, branch.head_commit_id)


def content(snapshot: Snapshot, artifact_id: str) -> dict[str, Any]:
    view = snapshot.artifacts.get(ArtifactId(artifact_id))
    if view is None or view.revision is None:
        raise ViolationError(
            code="CHANGES_ARTIFACT_NOT_FOUND",
            where=artifact_id,
            why="当前版本没有该对象",
            fix="使用当前版本中的稳定对象 ID",
        )
    return dict(view.revision.content)


def replay_content(
    reader: RepositoryReader, project: ProjectId, key: str, artifact_id: str
) -> dict[str, Any] | None:
    result = reader.find_commit_result(project, key)
    if result is None or result.commit_id is None:
        return None
    return content(reader.get_snapshot(project, CommitId(result.commit_id.value)), artifact_id)
