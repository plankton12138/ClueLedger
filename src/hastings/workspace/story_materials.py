"""故事资料的作者视图与整理覆盖（0.7）。

资料页要回答四个问题：有哪些资料、依据哪些章节、现在是否可用、如何补充或重新整理。
这一层只读：把各域的派生记录、当前磁盘状态与整理覆盖拼成作者能直接看的条目，不调用
模型，也不把给模型用的预算裁剪函数搬过来。

几条边界：

- **覆盖与有效性分开**：整理覆盖说明「这一轮是否读过并提交了结果」（有结果、无发现、
  信息不足），版本有效性说明「依据是否还对得上当前文件」。两者混成一个状态，作者就分不清
  「没整理」和「整理过但原文改了」。
- **无发现也要有指纹**：空结果同样是读过当前版本的证据，正文一改就失效，不能继续显示
  「已经核对无结果」。
- **证据只证明原文存在**：它不证明人物说法为真，也不代表质量评价。
- **条目 ID 稳定且不暴露磁盘路径**：ID 由域与稳定键散列得到，详情接口按 ID 查，
  不接受任意路径当记录标识。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

from hastings.workspace.chapter_index import ChapterIndexStore
from hastings.workspace.character_states import CharacterStateStore, build_roster
from hastings.workspace.clues import ClueStore
from hastings.workspace.disclosure import DisclosureStore, build_disclosure_state
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace, is_managed_path
from hastings.workspace.glossary import KIND_LABEL, GlossaryStore, build_glossary
from hastings.workspace.state_store import current_chapter, read_records, write_records
from hastings.workspace.story_index import (
    MODE_LABEL,
    EventObservation,
    StoryIndexStore,
)
from hastings.workspace.timeline import TimelineStore

__all__ = [
    "COVERAGE_OUTCOMES",
    "DOMAINS",
    "DOMAIN_LABEL",
    "CoverageRecord",
    "CoverageStore",
    "author_view",
    "entry_detail",
]

MaterialDomain = Literal[
    "chapter_index", "story_index", "disclosure", "roster", "timeline", "clues", "glossary"
]
CoverageOutcome = Literal["found", "empty", "insufficient"]
Freshness = Literal["valid", "stale", "source_missing", "unavailable", "none"]

DOMAINS: tuple[MaterialDomain, ...] = (
    "story_index",
    "chapter_index",
    "disclosure",
    "roster",
    "timeline",
    "clues",
    "glossary",
)

DOMAIN_LABEL: dict[str, str] = {
    "story_index": "章节索引与事件",
    "chapter_index": "章节概览",
    "disclosure": "身份披露",
    "roster": "人物状态",
    "timeline": "时间线",
    "clues": "线索与伏笔",
    "glossary": "名词表",
}

COVERAGE_OUTCOMES: tuple[CoverageOutcome, ...] = ("found", "empty", "insufficient")

#: 覆盖记录的结构版本与生成规则版本；规则变化时旧记录整份按未知处理，不猜兼容。
RECORD_VERSION = 1
RECIPE_VERSION = 1

_MAX_ENTRIES = 2_000
_DEFAULT_LIMIT = 100
_MAX_LIMIT = 200


@dataclass(frozen=True, slots=True)
class CoverageRecord:
    """一次整理在某个目标上的结果；同一目标后来的记录覆盖旧的。

    ``path`` 是正文路径；人物域另带 ``entity_path``。``digest`` 是提交结果时目标正文的
    指纹：正文一改，这条覆盖就失效，空结果也一样。
    """

    domain: MaterialDomain
    path: str
    entity_path: str
    digest: str
    outcome: CoverageOutcome
    task_id: str
    at: str
    note: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "domain": self.domain,
            "path": self.path,
            "entity_path": self.entity_path,
            "digest": self.digest,
            "outcome": self.outcome,
            "task_id": self.task_id,
            "at": self.at,
            "note": self.note,
        }


class CoverageStore:
    """作品状态目录里的 ``materials-coverage.json``；写入用临时文件整份替换。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.file = self.root / "materials-coverage.json"

    def records(self) -> tuple[CoverageRecord, ...]:
        rows: list[CoverageRecord] = []
        for item in read_records(
            self.file,
            record_version=RECORD_VERSION,
            recipe_version=RECIPE_VERSION,
            key="records",
        ):
            parsed = _coverage_from_raw(item)
            if parsed is not None:
                rows.append(parsed)
        return tuple(rows)

    def record(self, record: CoverageRecord) -> None:
        merged = {(item.domain, item.path, item.entity_path): item for item in self.records()}
        merged[(record.domain, record.path, record.entity_path)] = record
        write_records(
            self.file,
            prefix=".materials-coverage-",
            record_version=RECORD_VERSION,
            recipe_version=RECIPE_VERSION,
            key="records",
            entries=[item.to_dict() for item in merged.values()],
        )

    def for_domain(self, domain: MaterialDomain) -> tuple[CoverageRecord, ...]:
        return tuple(item for item in self.records() if item.domain == domain)

    def find(
        self, domain: MaterialDomain, path: str, entity_path: str = ""
    ) -> CoverageRecord | None:
        for item in self.records():
            if (item.domain, item.path, item.entity_path) == (domain, path, entity_path):
                return item
        return None

    def status(self, workspace: Workspace, record: CoverageRecord) -> Freshness:
        """按当前磁盘算这条覆盖是否还成立；只读，不调用模型。"""
        try:
            current = workspace.read(record.path).digest
        except WorkspaceError as exc:
            return "source_missing" if exc.code == "FILE_NOT_FOUND" else "unavailable"
        return "valid" if current == record.digest else "stale"


