"""StoryCatalog：把 Repository Snapshot 投影为故事目录（Phase 13 T13.2）。

领域命令不直接访问存储：application handler 先取 Snapshot，构造
StoryCatalog，再把它交给命令函数。目录提供按 ID 精确定位、按显示名索引与
歧义消解（同名角色允许多个，按名查询多结果时要求用户给 ID）。

本模块是「读」抽象，不产生写入；写路径只经 DomainChange 返回。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from hastings.domain.repository.models import RelationRevision, Snapshot
from hastings.domain.story import error_codes as ec
from hastings.domain.story import kinds
from hastings.domain.story.canon.models import (
    Character,
    Clue,
    Foreshadow,
    MysteryTruth,
    Place,
    RedHerring,
    Relationship,
    StoryPremise,
    TimelineEvent,
    WorldRule,
)
from hastings.domain.story.errors import violation
from hastings.domain.story.mystery.models import Disclosure, KnowledgeState, TruthFact
from hastings.domain.story.narrative.models import Beat, NarrativeNode

__all__ = ["DISPLAY_NAME_FIELDS", "StoryCatalog", "StoryEntry", "display_name", "decode"]

Decoder = Callable[[dict[str, Any]], Any]

_DECODERS: dict[str, Decoder] = {
    kinds.KIND_STORY_PREMISE: StoryPremise.from_content,
    kinds.KIND_CHARACTER: Character.from_content,
    kinds.KIND_RELATIONSHIP: Relationship.from_content,
    kinds.KIND_PLACE: Place.from_content,
    kinds.KIND_WORLD_RULE: WorldRule.from_content,
    kinds.KIND_MYSTERY_TRUTH: MysteryTruth.from_content,
    kinds.KIND_CLUE: Clue.from_content,
    kinds.KIND_RED_HERRING: RedHerring.from_content,
    kinds.KIND_FORESHADOW: Foreshadow.from_content,
    kinds.KIND_TIMELINE_EVENT: TimelineEvent.from_content,
    kinds.KIND_TRUTH_FACT: TruthFact.from_content,
    kinds.KIND_KNOWLEDGE: KnowledgeState.from_content,
    kinds.KIND_DISCLOSURE: Disclosure.from_content,
    kinds.KIND_SERIES: NarrativeNode.from_content,
    kinds.KIND_BOOK: NarrativeNode.from_content,
    kinds.KIND_ARC: NarrativeNode.from_content,
    kinds.KIND_CHAPTER: NarrativeNode.from_content,
    kinds.KIND_SCENE: NarrativeNode.from_content,
    kinds.KIND_BEAT: Beat.from_content,
}

# 供显示名索引使用的「名称字段」；未列出的 kind 无稳定显示名
DISPLAY_NAME_FIELDS: dict[str, str] = {
    kinds.KIND_CHARACTER: "name",
    kinds.KIND_PLACE: "name",
    kinds.KIND_STORY_PREMISE: "premise",
    kinds.KIND_WORLD_RULE: "rule",
    kinds.KIND_CLUE: "statement",
    kinds.KIND_RED_HERRING: "false_explanation",
    kinds.KIND_FORESHADOW: "setup",
    kinds.KIND_TRUTH_FACT: "statement",
    kinds.KIND_TIMELINE_EVENT: "time",
    kinds.KIND_SERIES: "title",
    kinds.KIND_BOOK: "title",
    kinds.KIND_ARC: "title",
    kinds.KIND_CHAPTER: "title",
    kinds.KIND_SCENE: "title",
    kinds.KIND_BEAT: "title",
}


def decode(kind: str, content: dict[str, Any]) -> Any:
    """按 kind 解码为领域模型；未注册 kind 返回原始 content。"""
    decoder = _DECODERS.get(kind)
    if decoder is None:
        return content
    return decoder(content)


def display_name(kind: str, content: dict[str, Any]) -> str:
    """从 content 中取显示名；无名称字段的 kind 返回空串。"""
    field = DISPLAY_NAME_FIELDS.get(kind)
    if field is None:
        return ""
    value = content.get(field, "")
    return value if isinstance(value, str) else str(value)


@dataclass(frozen=True, slots=True)
class StoryEntry:
    """Snapshot 中单个故事对象：身份 + 有效 Revision + 内容。"""

    artifact_id: str
    kind: str
    revision_id: str
    content: dict[str, Any]

    def decode(self) -> Any:
        return decode(self.kind, self.content)


@dataclass(frozen=True, slots=True)
class StoryCatalog:
    """某 Commit 的故事目录（只读）。commit_id 为 None 表示空目录。

    ``relations`` 保留该 Commit 的依赖边（如 ``supersedes``），供查询投影
    排除被替代的旧事实；命令层不据此写入。
    """

    commit_id: str | None
    entries: tuple[StoryEntry, ...]
    relations: tuple[RelationRevision, ...] = ()

    # ------------------------------------------------------------------
    # 构造与查询
    # ------------------------------------------------------------------
    @classmethod
    def from_snapshot(cls, snapshot: Snapshot) -> StoryCatalog:
        entries: list[StoryEntry] = []
        for view in snapshot.artifacts.values():
            if view.revision is None:
                continue
            if not kinds.is_story_kind(view.artifact.kind):
                continue
            entries.append(
                StoryEntry(
                    artifact_id=view.artifact.id.value,
                    kind=view.artifact.kind,
                    revision_id=view.revision.id.value,
                    content=dict(view.revision.content),
                )
            )
        entries.sort(key=lambda e: e.artifact_id)
        return cls(
            commit_id=snapshot.commit_id.value,
            entries=tuple(entries),
            relations=snapshot.relations,
        )

    def by_kind(self, kind: str) -> tuple[StoryEntry, ...]:
        return tuple(e for e in self.entries if e.kind == kind)

    def get(self, artifact_id: str) -> StoryEntry | None:
        return next((e for e in self.entries if e.artifact_id == artifact_id), None)

    def require(self, artifact_id: str, *, where: str) -> StoryEntry:
        entry = self.get(artifact_id)
        if entry is None:
            raise violation(
                ec.STORY_UNKNOWN_ARTIFACT,
                where,
                f"引用的对象 {artifact_id!r} 在当前快照中不存在",
                "改用快照内存在的 ID，或先创建该对象再引用",
                artifact_id=artifact_id,
            )
        return entry

    def names(self, kind: str) -> dict[str, tuple[str, ...]]:
        """返回 kind 内的「显示名 -> 该名下的所有 ID」索引（同名可多个）。"""
        index: dict[str, list[str]] = {}
        for entry in self.by_kind(kind):
            name = display_name(entry.kind, entry.content)
            if name:
                index.setdefault(name, []).append(entry.artifact_id)
        return {name: tuple(ids) for name, ids in index.items()}

    def resolve_name(self, kind: str, name: str, *, where: str) -> str:
        """按显示名解析到唯一 ID；同名多结果时要求用户选择（歧义错误）。"""
        candidates = self.names(kind).get(name, ())
        if not candidates:
            raise violation(
                ec.STORY_UNKNOWN_ARTIFACT,
                where,
                f"{kind} 中不存在显示名为 {name!r} 的对象",
                "换成已存在的显示名，或先创建该对象",
                name=name,
                kind=kind,
            )
        if len(candidates) > 1:
            raise violation(
                ec.STORY_AMBIGUOUS_NAME,
                where,
                f"{kind} 中显示名 {name!r} 对应 {len(candidates)} 个对象，无法唯一确定",
                "改用 ArtifactId 指定其中一个；同名对象允许共存，但按名定位必须唯一",
                name=name,
                kind=kind,
                candidates=list(candidates),
            )
        return candidates[0]

    def beats(self) -> tuple[StoryEntry, ...]:
        return self.by_kind(kinds.KIND_BEAT)

    def nodes(self) -> tuple[StoryEntry, ...]:
        result: list[StoryEntry] = []
        for kind in kinds.NARRATIVE_NODE_KINDS:
            result.extend(self.by_kind(kind))
        return tuple(result)

    def canon(self) -> tuple[StoryEntry, ...]:
        result: list[StoryEntry] = []
        for kind in kinds.CANON_KINDS:
            result.extend(self.by_kind(kind))
        return tuple(result)
