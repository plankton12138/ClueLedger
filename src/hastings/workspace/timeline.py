"""故事内时间线（0.6 V06-E4）。

一章一个时间锚点：这一章发生在什么时候、跨了多久、主要在哪。它解决的不是「记录历史」，
而是「下一章从哪儿接」——悬疑里最常见的时间错误（死亡时间早于检验时间、同一天跑了两座
城市）在写作时看不见，只有把上一章的锚点摆在眼前才可能写对。

刻意不解析自由文本：`when` 是「10月21日深夜」这样的原文说法，代码不做日期运算，也不
据此判断冲突。确定性检查只能做两件事——记录是否有效、哪些章还没有锚点；具体的先后矛盾
交给检查任务对着原文报。

记录存作品状态目录的 ``timeline.json``，不进作品 Git；源章节改过即失效。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from hastings.workspace.disclosure import EntryStatus, ObservedEvidence, check_evidence
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.state_store import current_chapter, read_records, write_records

__all__ = [
    "TimelineEntry",
    "TimelineState",
    "TimelineStore",
    "build_timeline",
    "publish_timeline",
    "timeline_section",
    "timeline_view",
]

RECORD_VERSION = 1
RECIPE_VERSION = 1


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    """一章的时间锚点；``when`` 是原文里的说法，不做日期解析。"""

    chapter: int
    when: str
    span: str
    location: str
    evidence: ObservedEvidence

    def to_dict(self) -> dict[str, object]:
        return {
            "chapter": self.chapter,
            "when": self.when,
            "span": self.span,
            "location": self.location,
            "evidence": {
                "path": self.evidence.path,
                "digest": self.evidence.digest,
                "start": self.evidence.start,
                "end": self.evidence.end,
                "quote": self.evidence.quote,
            },
        }


@dataclass(frozen=True, slots=True)
class TimelineState:
    current_chapter: int
    #: 有效锚点，按章序。
    entries: tuple[TimelineEntry, ...]
    #: 有正文但没有锚点的章号：注入时会说明这些章的时间未知。
    missing: tuple[int, ...]
    warnings: tuple[str, ...] = ()

    def anchor(self) -> TimelineEntry | None:
        return self.entries[-1] if self.entries else None


class TimelineStore:
    """作品状态目录里的 ``timeline.json``；一章一条，后来的核对覆盖旧的。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.file = self.root / "timeline.json"

    def entries(self) -> tuple[TimelineEntry, ...]:
        items: list[TimelineEntry] = []
        for item in read_records(
            self.file,
            record_version=RECORD_VERSION,
            recipe_version=RECIPE_VERSION,
            key="entries",
        ):
            parsed = _entry_from_payload(item)
            if parsed is not None:
                items.append(parsed)
        return tuple(items)

    def replace(self, entries: tuple[TimelineEntry, ...]) -> None:
        merged = {item.chapter: item for item in self.entries()}
        for item in entries:
            merged[item.chapter] = item
        write_records(
            self.file,
            prefix=".timeline-",
            record_version=RECORD_VERSION,
            recipe_version=RECIPE_VERSION,
            key="entries",
            entries=[item.to_dict() for item in merged.values()],
        )

    def status(self, workspace: Workspace, entry: TimelineEntry) -> EntryStatus:
        try:
            current = workspace.read(entry.evidence.path).digest
        except WorkspaceError:
            return "source_missing"
        return "valid" if current == entry.evidence.digest else "stale"


