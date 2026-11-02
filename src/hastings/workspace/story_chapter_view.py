"""正文随章聚合读取：当前章实际出现的构思与线索、明确计划项和失效引用。

这一层只读：从当前方案的正文关联、账本证据与章节索引投影出一页条目，不调用模型、
不建立副本，也不把计划项伪装成已经出现。空列表是合法结果；文件不可读或服务不可用时
按错误返回，不能伪装成「本章没有相关内容」。
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from hastings.workspace.clue_models import ClueContent, ClueEvidence
from hastings.workspace.clue_store import ClueLedgerStore, ClueSnapshot
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import FileEntry, Workspace
from hastings.workspace.source_locations import file_evidence_id
from hastings.workspace.story_bindings import anchor_state
from hastings.workspace.story_clue_catalog import StoryClueCatalog
from hastings.workspace.story_models import ProseBinding, StoryVariant
from hastings.workspace.story_store import StorySnapshot, StoryStore

__all__ = [
    "CHAPTER_CONTEXT_LIMIT",
    "ChapterContextItem",
    "ChapterContextView",
    "ChapterObject",
    "ChapterOccurrence",
    "chapter_context",
]

CHAPTER_CONTEXT_LIMIT = 50

Placement = Literal["observed", "planned", "unresolved"]
OccurrenceState = Literal["exact", "relocated", "missing", "ambiguous", "unavailable"]


class ChapterObject(BaseModel):
    kind: Literal["node", "clue"]
    id: str


class ChapterOccurrence(BaseModel):
    path: str
    quote: str
    start: int | None = None
    end: int | None = None
    state: OccurrenceState
    binding_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class ChapterContextItem(BaseModel):
    object: ChapterObject
    title: str
    placement: Placement
    occurrences: list[ChapterOccurrence] = Field(default_factory=list)


class ChapterContextView(BaseModel):
    chapter_path: str
    chapter_digest: str
    variant_id: str | None = None
    story_revision: str | None = None
    ledger_revision: str = ""
    view_revision: str
    items: list[ChapterContextItem] = Field(default_factory=list)
    next_cursor: str = ""
    truncated: bool = False


def _cursor_error() -> WorkspaceError:
    return WorkspaceError(
        code="CHAPTER_CONTEXT_CURSOR_CONFLICT",
        where="story.chapter",
        why="分页游标与当前章节、方案或修订不一致",
        fix="重新读取本章第一页",
    )


def _occurrence(
    binding: ProseBinding,
    text: str,
    digest: str,
    seen: dict[tuple[str, int | None, int | None, str], ChapterOccurrence],
) -> None:
    anchor = anchor_state(binding, text, digest)
    start = anchor.span.start if anchor.span is not None else None
    end = anchor.span.end if anchor.span is not None else None
    key = (binding.path, start, end, binding.quote)
    row = seen.get(key)
    if row is None:
        seen[key] = ChapterOccurrence(
            path=binding.path,
            quote=binding.quote,
            start=start,
            end=end,
            state=anchor.state,
            binding_ids=[binding.id],
        )
        return
    row.binding_ids = sorted({*row.binding_ids, binding.id})
    if row.state != "exact" and anchor.state == "exact":
        row.state = "exact"


def _add_evidence(
    seen: dict[tuple[str, int | None, int | None, str], ChapterOccurrence],
    *,
    workspace: Workspace,
    snapshot: ClueSnapshot,
    clue_id: str,
    source_chapter: int,
    role: str,
    proof: ClueEvidence,
    current_text: str,
    current_digest: str,
    chapter_number: int,
    proof_chapter: int,
) -> None:
    """账本证据只在仍能核对当前正文时算「实际出现」；否则保留原引用为失效。"""
    if proof_chapter != chapter_number:
        return
    evidence_id = file_evidence_id(
        workspace,
        snapshot,
        clue_id=clue_id,
        source_chapter=source_chapter,
        role=role,
        evidence=proof,
    )
    exact = (
        proof.digest == current_digest
        and 0 <= proof.start < proof.end <= len(current_text)
        and current_text[proof.start : proof.end] == proof.quote
    )
    key = (
        (proof.path, proof.start, proof.end, proof.quote)
        if exact
        else ("", None, None, proof.quote)
    )
    start, end = (proof.start, proof.end) if exact else (None, None)
    row = seen.get(key)
    if row is None:
        seen[key] = ChapterOccurrence(
            path=proof.path or "",
            quote=proof.quote,
            start=start,
            end=end,
            state="exact" if exact else "missing",
            evidence_ids=[evidence_id],
        )
        return
    row.evidence_ids = sorted({*row.evidence_ids, evidence_id})
    if not exact and row.state == "exact":
        # 同一位置既有有效关联又有失效证据：保留有效位置，失效来源仍记在原引用里。
        return


def _proof_rows(content: ClueContent, path: str) -> list[tuple[str, ClueEvidence, int]]:
    rows: list[tuple[str, ClueEvidence, int]] = []
    if content.evidence.path == path:
        rows.append(("plant", content.evidence, content.planted_chapter))
    if content.mention_evidence is not None and content.mention_evidence.path == path:
        rows.append(("mention", content.mention_evidence, content.source_chapter))
    if content.resolution_evidence is not None and content.resolution_evidence.path == path:
        rows.append(("resolve", content.resolution_evidence, content.resolution_chapter or 0))
    return rows


def _clue_title(
    clue_id: str,
    catalog: StoryClueCatalog | None,
    snapshot: ClueSnapshot,
) -> str:
    if catalog is not None and catalog.get(clue_id) is not None:
        return catalog.title(clue_id)
    ledger = snapshot.ledger
    if ledger is not None and clue_id in ledger.clues:
        return ledger.clues[clue_id].title
    return clue_id


def _merge_item(
    items: dict[tuple[str, str], ChapterContextItem],
    *,
    kind: Literal["node", "clue"],
    object_id: str,
    title: str,
    occurrence: ChapterOccurrence | None,
    planned: bool,
) -> None:
    key = (kind, object_id)
    item = items.get(key)
    if item is None:
        item = ChapterContextItem(
            object=ChapterObject(kind=kind, id=object_id),
            title=title,
            placement="planned" if planned else "unresolved",
        )
        items[key] = item
    if occurrence is None:
        return
    item.occurrences.append(occurrence)
    if occurrence.state in ("exact", "relocated"):
        item.placement = "observed"


def _dedupe_occurrences(rows: list[ChapterOccurrence]) -> list[ChapterOccurrence]:
    merged: dict[tuple[str, int | None, int | None, str], ChapterOccurrence] = {}
    for row in rows:
        key = (row.path, row.start, row.end, row.quote)
        current = merged.get(key)
        if current is None:
            merged[key] = row
            continue
        current.binding_ids = sorted({*current.binding_ids, *row.binding_ids})
        current.evidence_ids = sorted({*current.evidence_ids, *row.evidence_ids})
        if current.state != "exact" and row.state == "exact":
            current.state = "exact"
            current.start, current.end = row.start, row.end
    ordered = sorted(
        merged.values(),
        key=lambda row: (row.start is None, row.start or 0, row.end or 0, row.quote),
    )
    return ordered


def _view_revision(
    root: Path,
    chapter_path: str,
    chapter_digest: str,
    variant: StoryVariant | None,
    ledger_revision: str,
    items: list[ChapterContextItem],
) -> str:
    payload = {
        "workspace": str(root.resolve()),
        "chapter": [chapter_path, chapter_digest],
        "variant": variant.revision if variant is not None else None,
        "ledger": ledger_revision,
        "items": [
            [
                item.object.kind,
                item.object.id,
                item.placement,
                [
                    [row.path, row.start, row.end, row.state, row.binding_ids, row.evidence_ids]
                    for row in item.occurrences
                ],
            ]
            for item in items
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def _chapter_entry(workspace: Workspace, chapter_path: str) -> FileEntry:
    entry = next(
        (row for row in workspace.catalog().chapters if row.path == chapter_path and row.exists),
        None,
    )
    if entry is None or entry.chapter_number is None:
        raise WorkspaceError(
            code="PROSE_CHAPTER_NOT_FOUND",
            where="story.chapter",
            why=f"所选路径不是当前正文章节：{chapter_path}",
            fix="刷新目录后重新选择章节",
            details={"path": chapter_path},
        )
    return entry


def _variant_of(snapshot: StorySnapshot, variant_id: str | None) -> StoryVariant | None:
    document = snapshot.document
    if document is None:
        if variant_id:
            raise WorkspaceError(
                code="STORY_VARIANT_NOT_FOUND",
                where="story.chapter",
                why="当前作品没有构思结构",
                fix="先建立构思，或省略 variant_id 读取账本可验证的出处",
                details={"variant_id": variant_id},
            )
        return None
    target = variant_id or document.primary_variant_id
    variant = document.variants.get(target)
    if variant is None or target != document.primary_variant_id:
        raise WorkspaceError(
            code="STORY_VARIANT_NOT_FOUND",
            where="story.chapter",
            why=f"没有这个方案：{target}",
            fix="刷新构思后按当前主方案重试",
            details={"variant_id": target},
        )
    return variant


def chapter_context(
    workspace: Workspace,
    root: Path,
    *,
    chapter_path: str,
    variant_id: str | None = None,
    cursor: str = "",
    limit: int = 20,
) -> ChapterContextView:
    """读取一章的随章条目；同一对象多处出现保留多次，来源 ID 合并但不丢身份。"""
    entry = _chapter_entry(workspace, chapter_path)
    chapter_number = entry.chapter_number
    assert chapter_number is not None
    file = workspace.read(chapter_path)
    snapshot = StoryStore(root, workspace).read()
    variant = _variant_of(snapshot, variant_id)
    catalog = StoryClueCatalog.from_variant(variant) if variant is not None else None
    ledger_snapshot = ClueLedgerStore(root).read()
    ledger = ledger_snapshot.ledger

    items: dict[tuple[str, str], ChapterContextItem] = {}
    if variant is not None:
        for binding in variant.bindings.values():
            if binding.path != chapter_path:
                continue
            if binding.node_id is not None:
                node = variant.nodes.get(binding.node_id)
                if node is not None:
                    seen: dict[tuple[str, int | None, int | None, str], ChapterOccurrence] = {}
                    _occurrence(binding, file.text, file.digest, seen)
                    for row in seen.values():
                        _merge_item(
                            items,
                            kind="node",
                            object_id=node.id,
                            title=node.title,
                            occurrence=row,
                            planned=False,
                        )
            if binding.clue_id is not None:
                seen = {}
                _occurrence(binding, file.text, file.digest, seen)
                for row in seen.values():
                    _merge_item(
                        items,
                        kind="clue",
                        object_id=binding.clue_id,
                        title=_clue_title(binding.clue_id, catalog, ledger_snapshot),
                        occurrence=row,
                        planned=False,
                    )
        for node in variant.nodes.values():
            if node.planned_chapter_number != chapter_number:
                continue
            if ("node", node.id) in items:
                continue
            _merge_item(
                items,
                kind="node",
                object_id=node.id,
                title=node.title,
                occurrence=None,
                planned=True,
            )

    if ledger is not None:
        for contribution in ledger.contributions.values():
            content = contribution.content
            if contribution.source_path != chapter_path:
                continue
            for role, proof, proof_chapter in _proof_rows(content, chapter_path):
                seen = {}
                _add_evidence(
                    seen,
                    workspace=workspace,
                    snapshot=ledger_snapshot,
                    clue_id=contribution.clue_id,
                    source_chapter=content.source_chapter,
                    role=role,
                    proof=proof,
                    current_text=file.text,
                    current_digest=file.digest,
                    chapter_number=chapter_number,
                    proof_chapter=proof_chapter,
                )
                for row in seen.values():
                    _merge_item(
                        items,
                        kind="clue",
                        object_id=contribution.clue_id,
                        title=_clue_title(contribution.clue_id, catalog, ledger_snapshot),
                        occurrence=row,
                        planned=False,
                    )

    ordered: list[ChapterContextItem] = []
    rank = {"observed": 0, "planned": 1, "unresolved": 2}
    for item in items.values():
        item.occurrences = _dedupe_occurrences(item.occurrences)
        if not item.occurrences and item.placement != "planned":
            # 只有失效引用时仍是 unresolved；没有引用的对象不进入随章列表。
            continue
        ordered.append(item)
    ordered.sort(
        key=lambda item: (
            rank[item.placement],
            min(
                (row.start for row in item.occurrences if row.start is not None),
                default=10**9,
            ),
            item.object.kind,
            item.object.id,
        )
    )

    revision = _view_revision(
        root, chapter_path, file.digest, variant, ledger_snapshot.revision, ordered
    )
    size = min(CHAPTER_CONTEXT_LIMIT, max(1, limit))
    offset = 0
    if cursor:
        try:
            raw = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            if (
                raw["stamp"] != revision
                or type(raw["offset"]) is not int
                or not 0 <= raw["offset"] <= len(ordered)
            ):
                raise ValueError("游标范围已变化")
            offset = raw["offset"]
        except (ValueError, KeyError, TypeError) as exc:
            raise _cursor_error() from exc
    page = ordered[offset : offset + size]
    next_offset = offset + len(page)
    next_cursor = (
        base64.urlsafe_b64encode(
            json.dumps({"stamp": revision, "offset": next_offset}).encode()
        ).decode()
        if next_offset < len(ordered)
        else ""
    )
    return ChapterContextView(
        chapter_path=chapter_path,
        chapter_digest=file.digest,
        variant_id=variant.id if variant is not None else None,
        story_revision=variant.revision if variant is not None else None,
        ledger_revision=ledger_snapshot.revision,
        view_revision=revision,
        items=page,
        next_cursor=next_cursor,
        truncated=bool(next_cursor),
    )
