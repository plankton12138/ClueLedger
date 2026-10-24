"""有界资料检索（0.8 F1）。

回答三个问题，不混在一起：

- **L0 短目录**：这一轮装了哪些模块版本、正文有哪些章；只给定位，不加载原文。
- **L1 有界查询**：按显式 ID、人物关联、章区间与关键词筛出可定位的条目与观察；每条都带
  来源性质与版本，命中不等于读过。
- **L2 详情**：仍由 ``ModuleLibrary.read_entry`` 与作品 ``read_file`` 按区间读取，这里不复制
  另一套读取凭据。

几条边界：

- 只查本轮装配里的精确版本；未装模块不会因为名字相似出现在候选里。
- 正文观察按作品隔离；过期投影与过期旧版记录不当作当前依据，只在警告里说明跳过了哪一章。
- 排序是确定性的：显式 ID／关联 → 章区间 → 模块 → 关键词 → 目录；同分按 ID。
- 不做向量检索、不递归遍历；一次查询最多走装配里已有的简单关联。
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from hastings.library.catalog import CatalogEntry, CatalogRevision
from hastings.library.modules import ModuleLibrary
from hastings.library.presets import PRESETS
from hastings.workspace.chapter_index import ChapterIndexStore
from hastings.workspace.clues import ClueEntry, ClueStore
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.glossary import GlossaryStore
from hastings.workspace.module_assembly import Assembly, Binding
from hastings.workspace.state_store import current_chapter
from hastings.workspace.story_index import (
    MODE_LABEL,
    ChapterProjection,
    EventObservation,
    StoryIndexStore,
)
from hastings.workspace.timeline import TimelineStore

__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "MaterialHit",
    "MaterialQuery",
    "catalog_brief",
    "module_overview",
    "query_materials",
]

Source = Literal["author", "observed", "legacy_unverified"]
Freshness = Literal["valid", "stale", "source_missing", "unavailable", "none"]

DEFAULT_LIMIT = 20
MAX_LIMIT = 50
#: 一次查询返回的正文总量上限；长条目去详情读，不在这里把一条截成半句。
MAX_TOTAL_CHARS = 6_000
EXCERPT_MAX_CHARS = 240
#: 关键词落到条目正文时，一次查询最多扫多少字符与条目；超限明确说明没扫完，不静默漏项。
MAX_SCAN_CHARS = 200_000
MAX_SCAN_ENTRIES = 300
MATCH_CONTEXT_CHARS = 120

_SOURCE_RANK: dict[str, int] = {"author": 0, "observed": 1, "legacy_unverified": 2}
_MATCH_RANK: dict[str, int] = {"entity": 0, "chapter": 1, "module": 2, "keyword": 3}
_PLAN_MODULE_KINDS = frozenset({"clues", "plot"})


@dataclass(frozen=True, slots=True)
class MaterialQuery:
    """一次查询的输入；范围只能收窄，不能借参数扩大本轮装配。"""

    kinds: tuple[str, ...] = ()
    module_ids: tuple[str, ...] = ()
    text: str = ""
    entity_ids: tuple[str, ...] = ()
    chapter_paths: tuple[str, ...] = ()
    as_of_chapter: int | None = None
    include_plans: bool = True
    cursor: str = ""
    limit: int = DEFAULT_LIMIT


@dataclass(frozen=True, slots=True)
class MaterialHit:
    """一条可定位的结果；原文仍要按版本与区间读取。"""

    id: str
    kind: str
    source: Source
    title: str
    excerpt: str
    freshness: Freshness
    matched_by: tuple[str, ...] = ()
    module_ref: tuple[str, str, str, str] | None = None
    chapter: int | None = None
    entity_path: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "source": self.source,
            "title": self.title,
            "excerpt": self.excerpt,
            "freshness": self.freshness,
            "matched_by": list(self.matched_by),
            "module_ref": list(self.module_ref) if self.module_ref else None,
            "chapter": self.chapter,
            "entity_path": self.entity_path,
        }


def catalog_brief(
    workspace: Workspace,
    assembly: Assembly,
    library: ModuleLibrary | None,
    *,
    offset: int = 0,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, object]:
    """L0：已装模块版本与正文短目录；条目过多时只给数量与查询入口。"""
    size = _limit(limit)
    start = max(0, int(offset))
    warnings: list[str] = []
    bindings: list[dict[str, object]] = []
    labels: dict[str, str] = {item.kind: item.label for item in PRESETS}
    for binding in assembly.bindings:
        row: dict[str, object] = {
            "binding_id": binding.id,
            "module_id": binding.module.module_id,
            "revision_id": binding.module.revision_id,
            "label": binding.label,
            "kind": "",
            "name": binding.label,
            "entry_count": 0,
            "missing": False,
        }
        if library is not None:
            try:
                revision = library.revision(
                    module_id=binding.module.module_id,
                    revision_id=binding.module.revision_id,
                )
                row.update(
                    {
                        "kind": revision.module_kind,
                        "kind_label": labels.get(revision.module_kind, revision.module_kind),
                        "name": revision.module_name or binding.label,
                        "entry_count": len(revision.entries),
                    }
                )
            except WorkspaceError as exc:
                row["missing"] = True
                warnings.append(
                    f"已装模块「{binding.label or binding.module.module_id}」当前不可用：{exc.why}"
                )
        bindings.append(row)

    all_chapters = workspace.catalog().chapters
    chapters = [
        {
            "path": entry.path,
            "title": entry.display_title or entry.title or entry.path,
            "chapter": entry.chapter_number,
            "exists": entry.exists,
        }
        for entry in all_chapters[start : start + size]
    ]
    total_chapters = len(all_chapters)
    next_offset = start + len(chapters)
    return {
        "current_chapter": current_chapter(workspace),
        "bindings": bindings,
        "chapters": chapters,
        "total_chapters": total_chapters,
        "next_cursor": str(next_offset) if next_offset < total_chapters else "",
        "warnings": warnings,
    }


def module_overview(assembly: Assembly, library: ModuleLibrary | None, *, limit: int = 20) -> str:
    """L0 装配概览：只列已装实例与精确版本，不列未装库内容，也不读条目正文。"""
    if not assembly.bindings:
        if library is None:
            return "（没有装入模块；本轮只使用本作品正文与设定。）"
        return "（没有装入模块；本轮只使用本作品正文与设定。用 list_modules 查看当前装配。）"
    labels: dict[str, str] = {item.kind: item.label for item in PRESETS}
    lines: list[str] = []
    for binding in assembly.bindings[: max(1, limit)]:
        base = (
            f"- {binding.label or binding.module.module_id}"
            f"｜{binding.module.module_id}@{binding.module.revision_id}"
        )
        if library is None:
            lines.append(f"{base}｜（模块库不可用）")
            continue
        try:
            revision = library.revision(
                module_id=binding.module.module_id,
                revision_id=binding.module.revision_id,
            )
        except WorkspaceError as exc:
            lines.append(f"{base}｜（不可用：{exc.why}）")
            continue
        lines.append(
            f"{base}｜{labels.get(revision.module_kind, revision.module_kind)}"
            f"｜{len(revision.entries)} 条"
        )
    remaining = len(assembly.bindings) - min(len(assembly.bindings), max(1, limit))
    if remaining > 0:
        lines.append(f"- （还有 {remaining} 个模块未列出；到模块库查看精确版本）")
    if library is not None:
        lines.append(
            "- （用 list_modules 看完整装配，query_records 检索条目，read_record 读原文；"
            "命中不等于读过。）"
        )
    return "\n".join(lines)


def query_materials(
    workspace: Workspace,
    root: Path,
    assembly: Assembly,
    library: ModuleLibrary | None,
    query: MaterialQuery,
) -> dict[str, object]:
    """L1：返回有界候选、来源与实际检索范围；没有命中时也说明搜过什么。"""
    started = time.monotonic()
    size = _limit(query.limit)
    as_of = current_chapter(workspace, query.as_of_chapter)
    fingerprint = _fingerprint(query)
    offset = _cursor_offset(query.cursor, fingerprint)
    warnings: list[str] = []
    hits: list[MaterialHit] = []

    selected, missing = _selected_bindings(assembly, query.module_ids)
    for module_id in missing:
        warnings.append(f"模块 {module_id} 不在本轮装配里，未查询。")
    scan = {"chars": 0, "entries": 0, "limited": False}
    for binding in selected:
        if library is None:
            warnings.append("当前没有可用的模块库，只查询本作品观察。")
            break
        try:
            revision = library.revision(
                module_id=binding.module.module_id,
                revision_id=binding.module.revision_id,
            )
        except WorkspaceError as exc:
            warnings.append(
                f"已装模块「{binding.label or binding.module.module_id}」不可用：{exc.why}"
            )
            continue
        if not query.include_plans and revision.module_kind in _PLAN_MODULE_KINDS:
            continue
        for entry in revision.entries:
            hit = _entry_hit(library, revision, entry, query, scan)
            if hit is not None:
                hits.append(hit)
    if scan["limited"]:
        warnings.append(
            "关键词只扫了部分条目正文；还有条目没有扫到。缩小模块范围或给出更具体的词后重试。"
        )

    hits.extend(_observation_hits(workspace, root, query, as_of, warnings))

    filtered = [hit for hit in hits if _passes_filters(hit, query)]
    filtered.sort(key=_sort_key)
    page, consumed = _page(filtered, offset, size)
    next_offset = offset + consumed
    return {
        "items": [hit.to_dict() for hit in page],
        "total": len(filtered),
        "next_cursor": _cursor(next_offset, fingerprint) if next_offset < len(filtered) else "",
        "scope": {
            "as_of_chapter": as_of,
            "module_ids": [binding.module.module_id for binding in selected],
            "chapter_paths": [
                entry.path
                for entry in workspace.catalog().chapters
                if entry.chapter_number is not None and entry.chapter_number <= as_of
            ],
        },
        "metrics": _metrics(filtered, page, scan, started),
        "warnings": warnings,
    }


def _metrics(
    filtered: Sequence[MaterialHit],
    page: Sequence[MaterialHit],
    scan: dict[str, int | bool],
    started: float,
) -> dict[str, object]:
    """这次查询实际扫了什么、命中了多少；阈值校准与漏检复盘都读这一份。"""
    sources = {"author": 0, "observed": 0, "legacy_unverified": 0}
    matched: dict[str, int] = {}
    for hit in filtered:
        sources[hit.source] = sources.get(hit.source, 0) + 1
        for reason in hit.matched_by:
            matched[reason] = matched.get(reason, 0) + 1
    return {
        "candidates": len(filtered),
        "returned": len(page),
        "returned_chars": sum(len(hit.excerpt) for hit in page),
        "scanned_entries": int(scan["entries"]),
        "scanned_chars": int(scan["chars"]),
        "scan_limited": bool(scan["limited"]),
        "sources": sources,
        "matched_by": matched,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
    }


def _selected_bindings(
    assembly: Assembly, module_ids: Sequence[str]
) -> tuple[list[Binding], list[str]]:
    wanted = {item for item in module_ids if item}
    if not wanted:
        return list(assembly.bindings), []
    selected = [binding for binding in assembly.bindings if binding.module.module_id in wanted]
    missing = sorted(wanted - {binding.module.module_id for binding in selected})
    return selected, missing


def _entry_hit(
    library: ModuleLibrary,
    revision: CatalogRevision,
    entry: CatalogEntry,
    query: MaterialQuery,
    scan: dict[str, int | bool],
) -> MaterialHit | None:
    if query.kinds and entry.kind not in query.kinds:
        return None
    if not query.include_plans and str(entry.fields.get("basis") or "") == "plan":
        return None
    label = MODULE_LABEL(revision.module_kind)
    structural = " ".join(
        [entry.title, *entry.aliases, _fields_text(entry.fields), revision.module_name, label]
    )
    excerpt: str | None = ""
    if query.text:
        if _matches(structural, query.text):
            excerpt = structural
        else:
            excerpt = _scan_entry(library, revision, entry, query.text, scan)
            if excerpt is None:
                return None
    matched: list[str] = []
    if query.entity_ids and entry.entry_id in query.entity_ids:
        matched.append("entity")
    if query.module_ids:
        matched.append("module")
    if query.text:
        matched.append("keyword")
    return MaterialHit(
        id=f"{revision.module_id}:{revision.revision_id}:{entry.entry_id}",
        kind=entry.kind,
        source="author",
        title=entry.title,
        excerpt=_excerpt(
            excerpt
            or "｜".join(
                part
                for part in (
                    entry.title,
                    "、".join(entry.aliases),
                    _fields_text(entry.fields),
                )
                if part
            )
        ),
        freshness="valid",
        matched_by=tuple(matched),
        module_ref=(
            revision.library_id,
            revision.module_id,
            revision.revision_id,
            entry.entry_id,
        ),
    )


def _scan_entry(
    library: ModuleLibrary,
    revision: CatalogRevision,
    entry: CatalogEntry,
    needle: str,
    scan: dict[str, int | bool],
) -> str | None:
    """关键词不在结构字段时再扫正文；有界扫描，超限由调用方写进警告。"""
    if bool(scan["limited"]):
        return None
    matched_entries = int(scan["entries"])
    if matched_entries >= MAX_SCAN_ENTRIES:
        scan["limited"] = True
        return None
    scan["entries"] = matched_entries + 1
    start = 0
    while True:
        try:
            detail = library.read_entry(
                module_id=revision.module_id,
                revision_id=revision.revision_id,
                entry_id=entry.entry_id,
                start=start,
            )
        except WorkspaceError:
            return None
        text = str(detail.get("text") or "")
        scan["chars"] = int(scan["chars"]) + len(text)
        if int(scan["chars"]) > MAX_SCAN_CHARS:
            scan["limited"] = True
            if not text:
                return None
        index = " ".join(text.split()).casefold().find(needle.strip().casefold())
        if index >= 0:
            collapsed = " ".join(text.split())
            begin = max(0, index - MATCH_CONTEXT_CHARS)
            finish = min(len(collapsed), index + len(needle) + MATCH_CONTEXT_CHARS)
            return collapsed[begin:finish]
        if int(scan["chars"]) >= MAX_SCAN_CHARS:
            scan["limited"] = True
            return None
        if not detail.get("truncated"):
            return None
        next_cursor = detail.get("next_cursor")
        start = next_cursor if isinstance(next_cursor, int) else 0
        if start <= 0:
            return None


def _observation_hits(
    workspace: Workspace,
    root: Path,
    query: MaterialQuery,
    as_of: int,
    warnings: list[str],
) -> list[MaterialHit]:
    """本作品观察：统一章节索引优先，旧概览与时间锚点只在没有新投影时只读兜底。"""
    hits: list[MaterialHit] = []
    projections = StoryIndexStore(root).read_all()
    legacy = ChapterIndexStore(root).read_all()
    anchors = {item.chapter: item for item in TimelineStore(root).entries()}
    clues = ClueStore(root)
    glossary = GlossaryStore(root)

    for path, projection in projections.items():
        if projection.chapter > as_of:
            continue
        try:
            digest = workspace.read(path).digest
        except WorkspaceError:
            warnings.append(f"第{projection.chapter}章的原文当前不可用，观察未纳入查询。")
            continue
        if projection.digest != digest:
            warnings.append(f"第{projection.chapter}章的索引依据已改动，旧观察未纳入查询。")
            continue
        if query.chapter_paths and path not in query.chapter_paths:
            continue
        hits.extend(_projection_hits(path, projection, query))

    for path, record in legacy.items():
        if path in projections:
            continue  # 新投影已接管这一章，旧概览不再作为第二份来源。
        chapter = _chapter_number(path)
        if chapter is None or chapter > as_of:
            continue
        if query.chapter_paths and path not in query.chapter_paths:
            continue
        status = ChapterIndexStore(root).status(record, workspace)
        if status != "valid":
            warnings.append(f"第{chapter}章的旧版概览已不能作为当前依据，未纳入查询。")
            continue
        if not query.kinds or "chapter_summary" in query.kinds:
            haystack = f"{record.summary} {path}"
            if _matches(haystack, query.text):
                hits.append(
                    MaterialHit(
                        id=f"legacy-chapter:{path}",
                        kind="chapter_summary",
                        source="legacy_unverified",
                        title=f"第{chapter}章",
                        excerpt=_excerpt(record.summary),
                        freshness="valid",
                        matched_by=_matched(
                            chapter_match=bool(query.chapter_paths),
                            keyword_match=bool(query.text),
                        ),
                        chapter=chapter,
                    )
                )
        anchor = anchors.get(chapter)
        if anchor is not None and (not query.kinds or "timeline_anchor" in query.kinds):
            haystack = f"{anchor.when} {anchor.span} {anchor.location}"
            if _matches(haystack, query.text):
                hits.append(
                    MaterialHit(
                        id=f"legacy-anchor:{path}",
                        kind="timeline_anchor",
                        source="legacy_unverified",
                        title=f"第{chapter}章时间",
                        excerpt=_excerpt(haystack),
                        freshness="valid",
                        matched_by=_matched(
                            chapter_match=bool(query.chapter_paths),
                            keyword_match=bool(query.text),
                        ),
                        chapter=chapter,
                    )
                )

    for clue in _valid_clues(workspace, clues, warnings):
        if clue.planted_chapter > as_of:
            continue
        if not query.kinds or "clue" in query.kinds:
            haystack = f"{clue.clue} {clue.supports} {clue.evidence.path}"
            if _matches(haystack, query.text):
                hits.append(
                    MaterialHit(
                        id=f"clue:{clue.key}",
                        kind="clue",
                        source="observed",
                        title=clue.clue,
                        excerpt=_excerpt(
                            "｜".join(
                                part
                                for part in (
                                    clue.clue,
                                    f"埋设第 {clue.planted_chapter} 章",
                                    f"计划第 {clue.due_chapter} 章回收" if clue.due_chapter else "",
                                )
                                if part
                            )
                        ),
                        freshness="valid",
                        matched_by=_matched(
                            chapter_match=_clue_in_chapters(clue, query.chapter_paths),
                            keyword_match=bool(query.text),
                        ),
                        chapter=clue.planted_chapter,
                    )
                )

    if not query.kinds or "glossary" in query.kinds:
        for term in glossary.entries():
            if glossary.status(workspace, term) != "valid":
                warnings.append(f"名词「{term.term}」的依据已改动，未纳入查询。")
                continue
            haystack = " ".join([term.term, *term.aliases])
            if _matches(haystack, query.text):
                hits.append(
                    MaterialHit(
                        id=f"glossary:{term.term}",
                        kind="glossary",
                        source="observed",
                        title=term.term,
                        excerpt=_excerpt(haystack),
                        freshness="valid",
                        matched_by=_matched(keyword_match=bool(query.text)),
                    )
                )
    return hits


def _projection_hits(
    path: str, projection: ChapterProjection, query: MaterialQuery
) -> list[MaterialHit]:
    chapter = projection.chapter
    summary = projection.summary
    events = projection.events
    hits: list[MaterialHit] = []
    if (not query.kinds or "chapter_summary" in query.kinds) and _matches(
        f"{summary} {path}", query.text
    ):
        hits.append(
            MaterialHit(
                id=f"chapter-summary:{path}",
                kind="chapter_summary",
                source="observed",
                title=f"第{chapter}章",
                excerpt=_excerpt(summary),
                freshness="valid",
                matched_by=_matched(
                    chapter_match=bool(query.chapter_paths),
                    keyword_match=bool(query.text),
                ),
                chapter=chapter,
            )
        )
    for event in events:
        if query.kinds and "event" not in query.kinds:
            continue
        haystack = f"{event.text} {MODE_LABEL.get(event.mode, event.mode)} {event.story_time.label}"
        if not _matches(haystack, query.text):
            continue
        entity_match = _event_has_entity(event, query.entity_ids)
        hits.append(
            MaterialHit(
                id=f"event:{event.id}",
                kind="event",
                source="observed",
                title=event.text,
                excerpt=_excerpt(haystack),
                freshness="valid",
                matched_by=_matched(
                    entity_match=entity_match,
                    chapter_match=bool(query.chapter_paths),
                    keyword_match=bool(query.text),
                ),
                chapter=chapter,
            )
        )
    return hits


def _valid_clues(workspace: Workspace, store: ClueStore, warnings: list[str]) -> list[ClueEntry]:
    valid = []
    for clue in store.entries():
        if store.status(workspace, clue) == "valid":
            valid.append(clue)
        else:
            warnings.append(f"线索「{clue.key}」的依据已改动，未纳入查询。")
    return valid


def _event_has_entity(event: EventObservation, entity_ids: Sequence[str]) -> bool:
    if not entity_ids:
        return False
    wanted = set(entity_ids)
    refs = [event.speaker, *event.related]
    if event.author_event is not None:
        refs.append(event.author_event)
    return any(ref is not None and ref[3] in wanted for ref in refs)


def _clue_in_chapters(clue: ClueEntry, chapter_paths: Sequence[str]) -> bool:
    if not chapter_paths:
        return False
    return clue.evidence.path in chapter_paths


def _passes_filters(hit: MaterialHit, query: MaterialQuery) -> bool:
    if query.entity_ids:
        if "entity" not in hit.matched_by:
            return False
        wanted = set(query.entity_ids)
        if hit.module_ref is not None and hit.module_ref[3] not in wanted:
            return False
    return not (
        query.chapter_paths
        and hit.source != "author"
        and (hit.chapter is None or hit.chapter not in _chapter_numbers(query))
    )


def _chapter_numbers(query: MaterialQuery) -> set[int]:
    numbers: set[int] = set()
    for path in query.chapter_paths:
        number = _chapter_number(path)
        if number is not None:
            numbers.add(number)
    return numbers


def _matched(
    *,
    entity_match: bool = False,
    chapter_match: bool = False,
    module_match: bool = False,
    keyword_match: bool = False,
) -> tuple[str, ...]:
    values: list[str] = []
    if entity_match:
        values.append("entity")
    if chapter_match:
        values.append("chapter")
    if module_match:
        values.append("module")
    if keyword_match:
        values.append("keyword")
    return tuple(values)


def _sort_key(hit: MaterialHit) -> tuple[int, int, str]:
    match_rank = min((_MATCH_RANK.get(item, 9) for item in hit.matched_by), default=9)
    return (match_rank, _SOURCE_RANK.get(hit.source, 9), hit.id)


def _page(hits: Sequence[MaterialHit], offset: int, size: int) -> tuple[list[MaterialHit], int]:
    page: list[MaterialHit] = []
    used = 0
    for hit in hits[offset:]:
        if len(page) >= size:
            break
        cost = len(hit.excerpt)
        if page and used + cost > MAX_TOTAL_CHARS:
            break
        page.append(hit)
        used += cost
    return page, len(page)


def _excerpt(text: str) -> str:
    collapsed = " ".join(str(text).split())
    if len(collapsed) <= EXCERPT_MAX_CHARS:
        return collapsed
    return collapsed[: EXCERPT_MAX_CHARS - 1].rstrip() + "…"


def _fields_text(fields: dict[str, object]) -> str:
    parts: list[str] = []
    for key in sorted(fields):
        value = fields[key]
        if value in (None, "", (), []):
            continue
        parts.append(f"{key}={value}")
    return " ".join(parts)


def _matches(haystack: str, needle: str) -> bool:
    text = needle.strip().casefold()
    return not text or text in haystack.casefold()


def _limit(value: int) -> int:
    return max(1, min(int(value), MAX_LIMIT))


def _chapter_number(path: str) -> int | None:
    name = path.rsplit("/", 1)[-1]
    return int(name[:4]) if name[:4].isdigit() else None


def _fingerprint(query: MaterialQuery) -> str:
    payload = {
        "kinds": list(query.kinds),
        "module_ids": list(query.module_ids),
        "text": query.text,
        "entity_ids": list(query.entity_ids),
        "chapter_paths": list(query.chapter_paths),
        "as_of_chapter": query.as_of_chapter,
        "include_plans": query.include_plans,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _cursor(offset: int, fingerprint: str) -> str:
    return f"{offset}:{fingerprint}"


def _cursor_offset(cursor: str, fingerprint: str) -> int:
    if not cursor:
        return 0
    try:
        raw_offset, raw_fingerprint = cursor.split(":", 1)
        offset = int(raw_offset)
    except (ValueError, TypeError) as exc:
        raise _cursor_error(cursor) from exc
    if offset < 0 or raw_fingerprint != fingerprint:
        raise _cursor_error(cursor)
    return offset


def _cursor_error(cursor: str) -> WorkspaceError:
    return WorkspaceError(
        code="RETRIEVAL_CURSOR_STALE_CONFLICT",
        where="retrieval.query",
        why="分页游标不属于这次查询，或查询条件已经改变",
        fix="从第一页重新查询；不要混用两次不同条件的游标",
        details={"cursor": cursor},
    )


def MODULE_LABEL(kind: str) -> str:
    """模块类型的中文标签；未知类型按原值返回，不猜相近类型。"""
    return next((item.label for item in PRESETS if item.kind == kind), kind)