def _coverage_from_raw(item: dict[str, object]) -> CoverageRecord | None:
    domain = item.get("domain")
    outcome = item.get("outcome")
    if domain not in DOMAINS or outcome not in COVERAGE_OUTCOMES:
        return None
    path = item.get("path")
    digest = item.get("digest")
    task_id = item.get("task_id")
    at = item.get("at")
    if not all(isinstance(value, str) and value for value in (path, digest, at)):
        return None
    return CoverageRecord(
        domain=domain,
        path=str(path),
        entity_path=str(item.get("entity_path") or ""),
        digest=str(digest),
        outcome=outcome,
        task_id=str(task_id or ""),
        at=str(at),
        note=str(item.get("note") or ""),
    )


# -- 章节与实体清单 ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChapterRef:
    path: str
    chapter: int
    title: str
    digest: str


def chapter_refs(workspace: Workspace, as_of_chapter: int) -> tuple[ChapterRef, ...]:
    """按真实文件列出截至时点的章节，并读出当前指纹供发起整理时核对。"""
    rows: list[ChapterRef] = []
    for entry in workspace.catalog().chapters:
        if not entry.exists or entry.chapter_number is None:
            continue
        if entry.chapter_number > as_of_chapter:
            continue
        try:
            view = workspace.read(entry.path)
        except WorkspaceError:
            continue
        rows.append(
            ChapterRef(
                path=entry.path,
                chapter=entry.chapter_number,
                title=entry.display_title or entry.title or entry.path,
                digest=view.digest,
            )
        )
    return tuple(rows)


def _entity_rows(workspace: Workspace) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for entry in workspace.catalog().characters:
        if not entry.exists:
            continue
        rows.append(
            {
                "path": entry.path,
                "name": entry.display_title or entry.title or entry.path,
            }
        )
    return tuple(rows)


def _stable_id(domain: str, *parts: str) -> str:
    raw = "\x1f".join((domain, *parts))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# -- 作者视图 ---------------------------------------------------------------


def _evidence(
    path: str,
    digest: str,
    chapter: int | None,
    start: int,
    end: int,
    quote: str,
    freshness: Freshness,
) -> dict[str, object]:
    return {
        "path": path,
        "digest": digest,
        "chapter": chapter,
        "start": start,
        "end": end,
        "quote": quote,
        "freshness": freshness,
    }


def _entry(
    *,
    domain: MaterialDomain,
    key: str,
    title: str,
    subtitle: str = "",
    chapter: int | None = None,
    entity_path: str | None = None,
    coverage: str = "unrecorded",
    freshness: Freshness = "none",
    task_id: str = "",
    updated_at: str = "",
    data: dict[str, object] | None = None,
    evidence: tuple[dict[str, object], ...] = (),
    notes: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "id": _stable_id(domain, key),
        "domain": domain,
        "title": title,
        "subtitle": subtitle,
        "chapter": chapter,
        "entity_path": entity_path,
        "coverage": coverage,
        "freshness": freshness,
        "task_id": task_id,
        "updated_at": updated_at,
        "data": data or {},
        "evidence": list(evidence),
        "notes": list(notes),
    }


