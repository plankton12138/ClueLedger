"""统一章节索引（0.8 E）。

一次整理只产出一份数据，两个视图从它读：章节视图按章序看「这一章发生了什么」，故事视图
只对**可证明的先后**排序。摘要、事件观察、证据与覆盖分开存，但同章发布是一次原子替换，
重建一章只替换这一章的贡献。

几条边界：

- **观察不是事实**：每条事件带表达方式（叙述／证词／回忆／推测／梦／未知）。回忆既不
  自动等于虚假，也不自动等于真相；证据只证明原文存在，不证明人物说法为真。
- **引用必须是完整模块身份**：人物与作者事件都用库／模块／修订／条目四段引用，不同作品
  的同名人物不会因为名字相同被串在一起。
- **不知道就说不知道**：故事视图只对能证明的先后排成链；没有任何时间关系的事件单独列出，
  环与日期矛盾显示双方来源，不生成看似有效的全序。
- **旧记录只读兼容**：旧 ``chapter-index.json`` 的摘要与 ``timeline.json`` 的锚点在对应章
  重新发布之前照原样显示并标注来源；发布成功后按章切换，不做全量自动重建。
"""

from __future__ import annotations

import hashlib
import heapq
import json
import os
import tempfile
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from hastings.workspace.chapter_index import ChapterIndexStore
from hastings.workspace.disclosure import ObservedEvidence, check_evidence
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace, is_managed_path
from hastings.workspace.state_store import current_chapter
from hastings.workspace.timeline import TimelineStore

if TYPE_CHECKING:
    from hastings.workspace.story_materials import CoverageStore

__all__ = [
    "MAX_EVENTS_PER_CHAPTER",
    "OBSERVATION_MODES",
    "RECIPE_VERSION",
    "ChapterProjection",
    "EventObservation",
    "RecordRef",
    "StoryIndexStore",
    "StoryTime",
    "build_observation_id",
    "chapter_view",
    "story_view",
    "validate_projection",
]

#: 生成规则版本：校验或解析规则变化时递增，旧投影在视图里标为过期，不猜测兼容。
RECIPE_VERSION = 1

#: 单章摘要与单条事件的字符上限；事件条数上限防止一次提交淹掉整个视图。
SUMMARY_MAX_CHARS = 500
EVENT_TEXT_MAX_CHARS = 300
MAX_EVENTS_PER_CHAPTER = 30

#: 表达方式；未知也是合法取值，不允许因为「没写」就当成客观叙述。
OBSERVATION_MODES: tuple[str, ...] = (
    "narration",
    "testimony",
    "recollection",
    "hypothesis",
    "dream",
    "unknown",
)
MODE_LABEL: dict[str, str] = {
    "narration": "叙述",
    "testimony": "证词",
    "recollection": "回忆",
    "hypothesis": "推测",
    "dream": "梦",
    "unknown": "未知",
}

#: 完整模块身份：库、模块、修订、条目。四段缺一不算引用。
RecordRef = tuple[str, str, str, str]
_REF_PREFIX = ("lib_", "mod_", "rev_", "ent_")

ObservationMode = Literal[
    "narration", "testimony", "recollection", "hypothesis", "dream", "unknown"
]
StoryViewConflict = dict[str, object]


@dataclass(frozen=True, slots=True)
class StoryTime:
    """故事时间：原文说法 + 可选的标准日期 + 可证明的先后引用。

    ``normalized`` 只在原文能明确解析成日期时填；``before`` / ``after`` 是其他观察的 ID，
    表达「这件事在它之前／之后」，不要求两边都有日期。
    """

    label: str = ""
    normalized: str = ""
    before: tuple[str, ...] = ()
    after: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "normalized": self.normalized,
            "before": list(self.before),
            "after": list(self.after),
        }

    @classmethod
    def from_raw(cls, raw: object) -> StoryTime | None:
        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            return None
        try:
            return cls(
                label=str(raw.get("label") or ""),
                normalized=str(raw.get("normalized") or ""),
                before=tuple(str(item) for item in raw.get("before") or ()),
                after=tuple(str(item) for item in raw.get("after") or ()),
            )
        except TypeError:
            return None


