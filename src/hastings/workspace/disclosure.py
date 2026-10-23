"""身份披露状态（0.6 V06-E2）。

身份不是启动时写死的标签，而是随章节推进逐步给读者看的信息。这里只做三件事：

- 读元信息里的声明（第几章起读者已知、计划第几章披露）；
- 按当前章节算出「现在可以写什么」；
- 把冲突列成清单（别名撞车、两个真凶、同一事实既已披露又计划披露）。

声明由元信息实时算出，正文核对结果保存为可重建记录；装配和查询按任务时点筛选，
失效记录逐条排除，其他有效记录继续采用。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace, is_managed_path
from hastings.workspace.materials import MaterialLedger, ReadRequirement
from hastings.workspace.metadata import CharacterMeta, DeclaredFact, parse_character
from hastings.workspace.state_store import current_chapter, read_records, write_records

__all__ = [
    "Conflict",
    "DisclosureState",
    "DisclosureStore",
    "EntityState",
    "ObservedEvidence",
    "ObservedFact",
    "VisibleFact",
    "build_disclosure_state",
    "check_evidence",
    "publish_observed",
    "state_section",
    "state_view",
]

ConflictKind = Literal["alias_collision", "duplicate_culprit", "fact_redeclared"]
FactSource = Literal["text", "declared"]
EntryStatus = Literal["valid", "stale", "source_missing"]

#: 记录结构版本与生成规则版本；规则变化时旧记录整份过期，不猜测兼容。
RECORD_VERSION = 1
RECIPE_VERSION = 2

_CONFLICT_LABEL: dict[ConflictKind, str] = {
    "alias_collision": "别名重复",
    "duplicate_culprit": "两个真凶",
    "fact_redeclared": "同一事实重复声明",
}


@dataclass(frozen=True, slots=True)
class Conflict:
    path: str
    name: str
    kind: ConflictKind
    detail: str


@dataclass(frozen=True, slots=True)
class VisibleFact:
    """一条已经给读者看到的身份事实；``source`` 说明是正文核对过的还是作者声明的。"""

    fact: str
    chapter: int
    source: FactSource


@dataclass(frozen=True, slots=True)
class ObservedEvidence:
    """一处原文依据：版本、字符区间（左闭右开）与引文。"""

    path: str
    digest: str
    start: int
    end: int
    quote: str


@dataclass(frozen=True, slots=True)
class ObservedFact:
    """从正文核对到的一条披露。``entity_path`` 是它所属的小传。"""

    entity_path: str
    entity_digest: str
    fact: str
    chapter: int
    evidence: ObservedEvidence

    def to_dict(self) -> dict[str, object]:
        return {
            "entity_path": self.entity_path,
            "entity_digest": self.entity_digest,
            "fact": self.fact,
            "chapter": self.chapter,
            "evidence": {
                "path": self.evidence.path,
                "digest": self.evidence.digest,
                "start": self.evidence.start,
                "end": self.evidence.end,
                "quote": self.evidence.quote,
            },
        }


@dataclass(frozen=True, slots=True)
class EntityState:
    """一个角色在当前章节下的可见身份。"""

    path: str
    name: str
    surface: str
    aliases: tuple[str, ...]
    roles: tuple[str, ...]
    #: 已经写到读者眼前的披露，按章序排列；正文核对过的排在声明之前。
    revealed: tuple[VisibleFact, ...]
    #: 声明了起始章但正文还没到那一章的披露：不注入，等章节写出来再生效。
    upcoming: tuple[DeclaredFact, ...]
    #: 计划披露：只在作者侧显示，从不进入写作上下文。
    planned: tuple[DeclaredFact, ...]
    warnings: tuple[str, ...]
    #: 派生记录里这个角色的条目状态；没有记录时为空。
    record_status: EntryStatus | None = None
    current_chapter: int = 0

    @property
    def overdue(self) -> bool:
        """计划章已到，但这一版无法证明正文已经写到。"""
        return any(item.chapter <= self.current_chapter for item in self.planned)

    @property
    def verified(self) -> int:
        return sum(1 for item in self.revealed if item.source == "text")


@dataclass(frozen=True, slots=True)
class DisclosureState:
    current_chapter: int
    entities: tuple[EntityState, ...]
    conflicts: tuple[Conflict, ...]

    def visible(self) -> tuple[EntityState, ...]:
        return tuple(entity for entity in self.entities if entity.revealed)


class DisclosureStore:
    """作品状态目录里的 ``disclosure.json``：可重建的核对记录，不参与作品 Git。

    读取损坏按空处理——宁可退回「只按声明显示」，也不要让面板与装配一起失败。写入用
    临时文件替换，且是整份覆盖：同一角色同一事实只保留一条，后来的核对结果覆盖旧的。
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.file = self.root / "disclosure.json"

    def entries(self) -> tuple[ObservedFact, ...]:
        facts: list[ObservedFact] = []
        for item in read_records(
            self.file,
            record_version=RECORD_VERSION,
            recipe_version=RECIPE_VERSION,
            key="entries",
        ):
            parsed = _fact_from_payload(item)
            if parsed is not None:
                facts.append(parsed)
        return tuple(facts)

    def replace(self, facts: tuple[ObservedFact, ...]) -> None:
        merged = {(item.entity_path, item.fact): item for item in self.entries()}
        for item in facts:
            merged[(item.entity_path, item.fact)] = item
        write_records(
            self.file,
            prefix=".disclosure-",
            record_version=RECORD_VERSION,
            recipe_version=RECIPE_VERSION,
            key="entries",
            entries=[item.to_dict() for item in merged.values()],
        )

    def status(self, workspace: Workspace, fact: ObservedFact) -> EntryStatus:
        """按当前磁盘算条目状态：小传或原文任一被改过，这条核对就不再成立。"""
        for path, digest in (
            (fact.entity_path, fact.entity_digest),
            (fact.evidence.path, fact.evidence.digest),
        ):
            try:
                current = workspace.read(path).digest
            except WorkspaceError:
                return "source_missing"
            if current != digest:
                return "stale"
        return "valid"


