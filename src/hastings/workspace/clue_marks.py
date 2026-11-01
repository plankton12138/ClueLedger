"""正文里的线索标记：只投影已经登记的精确出处，不做词项猜测。

标记有两个来源，合并后按 ``clue_id + 区间`` 去重：

* 作者或候选登记的正文本关联（``ProseBinding.clue_id``），带精确码点区间；
* 账本里已提交的正文观察，只在当前正文仍与该贡献记录的摘要一致时显示。

引文在当前正文里找不到或出现多次时，不猜测位置，改为返回失效条目说明原因。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from hastings.workspace.clue_models import ClueContent, ClueEvidence
from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.story_bindings import anchor_state
from hastings.workspace.story_models import ClueKind, StoryVariant

__all__ = ["ClueMark", "ClueMarksView", "InvalidClueMark", "build_clue_marks"]

MarkSource = Literal["binding", "ledger"]
MarkState = Literal["ready", "empty", "stale", "unrecorded"]


class ClueMark(BaseModel):
    """一处可见标记；同一区间同时来自作者关联与账本观察时合并成一条。"""

    clue_id: str
    title: str
    kind: ClueKind | None = None
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str
    mode: str = "unknown"
    chapter: int = 0
    sources: list[MarkSource] = Field(default_factory=list)


class InvalidClueMark(BaseModel):
    """引文失效或有多处匹配：保留原引用与原因，不给猜测位置。"""

    clue_id: str
    title: str
    quote: str
    reason: Literal["missing", "ambiguous", "unavailable"]


class ClueMarksView(BaseModel):
    path: str
    digest: str
    state: MarkState
    marks: list[ClueMark] = Field(default_factory=list)
    invalid: list[InvalidClueMark] = Field(default_factory=list)
    ledger_revision: str = ""
    variant_revision: str = ""


def _invalid_reason(state: str) -> Literal["missing", "ambiguous", "unavailable"]:
    if state == "ambiguous":
        return "ambiguous"
    if state == "unavailable":
        return "unavailable"
    return "missing"


def _proofs(content: ClueContent, path: str) -> list[ClueEvidence]:
    rows: list[ClueEvidence] = []
    if content.evidence.path == path:
        rows.append(content.evidence)
    if content.mention_evidence is not None and content.mention_evidence.path == path:
        rows.append(content.mention_evidence)
    if (
        content.resolution_evidence is not None
        and content.resolution_evidence.path == path
        and content.resolution_chapter == content.source_chapter
    ):
        rows.append(content.resolution_evidence)
    return rows


def build_clue_marks(
    workspace: Workspace,
    root: Path,
    *,
    chapter_path: str,
    as_of_chapter: int | None = None,
    variant: StoryVariant | None = None,
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
    chapter_number = entry.chapter_number
    marks: dict[tuple[str, int, int], ClueMark] = {}
    invalid: list[InvalidClueMark] = []

    def title_of(clue_id: str) -> str:
        identity = ledger.clues.get(clue_id) if ledger is not None else None
        return identity.title if identity is not None else clue_id

    def kind_of(clue_id: str) -> ClueKind | None:
        if variant is None:
            return None
        plan = variant.clue_plans.get(clue_id)
        return plan.kind if plan is not None else None

    def add(mark: ClueMark) -> None:
        key = (mark.clue_id, mark.start, mark.end)
        previous = marks.get(key)
        if previous is None:
            marks[key] = mark
            return
        merged = sorted({*previous.sources, *mark.sources})
        marks[key] = previous.model_copy(update={"sources": merged})

    if variant is not None and (as_of_chapter is None or chapter_number <= as_of_chapter):
        for binding in variant.bindings.values():
            if binding.clue_id is None or binding.path != chapter_path:
                continue
            anchor = anchor_state(binding, file.text, file.digest)
            if anchor.span is None:
                invalid.append(
                    InvalidClueMark(
                        clue_id=binding.clue_id,
                        title=title_of(binding.clue_id),
                        quote=binding.quote,
                        reason=_invalid_reason(anchor.state),
                    )
                )
                continue
            add(
                ClueMark(
                    clue_id=binding.clue_id,
                    title=title_of(binding.clue_id),
                    kind=kind_of(binding.clue_id),
                    start=anchor.span.start,
                    end=anchor.span.end,
                    quote=anchor.span.quote,
                    chapter=chapter_number,
                    sources=["binding"],
                )
            )

    if ledger is not None:
        for contribution in ledger.contributions.values():
            content = contribution.content
            if contribution.source_path != chapter_path:
                continue
            if as_of_chapter is not None and content.source_chapter > as_of_chapter:
                continue
            if contribution.source_digest != file.digest:
                continue
            for proof in _proofs(content, chapter_path):
                if file.text[proof.start : proof.end] != proof.quote:
                    continue
                add(
                    ClueMark(
                        clue_id=contribution.clue_id,
                        title=title_of(contribution.clue_id),
                        kind=kind_of(contribution.clue_id),
                        start=proof.start,
                        end=proof.end,
                        quote=proof.quote,
                        mode=contribution.mode,
                        chapter=content.source_chapter or chapter_number,
                        sources=["ledger"],
                    )
                )

    ordered = sorted(marks.values(), key=lambda row: (row.start, row.end, row.title))
    coverage = ledger.coverage.get(chapter_path) if ledger is not None else None
    if ordered:
        state: MarkState = "ready"
    elif coverage is None:
        state = "unrecorded"
    elif coverage.digest != file.digest:
        state = "stale"
    else:
        state = "empty"
    return ClueMarksView(
        path=chapter_path,
        digest=file.digest,
        state=state,
        marks=ordered,
        invalid=invalid,
        ledger_revision=snapshot.revision,
        variant_revision=variant.revision if variant is not None else "",
    )