def _coverage_of(
    records: dict[tuple[str, str, str], CoverageRecord],
    workspace: Workspace,
    domain: MaterialDomain,
    path: str,
    entity_path: str = "",
) -> tuple[str, Freshness, str, str]:
    """目标上的覆盖结果：outcome、freshness、task_id、时间。没有记录时是 unrecorded。"""
    record = records.get((domain, path, entity_path))
    if record is None:
        return "unrecorded", "none", "", ""
    return record.outcome, _record_status(workspace, record), record.task_id, record.at


def _record_status(workspace: Workspace, record: CoverageRecord) -> Freshness:
    try:
        current = workspace.read(record.path).digest
    except WorkspaceError as exc:
        return "source_missing" if exc.code == "FILE_NOT_FOUND" else "unavailable"
    return "valid" if current == record.digest else "stale"


def _story_index_entries(
    workspace: Workspace,
    root: Path,
    as_of: int,
    chapters: tuple[ChapterRef, ...],
    coverage: dict[tuple[str, str, str], CoverageRecord],
) -> tuple[list[dict[str, object]], list[str]]:
    """统一索引的作者视图：摘要 + 带表达方式的事件；旧版概览不再单独占一页。"""
    projections = StoryIndexStore(root).read_all()
    warnings: list[str] = []
    entries: list[dict[str, object]] = []
    for ref in chapters:
        projection = projections.get(ref.path)
        outcome, cov_fresh, task_id, at = _coverage_of(coverage, workspace, "story_index", ref.path)
        if projection is None:
            entries.append(
                _entry(
                    domain="story_index",
                    key=ref.path,
                    title=ref.title,
                    subtitle="还没有整理这一章",
                    chapter=ref.chapter,
                    coverage=outcome,
                    freshness=cov_fresh,
                    task_id=task_id,
                    updated_at=at,
                    data={"summary": "", "events": [], "modes": {}},
                )
            )
            continue
        status = "valid" if projection.digest == ref.digest else "stale"
        freshness: Freshness = cast(Freshness, status)
        if status == "stale":
            warnings.append(f"{ref.title}的章节索引依据已改过，不再作为当前观察使用。")
        evidence = tuple(
            _evidence(
                item.path,
                item.digest,
                ref.chapter,
                item.start,
                item.end,
                item.quote,
                freshness,
            )
            for event in projection.events
            for item in event.evidence
        )
        entries.append(
            _entry(
                domain="story_index",
                key=ref.path,
                title=ref.title,
                subtitle=projection.summary,
                chapter=ref.chapter,
                coverage=outcome,
                freshness=freshness,
                task_id=task_id,
                updated_at=projection.generated_at or at,
                data={
                    "summary": projection.summary,
                    "events": [
                        {
                            "id": item.id,
                            "text": item.text,
                            "mode": item.mode,
                            "mode_label": MODE_LABEL.get(item.mode, item.mode),
                            "chapter": item.chapter,
                            "speaker": list(item.speaker) if item.speaker else None,
                            "story_time": item.story_time.to_dict(),
                        }
                        for item in projection.events
                    ],
                    "modes": _mode_counts(projection.events),
                },
                evidence=evidence,
            )
        )
    return entries, warnings


