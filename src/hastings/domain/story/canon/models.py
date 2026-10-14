"""Canon 领域模型（modules/story-domain.md §2.1）。

全部 frozen dataclass，引用只存稳定 ID（字符串形态的 ArtifactId），名称仅用于
显示与搜索。`legacy_metadata` 显式保留 V1 未知字段，不静默丢失。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hastings.domain.story.schema import (
    SCHEMA_VERSION,
    require_str,
    split_legacy,
    tuple_of_str,
)

__all__ = [
    "Character",
    "Clue",
    "Foreshadow",
    "MysteryTruth",
    "Place",
    "RedHerring",
    "Relationship",
    "StoryPremise",
    "TimelineEvent",
    "WorldRule",
]

# 角色在故事中的作用；ChangeCharacterRole 修改此字段
CHARACTER_ROLES = frozenset(("protagonist", "antagonist", "accomplice", "support", "witness"))


def _label(kind: str) -> str:
    return f"canon.{kind}"


@dataclass(frozen=True, slots=True)
class StoryPremise:
    """核心命题、类型、读者承诺、主题边界。"""

    premise: str
    genre: str = ""
    reader_promise: str = ""
    thematic_bounds: tuple[str, ...] = ()
    schema_version: int = SCHEMA_VERSION
    legacy_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "thematic_bounds", tuple(self.thematic_bounds))
        object.__setattr__(self, "legacy_metadata", dict(self.legacy_metadata))

    def to_content(self) -> dict[str, Any]:
        return {
            **self.legacy_metadata,
            "schema_version": self.schema_version,
            "premise": self.premise,
            "genre": self.genre,
            "reader_promise": self.reader_promise,
            "thematic_bounds": list(self.thematic_bounds),
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> StoryPremise:
        where = _label("premise")
        return cls(
            premise=require_str(content.get("premise"), field="premise", where=where),
            genre=str(content.get("genre", "")),
            reader_promise=str(content.get("reader_promise", "")),
            thematic_bounds=tuple_of_str(content.get("thematic_bounds")),
            schema_version=int(content.get("schema_version", SCHEMA_VERSION)),
            legacy_metadata=split_legacy(
                content,
                frozenset(
                    {"schema_version", "premise", "genre", "reader_promise", "thematic_bounds"}
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class Character:
    """身份、目标、需求、秘密、弧光、特征与在故事中的作用。"""

    name: str
    identity: str = ""
    goal: str = ""
    need: str = ""
    secret: str = ""
    arc: str = ""
    role: str = "support"
    traits: tuple[str, ...] = ()
    schema_version: int = SCHEMA_VERSION
    legacy_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "traits", tuple(self.traits))
        object.__setattr__(self, "legacy_metadata", dict(self.legacy_metadata))

    def to_content(self) -> dict[str, Any]:
        return {
            **self.legacy_metadata,
            "schema_version": self.schema_version,
            "name": self.name,
            "identity": self.identity,
            "goal": self.goal,
            "need": self.need,
            "secret": self.secret,
            "arc": self.arc,
            "role": self.role,
            "traits": list(self.traits),
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> Character:
        where = _label("character")
        return cls(
            name=require_str(content.get("name"), field="name", where=where),
            identity=str(content.get("identity", "")),
            goal=str(content.get("goal", "")),
            need=str(content.get("need", "")),
            secret=str(content.get("secret", "")),
            arc=str(content.get("arc", "")),
            role=str(content.get("role", "support")),
            traits=tuple_of_str(content.get("traits")),
            schema_version=int(content.get("schema_version", SCHEMA_VERSION)),
            legacy_metadata=split_legacy(
                content,
                frozenset(
                    {
                        "schema_version",
                        "name",
                        "identity",
                        "goal",
                        "need",
                        "secret",
                        "arc",
                        "role",
                        "traits",
                    }
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class Relationship:
    """双方、关系类型、公开状态、真实状态、演变。"""

    source_id: str
    target_id: str
    relation_type: str = ""
    public_state: str = ""
    true_state: str = ""
    evolution: str = ""
    schema_version: int = SCHEMA_VERSION
    legacy_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "legacy_metadata", dict(self.legacy_metadata))

    def to_content(self) -> dict[str, Any]:
        return {
            **self.legacy_metadata,
            "schema_version": self.schema_version,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation_type": self.relation_type,
            "public_state": self.public_state,
            "true_state": self.true_state,
            "evolution": self.evolution,
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> Relationship:
        where = _label("relationship")
        return cls(
            source_id=require_str(content.get("source_id"), field="source_id", where=where),
            target_id=require_str(content.get("target_id"), field="target_id", where=where),
            relation_type=str(content.get("relation_type", "")),
            public_state=str(content.get("public_state", "")),
            true_state=str(content.get("true_state", "")),
            evolution=str(content.get("evolution", "")),
            schema_version=int(content.get("schema_version", SCHEMA_VERSION)),
            legacy_metadata=split_legacy(
                content,
                frozenset(
                    {
                        "schema_version",
                        "source_id",
                        "target_id",
                        "relation_type",
                        "public_state",
                        "true_state",
                        "evolution",
                    }
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class Place:
    """空间、进入条件、相邻关系、重要特性。"""

    name: str
    description: str = ""
    entry_conditions: tuple[str, ...] = ()
    adjacent: tuple[str, ...] = ()
    features: tuple[str, ...] = ()
    schema_version: int = SCHEMA_VERSION
    legacy_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "entry_conditions", tuple(self.entry_conditions))
        object.__setattr__(self, "adjacent", tuple(self.adjacent))
        object.__setattr__(self, "features", tuple(self.features))
        object.__setattr__(self, "legacy_metadata", dict(self.legacy_metadata))

    def to_content(self) -> dict[str, Any]:
        return {
            **self.legacy_metadata,
            "schema_version": self.schema_version,
            "name": self.name,
            "description": self.description,
            "entry_conditions": list(self.entry_conditions),
            "adjacent": list(self.adjacent),
            "features": list(self.features),
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> Place:
        where = _label("place")
        return cls(
            name=require_str(content.get("name"), field="name", where=where),
            description=str(content.get("description", "")),
            entry_conditions=tuple_of_str(content.get("entry_conditions")),
            adjacent=tuple_of_str(content.get("adjacent")),
            features=tuple_of_str(content.get("features")),
            schema_version=int(content.get("schema_version", SCHEMA_VERSION)),
            legacy_metadata=split_legacy(
                content,
                frozenset(
                    {
                        "schema_version",
                        "name",
                        "description",
                        "entry_conditions",
                        "adjacent",
                        "features",
                    }
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class WorldRule:
    """规则、条件、例外、代价。"""

    rule: str
    conditions: tuple[str, ...] = ()
    exceptions: tuple[str, ...] = ()
    cost: str = ""
    schema_version: int = SCHEMA_VERSION
    legacy_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "conditions", tuple(self.conditions))
        object.__setattr__(self, "exceptions", tuple(self.exceptions))
        object.__setattr__(self, "legacy_metadata", dict(self.legacy_metadata))

    def to_content(self) -> dict[str, Any]:
        return {
            **self.legacy_metadata,
            "schema_version": self.schema_version,
            "rule": self.rule,
            "conditions": list(self.conditions),
            "exceptions": list(self.exceptions),
            "cost": self.cost,
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> WorldRule:
        where = _label("world_rule")
        return cls(
            rule=require_str(content.get("rule"), field="rule", where=where),
            conditions=tuple_of_str(content.get("conditions")),
            exceptions=tuple_of_str(content.get("exceptions")),
            cost=str(content.get("cost", "")),
            schema_version=int(content.get("schema_version", SCHEMA_VERSION)),
            legacy_metadata=split_legacy(
                content,
                frozenset({"schema_version", "rule", "conditions", "exceptions", "cost"}),
            ),
        )


@dataclass(frozen=True, slots=True)
class MysteryTruth:
    """真凶、协助者、动机、手法、掩盖方式、真实时间线。"""

    culprit_id: str
    motive: str = ""
    method: str = ""
    cover_up: str = ""
    accomplice_ids: tuple[str, ...] = ()
    true_timeline: tuple[str, ...] = ()
    schema_version: int = SCHEMA_VERSION
    legacy_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "accomplice_ids", tuple(self.accomplice_ids))
        object.__setattr__(self, "true_timeline", tuple(self.true_timeline))
        object.__setattr__(self, "legacy_metadata", dict(self.legacy_metadata))

    def to_content(self) -> dict[str, Any]:
        return {
            **self.legacy_metadata,
            "schema_version": self.schema_version,
            "culprit_id": self.culprit_id,
            "motive": self.motive,
            "method": self.method,
            "cover_up": self.cover_up,
            "accomplice_ids": list(self.accomplice_ids),
            "true_timeline": list(self.true_timeline),
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> MysteryTruth:
        where = _label("mystery_truth")
        return cls(
            culprit_id=require_str(content.get("culprit_id"), field="culprit_id", where=where),
            motive=str(content.get("motive", "")),
            method=str(content.get("method", "")),
            cover_up=str(content.get("cover_up", "")),
            accomplice_ids=tuple_of_str(content.get("accomplice_ids")),
            true_timeline=tuple_of_str(content.get("true_timeline")),
            schema_version=int(content.get("schema_version", SCHEMA_VERSION)),
            legacy_metadata=split_legacy(
                content,
                frozenset(
                    {
                        "schema_version",
                        "culprit_id",
                        "motive",
                        "method",
                        "cover_up",
                        "accomplice_ids",
                        "true_timeline",
                    }
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class Clue:
    """客观事实、来源、指向的真相、必要性、披露计划（不变量 7）。"""

    statement: str
    source: str = ""
    points_to: tuple[str, ...] = ()
    necessity: bool = False
    disclosure_plan: tuple[str, ...] = ()
    schema_version: int = SCHEMA_VERSION
    legacy_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "points_to", tuple(self.points_to))
        object.__setattr__(self, "disclosure_plan", tuple(self.disclosure_plan))
        object.__setattr__(self, "legacy_metadata", dict(self.legacy_metadata))

    def to_content(self) -> dict[str, Any]:
        return {
            **self.legacy_metadata,
            "schema_version": self.schema_version,
            "statement": self.statement,
            "source": self.source,
            "points_to": list(self.points_to),
            "necessity": self.necessity,
            "disclosure_plan": list(self.disclosure_plan),
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> Clue:
        where = _label("clue")
        return cls(
            statement=require_str(content.get("statement"), field="statement", where=where),
            source=str(content.get("source", "")),
            points_to=tuple_of_str(content.get("points_to")),
            necessity=bool(content.get("necessity", False)),
            disclosure_plan=tuple_of_str(content.get("disclosure_plan")),
            schema_version=int(content.get("schema_version", SCHEMA_VERSION)),
            legacy_metadata=split_legacy(
                content,
                frozenset(
                    {
                        "schema_version",
                        "statement",
                        "source",
                        "points_to",
                        "necessity",
                        "disclosure_plan",
                    }
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class RedHerring:
    """错误解释、真实依据（人物或世界原因）、解除方式（不变量 8）。"""

    false_explanation: str
    basis: str = ""
    resolution: str = ""
    schema_version: int = SCHEMA_VERSION
    legacy_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "legacy_metadata", dict(self.legacy_metadata))

    def to_content(self) -> dict[str, Any]:
        return {
            **self.legacy_metadata,
            "schema_version": self.schema_version,
            "false_explanation": self.false_explanation,
            "basis": self.basis,
            "resolution": self.resolution,
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> RedHerring:
        where = _label("red_herring")
        return cls(
            false_explanation=require_str(
                content.get("false_explanation"), field="false_explanation", where=where
            ),
            basis=str(content.get("basis", "")),
            resolution=str(content.get("resolution", "")),
            schema_version=int(content.get("schema_version", SCHEMA_VERSION)),
            legacy_metadata=split_legacy(
                content,
                frozenset({"schema_version", "false_explanation", "basis", "resolution"}),
            ),
        )


@dataclass(frozen=True, slots=True)
class Foreshadow:
    """建立内容、预期回收、允许的变体。"""

    setup: str
    expected_payoff: str = ""
    allowed_variants: tuple[str, ...] = ()
    schema_version: int = SCHEMA_VERSION
    legacy_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_variants", tuple(self.allowed_variants))
        object.__setattr__(self, "legacy_metadata", dict(self.legacy_metadata))

    def to_content(self) -> dict[str, Any]:
        return {
            **self.legacy_metadata,
            "schema_version": self.schema_version,
            "setup": self.setup,
            "expected_payoff": self.expected_payoff,
            "allowed_variants": list(self.allowed_variants),
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> Foreshadow:
        where = _label("foreshadow")
        return cls(
            setup=require_str(content.get("setup"), field="setup", where=where),
            expected_payoff=str(content.get("expected_payoff", "")),
            allowed_variants=tuple_of_str(content.get("allowed_variants")),
            schema_version=int(content.get("schema_version", SCHEMA_VERSION)),
            legacy_metadata=split_legacy(
                content,
                frozenset({"schema_version", "setup", "expected_payoff", "allowed_variants"}),
            ),
        )


@dataclass(frozen=True, slots=True)
class TimelineEvent:
    """时间、地点、参与者、前后状态。"""

    time: str
    place_id: str = ""
    participants: tuple[str, ...] = ()
    before_state: str = ""
    after_state: str = ""
    schema_version: int = SCHEMA_VERSION
    legacy_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "participants", tuple(self.participants))
        object.__setattr__(self, "legacy_metadata", dict(self.legacy_metadata))

    def to_content(self) -> dict[str, Any]:
        return {
            **self.legacy_metadata,
            "schema_version": self.schema_version,
            "time": self.time,
            "place_id": self.place_id,
            "participants": list(self.participants),
            "before_state": self.before_state,
            "after_state": self.after_state,
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> TimelineEvent:
        where = _label("timeline_event")
        return cls(
            time=require_str(content.get("time"), field="time", where=where),
            place_id=str(content.get("place_id", "")),
            participants=tuple_of_str(content.get("participants")),
            before_state=str(content.get("before_state", "")),
            after_state=str(content.get("after_state", "")),
            schema_version=int(content.get("schema_version", SCHEMA_VERSION)),
            legacy_metadata=split_legacy(
                content,
                frozenset(
                    {
                        "schema_version",
                        "time",
                        "place_id",
                        "participants",
                        "before_state",
                        "after_state",
                    }
                ),
            ),
        )
