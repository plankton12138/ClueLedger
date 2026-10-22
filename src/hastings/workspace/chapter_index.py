"""章节索引：可重建的派生数据（0.6 V06-A3）。

索引回答「这一章发生了什么」，用来在后续章节里定位伏笔、人物首次出现和时间地点。
它不是正式设定：作品仍然只有章节、小传、线索和真相，索引只是按当前原文生成的缓存，
可以随时重建，也不参与作品 Git。

几条边界：

- **只从当前原文生成**：不从上一版摘要递归压缩，避免误差逐版放大；源文件变了旧记录即
  失效，不再注入模型。
- **发布要能核对**：目标在本轮允许范围内、模型读完了当前版本、每条证据的区间与引文
  对得上、发布前再复核一次源指纹，缺一条都不发布。
- **证据只证明原文存在**：它不能证明人物说法为真，也不区分叙述、回忆与推测——那属于
  阅读判断，报告里用疑点表达，不能把索引当成事实账本。
- **状态与内容分开**：记录本身是「上次生成的结果」，``missing`` / ``valid`` / ``stale`` /
  ``source_missing`` / ``unavailable`` 由当前磁盘状态算出，不写回缓存。
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace, is_managed_path
from hastings.workspace.materials import MaterialLedger, SourceRef

__all__ = [
    "ENTRY_MAX",
    "EVIDENCE_MAX_CHARS",
    "INDEX_BUDGET",
    "MAX_INDEX_CHAPTERS",
    "RECIPE_VERSION",
    "SUMMARY_MAX_CHARS",
    "ChapterIndexDraft",
    "ChapterIndexEntry",
    "ChapterIndexRecord",
    "ChapterIndexStatus",
    "ChapterIndexStore",
    "IndexEvidence",
    "index_injection",
    "publish_index",
    "validate_draft",
]

#: 生成规则版本：解析或校验规则变化时递增，旧记录按过期处理，不猜测兼容。
RECIPE_VERSION = 1

#: 一轮最多更新的章节数；作者明确选择要更新的章节。
MAX_INDEX_CHAPTERS = 10

#: 单章摘要、单条引文与单类条目上限。
SUMMARY_MAX_CHARS = 500
EVIDENCE_MAX_CHARS = 160
ENTRY_MAX = 12

#: 有效索引注入提示词的总字符预算。
INDEX_BUDGET = 8_000

ChapterIndexStatus = Literal["missing", "valid", "stale", "source_missing", "unavailable"]
EntryKind = Literal["event", "character", "place", "clue"]

_ENTRY_LABEL: dict[str, str] = {
    "event": "事件",
    "character": "人物",
    "place": "时间地点",
    "clue": "线索",
}


@dataclass(frozen=True, slots=True)
class IndexEvidence:
    """一处原文依据：服务端字符区间 + 引文，``index`` 供条目引用。"""

    index: int
    start: int
    end: int
    quote: str


@dataclass(frozen=True, slots=True)
class ChapterIndexEntry:
    kind: EntryKind
    text: str
    evidence: tuple[int, ...]

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "text": self.text, "evidence": list(self.evidence)}


@dataclass(frozen=True, slots=True)
class ChapterIndexDraft:
    """模型提交的一章索引草稿；结构合法不代表语义正确。"""

    source_path: str
    source_digest: str
    summary: str
    evidence: tuple[IndexEvidence, ...]
    events: tuple[ChapterIndexEntry, ...] = ()
    characters: tuple[ChapterIndexEntry, ...] = ()
    places: tuple[ChapterIndexEntry, ...] = ()
    clues: tuple[ChapterIndexEntry, ...] = ()

    def entries(self) -> tuple[ChapterIndexEntry, ...]:
        return (*self.events, *self.characters, *self.places, *self.clues)


@dataclass(frozen=True, slots=True)
class ChapterIndexRecord:
    """已发布的索引记录：内容来自上一次成功发布，状态由当前磁盘算出。"""

    source_path: str
    source_digest: str
    recipe_version: int
    generated_at: str
    summary: str
    evidence: tuple[IndexEvidence, ...]
    events: tuple[ChapterIndexEntry, ...]
    characters: tuple[ChapterIndexEntry, ...]
    places: tuple[ChapterIndexEntry, ...]
    clues: tuple[ChapterIndexEntry, ...]
    status: ChapterIndexStatus = "missing"

    def entries(self) -> tuple[ChapterIndexEntry, ...]:
        return (*self.events, *self.characters, *self.places, *self.clues)

    def to_dict(self) -> dict[str, object]:
        return {
            "source_path": self.source_path,
            "source_digest": self.source_digest,
            "recipe_version": self.recipe_version,
            "generated_at": self.generated_at,
            "summary": self.summary,
            "status": self.status,
            "evidence": [
                {"index": item.index, "start": item.start, "end": item.end, "quote": item.quote}
                for item in self.evidence
            ],
            "events": [item.to_dict() for item in self.events],
            "characters": [item.to_dict() for item in self.characters],
            "places": [item.to_dict() for item in self.places],
            "clues": [item.to_dict() for item in self.clues],
        }

    @classmethod
    def from_raw(cls, raw: object) -> ChapterIndexRecord | None:
        """读缓存：结构不完整的记录按不可用丢弃，不让坏缓存影响写作。"""
        if not isinstance(raw, dict):
            return None
        path = raw.get("source_path")
        digest = raw.get("source_digest")
        version = raw.get("recipe_version")
        generated = raw.get("generated_at")
        summary = raw.get("summary")
        if not all(isinstance(item, str) and item for item in (path, digest, generated, summary)):
            return None
        if not isinstance(version, int):
            return None
        evidence = _evidence_from_raw(raw.get("evidence"))
        if evidence is None:
            return None
        entries: dict[str, tuple[ChapterIndexEntry, ...]] = {}
        for key, kind in (
            ("events", "event"),
            ("characters", "character"),
            ("places", "place"),
            ("clues", "clue"),
        ):
            parsed = _entries_from_raw(raw.get(key), cast(EntryKind, kind))
            if parsed is None:
                return None
            entries[key] = parsed
        return cls(
            source_path=str(path),
            source_digest=str(digest),
            recipe_version=version,
            generated_at=str(generated),
            summary=str(summary),
            evidence=evidence,
            events=entries["events"],
            characters=entries["characters"],
            places=entries["places"],
            clues=entries["clues"],
        )


class ChapterIndexStore:
    """作品绑定状态目录里的 ``chapter-index.json``；写入用临时文件替换。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.file = self.root / "chapter-index.json"

    def read_all(self) -> dict[str, ChapterIndexRecord]:
        try:
            raw = json.loads(self.file.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            return {}
        items = raw.get("entries") if isinstance(raw, dict) else None
        if not isinstance(items, dict):
            return {}
        records: dict[str, ChapterIndexRecord] = {}
        for value in items.values():
            record = ChapterIndexRecord.from_raw(value)
            if record is not None:
                records[record.source_path] = record
        return records

    def publish(
        self, draft: ChapterIndexDraft, *, expected_source: SourceRef
    ) -> ChapterIndexRecord:
        """整份替换目标章节的记录；中断不发布半份条目。"""
        record = ChapterIndexRecord(
            source_path=draft.source_path,
            source_digest=draft.source_digest,
            recipe_version=RECIPE_VERSION,
            generated_at=_now(),
            summary=draft.summary,
            evidence=draft.evidence,
            events=draft.events,
            characters=draft.characters,
            places=draft.places,
            clues=draft.clues,
        )
        records = self.read_all()
        if record.source_digest != expected_source.digest:
            raise WorkspaceError(
                code="CONTEXT_SOURCE_STALE_CONFLICT",
                where=f"chapter_index.publish:{record.source_path}",
                why="要发布的索引不是基于当前原文生成的",
                fix="重新读取这一章，按当前内容重新生成索引",
                details={
                    "path": record.source_path,
                    "expected_digest": expected_source.digest,
                    "draft_digest": record.source_digest,
                },
            )
        records[record.source_path] = record
        self._save(records)
        # 发布前刚核对过源指纹：这份记录此刻就是有效的。
        return _with_status(record, "valid")

    def status(self, record: ChapterIndexRecord, workspace: Workspace) -> ChapterIndexStatus:
        """按当前磁盘状态判断这条记录还能不能用；不调用模型。"""
        if record.recipe_version != RECIPE_VERSION:
            return "stale"
        try:
            view = workspace.read(record.source_path)
        except WorkspaceError as exc:
            return "source_missing" if exc.code == "FILE_NOT_FOUND" else "unavailable"
        return "valid" if view.digest == record.source_digest else "stale"

    def records(self, workspace: Workspace) -> tuple[ChapterIndexRecord, ...]:
        """所有已发布记录，附带按当前磁盘算出的状态。"""
        rows: list[ChapterIndexRecord] = []
        for record in self.read_all().values():
            rows.append(_with_status(record, self.status(record, workspace)))
        return tuple(sorted(rows, key=lambda item: item.source_path))

    def valid(self, workspace: Workspace) -> tuple[ChapterIndexRecord, ...]:
        """只有有效记录可以注入模型；过期记录保留在面板里提示重建。"""
        return tuple(item for item in self.records(workspace) if item.status == "valid")

    def find(self, workspace: Workspace, path: str) -> ChapterIndexRecord | None:
        for record in self.records(workspace):
            if record.source_path == path:
                return record
        return None

    def _save(self, records: dict[str, ChapterIndexRecord]) -> None:
        payload = {
            "version": 1,
            "recipe_version": RECIPE_VERSION,
            "entries": {
                path: {
                    "source_path": record.source_path,
                    "source_digest": record.source_digest,
                    "recipe_version": record.recipe_version,
                    "generated_at": record.generated_at,
                    "summary": record.summary,
                    "evidence": [
                        {
                            "index": item.index,
                            "start": item.start,
                            "end": item.end,
                            "quote": item.quote,
                        }
                        for item in record.evidence
                    ],
                    "events": [item.to_dict() for item in record.events],
                    "characters": [item.to_dict() for item in record.characters],
                    "places": [item.to_dict() for item in record.places],
                    "clues": [item.to_dict() for item in record.clues],
                }
                for path, record in records.items()
            },
        }
        handle: int | None = None
        temp_name: str | None = None
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            handle, temp_name = tempfile.mkstemp(
                dir=str(self.file.parent), prefix=".chapter-index-", suffix=".json"
            )
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                handle = None
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.replace(temp_name, self.file)
            temp_name = None
        except OSError as exc:
            raise WorkspaceError(
                code="CHAPTER_INDEX_WRITE_FAILED",
                where="chapter_index.save",
                why=f"无法保存章节索引：{exc.strerror or exc}",
                fix="检查应用状态目录是否可写；原作品内容不受影响",
            ) from exc
        finally:
            if handle is not None:
                os.close(handle)
            if temp_name is not None:
                with suppress(OSError):
                    os.unlink(temp_name)


def validate_draft(draft: ChapterIndexDraft, source_text: str) -> None:
    """结构、区间与引文校验；任何一条不成立都不发布。"""
    summary = draft.summary.strip()
    if not summary:
        raise _evidence_error(draft.source_path, "没有写这一章的摘要")
    if len(summary) > SUMMARY_MAX_CHARS:
        raise _evidence_error(
            draft.source_path, f"摘要超过 {SUMMARY_MAX_CHARS} 字符，请压缩后再提交"
        )
    if not draft.evidence:
        raise _evidence_error(draft.source_path, "没有任何原文依据")
    indexes = [item.index for item in draft.evidence]
    if indexes != list(range(1, len(indexes) + 1)):
        raise _evidence_error(draft.source_path, "证据编号必须从 1 开始连续递增")
    for item in draft.evidence:
        if item.start < 0 or item.end <= item.start or item.end > len(source_text):
            raise _evidence_error(draft.source_path, f"证据 {item.index} 的区间无效")
        if not item.quote.strip() or len(item.quote) > EVIDENCE_MAX_CHARS:
            raise _evidence_error(
                draft.source_path, f"证据 {item.index} 的引文为空或超过 {EVIDENCE_MAX_CHARS} 字符"
            )
        slice_text = source_text[item.start : item.end]
        if _collapse(item.quote) != _collapse(slice_text):
            raise _evidence_error(
                draft.source_path, f"证据 {item.index} 的引文与声明的原文区间不一致"
            )
    known = set(indexes)
    for entry in draft.entries():
        if entry.kind not in _ENTRY_LABEL:
            raise _evidence_error(draft.source_path, f"不支持的条目类别：{entry.kind}")
        if not entry.text.strip():
            raise _evidence_error(draft.source_path, "条目内容为空")
        if not entry.evidence:
            raise _evidence_error(draft.source_path, "每个条目至少要引用一处原文证据")
        missing = [index for index in entry.evidence if index not in known]
        if missing:
            raise _evidence_error(draft.source_path, f"条目引用了不存在的证据编号：{missing[0]}")
    if len(draft.events) > ENTRY_MAX:
        raise _evidence_error(draft.source_path, f"事件最多 {ENTRY_MAX} 条")


def publish_index(
    draft: ChapterIndexDraft,
    *,
    workspace: Workspace,
    ledger: MaterialLedger,
    store: ChapterIndexStore,
    allowed_paths: Sequence[str],
) -> ChapterIndexRecord:
    """发布一章索引：范围 → 当前原文 → 全文覆盖 → 引文 → 存储复核。"""
    path = draft.source_path
    if path not in allowed_paths:
        raise WorkspaceError(
            code="CHAPTER_INDEX_SCOPE_INVALID",
            where=f"chapter_index.publish:{path}",
            why="这一章不在本轮允许更新的索引范围内",
            fix="只更新作者选中的章节；需要更多章节时由作者重新发起",
            details={"path": path, "allowed": list(allowed_paths)},
        )
    if not is_managed_path(path) or not path.startswith("正文/"):
        raise WorkspaceError(
            code="CHAPTER_INDEX_SCOPE_INVALID",
            where=f"chapter_index.publish:{path}",
            why="索引只针对正文章节",
            fix="只选择 正文/ 下的章节文件",
            details={"path": path},
        )
    try:
        source = workspace.read(path)
    except WorkspaceError as exc:
        raise WorkspaceError(
            code="CONTEXT_SOURCE_NOT_FOUND",
            where=f"chapter_index.publish:{path}",
            why=f"要建索引的章节当前不可用：{path}（{exc.why}）",
            fix="刷新目录后重新选择章节",
            details={"path": path},
        ) from exc
    if source.digest != draft.source_digest:
        raise WorkspaceError(
            code="CONTEXT_SOURCE_STALE_CONFLICT",
            where=f"chapter_index.publish:{path}",
            why="这一章在本轮读取之后被改动，索引不能按旧内容发布",
            fix="重新读取当前内容后再生成索引",
            details={
                "path": path,
                "expected_digest": source.digest,
                "draft_digest": draft.source_digest,
            },
        )
    coverage = ledger.coverage(path)
    if coverage is None or coverage.ref.digest != source.digest or not coverage.complete:
        raise WorkspaceError(
            code="REFERENCE_NOT_FULLY_READ_CONFLICT",
            where=f"chapter_index.publish:{path}",
            why="发布索引前需要先读完这一章的当前全文",
            fix="用 read_file 读完整章（返回里带 next_cursor），再发布索引",
            details={"path": path},
        )
    validate_draft(draft, source.text)
    # store 在替换缓存前再次核对来源；中断不发布半份条目。
    return store.publish(draft, expected_source=SourceRef(source.path, source.digest))


def index_injection(records: Iterable[ChapterIndexRecord], *, budget: int = INDEX_BUDGET) -> str:
    """把有效索引装配成提示词片段；超预算按章节顺序截断并说明。"""
    blocks: list[str] = []
    used = 0
    for record in records:
        lines = [f"### {record.source_path}", f"摘要：{record.summary}"]
        for kind in ("event", "character", "place", "clue"):
            entries = {
                "event": record.events,
                "character": record.characters,
                "place": record.places,
                "clue": record.clues,
            }[kind]
            if not entries:
                continue
            lines.append(f"- {_ENTRY_LABEL[kind]}：")
            for entry in entries:
                marks = "、".join(str(index) for index in entry.evidence)
                lines.append(f"  - {entry.text}（证据 {marks}）")
        block = "\n".join(lines)
        if used + len(block) > budget:
            blocks.append("（索引较长，其余章节未装入；需要时用 read_file 读原文）")
            break
        blocks.append(block)
        used += len(block) + 2
    return "\n\n".join(blocks)


def _with_status(record: ChapterIndexRecord, status: ChapterIndexStatus) -> ChapterIndexRecord:
    return ChapterIndexRecord(
        source_path=record.source_path,
        source_digest=record.source_digest,
        recipe_version=record.recipe_version,
        generated_at=record.generated_at,
        summary=record.summary,
        evidence=record.evidence,
        events=record.events,
        characters=record.characters,
        places=record.places,
        clues=record.clues,
        status=status,
    )


def _evidence_from_raw(raw: object) -> tuple[IndexEvidence, ...] | None:
    if not isinstance(raw, list):
        return None
    items: list[IndexEvidence] = []
    for value in raw:
        if not isinstance(value, dict):
            return None
        index = value.get("index")
        start = value.get("start")
        end = value.get("end")
        quote = value.get("quote")
        if not isinstance(index, int) or not isinstance(start, int) or not isinstance(end, int):
            return None
        if not isinstance(quote, str):
            return None
        items.append(IndexEvidence(index=index, start=start, end=end, quote=quote))
    return tuple(items)


def _entries_from_raw(raw: object, kind: EntryKind) -> tuple[ChapterIndexEntry, ...] | None:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        return None
    items: list[ChapterIndexEntry] = []
    for value in raw:
        if not isinstance(value, dict):
            return None
        text = value.get("text")
        evidence = value.get("evidence")
        if not isinstance(text, str) or not isinstance(evidence, list):
            return None
        if not all(isinstance(index, int) for index in evidence):
            return None
        items.append(
            ChapterIndexEntry(
                kind=kind,
                text=text,
                evidence=tuple(int(index) for index in evidence),
            )
        )
    return tuple(items)


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _evidence_error(path: str, why: str) -> WorkspaceError:
    return WorkspaceError(
        code="CHAPTER_INDEX_EVIDENCE_INVALID",
        where=f"chapter_index.validate:{path}",
        why=why,
        fix="按当前原文重新摘录：引文要与声明的字符区间一致，条目要引用已有证据编号",
        details={"path": path},
    )