def _mode_counts(events: Sequence[EventObservation]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in events:
        counts[item.mode] = counts.get(item.mode, 0) + 1
    return counts


def _chapter_index_entries(
    workspace: Workspace,
    root: Path,
    as_of: int,
    chapters: tuple[ChapterRef, ...],
    coverage: dict[tuple[str, str, str], CoverageRecord],
) -> tuple[list[dict[str, object]], list[str]]:
    store = ChapterIndexStore(root)
    records = store.read_all()
    warnings: list[str] = []
    entries: list[dict[str, object]] = []
    for ref in chapters:
        record = records.get(ref.path)
        if record is None:
            outcome, cov_fresh, task_id, at = _coverage_of(
                coverage, workspace, "chapter_index", ref.path
            )
            entries.append(
                _entry(
                    domain="chapter_index",
                    key=ref.path,
                    title=ref.title,
                    subtitle="还没有整理这一章",
                    chapter=ref.chapter,
                    coverage=outcome,
                    freshness=cov_fresh,
                    task_id=task_id,
                    updated_at=at,
                    data={
                        "summary": "",
                        "events": [],
                        "characters": [],
                        "places": [],
                        "clues": [],
                    },
                )
            )
            continue
        status = store.status(record, workspace)
        freshness: Freshness = "none" if status == "missing" else cast(Freshness, status)
        if freshness == "stale":
            warnings.append(f"{ref.title}的概览依据已改过，不再作为当前事实使用。")
        outcome, _, task_id, at = _coverage_of(coverage, workspace, "chapter_index", ref.path)
        entries.append(
            _entry(
                domain="chapter_index",
                key=ref.path,
                title=ref.title,
                subtitle=record.summary,
                chapter=ref.chapter,
                coverage=outcome,
                freshness=freshness,
                task_id=task_id,
                updated_at=record.generated_at or at,
                data={
                    "summary": record.summary,
                    "events": [item.text for item in record.events],
                    "characters": [item.text for item in record.characters],
                    "places": [item.text for item in record.places],
                    "clues": [item.text for item in record.clues],
                },
                evidence=tuple(
                    _evidence(
                        ref.path,
                        record.source_digest,
                        ref.chapter,
                        item.start,
                        item.end,
                        item.quote,
                        freshness,
                    )
                    for item in record.evidence
                ),
            )
        )
    return entries, warnings


def _timeline_entries(
    workspace: Workspace,
    root: Path,
    as_of: int,
    chapters: tuple[ChapterRef, ...],
    coverage: dict[tuple[str, str, str], CoverageRecord],
) -> tuple[list[dict[str, object]], list[str]]:
    store = TimelineStore(root)
    by_chapter = {item.chapter: item for item in store.entries()}
    entries: list[dict[str, object]] = []
    warnings: list[str] = []
    seen: dict[int, str] = {}
    for ref in chapters:
        if ref.chapter in seen:
            warnings.append(
                f"第 {ref.chapter} 章出现在多个文件里（{seen[ref.chapter]}、{ref.path}）；"
                "章序重复时不能按时点整理，先修正文件名编号。"
            )
        seen.setdefault(ref.chapter, ref.path)
        record = by_chapter.get(ref.chapter)
        outcome, cov_fresh, task_id, at = _coverage_of(coverage, workspace, "timeline", ref.path)
        if record is None:
            entries.append(
                _entry(
                    domain="timeline",
                    key=ref.path,
                    title=ref.title,
                    subtitle="时间未知",
                    chapter=ref.chapter,
                    coverage=outcome,
                    freshness=cov_fresh,
                    task_id=task_id,
                    updated_at=at,
                    data={"when": "", "span": "", "location": ""},
                )
            )
            continue
        status = store.status(workspace, record)
        freshness: Freshness = cast(Freshness, status)
        if freshness != "valid":
            warnings.append(f"{ref.title}的时间锚点依据已改过或删除，不再采用。")
        subtitle = record.when
        if record.location:
            subtitle = f"{subtitle}｜{record.location}"
        entries.append(
            _entry(
                domain="timeline",
                key=ref.path,
                title=ref.title,
                subtitle=subtitle,
                chapter=ref.chapter,
                coverage=outcome,
                freshness=freshness,
                task_id=task_id,
                updated_at=at,
                data={
                    "when": record.when,
                    "span": record.span,
                    "location": record.location,
                },
                evidence=(
                    _evidence(
                        record.evidence.path,
                        record.evidence.digest,
                        record.chapter,
                        record.evidence.start,
                        record.evidence.end,
                        record.evidence.quote,
                        freshness,
                    ),
                ),
            )
        )
    return entries, warnings


def _clue_entries(
    workspace: Workspace,
    root: Path,
    as_of: int,
    coverage: dict[tuple[str, str, str], CoverageRecord],
) -> tuple[list[dict[str, object]], list[str]]:
    store = ClueStore(root)
    entries: list[dict[str, object]] = []
    warnings: list[str] = []
    for item in store.entries():
        if item.planted_chapter > as_of:
            continue
        status = store.status(workspace, item)
        freshness: Freshness = cast(Freshness, status)
        if freshness != "valid":
            warnings.append(f"线索「{item.clue}」的依据已改过或删除，不再采用。")
        resolved = (
            item.status == "resolved"
            and item.resolution_chapter is not None
            and item.resolution_chapter <= as_of
        )
        status_label = (
            "已回收" if resolved else ("已废弃" if item.status == "dropped" else "未回收")
        )
        overdue = (
            not resolved
            and item.status == "open"
            and item.due_chapter is not None
            and item.due_chapter <= as_of
        )
        outcome, _, task_id, at = _coverage_of(coverage, workspace, "clues", item.evidence.path)
        evidence = [
            _evidence(
                item.evidence.path,
                item.evidence.digest,
                item.planted_chapter,
                item.evidence.start,
                item.evidence.end,
                item.evidence.quote,
                freshness,
            )
        ]
        if item.resolution_evidence is not None:
            evidence.append(
                _evidence(
                    item.resolution_evidence.path,
                    item.resolution_evidence.digest,
                    item.resolution_chapter,
                    item.resolution_evidence.start,
                    item.resolution_evidence.end,
                    item.resolution_evidence.quote,
                    freshness,
                )
            )
        entries.append(
            _entry(
                domain="clues",
                key=item.clue.strip(),
                title=item.clue,
                subtitle=(
                    f"第{item.planted_chapter}章埋下"
                    + (f"｜计划第{item.due_chapter}章回收" if item.due_chapter else "｜未定回收章")
                    + (f"｜{status_label}" if status_label != "未回收" else "")
                ),
                chapter=item.planted_chapter,
                coverage=outcome,
                freshness=freshness,
                task_id=task_id,
                updated_at=at,
                data={
                    "clue": item.clue,
                    "planted_chapter": item.planted_chapter,
                    "due_chapter": item.due_chapter,
                    "status": "resolved" if resolved else item.status,
                    "label": status_label,
                    "overdue": overdue,
                    "supports": item.supports,
                    "resolution_chapter": item.resolution_chapter if resolved else None,
                },
                evidence=tuple(evidence),
            )
        )

    def rank(entry: dict[str, object]) -> tuple[int, int, int]:
        data = cast(dict[str, object], entry["data"])
        if data["status"] == "open":
            if data["overdue"]:
                return (0, int(cast(int, data["due_chapter"] or 0)), 0)
            if data["due_chapter"] is None:
                return (2, 0, -int(cast(int, data["planted_chapter"])))
            return (1, int(cast(int, data["due_chapter"])), 0)
        return (3, -int(cast(int, data["planted_chapter"])), 0)

    entries.sort(key=rank)
    return entries, warnings


def _glossary_entries(
    workspace: Workspace,
    root: Path,
    as_of: int,
    coverage: dict[tuple[str, str, str], CoverageRecord],
) -> tuple[list[dict[str, object]], list[str], list[dict[str, object]]]:
    store = GlossaryStore(root)
    state = build_glossary(workspace, store, as_of_chapter=as_of)
    entries: list[dict[str, object]] = []
    for term in state.terms:
        evidence: tuple[dict[str, object], ...] = ()
        freshness: Freshness = "valid"
        task_id = ""
        at = ""
        if term.evidence is not None:
            status = store.status(workspace, term)
            freshness = cast(Freshness, status)
            outcome, _, task_id, at = _coverage_of(
                coverage, workspace, "glossary", term.evidence.path
            )
            evidence = (
                _evidence(
                    term.evidence.path,
                    term.evidence.digest,
                    term.first_chapter,
                    term.evidence.start,
                    term.evidence.end,
                    term.evidence.quote,
                    freshness,
                ),
            )
        entries.append(
            _entry(
                domain="glossary",
                key=f"{term.term}\x1f{term.kind}",
                title=term.term,
                subtitle="、".join(term.aliases),
                chapter=term.first_chapter or None,
                coverage="found" if term.evidence is not None else "unrecorded",
                freshness=freshness,
                task_id=task_id,
                updated_at=at,
                data={
                    "term": term.term,
                    "kind": term.kind,
                    "label": KIND_LABEL[term.kind],
                    "aliases": list(term.aliases),
                    "first_chapter": term.first_chapter,
                    "source": term.source,
                },
                evidence=evidence,
            )
        )
    entries.sort(
        key=lambda item: (
            str(cast(dict[str, object], item["data"])["kind"]),
            str(item["title"]),
        )
    )
    conflicts: list[dict[str, object]] = [
        {"term": item.term, "kind": item.kind, "detail": item.detail} for item in state.conflicts
    ]
    return entries, list(state.warnings), conflicts


def _disclosure_entries(
    workspace: Workspace,
    root: Path,
    as_of: int,
    chapters: tuple[ChapterRef, ...],
    coverage: dict[tuple[str, str, str], CoverageRecord],
) -> tuple[list[dict[str, object]], list[str], list[dict[str, object]]]:
    store = DisclosureStore(root)
    state = build_disclosure_state(workspace, store, as_of_chapter=as_of)
    facts = store.entries()
    entries: list[dict[str, object]] = []
    warnings: list[str] = []
    for entity in state.entities:
        owned = [item for item in facts if item.entity_path == entity.path]
        evidence: list[dict[str, object]] = []
        for item in owned:
            if item.chapter > as_of:
                continue
            status = store.status(workspace, item)
            if status != "valid":
                continue
            evidence.append(
                _evidence(
                    item.evidence.path,
                    item.evidence.digest,
                    item.chapter,
                    item.evidence.start,
                    item.evidence.end,
                    item.evidence.quote,
                    "valid",
                )
            )
        # 覆盖按「人物 + 本次范围内的章节」汇总：只整理过第 6—8 章，就不能宣称前五章已核对。
        outcomes = []
        for ref in chapters:
            record = coverage.get(("disclosure", ref.path, entity.path))
            if record is not None:
                outcomes.append(record)
        outcome = _aggregate_outcome(outcomes)
        task_id = outcomes[-1].task_id if outcomes else ""
        at = outcomes[-1].at if outcomes else ""
        freshness: Freshness = (
            cast(Freshness, entity.record_status) if entity.record_status else "none"
        )
        entries.append(
            _entry(
                domain="disclosure",
                key=entity.path,
                title=entity.name,
                subtitle=entity.surface or "、".join(entity.roles),
                entity_path=entity.path,
                coverage=outcome,
                freshness=freshness,
                task_id=task_id,
                updated_at=at,
                data={
                    "name": entity.name,
                    "surface": entity.surface,
                    "aliases": list(entity.aliases),
                    "roles": list(entity.roles),
                    "revealed": [
                        {"fact": item.fact, "chapter": item.chapter, "source": item.source}
                        for item in entity.revealed
                    ],
                    "upcoming": [
                        {"fact": item.fact, "chapter": item.chapter} for item in entity.upcoming
                    ],
                    "planned": [
                        {"fact": item.fact, "chapter": item.chapter} for item in entity.planned
                    ],
                    "verified": entity.verified,
                },
                evidence=tuple(evidence),
                notes=entity.warnings,
            )
        )
        warnings.extend(f"{entity.name}：{item}" for item in entity.warnings)
    conflicts: list[dict[str, object]] = [
        {
            "path": item.path,
            "name": item.name,
            "kind": item.kind,
            "label": {
                "alias_collision": "别名重复",
                "duplicate_culprit": "两个真凶",
                "fact_redeclared": "同一事实重复声明",
            }[item.kind],
            "detail": item.detail,
        }
        for item in state.conflicts
    ]
    return entries, warnings, conflicts


def _roster_entries(
    workspace: Workspace,
    root: Path,
    as_of: int,
    chapters: tuple[ChapterRef, ...],
    coverage: dict[tuple[str, str, str], CoverageRecord],
) -> tuple[list[dict[str, object]], list[str]]:
    store = CharacterStateStore(root)
    state = build_roster(workspace, store, as_of_chapter=as_of)
    entries: list[dict[str, object]] = []
    warnings: list[str] = []
    for character in state.characters:
        latest = character.current
        outcomes = [
            record
            for ref in chapters
            if (record := coverage.get(("roster", ref.path, character.path))) is not None
        ]
        outcome = _aggregate_outcome(outcomes)
        task_id = outcomes[-1].task_id if outcomes else ""
        at = outcomes[-1].at if outcomes else ""
        if latest is None:
            freshness: Freshness = "stale" if character.stale else "none"
        else:
            freshness = cast(Freshness, store.status(workspace, latest))
        evidence: tuple[dict[str, object], ...] = ()
        if latest is not None:
            evidence = (
                _evidence(
                    latest.evidence.path,
                    latest.evidence.digest,
                    latest.chapter,
                    latest.evidence.start,
                    latest.evidence.end,
                    latest.evidence.quote,
                    freshness,
                ),
            )
        entries.append(
            _entry(
                domain="roster",
                key=character.path,
                title=character.name,
                subtitle=(
                    latest.location
                    if latest is not None and latest.location
                    else ("状态未知" if latest is None else "位置未记")
                ),
                entity_path=character.path,
                coverage=outcome,
                freshness=freshness,
                task_id=task_id,
                updated_at=at,
                data={
                    "name": character.name,
                    "location": latest.location if latest is not None else "",
                    # 没有核对记录时生死未知：不能用 alive=true 冒充「还活着」。
                    "alive": latest.alive if latest is not None else None,
                    "stance": latest.stance if latest is not None else "",
                    "chapter": latest.chapter if latest is not None else None,
                    "lag": character.lag,
                },
                evidence=evidence,
                notes=character.warnings,
            )
        )
        warnings.extend(f"{character.name}：{item}" for item in character.warnings)
    return entries, warnings


def _aggregate_outcome(records: list[CoverageRecord]) -> str:
    if not records:
        return "unrecorded"
    outcomes = {item.outcome for item in records}
    if "found" in outcomes:
        return "found"
    if "empty" in outcomes:
        return "empty"
    return "insufficient"


def _coverage_stats(
    workspace: Workspace,
    domain: MaterialDomain,
    chapters: tuple[ChapterRef, ...],
    entities: tuple[dict[str, object], ...],
    records: tuple[CoverageRecord, ...],
) -> dict[str, int]:
    scoped = [item for item in records if item.domain == domain]
    chapter_paths = {ref.path for ref in chapters}
    entity_paths = {item["path"] for item in entities}
    total = 0
    counts = {"found": 0, "empty": 0, "insufficient": 0, "stale": 0, "unrecorded": 0}
    for record in scoped:
        if record.path not in chapter_paths:
            continue
        if domain in ("disclosure", "roster") and record.entity_path not in entity_paths:
            continue
        if _record_status(workspace, record) == "stale":
            counts["stale"] += 1
        if record.outcome in counts:
            counts[record.outcome] += 1
    total = len(chapters) * len(entities) if domain in ("disclosure", "roster") else len(chapters)
    covered = counts["found"] + counts["empty"] + counts["insufficient"]
    counts["unrecorded"] = max(total - covered, 0)
    counts["total"] = total
    return counts


def _matches(entry: dict[str, object], query: str) -> bool:
    if not query:
        return True
    needle = query.casefold()
    haystack = " ".join(
        [
            str(entry["title"]),
            str(entry["subtitle"]),
            json.dumps(entry["data"], ensure_ascii=False),
        ]
    ).casefold()
    return needle in haystack


def _build(
    workspace: Workspace,
    root: Path,
    domain: MaterialDomain,
    as_of: int,
) -> tuple[
    list[dict[str, object]],
    list[str],
    list[dict[str, object]],
    tuple[ChapterRef, ...],
    tuple[dict[str, object], ...],
    dict[str, int],
]:
    chapters = chapter_refs(workspace, as_of)
    entities = _entity_rows(workspace)
    store = CoverageStore(root)
    coverage: dict[tuple[str, str, str], CoverageRecord] = {
        (item.domain, item.path, item.entity_path): item for item in store.records()
    }
    conflicts: list[dict[str, object]] = []
    if domain not in DOMAINS:
        raise WorkspaceError(
            code="MATERIAL_DOMAIN_INVALID",
            where=f"story_materials.view:{domain}",
            why=f"没有这种资料：{domain or '（空）'}",
            fix="用资料页列出的种类之一；未知种类不按相近种类替代",
            details={"domain": domain},
        )
    if domain == "story_index":
        entries, warnings = _story_index_entries(workspace, root, as_of, chapters, coverage)
    elif domain == "chapter_index":
        entries, warnings = _chapter_index_entries(workspace, root, as_of, chapters, coverage)
    elif domain == "timeline":
        entries, warnings = _timeline_entries(workspace, root, as_of, chapters, coverage)
    elif domain == "clues":
        entries, warnings = _clue_entries(workspace, root, as_of, coverage)
    elif domain == "glossary":
        entries, warnings, conflicts = _glossary_entries(workspace, root, as_of, coverage)
    elif domain == "disclosure":
        entries, warnings, conflicts = _disclosure_entries(
            workspace, root, as_of, chapters, coverage
        )
    else:
        entries, warnings = _roster_entries(workspace, root, as_of, chapters, coverage)
    stats = _coverage_stats(workspace, domain, chapters, entities, store.records())
    return entries, warnings, conflicts, chapters, entities, stats


def author_view(
    workspace: Workspace,
    root: Path,
    domain: MaterialDomain,
    *,
    as_of_chapter: int | None = None,
    query: str = "",
    limit: int = _DEFAULT_LIMIT,
    cursor: str = "",
) -> dict[str, object]:
    """作者视图：条目、证据摘要、覆盖统计与实际采用的时点。

    截止章只影响浏览；服务端仍以真实文件为准，超出全书末章时按末章返回并在
    ``as_of_chapter`` 里说明实际用了哪一章。
    """
    latest = current_chapter(workspace)
    as_of = latest if as_of_chapter is None else min(latest, max(0, as_of_chapter))
    entries, warnings, conflicts, chapters, entities, stats = _build(workspace, root, domain, as_of)
    filtered = [item for item in entries if _matches(item, query.strip())]
    offset = _parse_cursor(cursor)
    size = min(max(limit, 1), _MAX_LIMIT)
    page = filtered[offset : offset + size]
    next_cursor = str(offset + len(page)) if offset + len(page) < len(filtered) else ""
    return {
        "domain": domain,
        "label": DOMAIN_LABEL[domain],
        "as_of_chapter": as_of,
        "current_chapter": latest,
        "chapters": [
            {
                "path": item.path,
                "chapter": item.chapter,
                "title": item.title,
                "digest": item.digest,
            }
            for item in chapters
        ],
        "entities": list(entities),
        "entries": page,
        "total": len(filtered),
        "next_cursor": next_cursor,
        "coverage": stats,
        "warnings": warnings,
        "conflicts": conflicts,
    }


def entry_detail(
    workspace: Workspace,
    root: Path,
    domain: MaterialDomain,
    entry_id: str,
    *,
    as_of_chapter: int | None = None,
) -> dict[str, object]:
    """按稳定 ID 取完整条目；找不到时给出可修正的错误，不用空对象冒充。"""
    latest = current_chapter(workspace)
    as_of = latest if as_of_chapter is None else min(latest, max(0, as_of_chapter))
    entries, warnings, conflicts, _, _, stats = _build(workspace, root, domain, as_of)
    for item in entries:
        if item["id"] == entry_id:
            return {
                "domain": domain,
                "as_of_chapter": as_of,
                "entry": item,
                "coverage": stats,
                "warnings": warnings,
                "conflicts": conflicts,
            }
    raise WorkspaceError(
        code="MATERIAL_ENTRY_NOT_FOUND",
        where=f"story_materials.entry:{entry_id}",
        why="这条资料在当前截止章下不存在，可能来源已删除或时点已改变",
        fix="重新读取资料列表后再打开",
        details={"domain": domain, "entry": entry_id, "as_of_chapter": as_of},
    )


def _parse_cursor(cursor: str) -> int:
    try:
        value = int(cursor)
    except (TypeError, ValueError):
        return 0
    return max(value, 0)


def is_material_chapter_path(path: str) -> bool:
    """正文路径判断集中在这里：资料目标只能是正文章节。"""
    return is_managed_path(path) and path.startswith("正文/") and path.endswith(".md")


def now_iso() -> str:
    return _now()
