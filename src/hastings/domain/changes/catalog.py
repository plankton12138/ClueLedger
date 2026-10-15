"""ChangesCatalog：把 Repository Snapshot 投影为变更目录（只读）。

与 ManuscriptCatalog/StoryCatalog 同构：领域命令不直接访问存储，
application handler 先取 Snapshot、构造目录，再交给命令函数。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hastings.domain.changes import kinds
from hastings.domain.changes.error_codes import CHANGES_UNKNOWN_CHANGE_SET
from hastings.domain.changes.errors import violation
from hastings.domain.changes.models import ChangeSet
from hastings.domain.repository.models import Snapshot

__all__ = ["ChangeEntry", "ChangesCatalog", "decode"]


def decode(kind: str, content: dict[str, Any]) -> Any:
    """按 kind 解码为领域模型；未注册 kind 返回原始 content。"""
    if kind == kinds.KIND_CHANGE_SET:
        return ChangeSet.from_content(content)
    return content


@dataclass(frozen=True, slots=True)
class ChangeEntry:
    """Snapshot 中单个变更对象：身份 + 有效 Revision + 内容。"""

    artifact_id: str
    kind: str
    revision_id: str
    content: dict[str, Any]

    def model(self) -> Any:
        return decode(self.kind, self.content)


@dataclass(frozen=True, slots=True)
class ChangesCatalog:
    """某 Commit 的变更目录（只读）。commit_id 为 None 表示空目录。"""

    commit_id: str | None
    entries: tuple[ChangeEntry, ...]

    @classmethod
    def from_snapshot(cls, snapshot: Snapshot) -> ChangesCatalog:
        entries: list[ChangeEntry] = []
        for view in snapshot.artifacts.values():
            if view.revision is None:
                continue
            if not kinds.is_changes_kind(view.artifact.kind):
                continue
            entries.append(
                ChangeEntry(
                    artifact_id=view.artifact.id.value,
                    kind=view.artifact.kind,
                    revision_id=view.revision.id.value,
                    content=dict(view.revision.content),
                )
            )
        entries.sort(key=lambda e: e.artifact_id)
        return cls(commit_id=snapshot.commit_id.value, entries=tuple(entries))

    def get(self, artifact_id: str) -> ChangeEntry | None:
        return next((e for e in self.entries if e.artifact_id == artifact_id), None)

    def by_kind(self, kind: str) -> tuple[ChangeEntry, ...]:
        return tuple(e for e in self.entries if e.kind == kind)

    def require_change_set(self, change_set_id: str, *, where: str) -> ChangeEntry:
        """定位 ChangeSet；不存在即违规（错误消息给出下一步）。"""
        entry = self.get(change_set_id)
        if entry is None:
            raise violation(
                CHANGES_UNKNOWN_CHANGE_SET,
                where,
                f"ChangeSet {change_set_id!r} 在当前快照中不存在",
                "改用 open_change_set 返回的 change_set_id，或先打开新的变更集",
                change_set_id=change_set_id,
            )
        if entry.kind != kinds.KIND_CHANGE_SET:
            raise violation(
                CHANGES_UNKNOWN_CHANGE_SET,
                where,
                f"{change_set_id!r} 的类型是 {entry.kind!r}，不是 ChangeSet",
                f"ChangeSet 引用必须指向 {kinds.KIND_CHANGE_SET} 对象",
                change_set_id=change_set_id,
                kind=entry.kind,
            )
        return entry
