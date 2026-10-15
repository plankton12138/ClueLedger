"""ChangeSet 领域模型（modules/change-impact.md §2）：状态、种子与审计历史。

ChangeSet 是普通 Artifact（kind ``changes.change_set``），随 Commit 持久化；
状态迁移全部追加 ``TransitionRecord``——actor、时间、原因与基础 Commit
是审计的最小四件套，缺一不可（T15.1）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hastings.domain.changes import error_codes as ec
from hastings.domain.changes import status as st
from hastings.domain.changes.errors import violation
from hastings.domain.repository.requests import ArtifactChange

__all__ = [
    "EVENT_CHANGE_SET_OPENED",
    "EVENT_CHANGE_SET_TRANSITIONED",
    "SCHEMA_VERSION",
    "SOURCE_AGENT_PROPOSAL",
    "SOURCE_IMPORT",
    "SOURCE_MANUAL",
    "SOURCE_MIGRATION",
    "SOURCES",
    "ChangeScope",
    "ChangeSet",
    "ChangeSetChange",
    "SeedChange",
    "TransitionRecord",
]

SCHEMA_VERSION = 1

# 业务事件（领域契约）：随 Commit 同事务写入 Outbox（ADR-007）
EVENT_CHANGE_SET_OPENED = "changes.change_set.opened"
EVENT_CHANGE_SET_TRANSITIONED = "changes.change_set.transitioned"

SOURCE_MANUAL = "manual"
SOURCE_AGENT_PROPOSAL = "agent_proposal"
SOURCE_IMPORT = "import"
SOURCE_MIGRATION = "migration"
SOURCES = frozenset((SOURCE_MANUAL, SOURCE_AGENT_PROPOSAL, SOURCE_IMPORT, SOURCE_MIGRATION))


def _require_non_empty(value: str, field_name: str, where: str) -> str:
    if not value or not value.strip():
        raise violation(
            ec.CHANGES_INVALID_COMMAND,
            where,
            f"{field_name} 不能为空",
            f"提供非空的 {field_name}（它是审计与影响分析的输入，缺省无法解释本次变化）",
            field=field_name,
        )
    return value


@dataclass(frozen=True, slots=True)
class SeedChange:
    """已确认要改变的对象（可选字段与原因）。"""

    artifact_id: str
    field: str = ""
    reason: str = ""

    def to_content(self) -> dict[str, Any]:
        return {"artifact_id": self.artifact_id, "field": self.field, "reason": self.reason}

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> SeedChange:
        return cls(
            artifact_id=str(content.get("artifact_id", "")),
            field=str(content.get("field", "")),
            reason=str(content.get("reason", "")),
        )


@dataclass(frozen=True, slots=True)
class ChangeScope:
    """允许分析和修改的范围（T15.2 赋予遍历语义；本阶段是承载数据）。

    ``kinds`` 为 kind 白名单（空=不限）；``roots`` 为允许进入的子图根
    （如 arc/chapter 的 artifact id，空=不限）；``max_hops`` 为传播跳数上限
    （None=不限）。
    """

    kinds: tuple[str, ...] = ()
    roots: tuple[str, ...] = ()
    max_hops: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kinds", tuple(self.kinds))
        object.__setattr__(self, "roots", tuple(self.roots))

    def to_content(self) -> dict[str, Any]:
        return {
            "kinds": list(self.kinds),
            "roots": list(self.roots),
            "max_hops": self.max_hops,
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> ChangeScope:
        hops = content.get("max_hops")
        return cls(
            kinds=tuple(str(k) for k in (content.get("kinds") or ())),
            roots=tuple(str(r) for r in (content.get("roots") or ())),
            max_hops=int(hops) if hops is not None else None,
        )


@dataclass(frozen=True, slots=True)
class TransitionRecord:
    """一次状态迁移的审计记录：actor、时间、原因、基础 Commit（四件套）。"""

    from_status: str
    to_status: str
    actor_kind: str
    actor_id: str
    at: str  # ISO-8601（带时区）
    reason: str
    base_commit_id: str
    approval_ref: str = ""
    commit_id: str = ""

    def to_content(self) -> dict[str, Any]:
        return {
            "from_status": self.from_status,
            "to_status": self.to_status,
            "actor_kind": self.actor_kind,
            "actor_id": self.actor_id,
            "at": self.at,
            "reason": self.reason,
            "base_commit_id": self.base_commit_id,
            "approval_ref": self.approval_ref,
            "commit_id": self.commit_id,
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> TransitionRecord:
        return cls(
            from_status=str(content.get("from_status", "")),
            to_status=str(content.get("to_status", "")),
            actor_kind=str(content.get("actor_kind", "")),
            actor_id=str(content.get("actor_id", "")),
            at=str(content.get("at", "")),
            reason=str(content.get("reason", "")),
            base_commit_id=str(content.get("base_commit_id", "")),
            approval_ref=str(content.get("approval_ref", "")),
            commit_id=str(content.get("commit_id", "")),
        )


_CHANGE_SET_KEYS = frozenset(
    {
        "schema_version",
        "change_set_id",
        "intent",
        "source",
        "base_commit_id",
        "seed_changes",
        "scope",
        "status",
        "history",
    }
)


@dataclass(frozen=True, slots=True)
class ChangeSet:
    """变更集工作流载体（kind ``changes.change_set``）。"""

    change_set_id: str
    intent: str
    source: str
    base_commit_id: str
    status: str
    history: tuple[TransitionRecord, ...] = ()
    seed_changes: tuple[SeedChange, ...] = ()
    scope: ChangeScope = field(default_factory=ChangeScope)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "history", tuple(self.history))
        object.__setattr__(self, "seed_changes", tuple(self.seed_changes))
        if self.source not in SOURCES:
            raise violation(
                ec.CHANGES_INVALID_COMMAND,
                "changes.change_set",
                f"source 只能是 {sorted(SOURCES)}，收到 {self.source!r}",
                "把 source 改为 manual / agent_proposal / import / migration",
                source=self.source,
            )
        if self.status not in st.LEGAL_TRANSITIONS:
            raise violation(
                ec.CHANGES_INVALID_COMMAND,
                "changes.change_set",
                f"status 非法：{self.status!r}",
                f"status 必须是 {sorted(st.LEGAL_TRANSITIONS)} 之一",
                status=self.status,
            )

    def to_content(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "change_set_id": self.change_set_id,
            "intent": self.intent,
            "source": self.source,
            "base_commit_id": self.base_commit_id,
            "seed_changes": [s.to_content() for s in self.seed_changes],
            "scope": self.scope.to_content(),
            "status": self.status,
            "history": [h.to_content() for h in self.history],
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> ChangeSet:
        return cls(
            change_set_id=str(content.get("change_set_id", "")),
            intent=str(content.get("intent", "")),
            source=str(content.get("source", SOURCE_MANUAL)),
            base_commit_id=str(content.get("base_commit_id", "")),
            status=str(content.get("status", st.CHANGE_OPENED)),
            history=tuple(TransitionRecord.from_content(h) for h in (content.get("history") or [])),
            seed_changes=tuple(
                SeedChange.from_content(s) for s in (content.get("seed_changes") or [])
            ),
            scope=ChangeScope.from_content(content.get("scope") or {}),
            schema_version=int(content.get("schema_version", SCHEMA_VERSION)),
        )


@dataclass(frozen=True, slots=True)
class ChangeSetChange:
    """命令结果：要提交的 Artifact 变化 + 随提交落盘的事件 + 摘要。

    状态迁移永远改变内容（status + history），因此变更集命令没有 no-op 路径；
    ``is_empty`` 仅在无变化时为真（防御式保留，与其它领域同构）。
    """

    artifact_changes: tuple[ArtifactChange, ...] = ()
    events: tuple[tuple[str, dict[str, Any]], ...] = ()
    summary: str = ""

    def is_empty(self) -> bool:
        return not self.artifact_changes
