"""专有名词词典（0.6 V06-E3）。

同一个人三个名字、地名前后不一致，是长篇里最常见也最容易避免的漂移。这里把「这部作品
用过哪些名字」汇总成一份表：人物姓名与别名来自小传声明，地名、组织、物件由核对任务从
原文登记，每条带原文依据。

词汇表不新增设定文件，也不进作品 Git：它是从现有材料汇总出来的派生结果，删掉可以重建。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from hastings.workspace.assembly import Exclusion
from hastings.workspace.disclosure import (
    EntryStatus,
    ObservedEvidence,
    check_evidence,
)
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.metadata import parse_character
from hastings.workspace.state_store import read_records, write_records

__all__ = [
    "GlossaryConflict",
    "GlossaryState",
    "GlossaryStore",
    "GlossaryTerm",
    "TermKind",
    "build_glossary",
    "glossary_section",
    "glossary_view",
    "publish_terms",
]

TermKind = Literal["person", "place", "org", "item"]
TermSource = Literal["declared", "text"]

KIND_LABEL: dict[TermKind, str] = {
    "person": "人物",
    "place": "地点",
    "org": "组织",
    "item": "物件",
}

RECORD_VERSION = 2
RECIPE_VERSION = 2

ConflictKind = Literal["alias_collision", "name_collision", "kind_collision"]


@dataclass(frozen=True, slots=True)
class GlossaryTerm:
    term: str
    kind: TermKind
    aliases: tuple[str, ...]
    first_chapter: int
    source: TermSource
    #: 从原文登记的词带依据；人物姓名来自小传声明，没有原文依据。
    evidence: ObservedEvidence | None = None
    #: 这条贡献是哪一章的整理结果；重建一章只替换该章的贡献。
    source_chapter: int = 0

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "term": self.term,
            "kind": self.kind,
            "aliases": list(self.aliases),
            "first_chapter": self.first_chapter,
            "source_chapter": self.source_chapter,
        }
        if self.evidence is not None:
            payload["evidence"] = {
                "path": self.evidence.path,
                "digest": self.evidence.digest,
                "start": self.evidence.start,
                "end": self.evidence.end,
                "quote": self.evidence.quote,
            }
        return payload


@dataclass(frozen=True, slots=True)
class GlossaryConflict:
    term: str
    kind: ConflictKind
    detail: str


@dataclass(frozen=True, slots=True)
class GlossaryState:
    current_chapter: int
    terms: tuple[GlossaryTerm, ...]
    conflicts: tuple[GlossaryConflict, ...]
    warnings: tuple[str, ...] = ()


class GlossaryStore:
    """作品状态目录里的 ``glossary.json``；同名同类只留一条，后登记的覆盖旧的。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.file = self.root / "glossary.json"

    def entries(self) -> tuple[GlossaryTerm, ...]:
        terms: list[GlossaryTerm] = []
        for item in read_records(
            self.file,
            record_version=RECORD_VERSION,
            recipe_version=RECIPE_VERSION,
            key="terms",
        ):
            parsed = _term_from_payload(item)
            if parsed is not None:
                terms.append(parsed)
        return tuple(terms)

    def replace(self, terms: tuple[GlossaryTerm, ...]) -> None:
        merged = {(item.term, item.kind): item for item in self.entries()}
        for item in terms:
            merged[(item.term, item.kind)] = item
        write_records(
            self.file,
            prefix=".glossary-",
            record_version=RECORD_VERSION,
            recipe_version=RECIPE_VERSION,
            key="terms",
            entries=[item.to_dict() for item in merged.values()],
        )

    def replace_source(self, source_chapter: int, terms: tuple[GlossaryTerm, ...]) -> None:
        """只替换这一章的贡献；空批表示这一章确认没有新专名。"""
        kept = [item for item in self.entries() if item.source_chapter != source_chapter]
        merged = {(item.term, item.kind): item for item in terms}
        rows = [*kept, *merged.values()]
        write_records(
            self.file,
            prefix=".glossary-",
            record_version=RECORD_VERSION,
            recipe_version=RECIPE_VERSION,
            key="terms",
            entries=[item.to_dict() for item in rows],
        )

    def status(self, workspace: Workspace, term: GlossaryTerm) -> EntryStatus:
        evidence = term.evidence
        if evidence is None:
            return "valid"
        try:
            current = workspace.read(evidence.path).digest
        except WorkspaceError:
            return "source_missing"
        return "valid" if current == evidence.digest else "stale"


