"""正文线索下划线的只读投影：只使用已经提交的账本证据。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from hastings.workspace.clue_models import ClueContent, ClueEvidence, ClueMode
from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace

__all__ = ["ClueMark", "ClueMarksView", "build_clue_marks"]

MarkRole = Literal["plant", "mention", "resolve"]
MarkState = Literal["ready", "empty", "stale", "unrecorded"]

_ROLE_PRIORITY = {"plant": 3, "resolve": 2, "mention": 1}


class ClueMark(BaseModel):
    clue_id: str
    title: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str
    role: MarkRole
    mode: ClueMode = "unknown"
    chapter: int


class ClueMarksView(BaseModel):
    path: str
    digest: str
    state: MarkState
    marks: list[ClueMark] = Field(default_factory=list)
    ledger_revision: str = ""


def _proofs(content: ClueContent, path: str) -> list[tuple[MarkRole, ClueEvidence]]:
    result: list[tuple[MarkRole, ClueEvidence]] = []
    if content.evidence.path == path:
        role: MarkRole = "plant" if content.source_chapter == content.planted_chapter else "mention"
        result.append((role, content.evidence))
    if content.mention_evidence is not None and content.mention_evidence.path == path:
        result.append(("mention", content.mention_evidence))
    if (
        content.resolution_evidence is not None
        and content.resolution_evidence.path == path
        and content.resolution_chapter == content.source_chapter
    ):
        result.append(("resolve", content.resolution_evidence))
    return result


def build_clue_marks(
    workspace: Workspace,
    root: Path,
    *,
    chapter_path: str,
    as_of_chapter: int | None = None,
) -> ClueMarksView:
    entry = next(
        (row for row in workspace.catalog().chapters if row.path == chapter_path and row.exists),
        None,
    )
    if entry is None or entry.chapter_number is None:
        raise WorkspaceError(
            code="CLUE_CHAPTER_NOT_FOUND",
            where="clues.marks",
            why=f"所选路径不是当前正文章节：{chapter_path}",
            fix="刷新目录后重新选择章节",
            details={"path": chapter_path},
        )
    file = workspace.read(chapter_path)
    snapshot = ClueLedgerStore(root).read()
    ledger = snapshot.ledger
    marks: dict[tuple[str, int, int], ClueMark] = {}
    if ledger is not None:
        identities = ledger.clues
        for contribution in ledger.contributions.values():
            content = contribution.content
            if as_of_chapter is not None and content.source_chapter > as_of_chapter:
                continue
            if contribution.source_path != chapter_path:
                continue
            if contribution.source_digest != file.digest:
                continue
            identity = identities.get(contribution.clue_id)
            title = identity.title if identity is not None else content.clue
            for role, proof in _proofs(content, chapter_path):
                if file.text[proof.start : proof.end] != proof.quote:
                    continue
                key = (contribution.clue_id, proof.start, proof.end)
                previous = marks.get(key)
                if previous is not None and _ROLE_PRIORITY[previous.role] >= _ROLE_PRIORITY[role]:
                    continue
                marks[key] = ClueMark(
                    clue_id=contribution.clue_id,
                    title=title,
                    start=proof.start,
                    end=proof.end,
                    quote=proof.quote,
                    role=role,
                    mode=contribution.mode,
                    chapter=content.source_chapter or entry.chapter_number,
                )
    ordered = sorted(marks.values(), key=lambda row: (row.start, row.end, row.title))
    coverage = ledger.coverage.get(chapter_path) if ledger is not None else None
    if coverage is None:
        state: MarkState = "unrecorded"
    elif coverage.digest != file.digest:
        state = "stale"
    elif ordered:
        state = "ready"
    else:
        state = "empty"
    return ClueMarksView(
        path=chapter_path,
        digest=file.digest,
        state=state,
        marks=ordered,
        ledger_revision=snapshot.revision,
    )
