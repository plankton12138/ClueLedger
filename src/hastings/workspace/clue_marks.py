"""正文线索下划线的只读投影：优先使用已提交账本证据，缺失时回退到正文段落锚点。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from hastings.workspace.clue_models import ClueContent, ClueEvidence, ClueMode
from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.story_clue_catalog import StoryClueCatalog
from hastings.workspace.story_models import StoryNode, StoryVariant
from hastings.workspace.story_sync import current_span

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
    #: ledger 来自已提交账本证据；anchor 来自正文段落锚点与节点线索用途。
    source: Literal["ledger", "anchor"] = "ledger"


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


def _use_role(roles: list[str]) -> MarkRole:
    if any(role in ("resolve", "reinterpret") for role in roles):
        return "resolve"
    if any(role in ("plant", "misdirect") for role in roles):
        return "plant"
    return "mention"


_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_STOP_CHARS = set("的了与和在地是有无不可他她它我你这那一个又再就都也很更最一二三四五六七八九十")
_STOP_BIGRAMS = {
    "第1",
    "一章",
    "一个",
    "没有",
    "什么",
    "现在",
    "他们",
    "我们",
    "自己",
    "这个",
    "那个",
    "可以",
    "已经",
    "因为",
    "所以",
    "就是",
    "不是",
    "只是",
    "如果",
    "但是",
    "然后",
    "以及",
    "当时",
    "之前",
    "之后",
}


def _clue_tokens(node: StoryNode, catalog: StoryClueCatalog) -> dict[str, dict[str, int]]:
    """按节点内的线索用途提取区分性词；出现于多条线索的词不参与匹配。"""
    raw: dict[str, dict[str, int]] = {}
    texts = {
        use.clue_id: f"{catalog.title(use.clue_id)} {use.presentation}" for use in node.clue_uses
    }
    for use in node.clue_uses:
        title = catalog.title(use.clue_id)
        other = " ".join(text for clue_id, text in texts.items() if clue_id != use.clue_id)
        tokens: dict[str, int] = {}
        for run in _CJK_RE.findall(f"{title} {use.presentation}"):
            for index in range(len(run) - 1):
                bigram = run[index : index + 2]
                if bigram not in _STOP_BIGRAMS:
                    tokens[bigram] = 2
        for char in title:
            if char in _STOP_CHARS or not _CJK_RE.match(char) or char in other:
                continue
            tokens.setdefault(char, 1)
        raw[use.clue_id] = tokens
    counts: dict[str, int] = {}
    for tokens in raw.values():
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
    return {
        clue_id: {token: weight for token, weight in tokens.items() if counts[token] == 1}
        for clue_id, tokens in raw.items()
    }


def _best_clue(segment: str, token_maps: dict[str, dict[str, int]]) -> str | None:
    best_id = ""
    best_score = 0
    tied = False
    for clue_id, tokens in token_maps.items():
        score = sum(weight for token, weight in tokens.items() if token in segment)
        if score > best_score:
            best_id, best_score, tied = clue_id, score, False
        elif score == best_score and score > 0:
            tied = True
    return "" if tied else best_id or None


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
    if variant is not None:
        catalog = StoryClueCatalog.from_root(variant, root)
        chapter_number = entry.chapter_number or 0
        if as_of_chapter is not None and chapter_number > as_of_chapter:
            chapter_number = 0
        for binding in variant.bindings.values():
            if not chapter_number or binding.path != chapter_path:
                continue
            node = variant.nodes.get(binding.node_id)
            if node is None:
                continue
            span = current_span(binding, file.text)
            if span is None:
                continue
            start, end, _ = span
            segment = file.text[start:end]
            token_maps = _clue_tokens(node, catalog)
            best_clue = _best_clue(segment, token_maps)
            explicit = binding.clue_id if binding.clue_id in token_maps else ""
            for use in node.clue_uses:
                presentation = use.presentation.strip()
                local = segment.find(presentation) if presentation else -1
                if local >= 0:
                    mark_start = start + local
                    mark_end = mark_start + len(presentation)
                    mark_quote = presentation
                elif (
                    (explicit and use.clue_id == explicit)
                    or len(node.clue_uses) == 1
                    or best_clue == use.clue_id
                ):
                    # 构思里的呈现句常是摘要；匹配到具体线索时退到已锚定的正文句子。
                    mark_start, mark_end, mark_quote = start, end, binding.quote
                else:
                    continue
                key = (use.clue_id, mark_start, mark_end)
                if key in marks:
                    continue
                marks[key] = ClueMark(
                    clue_id=use.clue_id,
                    title=catalog.title(use.clue_id),
                    start=mark_start,
                    end=mark_end,
                    quote=mark_quote,
                    role=_use_role(use.roles),
                    mode=use.mode,
                    chapter=entry.chapter_number or 0,
                    source="anchor",
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
        ledger_revision=snapshot.revision,
    )
