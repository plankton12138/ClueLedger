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

from hastings.workspace.disclosure import ObservedEvidence, check_evidence
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace, is_managed_path

if TYPE_CHECKING:
    pass

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