def _fact_from_payload(item: object) -> ObservedFact | None:
    if not isinstance(item, dict):
        return None
    evidence = item.get("evidence")
    if not isinstance(evidence, dict):
        return None
    try:
        return ObservedFact(
            entity_path=str(item["entity_path"]),
            entity_digest=str(item["entity_digest"]),
            fact=str(item["fact"]),
            chapter=int(item["chapter"]),
            evidence=ObservedEvidence(
                path=str(evidence["path"]),
                digest=str(evidence["digest"]),
                start=int(evidence["start"]),
                end=int(evidence["end"]),
                quote=str(evidence["quote"]),
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None


def publish_observed(
    workspace: Workspace,
    store: DisclosureStore,
    facts: tuple[ObservedFact, ...],
) -> tuple[ObservedFact, ...]:
    """整批核对后发布：任一条证据对不上，整批不写，避免半份记录被当成完整结果。"""
    if not facts:
        raise WorkspaceError(
            code="STATE_EVIDENCE_INVALID",
            where="disclosure.publish",
            why="这一批没有条目",
            fix="至少给出一条带原文证据的披露",
        )
    for fact in facts:
        _check_fact(workspace, fact)
    store.replace(facts)
    return store.entries()


def _check_fact(workspace: Workspace, fact: ObservedFact) -> None:
    if not is_managed_path(fact.entity_path) or "/角色小传/" not in f"/{fact.entity_path}":
        raise WorkspaceError(
            code="STATE_ENTITY_INVALID",
            where=f"disclosure.publish:{fact.entity_path}",
            why="披露只能记在角色小传上",
            fix="把 entity 写成 设定集/角色小传/姓名.md",
            details={"path": fact.entity_path},
        )
    if fact.chapter < 1:
        raise WorkspaceError(
            code="STATE_EVIDENCE_INVALID",
            where="disclosure.publish",
            why="章节号必须是正整数",
            fix="写明这条披露从第几章开始对读者可见",
        )
    _require_digest(workspace, fact.entity_path, fact.entity_digest)
    check_evidence(workspace, fact.evidence, chapter=fact.chapter)


def check_evidence(
    workspace: Workspace,
    evidence: ObservedEvidence,
    *,
    chapter: int | None = None,
    ledger: MaterialLedger | None = None,
) -> None:
    """核对一处原文依据：版本、区间与引文三者一致才算通过。

    身份披露与名词词典共用这一份规则，避免两套实现放过不同的问题。
    """
    source = next((e for e in workspace.catalog().chapters if e.path == evidence.path), None)
    if (
        source is None
        or source.chapter_number is None
        or (chapter is not None and source.chapter_number != chapter)
    ):
        raise WorkspaceError(
            code="STATE_EVIDENCE_INVALID",
            where=f"state.publish:{evidence.path}",
            why="证据必须来自所登记章号的正文文件",
            fix="读取对应章节，并使用该章的版本、区间与引文",
        )
    _require_digest(workspace, evidence.path, evidence.digest)
    text = workspace.read(evidence.path).text
    start, end, quote = evidence.start, evidence.end, evidence.quote
    if not 0 <= start < end <= len(text):
        raise WorkspaceError(
            code="STATE_EVIDENCE_INVALID",
            where=f"state.publish:{evidence.path}",
            why=f"区间 {start}-{end} 超出当前原文（共 {len(text)} 字符）",
            fix="按 read_file 返回的字符位置重新取区间",
        )
    if text[start:end] != quote:
        raise WorkspaceError(
            code="STATE_EVIDENCE_INVALID",
            where=f"state.publish:{evidence.path}",
            why="引文与该区间上的原文不一致",
            fix="用 read_file 重新读这段原文，引文要与区间完全对应",
            details={"path": evidence.path, "start": start, "end": end},
        )
    if ledger is not None:
        ledger.require_target_read(
            workspace,
            ReadRequirement(
                path=evidence.path,
                mode="range",
                start=start,
                end=end,
            ),
        )


def _require_digest(workspace: Workspace, path: str, digest: str) -> None:
    try:
        current = workspace.read(path).digest
    except WorkspaceError as exc:
        raise WorkspaceError(
            code="STATE_SOURCE_STALE_CONFLICT",
            where=f"disclosure.publish:{path}",
            why="来源文件读不到，核对结果不能发布",
            fix="刷新目录，确认文件还在后重新读取",
            details={"path": path},
        ) from exc
    if current != digest:
        raise WorkspaceError(
            code="STATE_SOURCE_STALE_CONFLICT",
            where=f"disclosure.publish:{path}",
            why="来源文件在本轮读取之后被改过",
            fix="重新读取该文件，按新版本重做这一条",
            details={"path": path},
        )


def _entity(
    meta: CharacterMeta,
    current: int,
    observed: tuple[ObservedFact, ...],
    status: EntryStatus | None,
) -> EntityState:
    warnings = list(meta.warnings)
    revealed: list[VisibleFact] = []
    upcoming: list[DeclaredFact] = []
    verified = {item.fact: item for item in observed if item.chapter <= current}
    for fact in observed:
        if fact.chapter <= current:
            revealed.append(VisibleFact(fact=fact.fact, chapter=fact.chapter, source="text"))
    for declared in meta.revealed:
        if declared.chapter <= current:
            # 正文核对过的事实以正文为准，声明只补充还没有证据的部分。
            if declared.fact in verified:
                continue
            revealed.append(
                VisibleFact(fact=declared.fact, chapter=declared.chapter, source="declared")
            )
        else:
            upcoming.append(declared)
            warnings.append(
                f"声明第 {declared.chapter} 章披露「{declared.fact}」，"
                f"但正文只写到第 {current} 章，暂不生效。"
            )
    if status in ("stale", "source_missing"):
        warnings.append(
            "部分正文核对记录已失效（源文件被改过或已删除），只采用仍有效的记录与声明；"
            "失效的部分需重新核对。"
        )
    revealed.sort(key=lambda item: (item.chapter, item.source != "text"))
    return EntityState(
        path=meta.path,
        name=meta.title,
        surface=meta.surface,
        aliases=meta.aliases,
        roles=tuple(meta.roles),
        revealed=tuple(revealed),
        upcoming=tuple(upcoming),
        planned=meta.planned,
        warnings=tuple(warnings),
        record_status=status,
        current_chapter=current,
    )


def build_disclosure_state(
    workspace: Workspace,
    store: DisclosureStore | None = None,
    *,
    as_of_chapter: int | None = None,
) -> DisclosureState:
    """读全部小传，按当前章节算可见身份，并汇总跨文件的冲突。

    传入 ``store`` 时合并正文核对记录：有效的核对事实优先于作者声明，失效的记录不采用，
    只留下「已失效」的诊断。
    """
    current = current_chapter(workspace, as_of_chapter)
    metas: list[CharacterMeta] = []
    for entry in workspace.catalog().characters:
        if not entry.exists:
            continue
        meta = parse_character(entry.path, workspace.read(entry.path).text)
        metas.append(meta)
    records = store.entries() if store is not None else ()
    entities: list[EntityState] = []
    for meta in metas:
        owned = tuple(item for item in records if item.entity_path == meta.path)
        statuses = (
            {store.status(workspace, item) for item in owned}
            if store is not None and owned
            else set()
        )
        status: EntryStatus | None
        if not owned:
            status = None
        elif "source_missing" in statuses:
            status = "source_missing"
        elif "stale" in statuses:
            status = "stale"
        else:
            status = "valid"
        usable = tuple(
            item for item in owned if store is not None and store.status(workspace, item) == "valid"
        )
        entities.append(_entity(meta, current, usable, status))

    conflicts: list[Conflict] = []
    owners: dict[str, EntityState] = {}
    for entity in entities:
        for alias in entity.aliases:
            other = owners.get(alias)
            if other is None:
                owners[alias] = entity
            elif other.path != entity.path:
                # 双方各报一条：只报后来者，作者会以为先写的那份没有问题。
                conflicts.append(
                    Conflict(
                        path=entity.path,
                        name=entity.name,
                        kind="alias_collision",
                        detail=f"「{alias}」也指向 {other.name}（{other.path}）",
                    )
                )
                conflicts.append(
                    Conflict(
                        path=other.path,
                        name=other.name,
                        kind="alias_collision",
                        detail=f"「{alias}」也指向 {entity.name}（{entity.path}）",
                    )
                )
    culprits = [entity for entity in entities if "culprit" in entity.roles]
    if len(culprits) > 1:
        names = "、".join(entity.name for entity in culprits)
        for entity in culprits:
            conflicts.append(
                Conflict(
                    path=entity.path,
                    name=entity.name,
                    kind="duplicate_culprit",
                    detail=f"标为真凶的角色有多个：{names}",
                )
            )
    for entity, meta in zip(entities, metas, strict=True):
        planned = {item.fact for item in meta.planned}
        for item in meta.revealed:
            if item.fact in planned:
                conflicts.append(
                    Conflict(
                        path=entity.path,
                        name=entity.name,
                        kind="fact_redeclared",
                        detail=f"「{item.fact}」既写在已披露里，也写在计划披露里",
                    )
                )
    return DisclosureState(
        current_chapter=current,
        entities=tuple(entities),
        conflicts=tuple(conflicts),
    )


def state_section(state: DisclosureState) -> str:
    """写作上下文里的身份段：只给读者已经知道的部分，计划披露不出现。"""
    visible = state.visible()
    if not visible:
        return ""
    lines = [f"截至第 {state.current_chapter} 章，读者已知的人物身份："]
    for entity in visible:
        parts = [f"- {entity.name}"]
        if entity.aliases:
            parts.append(f"（{'、'.join(entity.aliases)}）")
        if entity.surface:
            parts.append(f"｜社会身份：{entity.surface}")
        facts = "、".join(f"{item.fact}（第{item.chapter}章起）" for item in entity.revealed)
        parts.append(f"｜读者已知：{facts}")
        lines.append("".join(parts))
    lines.append("以上只写正文已经交代给读者的内容；未列出的信息这一轮不要写出来。")
    return "\n".join(lines)


def state_view(state: DisclosureState) -> dict[str, object]:
    """给界面的视图：声明与计划分开，冲突单列。"""
    return {
        "domain": "disclosure",
        "current_chapter": state.current_chapter,
        "entities": [
            {
                "path": entity.path,
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
                "overdue": entity.overdue,
                "record_status": entity.record_status,
                "verified": entity.verified,
                "warnings": list(entity.warnings),
            }
            for entity in state.entities
        ],
        "conflicts": [
            {
                "path": conflict.path,
                "name": conflict.name,
                "kind": conflict.kind,
                "label": _CONFLICT_LABEL[conflict.kind],
                "detail": conflict.detail,
            }
            for conflict in state.conflicts
        ],
    }
