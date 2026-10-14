"""Narrative Plan 领域模型（modules/story-domain.md §2.2）。

结构为 Series → Book → Arc → Chapter → Scene → Beat。节点顺序使用可重排
`order_key`，不把「第几章」作为永久身份；Beat 是状态折叠的最小单位。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hastings.domain.story import error_codes as ec
from hastings.domain.story.errors import violation
from hastings.domain.story.schema import (
    SCHEMA_VERSION,
    require_str,
    split_legacy,
    tuple_of_str,
)

__all__ = ["Beat", "DisclosureSpec", "NarrativeNode", "StateChange"]

# 状态变化种类
STATE_KNOWLEDGE = "knowledge"
STATE_RELATIONSHIP = "relationship"
STATE_CUSTOM = "custom"

# 认知模式：knows 提前于真相揭示即为违规；misbelieves 是合法误解
KNOWLEDGE_MODES = frozenset(("knows", "suspects", "misbelieves", "unaware"))

AUDIENCE_READER = "reader"


@dataclass(frozen=True, slots=True)
class StateChange:
    """Beat 内的一次状态变化；折叠时按结构顺序应用（modules §2.3）。"""

    kind: str
    value: str
    character_id: str | None = None
    target_id: str | None = None
    note: str = ""

    def to_content(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "value": self.value,
            "character_id": self.character_id,
            "target_id": self.target_id,
            "note": self.note,
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> StateChange:
        return cls(
            kind=str(content.get("kind", STATE_CUSTOM)),
            value=str(content.get("value", "")),
            character_id=content.get("character_id"),
            target_id=content.get("target_id"),
            note=str(content.get("note", "")),
        )


@dataclass(frozen=True, slots=True)
class DisclosureSpec:
    """Beat 向某受众披露事实，及以何种解释获得（真/误导）。"""

    fact_id: str
    audience: str = AUDIENCE_READER
    interpretation: str = "true"

    def to_content(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "audience": self.audience,
            "interpretation": self.interpretation,
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> DisclosureSpec:
        return cls(
            fact_id=str(content.get("fact_id", "")),
            audience=str(content.get("audience", AUDIENCE_READER)),
            interpretation=str(content.get("interpretation", "true")),
        )


@dataclass(frozen=True, slots=True)
class NarrativeNode:
    """结构节点（Series/Book/Arc/Chapter/Scene）；kind 创建后不可变。"""

    node_id: str
    kind: str
    title: str = ""
    parent_id: str | None = None
    order_key: str = ""
    schema_version: int = SCHEMA_VERSION
    legacy_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "legacy_metadata", dict(self.legacy_metadata))

    def to_content(self) -> dict[str, Any]:
        return {
            **self.legacy_metadata,
            "schema_version": self.schema_version,
            "node_id": self.node_id,
            "kind": self.kind,
            "title": self.title,
            "parent_id": self.parent_id,
            "order_key": self.order_key,
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> NarrativeNode:
        where = "narrative.node"
        return cls(
            node_id=require_str(content.get("node_id"), field="node_id", where=where),
            kind=require_str(content.get("kind"), field="kind", where=where),
            title=str(content.get("title", "")),
            parent_id=content.get("parent_id"),
            order_key=str(content.get("order_key", "")),
            schema_version=int(content.get("schema_version", SCHEMA_VERSION)),
            legacy_metadata=split_legacy(
                content,
                frozenset({"schema_version", "node_id", "kind", "title", "parent_id", "order_key"}),
            ),
        )


@dataclass(frozen=True, slots=True)
class Beat:
    """节拍：叙事与状态折叠的最小单位（modules §2.2）。"""

    beat_id: str
    parent_id: str | None = None
    title: str = ""
    purpose: str = ""
    pov_character_id: str = ""
    participants: tuple[str, ...] = ()
    preconditions: tuple[str, ...] = ()
    state_changes: tuple[StateChange, ...] = ()
    reader_disclosures: tuple[DisclosureSpec, ...] = ()
    setups: tuple[str, ...] = ()
    payoffs: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    order_key: str = ""
    schema_version: int = SCHEMA_VERSION
    legacy_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "participants", tuple(self.participants))
        object.__setattr__(self, "preconditions", tuple(self.preconditions))
        object.__setattr__(self, "state_changes", tuple(self.state_changes))
        object.__setattr__(self, "reader_disclosures", tuple(self.reader_disclosures))
        object.__setattr__(self, "setups", tuple(self.setups))
        object.__setattr__(self, "payoffs", tuple(self.payoffs))
        object.__setattr__(self, "depends_on", tuple(self.depends_on))
        object.__setattr__(self, "constraints", tuple(self.constraints))
        object.__setattr__(self, "legacy_metadata", dict(self.legacy_metadata))

    def to_content(self) -> dict[str, Any]:
        return {
            **self.legacy_metadata,
            "schema_version": self.schema_version,
            "beat_id": self.beat_id,
            "parent_id": self.parent_id,
            "title": self.title,
            "purpose": self.purpose,
            "pov_character_id": self.pov_character_id,
            "participants": list(self.participants),
            "preconditions": list(self.preconditions),
            "state_changes": [c.to_content() for c in self.state_changes],
            "reader_disclosures": [d.to_content() for d in self.reader_disclosures],
            "setups": list(self.setups),
            "payoffs": list(self.payoffs),
            "depends_on": list(self.depends_on),
            "constraints": list(self.constraints),
            "order_key": self.order_key,
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> Beat:
        where = "narrative.beat"
        raw_changes = content.get("state_changes") or []
        raw_disclosures = content.get("reader_disclosures") or []
        if not isinstance(raw_changes, list) or not isinstance(raw_disclosures, list):
            raise violation(
                ec.STORY_INVALID_COMMAND,
                where,
                "state_changes/reader_disclosures 必须是列表",
                "把二者改为对象列表，每个元素含 kind/value 或 fact_id",
            )
        return cls(
            beat_id=require_str(content.get("beat_id"), field="beat_id", where=where),
            parent_id=content.get("parent_id"),
            title=str(content.get("title", "")),
            purpose=str(content.get("purpose", "")),
            pov_character_id=str(content.get("pov_character_id", "")),
            participants=tuple_of_str(content.get("participants")),
            preconditions=tuple_of_str(content.get("preconditions")),
            state_changes=tuple(StateChange.from_content(c) for c in raw_changes),
            reader_disclosures=tuple(DisclosureSpec.from_content(d) for d in raw_disclosures),
            setups=tuple_of_str(content.get("setups")),
            payoffs=tuple_of_str(content.get("payoffs")),
            depends_on=tuple_of_str(content.get("depends_on")),
            constraints=tuple_of_str(content.get("constraints")),
            order_key=str(content.get("order_key", "")),
            schema_version=int(content.get("schema_version", SCHEMA_VERSION)),
            legacy_metadata=split_legacy(
                content,
                frozenset(
                    {
                        "schema_version",
                        "beat_id",
                        "parent_id",
                        "title",
                        "purpose",
                        "pov_character_id",
                        "participants",
                        "preconditions",
                        "state_changes",
                        "reader_disclosures",
                        "setups",
                        "payoffs",
                        "depends_on",
                        "constraints",
                        "order_key",
                    }
                ),
            ),
        )