def _term_from_payload(item: object) -> GlossaryTerm | None:
    if not isinstance(item, dict):
        return None
    kind = item.get("kind")
    if kind not in KIND_LABEL:
        return None
    evidence_raw = item.get("evidence")
    evidence: ObservedEvidence | None = None
    if isinstance(evidence_raw, dict):
        try:
            evidence = ObservedEvidence(
                path=str(evidence_raw["path"]),
                digest=str(evidence_raw["digest"]),
                start=int(evidence_raw["start"]),
                end=int(evidence_raw["end"]),
                quote=str(evidence_raw["quote"]),
            )
        except (KeyError, TypeError, ValueError):
            evidence = None
    try:
        aliases = item.get("aliases")
        return GlossaryTerm(
            term=str(item["term"]),
            kind=kind,
            aliases=tuple(str(alias) for alias in aliases) if isinstance(aliases, list) else (),
            first_chapter=int(item.get("first_chapter") or 0),
            source="text",
            evidence=evidence,
            source_chapter=int(item.get("source_chapter") or 0),
        )
    except (KeyError, TypeError, ValueError):
        return None


def publish_terms(
    workspace: Workspace,
    store: GlossaryStore,
    terms: Sequence[GlossaryTerm],
    *,
    source_chapter: int | None = None,
) -> tuple[GlossaryTerm, ...]:
    """整批登记：每条都要有原文依据，任一条不过整批不写。

    ``source_chapter`` 给出时只替换该章的贡献；空批表示这一章确认没有新专名，
    移除该章旧贡献。给定来源时不再覆盖其他章登记过的词。
    """
    if not terms:
        if source_chapter is None:
            raise WorkspaceError(
                code="STATE_EVIDENCE_INVALID",
                where="glossary.publish",
                why="这一批没有词条",
                fix="至少给出一条带原文依据的词",
            )
        store.replace_source(source_chapter, ())
        return store.entries()
    for term in terms:
        if not term.term.strip():
            raise WorkspaceError(
                code="STATE_EVIDENCE_INVALID",
                where="glossary.publish",
                why="词条不能是空字符串",
                fix="写上作品里实际使用的名字",
            )
        if term.evidence is None:
            raise WorkspaceError(
                code="STATE_EVIDENCE_INVALID",
                where=f"glossary.publish:{term.term}",
                why="从原文登记的词必须给出一处原文依据",
                fix="用 read_file 读到这个词出现的句子，再登记区间与引文",
            )
        check_evidence(workspace, term.evidence, chapter=term.first_chapter)
    if source_chapter is None:
        store.replace(tuple(terms))
    else:
        tagged = tuple(replace(term, source_chapter=source_chapter) for term in terms)
        store.replace_source(source_chapter, tagged)
    return store.entries()


def _declared_terms(workspace: Workspace) -> list[GlossaryTerm]:
    terms: list[GlossaryTerm] = []
    for entry in workspace.catalog().characters:
        if not entry.exists:
            continue
        meta = parse_character(entry.path, workspace.read(entry.path).text)
        terms.append(
            GlossaryTerm(
                term=meta.title,
                kind="person",
                aliases=meta.aliases,
                first_chapter=0,
                source="declared",
            )
        )
    return terms


def _merged_contributions(entries: Sequence[GlossaryTerm]) -> tuple[GlossaryTerm, ...]:
    """同名同类只留最新一章的一份；别名取并集，首次出现章取最早。"""
    merged: dict[tuple[str, TermKind], GlossaryTerm] = {}
    order: list[tuple[str, TermKind]] = []
    for item in entries:
        key = (item.term, item.kind)
        current = merged.get(key)
        if current is None:
            merged[key] = item
            order.append(key)
            continue
        latest = item if item.source_chapter >= current.source_chapter else current
        merged[key] = replace(
            latest,
            aliases=tuple(dict.fromkeys((*current.aliases, *item.aliases))),
            first_chapter=min(current.first_chapter, item.first_chapter),
        )
    return tuple(merged[key] for key in order)