@dataclass(frozen=True, slots=True)
class EventObservation:
    """一条正文观察；``id`` 为空时在发布时按章、文本与首处证据算出稳定标识。"""

    text: str
    chapter: int
    mode: ObservationMode
    evidence: tuple[ObservedEvidence, ...]
    story_time: StoryTime = field(default_factory=StoryTime)
    speaker: RecordRef | None = None
    related: tuple[RecordRef, ...] = ()
    author_event: RecordRef | None = None
    id: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "text": self.text,
            "chapter": self.chapter,
            "mode": self.mode,
            "speaker": list(self.speaker) if self.speaker else None,
            "related": [list(item) for item in self.related],
            "author_event": list(self.author_event) if self.author_event else None,
            "story_time": self.story_time.to_dict(),
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True, slots=True)
class ChapterProjection:
    """一章的已发布投影：内容来自上一次成功发布，是否还成立由当前磁盘算出。"""

    path: str
    digest: str
    chapter: int
    summary: str
    events: tuple[EventObservation, ...] = ()
    recipe_version: int = RECIPE_VERSION
    generated_at: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "digest": self.digest,
            "chapter": self.chapter,
            "recipe_version": self.recipe_version,
            "generated_at": self.generated_at,
            "summary": self.summary,
            "events": [item.to_dict() for item in self.events],
        }


