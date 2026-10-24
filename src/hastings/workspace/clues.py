"""线索与伏笔台账（0.6 V06-E5）。

长篇里最容易丢的不是设定，是「第 3 章埋下、第 7 章该回收」的那类信息。写第 8 章时没人
提醒，它就永远留在第 3 章了。这一层只做一件事：把还没回收的线索按「该回收了没有」排好，
放进写作上下文。

三条边界：

- **线索不是真相。** 一条线索只说明读者看到过什么，不代表它的解释成立。
- **只登记原文里写到的。** 埋设与回收都要有原文依据；凭设定或大纲推测的不入账。
- **不做「候选/确认」两步。** 计划里写的是「作者在界面确认后才入账」，但本版不做作者手动
  修改状态，加一个只有作者能点的确认按钮等于新增一条作者写状态的路径。改为：核对任务登记
  即入账、面板显示原文依据、作者发现误判就让模型改或废弃。代价是误判会占预算，缓解是注入
  有固定上限且超期项排在最前。

记录存作品状态目录的 ``clues.json``，不进作品 Git；埋设或回收的原文改过即失效。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from hastings.workspace.assembly import Exclusion
from hastings.workspace.disclosure import EntryStatus, ObservedEvidence, check_evidence
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.state_store import current_chapter, read_records, write_records

__all__ = [
    "ClueEntry",
    "ClueState",
    "ClueStore",
    "build_clues",
    "clues_section",
    "clues_view",
    "publish_clues",
]

ClueStatus = Literal["open", "resolved", "dropped"]

RECORD_VERSION = 2
RECIPE_VERSION = 2

STATUS_LABEL: dict[ClueStatus, str] = {
    "open": "未回收",
    "resolved": "已回收",
    "dropped": "已废弃",
}


@dataclass(frozen=True, slots=True)
class ClueEntry:
    """一条线索：埋在哪一章、该在哪一章回收、现在什么状态。"""

    clue: str
    planted_chapter: int
    due_chapter: int | None
    status: ClueStatus
    #: 支撑哪种可能；空表示只记了「有这回事」。不影响是否注入。
    supports: str
    evidence: ObservedEvidence
    resolution_chapter: int | None = None
    resolution_evidence: ObservedEvidence | None = None
    #: 这条贡献是哪一章的整理结果；重建一章只替换该章的贡献。
    source_chapter: int = 0

    @property
    def key(self) -> str:
        return self.clue.strip()

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "clue": self.clue,
            "planted_chapter": self.planted_chapter,
            "due_chapter": self.due_chapter,
            "status": self.status,
            "supports": self.supports,
            "evidence": _evidence_dict(self.evidence),
            "source_chapter": self.source_chapter,
        }
        if self.resolution_chapter is not None:
            payload["resolution_chapter"] = self.resolution_chapter
        if self.resolution_evidence is not None:
            payload["resolution_evidence"] = _evidence_dict(self.resolution_evidence)
        return payload


def _evidence_dict(evidence: ObservedEvidence) -> dict[str, object]:
    return {
        "path": evidence.path,
        "digest": evidence.digest,
        "start": evidence.start,
        "end": evidence.end,
        "quote": evidence.quote,
    }


@dataclass(frozen=True, slots=True)
class ClueState:
    current_chapter: int
    #: 还没回收的线索，按「超期在前、该回收的次之、没定回收章的在后」排好。
    open: tuple[ClueEntry, ...]
    #: 已回收与已废弃：只在作者侧显示，不注入。
    closed: tuple[ClueEntry, ...]
    warnings: tuple[str, ...] = ()

    def overdue(self) -> tuple[ClueEntry, ...]:
        return tuple(item for item in self.open if self.is_overdue(item))

    def is_overdue(self, entry: ClueEntry) -> bool:
        return entry.due_chapter is not None and entry.due_chapter <= self.current_chapter


class ClueStore:
    """作品状态目录里的 ``clues.json``；同一句线索只留一条，后来的覆盖旧的。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.file = self.root / "clues.json"

    def entries(self) -> tuple[ClueEntry, ...]:
        items: list[ClueEntry] = []
        for raw in read_records(
            self.file,
            record_version=RECORD_VERSION,
            recipe_version=RECIPE_VERSION,
            key="entries",
        ):
            parsed = _entry_from_payload(raw)
            if parsed is not None:
                items.append(parsed)
        return tuple(items)

    def replace(self, entries: tuple[ClueEntry, ...]) -> None:
        merged = {item.key: item for item in self.entries()}
        for item in entries:
            merged[item.key] = item
        write_records(
            self.file,
            prefix=".clues-",
            record_version=RECORD_VERSION,
            recipe_version=RECIPE_VERSION,
            key="entries",
            entries=[item.to_dict() for item in merged.values()],
        )

    def replace_source(self, source_chapter: int, entries: tuple[ClueEntry, ...]) -> None:
        """只替换这一章的贡献：其他章的线索保留，同一章旧条目先移除再写入。

        同一线索被不同章节各写一份时，两份贡献都留在存储里；``build_clues`` 按
        章节顺序取最新的一份，作者侧不会看到重复行。
        """
        kept = [item for item in self.entries() if item.source_chapter != source_chapter]
        merged = {item.key: item for item in entries}
        rows = [*kept, *merged.values()]
        write_records(
            self.file,
            prefix=".clues-",
            record_version=RECORD_VERSION,
            recipe_version=RECIPE_VERSION,
            key="entries",
            entries=[item.to_dict() for item in rows],
        )

    def status(self, workspace: Workspace, entry: ClueEntry) -> EntryStatus:
        """埋设处与回收处都要对得上原文；任一失效即整条不采用。"""
        checks = [entry.evidence]
        if entry.resolution_evidence is not None:
            checks.append(entry.resolution_evidence)
        for evidence in checks:
            try:
                current = workspace.read(evidence.path).digest
            except WorkspaceError:
                return "source_missing"
            if current != evidence.digest:
                return "stale"
        return "valid"


