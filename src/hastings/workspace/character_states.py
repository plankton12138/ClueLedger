"""人物状态（0.6 V06-E4）：位置、生死、立场。

状态不是设定。设定说「林静是主治医师」，状态说「第 6 章时她在书房、还活着、对佐藤隐瞒」。
后者只能从正文读出来，随章节推移，而且必须带原文依据——没有它，「已死角色在第 9 章说话」
「同一个人同时在两个地方」这类问题只能靠人眼发现。

刻意不做声明字段：位置、生死、立场都由核对任务从原文读，作者不写第二份「应该是」。
作者对后续章节的打算写在正文或小传里，状态始终只有「原文测到什么」这一个来源。

与身份披露共用发布规则：整批校验、任一条不过整批不写、源文件改过即失效。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from hastings.workspace.disclosure import (
    EntryStatus,
    ObservedEvidence,
    check_evidence,
)
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace, is_managed_path
from hastings.workspace.metadata import parse_character
from hastings.workspace.state_store import current_chapter, read_records, write_records

__all__ = [
    "CharacterState",
    "CharacterStateStore",
    "RosterState",
    "StateSnapshot",
    "build_roster",
    "publish_states",
    "roster_section",
    "roster_view",
]

RECORD_VERSION = 1
RECIPE_VERSION = 1


@dataclass(frozen=True, slots=True)
class StateSnapshot:
    """一个角色在某一章正文里被核对到的一次状态。"""

    entity_path: str
    entity_digest: str
    chapter: int
    location: str
    alive: bool
    #: 立场：对某人隐瞒、倒向某方、暂时合作……可以是空。
    stance: str
    evidence: ObservedEvidence

    def to_dict(self) -> dict[str, object]:
        return {
            "entity_path": self.entity_path,
            "entity_digest": self.entity_digest,
            "chapter": self.chapter,
            "location": self.location,
            "alive": self.alive,
            "stance": self.stance,
            "evidence": {
                "path": self.evidence.path,
                "digest": self.evidence.digest,
                "start": self.evidence.start,
                "end": self.evidence.end,
                "quote": self.evidence.quote,
            },
        }


@dataclass(frozen=True, slots=True)
class CharacterState:
    """一个角色截至当前章的最后一次已知状态。"""

    path: str
    name: str
    #: 当前章及以前最后一个有效快照；从没核对过时为 None。
    current: StateSnapshot | None
    #: 最后一次核对距当前章差几章；没有记录时为 None。差值大说明状态可能是旧的。
    lag: int | None
    #: 有记录但因为源文件被改过而不采用时为真。
    stale: bool
    warnings: tuple[str, ...] = ()

    @property
    def dead(self) -> bool:
        return self.current is not None and not self.current.alive


@dataclass(frozen=True, slots=True)
class RosterState:
    current_chapter: int
    characters: tuple[CharacterState, ...]
    warnings: tuple[str, ...] = ()

    def known(self) -> tuple[CharacterState, ...]:
        return tuple(item for item in self.characters if item.current is not None)

    def dead(self) -> tuple[CharacterState, ...]:
        return tuple(item for item in self.known() if item.dead)


class CharacterStateStore:
    """作品状态目录里的 ``character-states.json``；同一角色同一章只留一条。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.file = self.root / "character-states.json"

    def entries(self) -> tuple[StateSnapshot, ...]:
        snapshots: list[StateSnapshot] = []
        for item in read_records(
            self.file,
            record_version=RECORD_VERSION,
            recipe_version=RECIPE_VERSION,
            key="entries",
        ):
            parsed = _snapshot_from_payload(item)
            if parsed is not None:
                snapshots.append(parsed)
        return tuple(snapshots)

    def replace(self, snapshots: tuple[StateSnapshot, ...]) -> None:
        merged = {(item.entity_path, item.chapter): item for item in self.entries()}
        for item in snapshots:
            merged[(item.entity_path, item.chapter)] = item
        write_records(
            self.file,
            prefix=".character-states-",
            record_version=RECORD_VERSION,
            recipe_version=RECIPE_VERSION,
            key="entries",
            entries=[item.to_dict() for item in merged.values()],
        )

    def status(self, workspace: Workspace, snapshot: StateSnapshot) -> EntryStatus:
        for path, digest in (
            (snapshot.entity_path, snapshot.entity_digest),
            (snapshot.evidence.path, snapshot.evidence.digest),
        ):
            try:
                current = workspace.read(path).digest
            except WorkspaceError:
                return "source_missing"
            if current != digest:
                return "stale"
        return "valid"


