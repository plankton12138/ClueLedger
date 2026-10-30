"""构思线索的统一读取事实：身份来自账本，计划来自构思。"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.story_models import StoryCluePlan, StoryVariant

if TYPE_CHECKING:
    from hastings.workspace.clue_store import ClueSnapshot

__all__ = ["StoryClueCatalog", "StoryClueFact"]


@dataclass(frozen=True, slots=True)
class StoryClueFact:
    clue_id: str
    title: str
    plan: StoryCluePlan | None
    identity_revision: str = ""
    has_identity: bool = False


@dataclass(frozen=True, slots=True)
class StoryClueCatalog:
    facts: Mapping[str, StoryClueFact]
    ledger_revision: str = ""

    @classmethod
    def from_variant(
        cls, variant: StoryVariant, snapshot: ClueSnapshot | None = None
    ) -> "StoryClueCatalog":
        identities = snapshot.ledger.clues if snapshot and snapshot.ledger else {}
        ids = set(variant.clue_plans)
        ids.update(identity_id for identity_id in identities)
        for node in variant.nodes.values():
            ids.update(use.clue_id for use in node.clue_uses)
        facts: dict[str, StoryClueFact] = {}
        for clue_id in sorted(ids):
            identity = identities.get(clue_id)
            facts[clue_id] = StoryClueFact(
                clue_id=clue_id,
                title=identity.title if identity is not None else clue_id,
                plan=variant.clue_plans.get(clue_id),
                identity_revision=identity.clue_revision if identity is not None else "",
                has_identity=identity is not None,
            )
        return cls(
            facts=facts,
            ledger_revision=snapshot.revision if snapshot is not None else "",
        )

    @classmethod
    def from_root(
        cls, variant: StoryVariant, root: Path
    ) -> "StoryClueCatalog":
        from hastings.workspace.clue_store import ClueLedgerStore

        return cls.from_variant(variant, ClueLedgerStore(root).read())

    def __iter__(self) -> Iterator[StoryClueFact]:
        return iter(self.facts.values())

    def get(self, clue_id: str) -> StoryClueFact | None:
        return self.facts.get(clue_id)

    def title(self, clue_id: str) -> str:
        fact = self.facts.get(clue_id)
        return fact.title if fact is not None else clue_id

    def plan(self, clue_id: str) -> StoryCluePlan | None:
        fact = self.facts.get(clue_id)
        return fact.plan if fact is not None else None

    def require_identity(self, clue_id: str) -> StoryClueFact:
        fact = self.facts.get(clue_id)
        if fact is None or not fact.has_identity:
            raise WorkspaceError(
                code="STORY_CLUE_REFERENCE_INVALID",
                where="story.clue",
                why=f"线索身份不存在：{clue_id}",
                fix="先在同一变更中创建身份，或重新读取线索后选择已有身份",
                details={"clue_id": clue_id},
            )
        return fact