def _entry_from_payload(raw: dict[str, object]) -> ClueEntry | None:
    status = raw.get("status")
    if status not in STATUS_LABEL:
        return None
    evidence = _evidence_from(raw.get("evidence"))
    if evidence is None:
        return None
    try:
        due = raw.get("due_chapter")
        resolution_chapter = raw.get("resolution_chapter")
        return ClueEntry(
            clue=str(raw["clue"]),
            planted_chapter=int(str(raw["planted_chapter"])),
            due_chapter=int(str(due)) if due is not None else None,
            status=status,
            supports=str(raw.get("supports") or ""),
            evidence=evidence,
            resolution_chapter=(
                int(str(resolution_chapter)) if resolution_chapter is not None else None
            ),
            resolution_evidence=_evidence_from(raw.get("resolution_evidence")),
            source_chapter=int(str(raw.get("source_chapter") or 0)),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _evidence_from(raw: object) -> ObservedEvidence | None:
    if not isinstance(raw, dict):
        return None
    try:
        return ObservedEvidence(
            path=str(raw["path"]),
            digest=str(raw["digest"]),
            start=int(str(raw["start"])),
            end=int(str(raw["end"])),
            quote=str(raw["quote"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def publish_clues(
    workspace: Workspace,
    store: ClueStore,
    entries: Sequence[ClueEntry],
    *,
    source_chapter: int | None = None,
) -> tuple[ClueEntry, ...]:
    """整批发布：任一条不过整批不写，避免半份台账被当成完整清单。

    ``source_chapter`` 是这一批来自哪一章的整理结果：给出时只替换该章的贡献，
    空批表示这一章确认没有线索，移除旧贡献而不是把旧记录继续当当前。
    """
    if not entries:
        if source_chapter is None:
            raise WorkspaceError(
                code="STATE_EVIDENCE_INVALID",
                where="clues.publish",
                why="这一批没有线索",
                fix="至少给出一条带原文依据的线索，或这一轮不登记",
            )
        store.replace_source(source_chapter, ())
        return store.entries()
    for entry in entries:
        _check_entry(workspace, entry)
    if source_chapter is None:
        store.replace(tuple(entries))
    else:
        tagged = tuple(replace(item, source_chapter=source_chapter) for item in entries)
        store.replace_source(source_chapter, tagged)
    return store.entries()


def _check_entry(workspace: Workspace, entry: ClueEntry) -> None:
    if not entry.key:
        raise WorkspaceError(
            code="STATE_EVIDENCE_INVALID",
            where="clues.publish",
            why="线索内容不能为空",
            fix="写一句读者能看到的事实，例如「门锁内侧有擦痕」",
        )
    if entry.planted_chapter < 1:
        raise WorkspaceError(
            code="STATE_EVIDENCE_INVALID",
            where="clues.publish",
            why="埋设章必须是正整数",
            fix="写明这条线索从第几章开始出现在正文里",
        )
    if entry.due_chapter is not None and entry.due_chapter < entry.planted_chapter:
        raise WorkspaceError(
            code="STATE_EVIDENCE_INVALID",
            where=f"clues.publish:{entry.key}",
            why=f"计划回收章（第{entry.due_chapter}章）早于埋设章（第{entry.planted_chapter}章）",
            fix="计划回收章要晚于或等于埋设章；不确定时先不填",
            details={"clue": entry.key},
        )
    if entry.status == "resolved":
        if entry.resolution_chapter is None or entry.resolution_evidence is None:
            raise WorkspaceError(
                code="STATE_EVIDENCE_INVALID",
                where=f"clues.publish:{entry.key}",
                why="标为已回收时必须给出回收章与回收处的原文依据",
                fix="给出回收章、区间与引文；只是打算回收就先保持未回收",
                details={"clue": entry.key},
            )
        if entry.resolution_chapter < entry.planted_chapter:
            raise WorkspaceError(
                code="STATE_EVIDENCE_INVALID",
                where=f"clues.publish:{entry.key}",
                why=f"回收章（第{entry.resolution_chapter}章）早于埋设章（第{entry.planted_chapter}章）",
                fix="确认这两章的章号",
                details={"clue": entry.key},
            )
        check_evidence(workspace, entry.resolution_evidence, chapter=entry.resolution_chapter)
    check_evidence(workspace, entry.evidence, chapter=entry.planted_chapter)


def build_clues(
    workspace: Workspace, store: ClueStore | None = None, *, as_of_chapter: int | None = None
) -> ClueState:
    """按当前章排出未回收线索；失效条目整条不采用，并说明退回了什么。"""
    current = current_chapter(workspace, as_of_chapter)
    records = _merged_contributions(store.entries()) if store is not None else ()
    usable: list[ClueEntry] = []
    warnings: list[str] = []
    dropped: list[str] = []
    for item in records:
        if as_of_chapter is not None and item.planted_chapter > current:
            continue
        if item.resolution_chapter is not None and item.resolution_chapter > current:
            item = replace(item, status="open", resolution_chapter=None, resolution_evidence=None)
        status = store.status(workspace, item) if store is not None else "valid"
        if status != "valid":
            dropped.append(item.key)
            continue
        if item.planted_chapter > current:
            # 埋设章还没写到：这一版的正文里根本没有它，不注入也不报错。
            continue
        usable.append(item)
    if dropped:
        names = "、".join(f"「{name}」" for name in dropped[:3])
        more = f" 等 {len(dropped)} 条" if len(dropped) > 3 else ""
        warnings.append(f"{names}{more}的原文已改过或删除，线索登记失效，暂不采用。")

    open_items = [item for item in usable if item.status == "open"]
    closed = [item for item in usable if item.status != "open"]
    return ClueState(
        current_chapter=current,
        open=tuple(_order(open_items, current)),
        closed=tuple(sorted(closed, key=lambda item: -item.planted_chapter)),
        warnings=tuple(warnings),
    )


def _merged_contributions(entries: Sequence[ClueEntry]) -> tuple[ClueEntry, ...]:
    """同一线索的多章贡献只保留最新一章的一份；旧章贡献仍留在存储里供追溯。"""
    latest: dict[str, ClueEntry] = {}
    for item in entries:
        current = latest.get(item.key)
        if current is None or item.source_chapter >= current.source_chapter:
            latest[item.key] = item
    return tuple(latest.values())


def _order(items: list[ClueEntry], current: int) -> list[ClueEntry]:
    """超期的最先，其次是这一章该回收的，然后是有计划的，最后是没定回收章的。"""

    def rank(item: ClueEntry) -> tuple[int, int, int]:
        if item.due_chapter is None:
            return (3, 0, item.planted_chapter)
        if item.due_chapter <= current:
            return (0, item.due_chapter, item.planted_chapter)
        if item.due_chapter == current + 1:
            return (1, item.due_chapter, item.planted_chapter)
        return (2, item.due_chapter, item.planted_chapter)

    return sorted(items, key=rank)


def clues_section(
    state: ClueState,
    *,
    budget: int = 500,
    limit: int = 8,
    excluded: list[Exclusion] | None = None,
) -> str:
    """写作上下文里的线索台账：只列未回收的，超期在前。

    ``excluded`` 不是模型可见文本：它把没列入的条目按 id、原因与入口交给注入记录，
    让作者能看出「不是没有线索，而是这一轮没装下」。
    """
    if not state.open:
        return ""
    header = f"尚未回收的线索（截至第 {state.current_chapter} 章）："
    suffix = [
        "这些是读者看到过的线索，不等于真相；这一章要么推进，要么明确不推进。",
        f"（还有 {len(state.open)} 条未列入，用 `read_state` 的 `domain=clues` 查看。）",
    ]
    reserved = len("\n".join(suffix)) + 1
    lines: list[str] = []
    used = len(header)
    hidden = 0
    for item in state.open:
        if len(lines) >= limit:
            hidden += 1
            _exclude_clue(excluded, item, "limit", 0)
            continue
        marks = [f"第{item.planted_chapter}章埋下：{item.key}"]
        if item.due_chapter is None:
            marks.append("｜未定回收章")
        elif item.due_chapter <= state.current_chapter:
            marks.append(f"｜计划第{item.due_chapter}章回收，已超期")
        else:
            marks.append(f"｜计划第{item.due_chapter}章回收")
        if item.supports:
            marks.append(f"｜指向：{item.supports}")
        line = "- " + "".join(marks)
        if used + len(line) + 1 > budget - reserved:
            hidden += 1
            _exclude_clue(excluded, item, "budget", len(line))
            continue
        lines.append(line)
        used += len(line) + 1
    if not lines:
        return ""
    lines.append("这些是读者看到过的线索，不等于真相；这一章要么推进，要么明确不推进。")
    if hidden:
        lines.append(f"（还有 {hidden} 条未列入，用 `read_state` 的 `domain=clues` 查看。）")
    return "\n".join([header, *lines])


def _exclude_clue(
    excluded: list[Exclusion] | None,
    item: ClueEntry,
    reason: Literal["budget", "limit"],
    chars: int,
) -> None:
    if excluded is None:
        return
    excluded.append(
        Exclusion(
            id=f"clue:{item.key}",
            reason=reason,
            chars=chars,
            location="read_state domain=clues",
        )
    )


def clues_view(state: ClueState) -> dict[str, object]:
    def row(item: ClueEntry) -> dict[str, object]:
        return {
            "clue": item.clue,
            "planted_chapter": item.planted_chapter,
            "due_chapter": item.due_chapter,
            "status": item.status,
            "label": STATUS_LABEL[item.status],
            "overdue": item.status == "open" and state.is_overdue(item),
            "supports": item.supports,
            "resolution_chapter": item.resolution_chapter,
            "quote": item.evidence.quote,
        }

    return {
        "domain": "clues",
        "current_chapter": state.current_chapter,
        "entries": [row(item) for item in (*state.open, *state.closed)],
        "overdue": [item.key for item in state.overdue()],
        "warnings": list(state.warnings),
    }
