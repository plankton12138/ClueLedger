"""领域命令共享助手（Phase 13 T13.3）。

命令（Canon/Narrative）共用同一套「引用校验 / revision 校验 / 变更组装」原语，
把三要素错误与「创建 vs 修订」的区分只写一次（四透镜①：错误消息是喂给模型的
API；②：硬约束集中在工具层）。

本模块不产生业务语义，只保证：引用存在且类型正确、基础 Revision 未过期、
新 ID 不被复用。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hastings.domain.repository.ids import ArtifactId, RevisionId
from hastings.domain.repository.requests import ArtifactChange
from hastings.domain.story import error_codes as ec
from hastings.domain.story.catalog import StoryCatalog, StoryEntry
from hastings.domain.story.errors import violation

__all__ = [
    "NewId",
    "check_revision",
    "create_change",
    "new_artifact_id",
    "require_ref",
    "revise_change",
]

# new_id：由 kind 生成一个未被占用的新 ArtifactId（application 侧接 IdGenerator 端口）
NewId = Callable[[str], str]


def require_ref(
    catalog: StoryCatalog, artifact_id: str, allowed: tuple[str, ...], *, cmd: str, field: str
) -> StoryEntry:
    """引用必须存在且类型正确；否则给出带字段定位的三要素错误。"""
    where = f"{cmd}.{field}"
    entry = catalog.require(artifact_id, where=where)
    if entry.kind not in allowed:
        raise violation(
            ec.STORY_INVALID_COMMAND,
            where,
            f"{artifact_id!r} 的类型是 {entry.kind!r}，不能作为 {field} 引用",
            f"{field} 期望类型 {sorted(allowed)}；确认是否引用了错误对象",
            artifact_id=artifact_id,
            kind=entry.kind,
            expected=list(allowed),
        )
    return entry


def check_revision(
    entry: StoryEntry, expected_revision: str | None, *, cmd: str, field: str = "expected_revision"
) -> None:
    """乐观并发检查：期望 Revision 与当前不符时拒绝（不变量 5）。"""
    if expected_revision is not None and expected_revision != entry.revision_id:
        raise violation(
            ec.STORY_STALE_REVISION,
            f"{cmd}.{field}",
            f"基础 Revision {expected_revision!r} 已过期，当前为 {entry.revision_id!r}",
            "重新读取最新 Revision 后再提交；勿基于旧版本覆盖他人的修改",
            expected=expected_revision,
            current=entry.revision_id,
        )


def new_artifact_id(catalog: StoryCatalog, new_id: NewId, kind: str, *, cmd: str) -> str:
    """生成新 ID 并检查唯一性；创建与替代都必须产生新身份，不得复用。"""
    artifact_id = new_id(kind)
    if catalog.get(artifact_id) is not None:
        raise violation(
            ec.STORY_DUPLICATE_ID,
            f"{cmd}.id",
            f"生成的 ID {artifact_id!r} 已存在，不能覆盖既有对象",
            "换一个未被占用的 ID；创建与替代都必须产生新身份，不得复用",
            artifact_id=artifact_id,
        )
    return artifact_id


def create_change(artifact_id: str, kind: str, content: dict[str, Any]) -> ArtifactChange:
    """新 Artifact 的内容变化：kind 必填，old_revision_id 必须为 None。"""
    return ArtifactChange(
        artifact_id=ArtifactId(artifact_id), content=content, old_revision_id=None, kind=kind
    )


def revise_change(entry: StoryEntry, content: dict[str, Any]) -> ArtifactChange:
    """既有 Artifact 的修订：绑定基础 Revision，kind 留空。"""
    return ArtifactChange(
        artifact_id=ArtifactId(entry.artifact_id),
        content=content,
        old_revision_id=RevisionId(entry.revision_id),
        kind=None,
    )
