"""章级线索登记（v1.3 最小可行性切片，规格见 ``dev-notes/v1.3/clue-registry-proposal.md``）。

与 ``prose_ledger`` 同一哲学：纯函数、确定性、早失败，本模块不接入事务层，
只回答一个问题——**线索能不能作为随章维护的结构化事实跑起来**。

三条设计决策（提案 §2，已冻结）：

- **哈希锚定**：锚是段落内容哈希，不是段落下标；解析失败标失联，绝不静默。
- **章级权威**：每章 ``clues/ch{N}.json`` 是唯一权威，聚合视图只是派生产物，
  不落盘、坏了可重建，不存在双写。
- **计划先行**：``origin`` 区分计划线索与意外线索，意外线索必须带说明。

与 v0.6 旧线索账（``clues.py`` 的全局 ``clue-ledger.json``）无关，互不影响。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from hastings.workspace.atomic_io import atomic_write_json, locked
from hastings.workspace.errors import WorkspaceError

__all__ = [
    "CHAPTER_CLUES_SCHEMA",
    "CLUES_DIRNAME",
    "ClueAnchor",
    "ClueEntry",
    "ClueFinding",
    "CluePlan",
    "ClueState",
    "aggregate_clues",
    "anchor_paragraph",
    "clues_path",
    "para_hash",
    "paragraphs",
    "read_chapter_clues",
    "resolve_anchor",
    "validate_commit",
    "validate_entries",
    "write_chapter_clues",
]

#: 章级线索文件所在目录（与 ``prose-ledger.json`` 同级）。
CLUES_DIRNAME = "clues"

#: 章级线索数据结构版本。字段变更时递增。
CHAPTER_CLUES_SCHEMA = 1

#: 锚定前缀长度（失联时供重新定位）。
_ANCHOR_PREFIX_LEN = 16

Kind = Literal["伏笔", "悬念", "象征", "物件", "信息"]
Status = Literal["planted", "advanced", "resolved", "retired"]
Origin = Literal["planned", "emergent"]

_KINDS: frozenset[str] = frozenset({"伏笔", "悬念", "象征", "物件", "信息"})
_STATUSES: frozenset[str] = frozenset({"planted", "advanced", "resolved", "retired"})


def _error(code: str, where: str, why: str, fix: str) -> WorkspaceError:
    return WorkspaceError(code=code, where=where, why=why, fix=fix)


# ---------------------------------------------------------------------------
# 锚定


def para_hash(text: str) -> str:
    """段落内容哈希：去除首尾空白后取 SHA-256 前 16 位十六进制。"""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def paragraphs(text: str) -> list[str]:
    """正文按行切段，去空行、去首尾空白。"""
    return [stripped for line in text.splitlines() if (stripped := line.strip())]


def anchor_paragraph(text: str, chapter_revision: str) -> ClueAnchor:
    """为一段正文构造锚（内容哈希 + 定位前缀 + 锚定时章节修订号）。"""
    stripped = text.strip()
    return ClueAnchor(
        para_hash=para_hash(stripped),
        para_prefix=stripped[:_ANCHOR_PREFIX_LEN],
        chapter_revision=chapter_revision,
    )


def resolve_anchor(anchor: ClueAnchor, prose_text: str) -> bool:
    """锚是否仍指向本章某段正文（哈希精确匹配；对不上就是失联，不做模糊重试）。"""
    target = anchor.para_hash
    return any(para_hash(par) == target for par in paragraphs(prose_text))


# ---------------------------------------------------------------------------
# 数据结构与校验


@dataclass(frozen=True)
class ClueAnchor:
    """段落级锚定：内容哈希为主，前缀只用于失联后的重定位提示。"""

    para_hash: str
    para_prefix: str
    chapter_revision: str

    def to_json(self) -> dict[str, str]:
        return {
            "para_hash": self.para_hash,
            "para_prefix": self.para_prefix,
            "chapter_revision": self.chapter_revision,
        }

    @classmethod
    def from_json(cls, value: object) -> ClueAnchor:
        if not isinstance(value, dict):
            raise ValueError("anchor 不是对象")
        return cls(
            para_hash=str(value["para_hash"]),
            para_prefix=str(value["para_prefix"]),
            chapter_revision=str(value["chapter_revision"]),
        )


@dataclass(frozen=True)
class ResolvesRef:
    """跨章回收引用：本章是回收方时填写。"""

    clue_id: str
    planted_in: int
    anchor: ClueAnchor | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "clue_id": self.clue_id,
            "planted_in": self.planted_in,
            "anchor": self.anchor.to_json() if self.anchor else None,
        }

    @classmethod
    def from_json(cls, value: object) -> ResolvesRef:
        if not isinstance(value, dict):
            raise ValueError("resolves 不是对象")
        anchor = value.get("anchor")
        return cls(
            clue_id=str(value["clue_id"]),
            planted_in=int(value["planted_in"]),
            anchor=ClueAnchor.from_json(anchor) if anchor else None,
        )


@dataclass(frozen=True)
class ClueEntry:
    """单条线索登记。状态机与校验规则见提案 §3.2—§3.3。"""

    id: str
    kind: Kind
    status: Status
    origin: Origin
    note: str | None = None
    anchor: ClueAnchor | None = None
    resolves: ResolvesRef | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "origin": self.origin,
            "note": self.note,
            "anchor": self.anchor.to_json() if self.anchor else None,
            "resolves": self.resolves.to_json() if self.resolves else None,
        }

    @classmethod
    def from_json(cls, value: object) -> ClueEntry:
        if not isinstance(value, dict):
            raise ValueError("条目不是对象")
        anchor = value.get("anchor")
        resolves = value.get("resolves")
        return cls(
            id=str(value["id"]),
            kind=value["kind"],
            status=value["status"],
            origin=value["origin"],
            note=value.get("note"),
            anchor=ClueAnchor.from_json(anchor) if anchor else None,
            resolves=ResolvesRef.from_json(resolves) if resolves else None,
        )


@dataclass(frozen=True)
class ChapterClues:
    """一章的线索登记文件内容（权威数据）。"""

    chapter: int
    revision: str
    entries: tuple[ClueEntry, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "schema": CHAPTER_CLUES_SCHEMA,
            "chapter": self.chapter,
            "revision": self.revision,
            "entries": [entry.to_json() for entry in self.entries],
        }


def validate_entries(entries: tuple[ClueEntry, ...] | list[ClueEntry], chapter: int) -> None:
    """写入前校验（早失败）。规则见提案 §3.3；空响在聚合侧检测。"""
    where = f"clues.ch{chapter}"
    seen: set[str] = set()
    local_ids = {entry.id for entry in entries}
    for entry in entries:
        if entry.id in seen:
            raise _error(
                "CLUE_DUPLICATE_ID",
                where,
                f"线索 {entry.id} 在本章登记中重复",
                "每条线索本章只登记一次；多次推进合并为一条最新状态",
            )
        seen.add(entry.id)
        if entry.kind not in _KINDS:
            raise _error(
                "CLUE_ENTRY_INVALID",
                where,
                f"线索 {entry.id} 的 kind 非法：{entry.kind!r}",
                f"kind 取值限 {'/'.join(sorted(_KINDS))}",
            )
        if entry.status not in _STATUSES:
            raise _error(
                "CLUE_ENTRY_INVALID",
                where,
                f"线索 {entry.id} 的 status 非法：{entry.status!r}",
                f"status 取值限 {'/'.join(sorted(_STATUSES))}",
            )
        if entry.origin not in {"planned", "emergent"}:
            raise _error(
                "CLUE_ENTRY_INVALID",
                where,
                f"线索 {entry.id} 的 origin 非法：{entry.origin!r}",
                "origin 取值限 planned/emergent",
            )
        if entry.origin == "emergent" and not (entry.note or "").strip():
            raise _error(
                "CLUE_EMERGENT_NOTE_REQUIRED",
                where,
                f"意外线索 {entry.id} 缺少说明",
                "意外线索必须带 note 说明它从哪段长出来的",
            )
        if entry.status == "retired" and not (entry.note or "").strip():
            raise _error(
                "CLUE_RETIRED_NOTE_REQUIRED",
                where,
                f"线索 {entry.id} 声明不收但缺少理由",
                "retired 必须带 note 说明为什么不收（如「纯细节不收」）",
            )
        if entry.status in {"planted", "advanced"} and entry.anchor is None:
            raise _error(
                "CLUE_ANCHOR_REQUIRED",
                where,
                f"线索 {entry.id} 状态为 {entry.status} 但没有锚",
                "planted/advanced 必须锚定到本章某段正文",
            )
        if entry.status == "resolved" and entry.resolves is None:
            raise _error(
                "CLUE_RESOLVES_REQUIRED",
                where,
                f"线索 {entry.id} 标记已收但没有回收引用",
                "resolved 必须填写 resolves（指向被收线索的埋设记录）",
            )
        ref = entry.resolves
        if ref is not None:
            if ref.planted_in > chapter:
                raise _error(
                    "CLUE_RESOLUTION_AHEAD",
                    where,
                    f"线索 {entry.id} 声称回收第 {ref.planted_in} 章埋的线索，"
                    f"但本章是第 {chapter} 章",
                    "planted_in 不得晚于本章；埋收同章时两条登记须同在本章文件内",
                )
            if ref.planted_in == chapter and ref.clue_id == entry.id:
                raise _error(
                    "CLUE_SELF_RESOLUTION",
                    where,
                    f"线索 {entry.id} 不得指向本章自己埋的记录",
                    "埋收同章不算线索推进：埋与收须是本章文件内两个条目",
                )
            if ref.planted_in == chapter and ref.clue_id not in local_ids:
                raise _error(
                    "CLUE_ORPHAN_RESOLUTION",
                    where,
                    f"线索 {entry.id} 声称回收本章埋的 {ref.clue_id}，"
                    "但本章登记里没有它的 planted 条目",
                    "埋收同章时两条登记（埋与收）必须同在本章文件内",
                )


def clues_path(root: Path, chapter: int) -> Path:
    return Path(root) / CLUES_DIRNAME / f"ch{chapter}.json"


def validate_commit(
    root: Path,
    chapter: int,
    entries: list[ClueEntry],
) -> None:
    """提交门校验：章内规则之外，对照此前各章聚合视图拦截空响。

    这是提案 §9 定案 5 的强制点：空响是确定性矛盾，拦截提交；
    失联、哑火等其余发现项不在此处拦（见 ``aggregate_clues`` 报告）。
    随章提交事务调用（接入事务层后），幂等键复用章提交事务。
    """
    validate_entries(entries, chapter)
    prior = [c for c in range(1, chapter) if clues_path(root, c).exists()]
    prior_states: dict[str, ClueState] = {}
    if prior:
        prior_states = aggregate_clues(root, prior).states
    for entry in entries:
        ref = entry.resolves
        if ref is None or ref.planted_in == chapter:
            continue
        target = prior_states.get(ref.clue_id)
        if target is None or target.planted_in != ref.planted_in:
            raise _error(
                "CLUE_ORPHAN_RESOLUTION",
                f"clues.ch{chapter}",
                f"线索 {entry.id} 声称回收第 {ref.planted_in} 章埋的 {ref.clue_id}，"
                "但此前各章没有匹配的埋设记录（空响）",
                "先在对应章补登埋设，或改为本章新埋一条线索",
            )


def write_chapter_clues(root: Path, chapter: int, revision: str, entries: list[ClueEntry]) -> None:
    """写入一章的线索登记（校验先行，原子落盘，跨进程锁）。"""
    entries_tuple = tuple(entries)
    validate_entries(entries_tuple, chapter)
    document = ChapterClues(chapter=chapter, revision=revision, entries=entries_tuple)
    path = clues_path(root, chapter)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path.with_name(path.name + ".lock")):
        atomic_write_json(path, document.to_json())


def read_chapter_clues(root: Path, chapter: int) -> ChapterClues | None:
    """严格读取一章的线索登记；文件缺失返回 None，损坏带归因上抛。"""
    path = clues_path(root, chapter)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise _error(
            "CLUE_FILE_CORRUPT",
            f"clues.ch{chapter}",
            f"第 {chapter} 章的线索登记无法解析：{exc}",
            "从状态备份恢复，或删除该文件后由模型补登（对账会提示）",
        ) from exc
    if not isinstance(raw, dict) or raw.get("schema") != CHAPTER_CLUES_SCHEMA:
        raise _error(
            "CLUE_FILE_CORRUPT",
            f"clues.ch{chapter}",
            f"第 {chapter} 章的线索登记 schema 不符",
            f"当前 schema 为 {CHAPTER_CLUES_SCHEMA}；旧数据需迁移后读取",
        )
    try:
        entries = tuple(ClueEntry.from_json(item) for item in raw["entries"])
        document = ChapterClues(
            chapter=int(raw["chapter"]), revision=str(raw["revision"]), entries=entries
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise _error(
            "CLUE_FILE_CORRUPT",
            f"clues.ch{chapter}",
            f"第 {chapter} 章的线索登记字段不完整：{exc}",
            "从状态备份恢复，或删除该文件后由模型补登（对账会提示）",
        ) from exc
    validate_entries(document.entries, document.chapter)
    return document


# ---------------------------------------------------------------------------
# 聚合视图与对账


@dataclass
class ClueState:
    """一条线索在聚合视图里的合并状态（派生产物，不落盘）。"""

    id: str
    kind: str
    origin: str
    status: str
    planted_in: int | None
    last_chapter: int
    note: str | None = None
    resolved_in: int | None = None


@dataclass
class CluePlan:
    """结构层「计划线索」节的一条声明（本切片用结构化输入，解析留给 P1）。"""

    id: str
    plant_chapter: int
    resolve_chapter: int | None = None
    note: str | None = None


@dataclass
class ClueFinding:
    """对账发现项：code + 可读说明，全部确定性推导。"""

    code: str
    message: str


@dataclass
class ClueAggregate:
    """跨章聚合结果：合并状态 + 发现项。"""

    states: dict[str, ClueState] = field(default_factory=dict)
    findings: tuple[ClueFinding, ...] = ()


def aggregate_clues(
    root: Path,
    chapters: list[int] | tuple[int, ...],
    *,
    prose_by_chapter: dict[int, str] | None = None,
    plans: list[CluePlan] | None = None,
) -> ClueAggregate:
    """从各章权威文件聚合线索状态并执行确定性检测。

    ``prose_by_chapter`` 提供各章正文文本时做锚失联检测；
    ``plans`` 提供结构层计划线索时做哑火/悬枪检测。
    """
    states: dict[str, ClueState] = {}
    findings: list[ClueFinding] = []
    prose = prose_by_chapter or {}

    for chapter in sorted(chapters):
        document = read_chapter_clues(root, chapter)
        if document is None:
            continue
        for entry in document.entries:
            state = states.get(entry.id)
            if state is None:
                states[entry.id] = ClueState(
                    id=entry.id,
                    kind=entry.kind,
                    origin=entry.origin,
                    status=entry.status,
                    planted_in=(
                        chapter if entry.status in {"planted", "advanced", "resolved"} else None
                    ),
                    last_chapter=chapter,
                    note=entry.note,
                    resolved_in=chapter if entry.status == "resolved" else None,
                )
            else:
                state.status = entry.status
                state.last_chapter = chapter
                if entry.note:
                    state.note = entry.note
                if entry.status == "resolved":
                    state.resolved_in = chapter
            ref = entry.resolves
            if ref is not None:
                target = states.get(ref.clue_id)
                if (
                    target is None
                    or target.planted_in is None
                    or target.planted_in != ref.planted_in
                ):
                    findings.append(
                        ClueFinding(
                            code="CLUE_ORPHAN_RESOLUTION",
                            message=(
                                f"第 {chapter} 章 {entry.id} 声称回收第 {ref.planted_in} 章"
                                f"埋的 {ref.clue_id}，但聚合视图里没有匹配的埋设记录（空响）"
                            ),
                        )
                    )
            anchor = entry.anchor or (ref.anchor if ref else None)
            if anchor is not None:
                chapter_text = prose.get(chapter)
                if chapter_text is not None and not resolve_anchor(anchor, chapter_text):
                    findings.append(
                        ClueFinding(
                            code="CLUE_ORPHANED_ANCHOR",
                            message=(
                                f"第 {chapter} 章 {entry.id} 锚失联，"
                                f"前缀「{anchor.para_prefix}」，请重新锚定或声明 retired"
                            ),
                        )
                    )

    written = max(chapters) if chapters else 0
    for plan in plans or []:
        state = states.get(plan.id)
        if plan.plant_chapter <= written and (state is None or state.planted_in is None):
            findings.append(
                ClueFinding(
                    code="CLUE_NOT_PLANTED",
                    message=(
                        f"计划线索 {plan.id} 声明第 {plan.plant_chapter} 章埋设，"
                        f"已写到第 {written} 章仍无 planted 记录（哑火）"
                    ),
                )
            )
        if (
            plan.resolve_chapter is not None
            and plan.resolve_chapter <= written
            and (state is None or state.status not in {"resolved", "retired"})
        ):
            findings.append(
                ClueFinding(
                    code="CLUE_UNRESOLVED",
                    message=(
                        f"计划线索 {plan.id} 声明第 {plan.resolve_chapter} 章回收，"
                        f"已写到第 {written} 章仍未收（悬枪）"
                    ),
                )
            )
    return ClueAggregate(states=states, findings=tuple(findings))
