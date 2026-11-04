"""正文修订的持久化与可分析状态（1.2 修订学习 P1）。

一次修订 = 一次已入稿的正文改动，连同它前后的完整文本。记录从变更记录派生：
正文保存与候选采用都会在 ``StoryChangeRecord.inverse_prose_patches`` 里留下
「改后 → 改前」的完整补丁，因此修订不依赖进程内状态，提交后崩溃也能补齐。
模型经由文件工具改稿没有结构变更记录，直接按操作号与前后指纹落一条记录。

修订不改变正文本身，也不进入 Git：它只回答「这次改动是什么、还能不能分析」。
分析资格每次按当前正文重新计算，提交历史或切换章节都不影响它。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from hastings.workspace.atomic_io import atomic_write_json, locked
from hastings.workspace.files import Workspace, digest_bytes, normalize_newlines
from hastings.workspace.revision_diff import (
    ALGORITHM_VERSION,
    DiffHunk,
    active_hunk_ids,
    build_hunks,
)
from hastings.workspace.story_models import StoryChangeRecord, StoryDocument
from hastings.workspace.story_store import MAIN_VARIANT_ID, StoryStore

__all__ = [
    "RevisionDetail",
    "RevisionRecord",
    "RevisionStore",
    "RevisionSource",
    "record_tool_revision",
    "sync_chapter_revisions",
]

RevisionSource = Literal["author", "model", "candidate", "author_after_model"]

_SOURCES = frozenset({"author", "model", "candidate", "author_after_model"})
_SCHEMA_VERSION = 1
#: 只同步真正的正文改动；撤销、改名这类提交不是修订学习要看的证据。
_REVISION_KINDS = frozenset({"prose_save", "change"})


@dataclass(frozen=True, slots=True)
class RevisionRecord:
    id: str
    chapter_id: str
    path: str
    change_id: str
    source: RevisionSource
    before_digest: str
    after_digest: str
    confirmed_at: str
    before_text: str
    after_text: str
    author_message: str = ""
    operation_id: str = ""
    candidate_ref: str = ""
    conversation_id: str = ""

    @property
    def hunks(self) -> list[DiffHunk]:
        return build_hunks(self.before_text, self.after_text, scope=self.id)

    def summary(self) -> dict[str, Any]:
        hunks = self.hunks
        changed = sum(len(h.after) for h in hunks if h.op != "delete")
        removed = sum(len(h.before) for h in hunks if h.op != "insert")
        return {
            "id": self.id,
            "chapter_id": self.chapter_id,
            "path": self.path,
            "change_id": self.change_id,
            "source": self.source,
            "before_digest": self.before_digest,
            "after_digest": self.after_digest,
            "confirmed_at": self.confirmed_at,
            "author_message": self.author_message,
            "operation_id": self.operation_id,
            "candidate_ref": self.candidate_ref,
            "conversation_id": self.conversation_id,
            "hunk_count": len(hunks),
            "added_chars": changed,
            "removed_chars": removed,
        }


@dataclass(frozen=True, slots=True)
class RevisionDetail:
    record: RevisionRecord
    hunks: list[DiffHunk]
    active_hunk_ids: set[str]

    @property
    def analyzable(self) -> bool:
        return bool(self.active_hunk_ids)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _has_body(text: str) -> bool:
    """只有标题行的骨架不算已有正文：首次起草不是修订。"""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return True
    return False


def _is_pure_append(before: str, after: str) -> bool:
    """整段追加属于续写，不是对已有正文的修改。"""
    return bool(before.strip()) and after.startswith(before)


def revision_id(*, change_id: str, chapter_id: str, after_digest: str) -> str:
    identity = "\n".join([change_id, chapter_id, after_digest])
    return "revision_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


class RevisionStore:
    """作品状态目录下的一组修订记录；每份修订一份 JSON，写入按标识幂等。"""

    def __init__(self, state_root: Path) -> None:
        self.root = Path(state_root) / "story" / "prose-revisions"

    def path_of(self, revision_id_value: str) -> Path:
        return self.root / f"{revision_id_value}.json"

    def get(self, revision_id_value: str) -> RevisionRecord | None:
        target = self.path_of(revision_id_value)
        if not target.is_file():
            return None
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return _decode(raw)

    def list(
        self,
        *,
        chapter_id: str | None = None,
        path: str | None = None,
        limit: int = 50,
    ) -> list[RevisionRecord]:
        if not self.root.is_dir():
            return []
        rows: list[RevisionRecord] = []
        for entry in self.root.glob("revision_*.json"):
            try:
                raw = json.loads(entry.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            record = _decode(raw)
            if record is None:
                continue
            if chapter_id is not None and record.chapter_id != chapter_id:
                continue
            if path is not None and record.path != path:
                continue
            rows.append(record)
        rows.sort(key=lambda item: (item.confirmed_at, item.id), reverse=True)
        return rows[:limit]

    def ensure(self, record: RevisionRecord) -> RevisionRecord:
        """按标识幂等写入；已有记录不被后来的同标识写入覆盖。"""
        target = self.path_of(record.id)
        with locked(target.with_suffix(".lock")):
            existing = self.get(record.id)
            if existing is not None:
                return existing
            atomic_write_json(target, _encode(record), prefix=".revision-")
        return record

    def detail(self, revision_id_value: str, current_text: str) -> RevisionDetail | None:
        record = self.get(revision_id_value)
        if record is None:
            return None
        hunks = record.hunks
        return RevisionDetail(
            record=record,
            hunks=hunks,
            active_hunk_ids=active_hunk_ids(hunks, current_text),
        )


def _encode(record: RevisionRecord) -> dict[str, Any]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "algorithm": ALGORITHM_VERSION,
        "id": record.id,
        "chapter_id": record.chapter_id,
        "path": record.path,
        "change_id": record.change_id,
        "source": record.source,
        "before_digest": record.before_digest,
        "after_digest": record.after_digest,
        "confirmed_at": record.confirmed_at,
        "author_message": record.author_message,
        "operation_id": record.operation_id,
        "candidate_ref": record.candidate_ref,
        "conversation_id": record.conversation_id,
        "before_text": record.before_text,
        "after_text": record.after_text,
    }


def _decode(raw: object) -> RevisionRecord | None:
    if not isinstance(raw, dict):
        return None
    if raw.get("schema_version") != _SCHEMA_VERSION:
        return None
    source = raw.get("source")
    if source not in _SOURCES:
        return None
    values = {
        key: raw.get(key)
        for key in (
            "id",
            "chapter_id",
            "path",
            "change_id",
            "before_digest",
            "after_digest",
            "confirmed_at",
            "before_text",
            "after_text",
        )
    }
    if not all(isinstance(value, str) for value in values.values()):
        return None
    text = {key: str(value) for key, value in values.items()}
    return RevisionRecord(
        id=text["id"],
        chapter_id=text["chapter_id"],
        path=text["path"],
        change_id=text["change_id"],
        source=source,
        before_digest=text["before_digest"],
        after_digest=text["after_digest"],
        confirmed_at=text["confirmed_at"],
        before_text=text["before_text"],
        after_text=text["after_text"],
        author_message=str(raw.get("author_message") or ""),
        operation_id=str(raw.get("operation_id") or ""),
        candidate_ref=str(raw.get("candidate_ref") or ""),
        conversation_id=str(raw.get("conversation_id") or ""),
    )


def _full_file_patch(record: StoryChangeRecord, path: str) -> tuple[str, str] | None:
    """取回「改后 → 改前」的整文件补丁；局部补丁不构成一次修订。"""
    for patch in record.inverse_prose_patches:
        if patch.path != path:
            continue
        after = patch.expected
        before = patch.replacement
        if patch.start != 0 or patch.end != len(after):
            return None
        return normalize_newlines(before), normalize_newlines(after)
    return None


def sync_chapter_revisions(
    store: StoryStore,
    workspace: Workspace,
    *,
    chapter_id: str,
    path: str,
    revisions: RevisionStore,
) -> list[RevisionRecord]:
    """从已提交的变更记录补齐本章修订；返回按时间倒序的记录。"""
    del workspace
    snapshot = store.read()
    document = snapshot.document
    if document is None:
        return revisions.list(chapter_id=chapter_id)
    for change_id in document.changes:
        _record_from_document(
            document,
            change_id=change_id,
            chapter_id=chapter_id,
            path=path,
            revisions=revisions,
        )
    return revisions.list(chapter_id=chapter_id)


def record_change_revision(
    store: StoryStore,
    *,
    change_id: str,
    chapter_id: str,
    path: str,
    revisions: RevisionStore,
) -> RevisionRecord | None:
    """只补齐指定的一次变更，供保存成功后立即登记，不必扫描整份历史。"""
    document = store.read().document
    if document is None or not change_id:
        return None
    return _record_from_document(
        document, change_id=change_id, chapter_id=chapter_id, path=path, revisions=revisions
    )


def _record_from_document(
    document: StoryDocument,
    *,
    change_id: str,
    chapter_id: str,
    path: str,
    revisions: RevisionStore,
) -> RevisionRecord | None:
    record = document.changes.get(change_id)
    if record is None or record.variant_id != MAIN_VARIANT_ID:
        return None
    receipt = document.requests.get(record.request_key)
    kind = receipt.kind if receipt is not None else ""
    if kind not in _REVISION_KINDS:
        return None
    result = dict(receipt.result) if receipt is not None else {}
    source_value = result.get("revision_source")
    if not isinstance(source_value, str) or source_value not in _SOURCES:
        # 没有来源标记的正文变更不猜：只有候选采用与人工保存会登记来源。
        return None
    source = cast(RevisionSource, source_value)
    texts = _full_file_patch(record, path)
    if texts is None:
        return None
    before_text, after_text = texts
    if not _has_body(before_text) or _is_pure_append(before_text, after_text):
        return None
    if before_text == after_text:
        return None
    return revisions.ensure(
        RevisionRecord(
            id=revision_id(
                change_id=change_id,
                chapter_id=chapter_id,
                after_digest=digest_bytes(after_text.encode("utf-8")),
            ),
            chapter_id=chapter_id,
            path=path,
            change_id=change_id,
            source=source,
            before_digest=digest_bytes(before_text.encode("utf-8")),
            after_digest=digest_bytes(after_text.encode("utf-8")),
            confirmed_at=str(
                result.get("revision_confirmed_at") or _change_time(document, change_id)
            ),
            before_text=before_text,
            after_text=after_text,
            author_message=str(result.get("revision_author_message") or ""),
            operation_id=str(result.get("revision_operation_id") or ""),
            candidate_ref=str(result.get("revision_candidate_ref") or ""),
            conversation_id=str(result.get("revision_conversation_id") or ""),
        )
    )


def _change_time(document: StoryDocument, change_id: str) -> str:
    record = document.changes.get(change_id)
    return str(getattr(record, "created_at", "") or _now())


def record_tool_revision(
    revisions: RevisionStore,
    store: StoryStore,
    *,
    path: str,
    before_text: str,
    after_text: str,
    before_digest: str,
    after_digest: str,
    operation_id: str = "",
    author_message: str = "",
    conversation_id: str = "",
) -> RevisionRecord | None:
    """模型经文件工具改稿后的修订；没有结构变更记录，按操作与前后来落。"""
    before = normalize_newlines(before_text)
    after = normalize_newlines(after_text)
    if before == after or not _has_body(before) or _is_pure_append(before, after):
        return None
    variant = store.read().variant(MAIN_VARIANT_ID)
    plan = variant.chapter_for_path(path) if variant is not None else None
    if plan is None:
        return None
    identity = "\n".join([operation_id or "tool", path, before_digest, after_digest])
    value = "revision_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return revisions.ensure(
        RevisionRecord(
            id=value,
            chapter_id=plan.id,
            path=path,
            change_id="",
            source="model",
            before_digest=before_digest,
            after_digest=after_digest,
            confirmed_at=_now(),
            before_text=before,
            after_text=after,
            author_message=author_message[:4000],
            operation_id=operation_id,
            conversation_id=conversation_id,
        )
    )