def _entry_from_payload(item: dict[str, object]) -> TimelineEntry | None:
    evidence = item.get("evidence")
    if not isinstance(evidence, dict):
        return None
    try:
        return TimelineEntry(
            chapter=int(str(item["chapter"])),
            when=str(item["when"]),
            span=str(item.get("span") or ""),
            location=str(item.get("location") or ""),
            evidence=ObservedEvidence(
                path=str(evidence["path"]),
                digest=str(evidence["digest"]),
                start=int(str(evidence["start"])),
                end=int(str(evidence["end"])),
                quote=str(evidence["quote"]),
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None


def publish_timeline(
    workspace: Workspace, store: TimelineStore, entries: Sequence[TimelineEntry]
) -> tuple[TimelineEntry, ...]:
    """整批发布：每条都要能对上原文，任一条不过整批不写。"""
    if not entries:
        raise WorkspaceError(
            code="STATE_EVIDENCE_INVALID",
            where="timeline.publish",
            why="这一批没有时间锚点",
            fix="至少给出一条带原文依据的锚点",
        )
    for entry in entries:
        _check_entry(workspace, entry)
    store.replace(tuple(entries))
    return store.entries()


def _check_entry(workspace: Workspace, entry: TimelineEntry) -> None:
    if entry.chapter < 1:
        raise WorkspaceError(
            code="STATE_EVIDENCE_INVALID",
            where="timeline.publish",
            why="章节号必须是正整数",
            fix="写明这条锚点属于第几章",
        )
    if not entry.when.strip():
        raise WorkspaceError(
            code="STATE_EVIDENCE_INVALID",
            where=f"timeline.publish:{entry.chapter}",
            why="时间锚点不能为空",
            fix="写上原文里交代的时间，例如「10月21日深夜」",
        )
    check_evidence(workspace, entry.evidence)


def build_timeline(workspace: Workspace, store: TimelineStore | None = None) -> TimelineState:
    """有效锚点按章序排列；失效的丢掉并说明，缺锚点的章列出来。"""
    current = current_chapter(workspace)
    chapters = sorted(
        entry.chapter_number
        for entry in workspace.catalog().chapters
        if entry.chapter_number is not None and entry.exists
    )
    records = store.entries() if store is not None else ()
    usable: list[TimelineEntry] = []
    warnings: list[str] = []
    dropped: list[int] = []
    for item in records:
        status = store.status(workspace, item) if store is not None else "valid"
        if status == "valid" and item.chapter in chapters:
            usable.append(item)
        else:
            dropped.append(item.chapter)
    usable.sort(key=lambda item: item.chapter)
    if dropped:
        marks = "、".join(f"第{chapter}章" for chapter in sorted(set(dropped)))
        warnings.append(f"{marks}的时间锚点已失效（源文件被改过或已删除），不采用。")
    covered = {item.chapter for item in usable}
    missing = tuple(chapter for chapter in chapters if chapter not in covered)
    if missing and usable:
        marks = "、".join(f"第{chapter}章" for chapter in missing)
        warnings.append(f"{marks}没有时间锚点，这些章的时间跨度未知。")
    return TimelineState(
        current_chapter=current,
        entries=tuple(usable),
        missing=missing,
        warnings=tuple(warnings),
    )


def _anchor_line(entry: TimelineEntry) -> str:
    parts = [f"- 第{entry.chapter}章：{entry.when}"]
    if entry.span:
        parts.append(f"｜{entry.span}")
    if entry.location:
        parts.append(f"｜{entry.location}")
    return "".join(parts)


def timeline_section(state: TimelineState, *, limit: int = 3, budget: int = 400) -> str:
    """写作上下文里的时间线：最近几个锚点，外加「从哪接」。"""
    if not state.entries:
        return ""
    recent = state.entries[-limit:]
    header = "故事时间（按原文核对，最近几章）："
    lines = [header, *(_anchor_line(entry) for entry in recent)]
    if state.missing:
        marks = "、".join(f"第{chapter}章" for chapter in state.missing)
        lines.append(f"（{marks}还没有时间锚点，时间跨度未知。）")
    lines.append(f"下一章从第 {recent[-1].chapter} 章之后接着写；写时间词要与这里连续。")
    text = "\n".join(lines)
    if len(text) > budget and len(recent) > 1:
        # 超预算先丢弃早的锚点：最近一章的时间是「从哪接」的唯一依据。
        lines = [header, _anchor_line(recent[-1]), lines[-1]]
        text = "\n".join(lines)
    return text


def timeline_view(state: TimelineState) -> dict[str, object]:
    return {
        "domain": "timeline",
        "current_chapter": state.current_chapter,
        "entries": [
            {
                "chapter": entry.chapter,
                "when": entry.when,
                "span": entry.span,
                "location": entry.location,
            }
            for entry in state.entries
        ],
        "missing": list(state.missing),
        "warnings": list(state.warnings),
    }