class StoryIndexStore:
    """作品状态目录里的 ``story-index.json``；一章一条，发布时只替换该章。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.file = self.root / "story-index.json"
        self.lock_file = self.root / ".story-index.lock"

    def read_all(self) -> dict[str, ChapterProjection]:
        try:
            payload = json.loads(self.file.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            return {}
        raw = payload.get("chapters") if isinstance(payload, dict) else None
        if not isinstance(raw, dict):
            return {}
        rows: dict[str, ChapterProjection] = {}
        for value in raw.values():
            parsed = _projection_from_raw(value)
            if parsed is not None:
                rows[parsed.path] = parsed
        return rows

    def publish(self, projection: ChapterProjection, *, expected_digest: str) -> ChapterProjection:
        """校验指纹后只替换这一章；锁内重读，另一个进程刚发的章节不会被抹掉。"""
        if projection.digest != expected_digest:
            raise WorkspaceError(
                code="STORY_INDEX_SOURCE_STALE_CONFLICT",
                where=f"story_index.publish:{projection.path}",
                why="要发布的索引不是基于当前原文生成的",
                fix="重新读取这一章，按当前内容重新整理",
                details={
                    "path": projection.path,
                    "expected_digest": expected_digest,
                    "projection_digest": projection.digest,
                },
            )
        prepared = _with_ids(projection, generated_at=_now())
        with _locked(self.lock_file):
            rows = self.read_all()
            rows[prepared.path] = prepared
            self._save(rows)
        return prepared

    def remove(self, path: str) -> None:
        """重建一章确认没有可发布的观察时，只移除这一章的贡献。

        其他章的投影、作者安排与覆盖记录都不动；没有这一章时是空操作，
        网络重试不会报错，也不会顺手清掉别的章。
        """
        with _locked(self.lock_file):
            rows = self.read_all()
            if path not in rows:
                return
            del rows[path]
            self._save(rows)

    def _save(self, rows: Mapping[str, ChapterProjection]) -> None:
        payload = {
            "version": 1,
            "recipe_version": RECIPE_VERSION,
            "generated_at": _now(),
            "chapters": {path: item.to_dict() for path, item in rows.items()},
        }
        handle: int | None = None
        temp_name: str | None = None
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            handle, temp_name = tempfile.mkstemp(
                dir=str(self.file.parent), prefix=".story-index-", suffix=".json"
            )
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                handle = None
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.replace(temp_name, self.file)
            temp_name = None
        except OSError as exc:
            raise WorkspaceError(
                code="STORY_INDEX_WRITE_FAILED",
                where="story_index.save",
                why=f"无法保存章节索引：{exc.strerror or exc}",
                fix="检查应用状态目录是否可写；原作品内容不受影响",
            ) from exc
        finally:
            if handle is not None:
                os.close(handle)
            if temp_name is not None:
                with suppress(OSError):
                    os.unlink(temp_name)


def build_observation_id(chapter: int, text: str, start: int, end: int) -> str:
    """稳定标识：同章、同文本、同首处区间重复提交得到同一个 ID。"""
    raw = f"{chapter}\x1f{_collapse(text)}\x1f{start}\x1f{end}"
    return f"obs_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def validate_projection(workspace: Workspace, projection: ChapterProjection) -> None:
    """结构、范围、来源与证据校验；任一条不成立都不发布。"""
    path = projection.path
    entry = next((item for item in workspace.catalog().chapters if item.path == path), None)
    if not is_managed_path(path) or not path.startswith("正文/") or entry is None:
        raise WorkspaceError(
            code="STORY_INDEX_SCOPE_INVALID",
            where=f"story_index.validate:{path}",
            why="章节索引只针对正文/ 下的章节",
            fix="选择正文章节；设定集与灵感不属于章节索引",
            details={"path": path},
        )
    if entry.chapter_number != projection.chapter:
        raise WorkspaceError(
            code="STORY_INDEX_SCOPE_INVALID",
            where=f"story_index.validate:{path}",
            why=(
                f"章号与文件名不一致：声明的第 {projection.chapter} 章，"
                f"实际第 {entry.chapter_number} 章"
            ),
            fix="按目录里的章号提交，不要自己编号",
            details={"path": path, "declared": projection.chapter},
        )
    try:
        current = workspace.read(path)
    except WorkspaceError as exc:
        raise _source_error(path, f"这一章当前不可用：{exc.why}") from exc
    if current.digest != projection.digest:
        raise WorkspaceError(
            code="STORY_INDEX_SOURCE_STALE_CONFLICT",
            where=f"story_index.validate:{path}",
            why="这一章在整理之后被改动，索引不能按旧内容发布",
            fix="重新读取当前内容后再整理这一章",
            details={"path": path, "expected": current.digest, "given": projection.digest},
        )
    summary = projection.summary.strip()
    if not summary:
        raise _evidence_error(path, "没有写这一章的摘要")
    if len(summary) > SUMMARY_MAX_CHARS:
        raise _evidence_error(path, f"摘要超过 {SUMMARY_MAX_CHARS} 字符，请压缩后再提交")
    if len(projection.events) > MAX_EVENTS_PER_CHAPTER:
        raise _event_error(path, f"一章最多 {MAX_EVENTS_PER_CHAPTER} 条事件")
    for event in projection.events:
        _validate_event(workspace, projection, event)


def _validate_event(
    workspace: Workspace, projection: ChapterProjection, event: EventObservation
) -> None:
    path = projection.path
    if not event.text.strip():
        raise _event_error(path, "事件内容为空")
    if len(event.text) > EVENT_TEXT_MAX_CHARS:
        raise _event_error(path, f"事件内容超过 {EVENT_TEXT_MAX_CHARS} 字符，请拆成几条")
    if event.mode not in OBSERVATION_MODES:
        raise _event_error(path, f"不支持的表达方式：{event.mode}")
    if event.chapter != projection.chapter:
        raise _event_error(path, "事件的章号必须与所在章节一致")
    for ref in (event.speaker, *event.related, event.author_event):
        if ref is not None and not _is_record_ref(ref):
            raise _event_error(
                path,
                "人物与作者事件必须使用完整的库／模块／修订／条目引用，不能只给名字或半段 ID",
            )
    if not event.evidence:
        raise _evidence_error(path, "每条事件至少要有一处原文依据")
    if event.story_time.normalized:
        _parse_date(event.story_time.normalized)
    for other in (*event.story_time.before, *event.story_time.after):
        if not other.strip():
            raise _event_error(path, "先后关系引用了空的观察 ID")
        if other == event.id and other:
            raise _event_error(path, "事件不能声明发生在自己之前或之后")
    for evidence in event.evidence:
        try:
            check_evidence(workspace, evidence, chapter=projection.chapter)
        except WorkspaceError as exc:
            raise _evidence_error(path, f"证据核对未通过：{exc.why}") from exc


# -- 章节视图 ---------------------------------------------------------------


def chapter_view(
    workspace: Workspace, root: Path, *, as_of_chapter: int | None = None
) -> dict[str, object]:
    """按章序看每一章：摘要、事件、旧版条目、时间锚点、覆盖与有效性。"""
    as_of = current_chapter(workspace, as_of_chapter)
    projections = StoryIndexStore(root).read_all()
    legacy = ChapterIndexStore(root)
    anchors = {item.chapter: item for item in TimelineStore(root).entries()}
    from hastings.workspace.story_materials import CoverageStore

    coverage = CoverageStore(root)
    chapters: list[dict[str, object]] = []
    warnings: list[str] = []

    for entry in workspace.catalog().chapters:
        if not entry.exists or entry.chapter_number is None or entry.chapter_number > as_of:
            continue
        try:
            digest = workspace.read(entry.path).digest
        except WorkspaceError:
            continue
        projection = projections.get(entry.path)
        row: dict[str, object] = {
            "path": entry.path,
            "chapter": entry.chapter_number,
            "title": entry.display_title or entry.title or entry.path,
            "source": "none",
            "status": "missing",
            "summary": "",
            "events": [],
            "legacy_entries": None,
            "anchor": None,
            "coverage": _coverage_row(coverage, workspace, entry.path, digest),
        }
        if projection is not None:
            status = _projection_status(projection, digest)
            row.update(
                {
                    "source": "story_index",
                    "status": status,
                    "summary": projection.summary,
                    "events": [_event_view(item) for item in projection.events],
                }
            )
            if status != "valid":
                warnings.append(f"第{entry.chapter_number}章的索引依据的原文已改动，需要重新整理。")
            elif _has_story_time(projection):
                # 新投影自带时间描述：旧锚点按章让位，不两份并列。
                row["anchor"] = None
        else:
            record = legacy.read_all().get(entry.path)
            if record is not None:
                status = legacy.status(record, workspace)
                row.update(
                    {
                        "source": "legacy",
                        "status": status,
                        "summary": record.summary,
                        "legacy_entries": {
                            "events": [item.to_dict() for item in record.events],
                            "characters": [item.to_dict() for item in record.characters],
                            "places": [item.to_dict() for item in record.places],
                            "clues": [item.to_dict() for item in record.clues],
                        },
                    }
                )
                if status != "valid":
                    warnings.append(f"第{entry.chapter_number}章的旧版摘要已不能作为当前依据。")
            anchor = anchors.get(entry.chapter_number)
            if anchor is not None:
                row["anchor"] = {
                    "when": anchor.when,
                    "span": anchor.span,
                    "location": anchor.location,
                    "source": "legacy",
                }
            if projection is None and record is None:
                warnings.append(f"第{entry.chapter_number}章还没有章节索引。")
        chapters.append(row)

    return {
        "mode": "chapter",
        "as_of_chapter": as_of,
        "chapters": chapters,
        "warnings": warnings,
    }


def _coverage_row(
    store: CoverageStore, workspace: Workspace, path: str, digest: str
) -> dict[str, object] | None:
    """覆盖、freshness 与任务终态分开：这里只回答「这一轮读到了什么」。"""
    del digest
    for domain in ("story_index", "chapter_index", "timeline"):
        record = store.find(domain, path)
        if record is None:
            continue
        return {
            "domain": domain,
            "outcome": record.outcome,
            "at": record.at,
            "task_id": record.task_id,
            "fresh": store.status(workspace, record) == "valid",
        }
    return None


# -- 故事视图 ---------------------------------------------------------------


def story_view(
    workspace: Workspace, root: Path, *, as_of_chapter: int | None = None
) -> dict[str, object]:
    """只对可证明的先后排序：链、孤立事件与矛盾分组显示，不伪造全序。"""
    as_of = current_chapter(workspace, as_of_chapter)
    store = StoryIndexStore(root)
    warnings: list[str] = []
    events: list[EventObservation] = []
    for path, projection in sorted(store.read_all().items(), key=lambda item: item[0]):
        if projection.chapter > as_of:
            continue
        try:
            digest = workspace.read(path).digest
        except WorkspaceError:
            warnings.append(f"第{projection.chapter}章的原文当前不可用，其观察未纳入故事视图。")
            continue
        if _projection_status(projection, digest) != "valid":
            warnings.append(f"第{projection.chapter}章的观察依据已改动，未纳入故事视图。")
            continue
        events.extend(projection.events)

    by_id = {item.id: item for item in events if item.id}
    edges: dict[str, set[str]] = {item.id: set() for item in events if item.id}
    edge_kind: dict[tuple[str, str], str] = {}

    def add_edge(source: str, target: str, kind: str) -> None:
        if source not in edges or target not in edges or source == target:
            return
        edges[source].add(target)
        edge_kind.setdefault((source, target), kind)

    for event in events:
        if not event.id:
            continue
        for other in event.story_time.after:
            if other in by_id:
                add_edge(other, event.id, "relation")
            else:
                warnings.append(f"「{event.text}」引用的观察 {other} 不在当前索引里，先后未确定。")
        for other in event.story_time.before:
            if other in by_id:
                add_edge(event.id, other, "relation")
            else:
                warnings.append(f"「{event.text}」引用的观察 {other} 不在当前索引里，先后未确定。")

    dated = [(item, _date_or_none(item.story_time.normalized)) for item in events]
    for left, left_date in dated:
        if left_date is None or not left.id:
            continue
        for right, right_date in dated:
            if right_date is None or not right.id or left.id == right.id:
                continue
            if left_date < right_date:
                add_edge(left.id, right.id, "date")

    components = _strongly_connected(edges)
    in_cycle: set[str] = set()
    conflicts: list[StoryViewConflict] = []
    for component in components:
        if len(component) == 1 and not _has_self_loop(edges, component[0]):
            continue
        in_cycle.update(component)
        kinds = {
            edge_kind.get((source, target), "relation")
            for source in component
            for target in edges.get(source, ())
            if target in component
        }
        kind = "date_conflict" if "date" in kinds else "cycle"
        detail = (
            "日期顺序与声明的先后关系互相矛盾"
            if kind == "date_conflict"
            else "这些观察的先后关系形成循环"
        )
        conflicts.append(
            {
                "kind": kind,
                "detail": detail,
                "ids": sorted(component),
                "events": [_event_brief(by_id[item]) for item in sorted(component)],
            }
        )

    remaining = [item for item in events if item.id and item.id not in in_cycle]
    #: 来源顺序只用于稳定显示：组与组之间没有可证明的先后，不能用散列 ID 决定谁在前。
    source_order = {item.id: index for index, item in enumerate(events) if item.id}
    isolated = [
        item
        for item in remaining
        if not edges.get(item.id) and not any(item.id in targets for targets in edges.values())
    ]
    linked = [item for item in remaining if item not in isolated]
    ordered = _topological(linked, edges, source_order=source_order)
    chains: list[list[EventObservation]] = []
    reach: dict[str, set[str]] = {}
    for event in ordered:
        for chain in chains:
            tail = chain[-1]
            if tail.id not in reach:
                reach[tail.id] = _reachable(edges, tail.id)
            if event.id in reach[tail.id]:
                chain.append(event)
                break
        else:
            chains.append([event])
    # 组与组之间没有可证明的先后：只有第一组不带「先后未确定」。
    groups: list[dict[str, object]] = [
        {"provisional": index > 0, "events": [_event_view(item) for item in chain]}
        for index, chain in enumerate(chains)
    ]

    return {
        "mode": "story",
        "as_of_chapter": as_of,
        "groups": groups,
        "unknown": [
            _event_view(item)
            for item in sorted(
                isolated, key=lambda item: (item.chapter, source_order.get(item.id, 0))
            )
        ],
        "conflicts": conflicts,
        "warnings": warnings,
    }


def _event_view(event: EventObservation) -> dict[str, object]:
    return {
        "id": event.id,
        "text": event.text,
        "chapter": event.chapter,
        "mode": event.mode,
        "mode_label": MODE_LABEL.get(event.mode, event.mode),
        "speaker": list(event.speaker) if event.speaker else None,
        "related": [list(item) for item in event.related],
        "author_event": list(event.author_event) if event.author_event else None,
        "story_time": event.story_time.to_dict(),
        "evidence": [
            {"path": item.path, "start": item.start, "end": item.end, "quote": item.quote}
            for item in event.evidence
        ],
    }


def _event_brief(event: EventObservation) -> dict[str, object]:
    return {
        "id": event.id,
        "text": event.text,
        "chapter": event.chapter,
        "mode": event.mode,
        "mode_label": MODE_LABEL.get(event.mode, event.mode),
    }


def _topological(
    events: Sequence[EventObservation],
    edges: Mapping[str, set[str]],
    *,
    source_order: Mapping[str, int],
) -> list[EventObservation]:
    """稳定拓扑序：同层按章号与来源顺序排，避免每次刷新视图顺序跳动。"""
    nodes = {item.id: item for item in events}
    incoming = {node: 0 for node in nodes}
    for source, targets in edges.items():
        if source not in nodes:
            continue
        for target in targets:
            if target in incoming:
                incoming[target] += 1
    ready = [
        (nodes[node].chapter, source_order.get(node, 0), node)
        for node, count in incoming.items()
        if count == 0
    ]
    heapq.heapify(ready)
    ordered: list[EventObservation] = []
    while ready:
        _, _, node = heapq.heappop(ready)
        ordered.append(nodes[node])
        for target in sorted(edges.get(node, ())):
            if target not in incoming:
                continue
            incoming[target] -= 1
            if incoming[target] == 0:
                heapq.heappush(ready, (nodes[target].chapter, source_order.get(target, 0), target))
    # 环里的节点已在调用前移除；这里再兜一次，不丢事件。
    seen = {item.id for item in ordered}
    ordered.extend(item for item in events if item.id not in seen)
    return ordered


def _reachable(edges: Mapping[str, set[str]], start: str) -> set[str]:
    seen: set[str] = set()
    stack = list(edges.get(start, ()))
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(edges.get(node, ()))
    return seen


def _has_self_loop(edges: Mapping[str, set[str]], node: str) -> bool:
    return node in edges.get(node, ())


def _strongly_connected(edges: Mapping[str, set[str]]) -> list[list[str]]:
    """Tarjan；返回每个强连通分量，长度 1 且无自环的分量不是环。"""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    counter = [0]
    components: list[list[str]] = []

    def visit(node: str) -> None:
        index[node] = low[node] = counter[0]
        counter[0] += 1
        stack.append(node)
        on_stack.add(node)
        for target in sorted(edges.get(node, ())):
            if target not in edges:
                continue
            if target not in index:
                visit(target)
                low[node] = min(low[node], low[target])
            elif target in on_stack:
                low[node] = min(low[node], index[target])
        if low[node] == index[node]:
            component: list[str] = []
            while True:
                item = stack.pop()
                on_stack.discard(item)
                component.append(item)
                if item == node:
                    break
            components.append(sorted(component))

    for node in sorted(edges):
        if node not in index:
            visit(node)
    return components


# -- 读取 -------------------------------------------------------------------


def _projection_from_raw(raw: object) -> ChapterProjection | None:
    if not isinstance(raw, dict):
        return None
    path = raw.get("path")
    digest = raw.get("digest")
    chapter = raw.get("chapter")
    summary = raw.get("summary")
    version = raw.get("recipe_version")
    generated = raw.get("generated_at")
    if not all(isinstance(item, str) for item in (path, digest, summary)):
        return None
    if not isinstance(chapter, int) or not isinstance(version, int):
        return None
    events_raw = raw.get("events")
    if not isinstance(events_raw, list):
        return None
    events: list[EventObservation] = []
    for value in events_raw:
        parsed = _event_from_raw(value)
        if parsed is None:
            return None
        events.append(parsed)
    return ChapterProjection(
        path=str(path),
        digest=str(digest),
        chapter=chapter,
        summary=str(summary),
        events=tuple(events),
        recipe_version=version,
        generated_at=str(generated or ""),
    )


def _event_from_raw(raw: object) -> EventObservation | None:
    if not isinstance(raw, dict):
        return None
    text = raw.get("text")
    chapter = raw.get("chapter")
    mode = raw.get("mode")
    evidence_raw = raw.get("evidence")
    if (
        not isinstance(text, str)
        or not isinstance(chapter, int)
        or not isinstance(evidence_raw, list)
    ):
        return None
    if mode not in OBSERVATION_MODES:
        return None
    evidence: list[ObservedEvidence] = []
    for value in evidence_raw:
        if not isinstance(value, dict):
            return None
        try:
            evidence.append(
                ObservedEvidence(
                    path=str(value["path"]),
                    digest=str(value["digest"]),
                    start=int(str(value["start"])),
                    end=int(str(value["end"])),
                    quote=str(value["quote"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            return None
    story_time = StoryTime.from_raw(raw.get("story_time"))
    if story_time is None:
        return None
    speaker = _ref_from_raw(raw.get("speaker"))
    related_raw = raw.get("related")
    related: list[RecordRef] = []
    if isinstance(related_raw, list):
        for value in related_raw:
            parsed = _ref_from_raw(value)
            if parsed is None:
                return None
            related.append(parsed)
    author_event = _ref_from_raw(raw.get("author_event"))
    return EventObservation(
        text=text,
        chapter=chapter,
        mode=cast(ObservationMode, mode),
        evidence=tuple(evidence),
        story_time=story_time,
        speaker=speaker,
        related=tuple(related),
        author_event=author_event,
        id=str(raw.get("id") or ""),
    )


def _ref_from_raw(raw: object) -> RecordRef | None:
    if raw is None:
        return None
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    parts = tuple(str(item) for item in raw)
    if not all(parts):
        return None
    return cast(RecordRef, parts)


def _projection_status(projection: ChapterProjection, digest: str) -> str:
    if projection.recipe_version != RECIPE_VERSION:
        return "stale"
    return "valid" if projection.digest == digest else "stale"


def _has_story_time(projection: ChapterProjection) -> bool:
    return any(item.story_time.label or item.story_time.normalized for item in projection.events)


# -- 工具 -------------------------------------------------------------------


def _with_ids(projection: ChapterProjection, *, generated_at: str) -> ChapterProjection:
    events: list[EventObservation] = []
    for event in projection.events:
        if event.id:
            events.append(event)
            continue
        first = event.evidence[0] if event.evidence else None
        events.append(
            EventObservation(
                text=event.text,
                chapter=event.chapter,
                mode=event.mode,
                evidence=event.evidence,
                story_time=event.story_time,
                speaker=event.speaker,
                related=event.related,
                author_event=event.author_event,
                id=build_observation_id(
                    event.chapter,
                    event.text,
                    first.start if first else 0,
                    first.end if first else 0,
                ),
            )
        )
    return ChapterProjection(
        path=projection.path,
        digest=projection.digest,
        chapter=projection.chapter,
        summary=projection.summary.strip(),
        events=tuple(events),
        recipe_version=RECIPE_VERSION,
        generated_at=generated_at,
    )


def _is_record_ref(ref: Sequence[str]) -> bool:
    if len(ref) != 4:
        return False
    return all(
        isinstance(part, str) and part.startswith(prefix) and len(part) > len(prefix)
        for part, prefix in zip(ref, _REF_PREFIX, strict=True)
    )


def _date_or_none(value: str) -> date | None:
    try:
        return _parse_date(value)
    except WorkspaceError:
        return None


def _parse_date(value: str) -> date | None:
    """只接受明确的 ISO 日期；解析不了就报错，不猜「大概是几号」。"""
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise WorkspaceError(
            code="STORY_INDEX_EVENT_INVALID",
            where="story_index.validate",
            why=f"标准日期必须是 YYYY-MM-DD 形式：{value}",
            fix="写原文能明确对应的日期；不确定时留空，只写原文说法",
            details={"normalized": value},
        ) from exc


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _evidence_error(path: str, why: str) -> WorkspaceError:
    return WorkspaceError(
        code="STORY_INDEX_EVIDENCE_INVALID",
        where=f"story_index.validate:{path}",
        why=why,
        fix="按当前原文重新摘录：引文要与声明的字符区间一致，事件要引用本章证据",
        details={"path": path},
    )


def _event_error(path: str, why: str) -> WorkspaceError:
    return WorkspaceError(
        code="STORY_INDEX_EVENT_INVALID",
        where=f"story_index.validate:{path}",
        why=why,
        fix="每条事件写清表达方式、内容与完整引用；不确定的先后关系不要写",
        details={"path": path},
    )


def _source_error(path: str, why: str) -> WorkspaceError:
    return WorkspaceError(
        code="STORY_INDEX_SOURCE_NOT_FOUND",
        where=f"story_index.validate:{path}",
        why=why,
        fix="刷新目录后重新选择章节",
        details={"path": path},
    )


# -- 锁 ---------------------------------------------------------------------

_THREAD_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    key = str(path.resolve())
    with _LOCKS_GUARD:
        lock = _THREAD_LOCKS.setdefault(key, threading.Lock())
    with lock:
        handle = open(path, "a+b")  # noqa: SIM115 - 生命周期与 with 一致
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            _lock_file(handle)
            yield
        finally:
            with suppress(OSError):
                _unlock_file(handle)
            handle.close()


def _lock_file(handle: object) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)  # type: ignore[attr-defined]
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)  # type: ignore[attr-defined]


def _unlock_file(handle: object) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)  # type: ignore[attr-defined]
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
