"""从账本中的依据标识解析真实来源；浏览不初始化账本或提供模型授权。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from hastings.workspace.clue_models import (
    ClueEvidence,
    FileSource,
)
from hastings.workspace.clue_store import ClueLedgerStore, ClueSnapshot
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace, is_managed_path
from hastings.workspace.state import ConversationStore
from hastings.workspace.state_store import current_chapter

_ID = re.compile(r"^ev_([a-f0-9]{20})_([a-f0-9]{32})$")


class SourceLocation(BaseModel):
    evidence_id: str
    clue_id: str
    source: FileSource
    freshness: Literal["valid", "stale", "source_missing", "unavailable", "invalid_range"]
    quote: str
    chapter: int | None
    as_of_chapter: int
    offset_unit: Literal["unicode_codepoint"] = "unicode_codepoint"
    start: int | None = None
    end: int | None = None
    readable: bool = False
    current_digest: str | None = None
    # 模块与无法直接阅读的来源共用有界原文片段；它不计为模型已读。
    excerpt: str = ""
    excerpt_start: int = 0
    saved_path: str | None = None


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    clue_id: str
    source: FileSource
    start: int
    end: int
    quote: str
    chapter: int | None
    visible_from: int


def _hash(value: object, length: int) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()[:length]


def _scope(workspace: Workspace, snapshot: ClueSnapshot) -> str:
    return _hash(
        snapshot.ledger.workspace_lineage if snapshot.ledger else str(workspace.root.resolve()), 20
    )


def file_evidence_id(
    workspace: Workspace,
    snapshot: ClueSnapshot,
    *,
    clue_id: str,
    source_chapter: int,
    role: str,
    evidence: ClueEvidence,
) -> str:
    scope = _scope(workspace, snapshot)
    return f"ev_{scope}_{_hash([clue_id, source_chapter, role, evidence.model_dump()], 32)}"


def evidence_records(workspace: Workspace, snapshot: ClueSnapshot) -> tuple[EvidenceRecord, ...]:
    rows: list[EvidenceRecord] = []
    if snapshot.ledger is None:
        return ()
    for entry in snapshot.entries():
        proofs = [("plant", entry.evidence, entry.planted_chapter)]
        if entry.mention_evidence is not None:
            proofs.append(("mention", entry.mention_evidence, entry.source_chapter))
        if entry.resolution_evidence is not None and entry.resolution_chapter is not None:
            proofs.append(("resolve", entry.resolution_evidence, entry.resolution_chapter))
        for role, evidence, chapter in proofs:
            saved = ClueEvidence(
                path=evidence.path,
                digest=evidence.digest,
                start=evidence.start,
                end=evidence.end,
                quote=evidence.quote,
            )
            evidence_id = file_evidence_id(
                workspace,
                snapshot,
                clue_id=entry.clue_id,
                source_chapter=entry.source_chapter,
                role=role,
                evidence=saved,
            )
            rows.append(
                EvidenceRecord(
                    evidence_id=evidence_id,
                    clue_id=entry.clue_id,
                    source=FileSource(kind="file", path=evidence.path, digest=evidence.digest),
                    start=evidence.start,
                    end=evidence.end,
                    quote=evidence.quote,
                    chapter=chapter,
                    visible_from=max(entry.source_chapter, chapter),
                )
            )
    return tuple(rows)


def resolve_source_location(
    workspace: Workspace,
    root: Path,
    evidence_id: str,
    *,
    as_of_chapter: int | None = None,
) -> SourceLocation:
    match = _ID.fullmatch(evidence_id)
    if match is None:
        raise WorkspaceError(
            code="CLUE_SOURCE_INVALID",
            where="clues.source",
            why="依据标识格式无效",
            fix="从线索详情重新选择原文依据",
        )
    snapshot = ClueLedgerStore(root).read()
    if match[1] != _scope(workspace, snapshot):
        raise WorkspaceError(
            code="CLUE_ACCESS_DENIED",
            where="clues.source",
            why="依据标识不属于当前作品",
            fix="切回依据所属作品，或从当前作品重新选择依据",
        )
    as_of = current_chapter(workspace, as_of_chapter)
    record = next(
        (
            row
            for row in evidence_records(workspace, snapshot)
            if row.evidence_id == evidence_id and row.visible_from <= as_of
        ),
        None,
    )
    if record is None:
        raise WorkspaceError(
            code="CLUE_SOURCE_NOT_FOUND",
            where="clues.source",
            why="当前截止章下没有这条原文依据",
            fix="刷新线索详情并核对查看截止章",
        )
    result = SourceLocation(
        evidence_id=record.evidence_id,
        clue_id=record.clue_id,
        source=record.source,
        freshness="source_missing",
        quote=record.quote,
        chapter=record.chapter,
        as_of_chapter=as_of,
    )
    if not is_managed_path(record.source.path):
        raise WorkspaceError(
            code="CLUE_ACCESS_DENIED",
            where="clues.source",
            why="依据来源不是受管作品文件",
            fix="重新整理受管正文或设定，不能提交任意磁盘路径",
        )
    path = record.source.path
    if not (workspace.root / path).exists():
        path = _recorded_move(root, path, record.source.digest)
    if path != record.source.path:
        result.saved_path = record.source.path
        result.source = record.source.model_copy(update={"path": path})
    current_number = next(
        (
            row.chapter_number
            for row in workspace.catalog().chapters
            if row.path == path and row.exists
        ),
        None,
    )
    if record.chapter is not None and current_number is not None and current_number > as_of:
        raise WorkspaceError(
            code="CLUE_SOURCE_NOT_FOUND",
            where="clues.source",
            why="来源文件当前章号超出查看截止章",
            fix="核对章节顺序后重新整理",
        )
    try:
        view = workspace.read(path)
    except WorkspaceError as exc:
        result.freshness = "source_missing" if exc.code == "FILE_NOT_FOUND" else "unavailable"
        return result
    text, digest = view.text, view.digest
    expected = record.source.digest
    if (
        record.chapter is not None
        and current_number is not None
        and current_number != record.chapter
    ):
        result.freshness = "stale"
    result.readable = True
    result.current_digest = digest
    if digest != expected:
        result.freshness = "stale"
    elif result.freshness != "stale":
        result.freshness = (
            "valid"
            if text[record.start : record.end] == record.quote
            and 0 <= record.start < record.end <= len(text)
            else "invalid_range"
        )
    if result.freshness == "valid":
        result.start, result.end = record.start, record.end
    # 不在失效来源上应用旧区间；只展示当前文件开头，旧引文另列。
    begin = max(0, record.start - 256) if result.freshness == "valid" else 0
    finish = min(len(text), record.end + 256) if result.freshness == "valid" else 512
    result.excerpt, result.excerpt_start = text[begin:finish], begin
    return result


def _recorded_move(root: Path, path: str, digest: str) -> str:
    """仅跟随服务保存的成功移动事件；不搜索同文文件，也不修复会话索引。"""
    moves: dict[str, set[str]] = {}
    store = ConversationStore(root)
    for log in root.glob("*.jsonl"):
        for event in store.events(log.stem):
            if (
                event.get("type") == "file_changed"
                and event.get("action") == "move"
                and event.get("before_digest") == digest
                and event.get("after_digest") == digest
            ):
                source, target = event.get("path"), event.get("target")
                if isinstance(source, str) and isinstance(target, str) and is_managed_path(target):
                    moves.setdefault(source, set()).add(target)
    current, visited = path, set()
    while current in moves and current not in visited:
        visited.add(current)
        choices = moves[current]
        if len(choices) != 1:
            return path
        current = next(iter(choices))
    return path if current in visited else current
