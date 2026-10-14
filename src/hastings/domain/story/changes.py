"""领域变更与命令周边的值对象（modules/story-domain.md §3/§4）。

`DomainChange` 是领域命令的唯一输出：它描述「要提交什么」（Artifact 内容变化
与关系变化）、「影响什么」（影响种子）、「提醒什么」（警告）以及给用户读的摘要。
命令不调用 CommitRepository；application handler 负责把 DomainChange 组装成
CommitRequest 并交给 Commit Service。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hastings.domain.repository.ids import ArtifactId
from hastings.domain.repository.requests import ArtifactChange, RelationChange

__all__ = [
    "CERTAINTY_EXPLICIT",
    "CERTAINTY_INFERRED",
    "CERTAINTY_SEMANTIC",
    "DomainChange",
    "FindingSeed",
    "ImpactSeed",
    "RelationSpec",
    "StoryWarning",
]

CERTAINTY_EXPLICIT = "explicit"
CERTAINTY_INFERRED = "inferred"
CERTAINTY_SEMANTIC = "semantic"


@dataclass(frozen=True, slots=True)
class RelationSpec:
    """命令产生的依赖边（modules §4）。ID 使用字符串，提交时转成 ArtifactId。"""

    action: str
    source: str
    relation_type: str
    target: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_change(self) -> RelationChange:
        return RelationChange(
            action=self.action,
            source=ArtifactId(self.source),
            relation_type=self.relation_type,
            target=ArtifactId(self.target),
            metadata=dict(self.metadata),
        )


@dataclass(frozen=True, slots=True)
class ImpactSeed:
    """影响种子：提示下游阶段哪些对象可能受本次变化影响（不执行语义分析）。

    certainty：explicit（作者显式声明）/ inferred（结构可推导）/ semantic
    （需正文语义分析，Phase 15 才处理）。Phase 13 只产生 explicit/inferred。
    """

    target_kind: str
    target_id: str
    reason: str
    certainty: str = CERTAINTY_EXPLICIT


@dataclass(frozen=True, slots=True)
class StoryWarning:
    """非致命警告：合法但偏离预期（modules §7 泄压阀）。"""

    code: str
    where: str
    why: str
    fix: str = ""


@dataclass(frozen=True, slots=True)
class FindingSeed:
    """确定性规则的输出（modules §7）；severity 由项目策略决定，领域只给种子。"""

    code: str
    where: str
    why: str
    fix: str
    severity_hint: str = "warning"
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", dict(self.details))


@dataclass(frozen=True, slots=True)
class DomainChange:
    """领域命令结果；仍需由 Guard 与 Commit Service 提交（modules §3）。"""

    artifact_changes: tuple[ArtifactChange, ...] = ()
    relation_changes: tuple[RelationChange, ...] = ()
    impact_seeds: tuple[ImpactSeed, ...] = ()
    warnings: tuple[StoryWarning, ...] = ()
    findings: tuple[FindingSeed, ...] = ()
    summary: str = ""

    def is_empty(self) -> bool:
        """无内容变化 = 合法空结果（no-op），handler 据此跳过提交。"""
        return not self.artifact_changes and not self.relation_changes
