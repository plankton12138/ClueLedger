"""悬疑三层事实模型（modules/story-domain.md §2.3）。

真相（TruthFact）/ 角色认知（KnowledgeState，含合法误解模式 misbelieves）/
读者披露（Disclosure）三者相互独立，各自以 Artifact 存储，不共用字段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hastings.domain.story.schema import (
    SCHEMA_VERSION,
    require_str,
    split_legacy,
)

__all__ = ["Disclosure", "KnowledgeState", "TruthFact"]

# 认知模式：knows 必须在真相揭示之后；misbelieves/suspects 是合法认知
KNOWLEDGE_KNOWS = "knows"
KNOWLEDGE_SUSPECTS = "suspects"
KNOWLEDGE_MISBELIEVES = "misbelieves"
KNOWLEDGE_UNAWARE = "unaware"

KNOWLEDGE_MODES = frozenset(
    (KNOWLEDGE_KNOWS, KNOWLEDGE_SUSPECTS, KNOWLEDGE_MISBELIEVES, KNOWLEDGE_UNAWARE)
)


@dataclass(frozen=True, slots=True)
class TruthFact:
    """故事世界中实际发生的事情；可关联 TimelineEvent。"""

    statement: str
    event_id: str | None = None
    schema_version: int = SCHEMA_VERSION
    legacy_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "legacy_metadata", dict(self.legacy_metadata))

    def to_content(self) -> dict[str, Any]:
        return {
            **self.legacy_metadata,
            "schema_version": self.schema_version,
            "statement": self.statement,
            "event_id": self.event_id,
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> TruthFact:
        where = "mystery.truth_fact"
        return cls(
            statement=require_str(content.get("statement"), field="statement", where=where),
            event_id=content.get("event_id"),
            schema_version=int(content.get("schema_version", SCHEMA_VERSION)),
            legacy_metadata=split_legacy(
                content, frozenset({"schema_version", "statement", "event_id"})
            ),
        )


@dataclass(frozen=True, slots=True)
class KnowledgeState:
    """某角色对某事实在某 Beat 的认知状态（modules §2.3 角色认知层）。"""

    character_id: str
    fact_id: str
    mode: str = KNOWLEDGE_UNAWARE
    at_beat_id: str | None = None
    note: str = ""
    schema_version: int = SCHEMA_VERSION
    legacy_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "legacy_metadata", dict(self.legacy_metadata))

    def to_content(self) -> dict[str, Any]:
        return {
            **self.legacy_metadata,
            "schema_version": self.schema_version,
            "character_id": self.character_id,
            "fact_id": self.fact_id,
            "mode": self.mode,
            "at_beat_id": self.at_beat_id,
            "note": self.note,
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> KnowledgeState:
        where = "mystery.knowledge"
        return cls(
            character_id=require_str(
                content.get("character_id"), field="character_id", where=where
            ),
            fact_id=require_str(content.get("fact_id"), field="fact_id", where=where),
            mode=str(content.get("mode", KNOWLEDGE_UNAWARE)),
            at_beat_id=content.get("at_beat_id"),
            note=str(content.get("note", "")),
            schema_version=int(content.get("schema_version", SCHEMA_VERSION)),
            legacy_metadata=split_legacy(
                content,
                frozenset(
                    {"schema_version", "character_id", "fact_id", "mode", "at_beat_id", "note"}
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class Disclosure:
    """读者在何时、以何种解释获得某事实（modules §2.3 读者披露层）。"""

    fact_id: str
    audience: str = "reader"
    at_beat_id: str | None = None
    interpretation: str = "true"
    note: str = ""
    schema_version: int = SCHEMA_VERSION
    legacy_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "legacy_metadata", dict(self.legacy_metadata))

    def to_content(self) -> dict[str, Any]:
        return {
            **self.legacy_metadata,
            "schema_version": self.schema_version,
            "fact_id": self.fact_id,
            "audience": self.audience,
            "at_beat_id": self.at_beat_id,
            "interpretation": self.interpretation,
            "note": self.note,
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> Disclosure:
        where = "mystery.disclosure"
        return cls(
            fact_id=require_str(content.get("fact_id"), field="fact_id", where=where),
            audience=str(content.get("audience", "reader")),
            at_beat_id=content.get("at_beat_id"),
            interpretation=str(content.get("interpretation", "true")),
            note=str(content.get("note", "")),
            schema_version=int(content.get("schema_version", SCHEMA_VERSION)),
            legacy_metadata=split_legacy(
                content,
                frozenset(
                    {
                        "schema_version",
                        "fact_id",
                        "audience",
                        "at_beat_id",
                        "interpretation",
                        "note",
                    }
                ),
            ),
        )