def _snapshot_from_payload(item: dict[str, object]) -> StateSnapshot | None:
    evidence = item.get("evidence")
    if not isinstance(evidence, dict):
        return None
    try:
        return StateSnapshot(
            entity_path=str(item["entity_path"]),
            entity_digest=str(item["entity_digest"]),
            chapter=int(str(item["chapter"])),
            location=str(item.get("location") or ""),
            alive=bool(item.get("alive", True)),
            stance=str(item.get("stance") or ""),
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


def publish_states(
    workspace: Workspace,
    store: CharacterStateStore,
    snapshots: Sequence[StateSnapshot],
) -> tuple[StateSnapshot, ...]:
    """整批发布：任一条不过整批不写，避免半份状态被当成当前状态。"""
    if not snapshots:
        raise WorkspaceError(
            code="STATE_EVIDENCE_INVALID",
            where="roster.publish",
            why="这一批没有状态",
            fix="至少给出一条带原文依据的状态",
        )
    for snapshot in snapshots:
        _check_snapshot(workspace, snapshot)
    store.replace(tuple(snapshots))
    return store.entries()


def _check_snapshot(workspace: Workspace, snapshot: StateSnapshot) -> None:
    if not is_managed_path(snapshot.entity_path) or "/角色小传/" not in f"/{snapshot.entity_path}":
        raise WorkspaceError(
            code="STATE_ENTITY_INVALID",
            where=f"roster.publish:{snapshot.entity_path}",
            why="人物状态只能记在角色小传上",
            fix="把 entity 写成 设定集/角色小传/姓名.md",
            details={"path": snapshot.entity_path},
        )
    if snapshot.chapter < 1:
        raise WorkspaceError(
            code="STATE_EVIDENCE_INVALID",
            where="roster.publish",
            why="章节号必须是正整数",
            fix="写明这条状态出自第几章",
        )
    if not snapshot.location.strip() and not snapshot.stance.strip():
        raise WorkspaceError(
            code="STATE_EVIDENCE_INVALID",
            where=f"roster.publish:{snapshot.entity_path}",
            why="一条状态至少要给出位置或立场",
            fix="写上原文里能确认的地点，或这一章结束时的立场",
        )
    try:
        current = workspace.read(snapshot.entity_path).digest
    except WorkspaceError as exc:
        raise WorkspaceError(
            code="STATE_SOURCE_STALE_CONFLICT",
            where=f"roster.publish:{snapshot.entity_path}",
            why="角色小传读不到，状态不能发布",
            fix="确认小传还在后重新读取",
            details={"path": snapshot.entity_path},
        ) from exc
    if current != snapshot.entity_digest:
        raise WorkspaceError(
            code="STATE_SOURCE_STALE_CONFLICT",
            where=f"roster.publish:{snapshot.entity_path}",
            why="小传在上次读取之后被改过",
            fix="重新 read_file 拿到最新 digest 后再发布",
            details={"path": snapshot.entity_path},
        )
    check_evidence(workspace, snapshot.evidence)


def build_roster(workspace: Workspace, store: CharacterStateStore | None = None) -> RosterState:
    """按当前章节算每个人的最新状态；失效快照退回上一个有效章，并说明退到哪。"""
    current = current_chapter(workspace)
    records = store.entries() if store is not None else ()
    warnings: list[str] = []
    characters: list[CharacterState] = []
    for entry in workspace.catalog().characters:
        if not entry.exists:
            continue
        meta = parse_character(entry.path, workspace.read(entry.path).text)
        owned = [item for item in records if item.entity_path == meta.path]
        usable: list[StateSnapshot] = []
        dropped: list[int] = []
        for item in owned:
            status = store.status(workspace, item) if store is not None else "valid"
            if status != "valid":
                dropped.append(item.chapter)
            elif item.chapter <= current:
                usable.append(item)
            # 记录属于当前章之后的章节：章号变了或文件被删，不报「失效」也不采用。
        usable.sort(key=lambda item: item.chapter)
        local_warnings: list[str] = []
        if dropped:
            chapters = "、".join(f"第{chapter}章" for chapter in sorted(set(dropped)))
            local_warnings.append(f"{chapters}的状态记录已失效（源文件被改过或已删除），不采用。")
        latest = usable[-1] if usable else None
        lag = current - latest.chapter if latest is not None else None
        if latest is not None and lag is not None and lag > 0:
            local_warnings.append(
                f"最后一次核对在第 {latest.chapter} 章，此后 {lag} 章没有核对记录；"
                "这些章里的变动未知。"
            )
        characters.append(
            CharacterState(
                path=meta.path,
                name=meta.title,
                current=latest,
                lag=lag,
                stale=bool(dropped),
                warnings=tuple(local_warnings),
            )
        )
        warnings.extend(f"{meta.title}：{item}" for item in local_warnings)
    return RosterState(
        current_chapter=current,
        characters=tuple(characters),
        warnings=tuple(warnings),
    )


def roster_section(state: RosterState, *, budget: int = 700, limit: int = 10) -> str:
    """写作上下文里的人物状态：已死亡的人最优先，其次按最后核对章倒序。

    超出预算时按行裁掉并在末尾说明还有几人未列入——整块丢掉等于写作时完全没有状态，
    比少给几个人更危险。
    """
    known = [item for item in state.known() if item.current is not None]
    if not known:
        return ""
    ordered = sorted(
        known,
        key=lambda item: (
            0 if item.dead else 1,
            -(item.current.chapter if item.current is not None else 0),
        ),
    )
    header = f"截至第 {state.current_chapter} 章的人物状态（按原文核对，最近一次在前）："
    lines: list[str] = []
    used = len(header)
    dropped = 0
    for index, item in enumerate(ordered):
        snapshot = item.current
        assert snapshot is not None  # known() 已过滤
        parts = [f"- {item.name}", f"｜{snapshot.location or '位置未记'}"]
        parts.append("｜已死亡" if not snapshot.alive else "｜在世")
        if snapshot.stance:
            parts.append(f"｜立场：{snapshot.stance}")
        parts.append(f"（第{snapshot.chapter}章核对")
        parts.append("，已落后）" if item.lag else "）")
        line = "".join(parts)
        if index >= limit:
            dropped += 1
            continue
        if used + len(line) + 1 > budget - 60:
            dropped += 1
            continue
        lines.append(line)
        used += len(line) + 1
    if not lines:
        return ""
    lines.append("以上是原文核对到的最近状态；没核对到的章节不要当作已知。")
    if dropped:
        lines.append(f"（还有 {dropped} 人未列入，用 `read_state` 查具体某人。）")
    lines.append("写已死角色时只能是回忆、转述或尸体；不要让他在新场景里行动。")
    return "\n".join([header, *lines])


def roster_view(state: RosterState) -> dict[str, object]:
    return {
        "domain": "roster",
        "current_chapter": state.current_chapter,
        "characters": [
            {
                "path": item.path,
                "name": item.name,
                "location": item.current.location if item.current else "",
                "alive": item.current.alive if item.current else True,
                "stance": item.current.stance if item.current else "",
                "chapter": item.current.chapter if item.current else None,
                "lag": item.lag,
                "stale": item.stale,
                "warnings": list(item.warnings),
            }
            for item in state.characters
        ],
        "warnings": list(state.warnings),
    }