def build_glossary(
    workspace: Workspace, store: GlossaryStore | None = None, *, as_of_chapter: int | None = None
) -> GlossaryState:
    """声明的人物姓名在前，原文登记的专名在后；失效登记不采用，只留诊断。"""
    current = max(
        (
            entry.chapter_number
            for entry in workspace.catalog().chapters
            if entry.chapter_number is not None and entry.exists
        ),
        default=0,
    )
    warnings: list[str] = []
    if as_of_chapter is not None:
        current = min(current, max(0, as_of_chapter))
    terms = _declared_terms(workspace)
    if store is not None:
        for item in _merged_contributions(store.entries()):
            if item.first_chapter > current:
                continue
            status = store.status(workspace, item)
            if status != "valid":
                warnings.append(f"「{item.term}」的原文登记已失效，暂不采用。")
                continue
            terms.append(item)

    conflicts: list[GlossaryConflict] = []
    owners: dict[str, GlossaryTerm] = {}
    for term in terms:
        names = (term.term, *term.aliases)
        for name in names:
            owner = owners.get(name)
            if owner is None:
                owners[name] = term
                continue
            if owner.term == term.term and owner.kind == term.kind:
                continue
            conflict_kind: ConflictKind = (
                "kind_collision" if owner.term == term.term else "alias_collision"
            )
            if name == term.term and owner.term != term.term:
                conflict_kind = "name_collision"
            conflicts.append(
                GlossaryConflict(
                    term=name,
                    kind=conflict_kind,
                    detail=f"「{name}」同时指向 {owner.term}（{KIND_LABEL[owner.kind]}）"
                    f"与 {term.term}（{KIND_LABEL[term.kind]}）",
                )
            )
    return GlossaryState(
        current_chapter=current,
        terms=tuple(terms),
        conflicts=tuple(conflicts),
        warnings=tuple(warnings),
    )


def glossary_section(
    state: GlossaryState,
    *,
    limit: int = 24,
    excluded: list[Exclusion] | None = None,
) -> str:
    """写作上下文里的专名表：按类别列名字与别名，只给已经出现过的。

    每类最多 ``limit`` 个；超出的名字按类别记进 ``excluded``，仍然可以用
    ``read_state`` 的 ``domain=glossary`` 查全表。
    """
    grouped: dict[TermKind, list[str]] = {"person": [], "place": [], "org": [], "item": []}
    hidden = 0
    for term in state.terms:
        if term.source == "text" and term.first_chapter > state.current_chapter:
            continue
        label = term.term + (f"（{'、'.join(term.aliases)}）" if term.aliases else "")
        if len(grouped[term.kind]) >= limit:
            hidden += 1
            if excluded is not None:
                excluded.append(
                    Exclusion(
                        id=f"glossary:{term.term}",
                        reason="limit",
                        chars=len(label),
                        location="read_state domain=glossary",
                    )
                )
            continue
        grouped[term.kind].append(label)
    lines: list[str] = []
    for kind in ("person", "place", "org", "item"):
        names = grouped[kind]
        if names:
            lines.append(f"- {KIND_LABEL[kind]}：{'、'.join(names)}")
    if not lines:
        return ""
    lines.append("写到的名字用这里的写法；要新增专名时先确认作品里确实用了它。")
    if hidden:
        lines.append(f"（还有 {hidden} 个专名未列出，用 `read_state` 查看全表。）")
    return "\n".join(lines)


def glossary_view(state: GlossaryState) -> dict[str, object]:
    return {
        "domain": "glossary",
        "current_chapter": state.current_chapter,
        "terms": [
            {
                "term": term.term,
                "kind": term.kind,
                "label": KIND_LABEL[term.kind],
                "aliases": list(term.aliases),
                "first_chapter": term.first_chapter,
                "source": term.source,
            }
            for term in state.terms
        ],
        "conflicts": [
            {"term": conflict.term, "kind": conflict.kind, "detail": conflict.detail}
            for conflict in state.conflicts
        ],
        "warnings": list(state.warnings),
    }
