"""Repository 领域值对象（Phase 12 T12.1）。

ID 是稳定主键，不是显示名：不接受空串、纯空白、路径分隔符或相对路径片段，
创建后不可变（modules/repository-kernel.md §2）。时间一律带时区 UTC，
由 Clock 端口注入，领域对象不自行取「现在」。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "Actor",
    "ArtifactId",
    "BranchId",
    "CommitId",
    "ContentHash",
    "JobId",
    "ProjectId",
    "RevisionId",
    "Scope",
]

_FORBIDDEN_ID_CHARS = ("/", "\\", "\n", "\r", "\t")
VALID_ACTOR_KINDS = ("human", "agent", "system")


def _validated_id(value: object, cls_name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{cls_name} 必须是 str，得到 {type(value).__name__}")
    if (
        not value
        or value.strip() != value
        or any(c in value for c in _FORBIDDEN_ID_CHARS)
        or value in (".", "..")
    ):
        raise ValueError(
            f"{cls_name} 非法（哪里错：值为 {value!r}；"
            f"为什么错：ID 不得为空、含首尾空白、路径分隔符 / \\ 或相对路径片段；"
            f"怎样算对：使用生成器产出的 <prefix>_<后缀> 形式）"
        )


@dataclass(frozen=True, slots=True)
class ProjectId:
    """小说项目边界 ID。"""

    value: str

    def __post_init__(self) -> None:
        _validated_id(self.value, "ProjectId")


@dataclass(frozen=True, slots=True)
class ArtifactId:
    """可版本化对象身份 ID。"""

    value: str

    def __post_init__(self) -> None:
        _validated_id(self.value, "ArtifactId")


@dataclass(frozen=True, slots=True)
class RevisionId:
    """不可变内容版本 ID。"""

    value: str

    def __post_init__(self) -> None:
        _validated_id(self.value, "RevisionId")


@dataclass(frozen=True, slots=True)
class CommitId:
    """原子提交 ID。"""

    value: str

    def __post_init__(self) -> None:
        _validated_id(self.value, "CommitId")


@dataclass(frozen=True, slots=True)
class BranchId:
    """可移动分支引用 ID。"""

    value: str

    def __post_init__(self) -> None:
        _validated_id(self.value, "BranchId")


@dataclass(frozen=True, slots=True)
class JobId:
    """后台任务/租约持有者 ID。"""

    value: str

    def __post_init__(self) -> None:
        _validated_id(self.value, "JobId")


@dataclass(frozen=True, slots=True)
class ContentHash:
    """内容寻址哈希：小写 64 位十六进制（SHA-256），由 serialization.content_hash 产出。"""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError("ContentHash 必须是 str")
        if len(self.value) != 64 or any(c not in "0123456789abcdef" for c in self.value):
            raise ValueError(
                "ContentHash 非法（哪里错：值不是 64 位小写十六进制；"
                "为什么错：内容哈希固定使用 SHA-256 十六进制形式；"
                "怎样算对：调用 serialization.content_hash 生成）"
            )


@dataclass(frozen=True, slots=True)
class Actor:
    """提交者身份：kind 限定三值，id 为稳定 ID 而非显示名。"""

    kind: str
    id: str

    def __post_init__(self) -> None:
        if self.kind not in VALID_ACTOR_KINDS:
            raise ValueError(
                f"Actor.kind 非法（哪里错：得到 {self.kind!r}；"
                f"为什么错：kind 只接受 {VALID_ACTOR_KINDS}；"
                f"怎样算对：人类用 human、工作者 agent、仓库内部 system）"
            )
        _validated_id(self.id, "Actor.id")


@dataclass(frozen=True, slots=True)
class Scope:
    """Diff/查询范围：artifact_kinds 为空表示不过滤。"""

    artifact_kinds: tuple[str, ...] = ()

    def is_unfiltered(self) -> bool:
        return not self.artifact_kinds
