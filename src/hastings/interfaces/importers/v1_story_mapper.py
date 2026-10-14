"""V1 工作区 → V2 提案的 dry-run 映射器（Phase 13 T13.8；plan §4/§6/§8）。

职责边界：只读 V1 账本，把每个源字段归类为 mapped / legacy / ambiguous / error
之一，并组装一个可提交的 DomainChange 提案；本模块不持有 Repository 写端口、
不写盘，为 Phase 17 真迁移提供证据。

归类口径（每个源字段恰有一种结果，不静默丢失）：
- mapped：V2 目标字段原样承接该值；
- legacy：V2 无对应概念，值原样保留进目标 Artifact 的 legacy_metadata；
- ambiguous：V2 有对应字段，但 V1 的值（自由文本引用 / 显示名）无法自动转换，
  须作者决策；值同时保留进 legacy_metadata，绝不丢弃；
- error：V2 要求的字段在 V1 源中根本不存在（无法从源推导），该实体不进入提案。

四透镜落地：错误消息是喂给模型的 API——每条 error 带「哪里错 / 为什么错 /
怎样算对」三要素与字段级定位；未覆盖的新源字段一律降级为 error，噪音暴露而非沉默。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from hastings.bible.models import (
    Character as V1Character,
)
from hastings.bible.models import (
    Clue as V1Clue,
)
from hastings.bible.models import (
    Foreshadow as V1Foreshadow,
)
from hastings.bible.models import (
    Method as V1Method,
)
from hastings.bible.models import (
    OutlineEntry as V1Outline,
)
from hastings.bible.models import (
    TimelineEntry as V1Timeline,
)
from hastings.bible.store import StoryWorkspace, list_characters, load_ledger
from hastings.domain.story import error_codes as ec
from hastings.domain.story import kinds
from hastings.domain.story.canon.models import (
    Character,
    Clue,
    Foreshadow,
    TimelineEvent,
)
from hastings.domain.story.changes import DomainChange
from hastings.domain.story.ids import deterministic_story_id
from hastings.domain.story.narrative.models import Beat, NarrativeNode
from hastings.domain.story.refs import create_change

__all__ = [
    "IMPORT_OUTCOMES",
    "OUTCOME_AMBIGUOUS",
    "OUTCOME_ERROR",
    "OUTCOME_LEGACY",
    "OUTCOME_MAPPED",
    "EntityMapping",
    "FieldMapping",
    "ImportIssue",
    "ImportPlan",
    "ImportReport",
    "SourceNote",
    "map_v1_workspace",
]

OUTCOME_MAPPED = "mapped"
OUTCOME_LEGACY = "legacy"
OUTCOME_AMBIGUOUS = "ambiguous"
OUTCOME_ERROR = "error"
IMPORT_OUTCOMES = frozenset((OUTCOME_MAPPED, OUTCOME_LEGACY, OUTCOME_AMBIGUOUS, OUTCOME_ERROR))

# 归类说明文案（多处复用，避免同义字段各写一套措辞）
_NAME_NOTE = "V2 对象以稳定 ID 为身份、无 name 字段；原名原样保留于 legacy_metadata"
_CHAPTER_NOTE = "章节引用（chNN）无法自动确定对应 Beat，须作者指定披露/发生位置"
_POINTS_TO_NOTE = "V1 为自由文本指向；V2 需 Truth/事实的稳定 ID 引用"
_PARTICIPANTS_NOTE = "V1 存角色显示名，V2 需角色稳定 ID；须作者确认目标角色"


@dataclass(frozen=True, slots=True)
class FieldMapping:
    """单个 V1 源字段的归类结果。"""

    field: str
    outcome: str
    target: str = "-"
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ImportIssue:
    """阻断迁移的字段级错误（三要素：哪里错 / 为什么错 / 怎样算对）。"""

    code: str
    where: str
    why: str
    fix: str


@dataclass(frozen=True, slots=True)
class EntityMapping:
    """一个源实体的映射：字段归类、目标 Artifact 身份与可规划性。"""

    entity_type: str
    source_name: str
    target_kind: str
    target_id: str
    fields: tuple[FieldMapping, ...]
    issues: tuple[ImportIssue, ...] = ()
    planned: bool = True
    artifact_count: int = 1


@dataclass(frozen=True, slots=True)
class SourceNote:
    """未纳入映射的源（整文件/整目录），显式记录原因而非静默跳过。"""

    path: str
    reason: str


@dataclass(frozen=True, slots=True)
class ImportReport:
    """dry-run 报告：实体映射 + 预期数量 + 无法表达 / 歧义 / 错误汇总。"""

    workspace: str
    entities: tuple[EntityMapping, ...]
    expected_artifacts: int
    expected_relations: int
    unmapped_sources: tuple[SourceNote, ...] = ()

    @property
    def legacy_fields(self) -> tuple[tuple[str, str, str], ...]:
        """无法表达字段（entity_type, source_name, field），值已保留进 legacy_metadata。"""
        return self._fields_with(OUTCOME_LEGACY)

    @property
    def ambiguous_fields(self) -> tuple[tuple[str, str, str], ...]:
        """歧义字段（entity_type, source_name, field），须作者决策后才能落定。"""
        return self._fields_with(OUTCOME_AMBIGUOUS)

    @property
    def errors(self) -> tuple[ImportIssue, ...]:
        """阻断性错误汇总（每条带三要素）。"""
        return tuple(issue for entity in self.entities for issue in entity.issues)

    def _fields_with(self, outcome: str) -> tuple[tuple[str, str, str], ...]:
        return tuple(
            (entity.entity_type, entity.source_name, mapping.field)
            for entity in self.entities
            for mapping in entity.fields
            if mapping.outcome == outcome
        )


@dataclass(frozen=True, slots=True)
class ImportPlan:
    """dry-run 输出：报告 + 可提交提案（每个可规划实体一条 DomainChange）。"""

    report: ImportReport
    proposal: tuple[DomainChange, ...]


# --------------------------------------------------------------------------
# 内部助手
# --------------------------------------------------------------------------
def _fm(field: str, outcome: str, target: str = "-", detail: str = "") -> FieldMapping:
    return FieldMapping(field=field, outcome=outcome, target=target, detail=detail)


def _cover(model: BaseModel, table: Mapping[str, FieldMapping]) -> tuple[FieldMapping, ...]:
    """按 V1 schema 逐字段取归类；未覆盖字段记为 error（新增源字段必被看见）。"""
    result: list[FieldMapping] = []
    for name in type(model).model_fields:
        mapping = table.get(name)
        if mapping is None:
            mapping = FieldMapping(
                field=name,
                outcome=OUTCOME_ERROR,
                detail="映射表未覆盖该字段；须显式归类后才能迁移（不静默丢弃）",
            )
        result.append(mapping)
    return tuple(result)


def _target_id(kind: str, entity_type: str, key: str) -> str:
    """稳定 ID：同 (kind, 实体类型, 源主键) 在任意次导入中一致（T13.2）。"""
    return deterministic_story_id(kind, f"v1:{entity_type}:{key}")


def _single(
    kind: str, entity_type: str, key: str, content: dict[str, Any], summary: str
) -> tuple[str, DomainChange]:
    artifact_id = _target_id(kind, entity_type, key)
    change = DomainChange(
        artifact_changes=(create_change(artifact_id, kind, content),), summary=summary
    )
    return artifact_id, change


@dataclass(frozen=True, slots=True)
class _Draft:
    mapping: EntityMapping
    change: DomainChange | None


# --------------------------------------------------------------------------
# 逐账本映射
# --------------------------------------------------------------------------
def _map_character(m: V1Character) -> _Draft:
    fields = _cover(
        m,
        {
            "name": _fm("name", OUTCOME_MAPPED, "name"),
            "identity": _fm("identity", OUTCOME_MAPPED, "identity"),
            "motivation": _fm("motivation", OUTCOME_MAPPED, "goal", "动机归入目标 goal"),
            "secret": _fm("secret", OUTCOME_MAPPED, "secret"),
            "arc": _fm("arc", OUTCOME_MAPPED, "arc"),
            "relationships": _fm(
                "relationships", OUTCOME_AMBIGUOUS, "-", "V2 需 Relationship 对象与双方稳定 ID"
            ),
            "notes": _fm("notes", OUTCOME_LEGACY, "legacy_metadata"),
        },
    )
    content = Character(
        name=m.name,
        identity=m.identity,
        goal=m.motivation,
        secret=m.secret,
        arc=m.arc,
        legacy_metadata={"v1": {"relationships": list(m.relationships), "notes": m.notes}},
    ).to_content()
    artifact_id, change = _single(
        kinds.KIND_CHARACTER, "character", m.name, content, f"V1 导入：角色「{m.name}」"
    )
    return _Draft(
        EntityMapping(
            entity_type="character",
            source_name=m.name,
            target_kind=kinds.KIND_CHARACTER,
            target_id=artifact_id,
            fields=fields,
        ),
        change,
    )


def _map_clue(m: V1Clue) -> _Draft:
    kind = kinds.KIND_RED_HERRING if m.is_red_herring else kinds.KIND_CLUE
    content_target = "false_explanation" if m.is_red_herring else "statement"
    fields = _cover(
        m,
        {
            "name": _fm("name", OUTCOME_LEGACY, "legacy_metadata", _NAME_NOTE),
            "content": _fm("content", OUTCOME_MAPPED, content_target),
            "chapter": _fm("chapter", OUTCOME_AMBIGUOUS, "-", _CHAPTER_NOTE),
            "points_to": _fm("points_to", OUTCOME_AMBIGUOUS, "-", _POINTS_TO_NOTE),
            "is_red_herring": _fm(
                "is_red_herring", OUTCOME_MAPPED, "kind", "据其分派 story.clue / story.red_herring"
            ),
            "is_genuine": _fm(
                "is_genuine",
                OUTCOME_LEGACY,
                "legacy_metadata",
                "V2 无对应字段；genuine 与 necessity 语义不同，须作者另行判定必要性",
            ),
            "status": _fm("status", OUTCOME_LEGACY, "legacy_metadata"),
        },
    )
    legacy = {
        "v1": {
            "name": m.name,
            "chapter": m.chapter,
            "points_to": m.points_to,
            "is_genuine": m.is_genuine,
            "status": m.status,
        }
    }
    if m.is_red_herring:
        # 干扰项缺 basis（不变量 8），不可规划，故不构造内容；仅报告阻断原因
        issues = (
            ImportIssue(
                code=ec.STORY_RED_HERRING_WITHOUT_CAUSE,
                where=f"v1.clue[{m.name}].basis",
                why="V1 干扰项只记录错误解释，没有真实人物或世界原因（不变量 8）",
                fix="由作者补写 basis（真实依据）后重新导入；无依据的干扰项会让公平性规则失效",
            ),
        )
        return _Draft(
            EntityMapping(
                entity_type="clue",
                source_name=m.name,
                target_kind=kind,
                target_id=_target_id(kind, "clue", m.name),
                fields=fields,
                issues=issues,
                planned=False,
                artifact_count=0,
            ),
            None,
        )
    clue_content = Clue(statement=m.content, legacy_metadata=legacy).to_content()
    artifact_id, change = _single(
        kinds.KIND_CLUE, "clue", m.name, clue_content, f"V1 导入：线索「{m.name}」"
    )
    return _Draft(
        EntityMapping(
            entity_type="clue",
            source_name=m.name,
            target_kind=kinds.KIND_CLUE,
            target_id=artifact_id,
            fields=fields,
        ),
        change,
    )


def _map_foreshadow(m: V1Foreshadow) -> _Draft:
    fields = _cover(
        m,
        {
            "name": _fm("name", OUTCOME_LEGACY, "legacy_metadata", _NAME_NOTE),
            "content": _fm("content", OUTCOME_MAPPED, "setup"),
            "plant_chapter": _fm("plant_chapter", OUTCOME_AMBIGUOUS, "-", _CHAPTER_NOTE),
            "plant_mode": _fm("plant_mode", OUTCOME_LEGACY, "legacy_metadata"),
            "expected_resolve_chapter": _fm(
                "expected_resolve_chapter", OUTCOME_AMBIGUOUS, "expected_payoff", _CHAPTER_NOTE
            ),
            "status": _fm("status", OUTCOME_LEGACY, "legacy_metadata"),
        },
    )
    content = Foreshadow(
        setup=m.content,
        legacy_metadata={
            "v1": {
                "name": m.name,
                "plant_chapter": m.plant_chapter,
                "plant_mode": m.plant_mode,
                "expected_resolve_chapter": m.expected_resolve_chapter,
                "status": m.status,
            }
        },
    ).to_content()
    artifact_id, change = _single(
        kinds.KIND_FORESHADOW, "foreshadow", m.name, content, f"V1 导入：伏笔「{m.name}」"
    )
    return _Draft(
        EntityMapping(
            entity_type="foreshadow",
            source_name=m.name,
            target_kind=kinds.KIND_FORESHADOW,
            target_id=artifact_id,
            fields=fields,
        ),
        change,
    )


def _map_method(m: V1Method) -> _Draft:
    fields = _cover(
        m,
        {
            "name": _fm("name", OUTCOME_LEGACY, "legacy_metadata", _NAME_NOTE),
            "description": _fm("description", OUTCOME_MAPPED, "method"),
            "time_window": _fm(
                "time_window", OUTCOME_AMBIGUOUS, "-", "自然语言窗口；V2 需结构化时间引用"
            ),
            "requirements": _fm("requirements", OUTCOME_LEGACY, "legacy_metadata"),
            "alibi_claims": _fm("alibi_claims", OUTCOME_LEGACY, "legacy_metadata"),
            "history": _fm("history", OUTCOME_LEGACY, "legacy_metadata"),
            "status": _fm("status", OUTCOME_LEGACY, "legacy_metadata"),
        },
    )
    issues = (
        ImportIssue(
            code=ec.STORY_MISSING_REQUIRED_FIELD,
            where=f"v1.method[{m.name}].culprit_id",
            why="V2 MysteryTruth 必填 culprit_id（真凶稳定 ID），V1 手法账本无真凶字段",
            fix="由作者确认真凶（角色稳定 ID）后，用命令补全 MysteryTruth 再导入",
        ),
    )
    return _Draft(
        EntityMapping(
            entity_type="method",
            source_name=m.name,
            target_kind=kinds.KIND_MYSTERY_TRUTH,
            target_id=_target_id(kinds.KIND_MYSTERY_TRUTH, "method", m.name),
            fields=fields,
            issues=issues,
            planned=False,
            artifact_count=0,
        ),
        None,
    )


def _compose_time(m: V1Timeline) -> str:
    text = f"第{m.day}天"
    if m.t_start:
        window = m.t_start if not m.t_end else f"{m.t_start}-{m.t_end}"
        text = f"{text} {window}"
    return text


def _map_timeline(m: V1Timeline) -> _Draft:
    fields = _cover(
        m,
        {
            "name": _fm("name", OUTCOME_LEGACY, "legacy_metadata", _NAME_NOTE),
            "day": _fm("day", OUTCOME_MAPPED, "time", "与 t_start/t_end 组合为时间描述"),
            "t_start": _fm("t_start", OUTCOME_LEGACY, "legacy_metadata"),
            "t_end": _fm("t_end", OUTCOME_LEGACY, "legacy_metadata"),
            "chapter": _fm("chapter", OUTCOME_AMBIGUOUS, "-", _CHAPTER_NOTE),
            "participants": _fm(
                "participants", OUTCOME_AMBIGUOUS, "participants", _PARTICIPANTS_NOTE
            ),
            "note": _fm("note", OUTCOME_LEGACY, "legacy_metadata"),
        },
    )
    content = TimelineEvent(
        time=_compose_time(m),
        legacy_metadata={
            "v1": {
                "name": m.name,
                "t_start": m.t_start,
                "t_end": m.t_end,
                "chapter": m.chapter,
                "participants": list(m.participants),
                "note": m.note,
            }
        },
    ).to_content()
    artifact_id, change = _single(
        kinds.KIND_TIMELINE_EVENT, "timeline", m.name, content, f"V1 导入：时间线事件「{m.name}」"
    )
    return _Draft(
        EntityMapping(
            entity_type="timeline",
            source_name=m.name,
            target_kind=kinds.KIND_TIMELINE_EVENT,
            target_id=artifact_id,
            fields=fields,
        ),
        change,
    )


def _map_outline(m: V1Outline) -> _Draft:
    fields = _cover(
        m,
        {
            "name": _fm("name", OUTCOME_MAPPED, "title"),
            "chapter": _fm("chapter", OUTCOME_MAPPED, "order_key", "章号降为可重排顺序键"),
            "act": _fm("act", OUTCOME_LEGACY, "legacy_metadata"),
            "beats": _fm("beats", OUTCOME_MAPPED, "narrative.beat", "每条节拍生成一个 Beat 节点"),
            "plant_plans": _fm(
                "plant_plans", OUTCOME_AMBIGUOUS, "-", "伏笔名为自由文本；V2 需伏笔稳定 ID"
            ),
            "end_hook": _fm("end_hook", OUTCOME_LEGACY, "legacy_metadata"),
            "status": _fm("status", OUTCOME_LEGACY, "legacy_metadata"),
        },
    )
    chapter_id = _target_id(kinds.KIND_CHAPTER, "outline", m.name)
    node = NarrativeNode(
        node_id=chapter_id,
        kind=kinds.KIND_CHAPTER,
        title=m.name,
        order_key=f"{m.chapter:03d}",
        legacy_metadata={
            "v1": {
                "act": m.act,
                "plant_plans": list(m.plant_plans),
                "end_hook": m.end_hook,
                "status": m.status,
            }
        },
    )
    changes = [create_change(chapter_id, kinds.KIND_CHAPTER, node.to_content())]
    for index, beat_text in enumerate(m.beats):
        beat_id = deterministic_story_id(kinds.KIND_BEAT, f"v1:outline:{m.name}:beat:{index:03d}")
        beat = Beat(
            beat_id=beat_id, parent_id=chapter_id, title=beat_text, order_key=f"{index:03d}"
        )
        changes.append(create_change(beat_id, kinds.KIND_BEAT, beat.to_content()))
    change = DomainChange(
        artifact_changes=tuple(changes),
        summary=f"V1 导入：章纲「{m.name}」（含 {len(m.beats)} 个节拍）",
    )
    return _Draft(
        EntityMapping(
            entity_type="outline",
            source_name=m.name,
            target_kind=kinds.KIND_CHAPTER,
            target_id=chapter_id,
            fields=fields,
            artifact_count=len(changes),
        ),
        change,
    )


_UNMAPPED_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("story.yaml", "工作区生命周期（stage/stage_history）不是故事事实，归项目生命周期管理"),
    ("chapters", "正文与历史版本不在本阶段范围（Phase 13 不迁移/生成正文）"),
    ("sessions.json", "会话索引属 Agent 运行记录，不是故事事实"),
    ("sessions", "会话日志属 Agent 运行记录，不是故事事实"),
    ("events.jsonl", "事件流属运行审计，不是故事事实"),
)


def _unmapped_sources(workspace: StoryWorkspace) -> tuple[SourceNote, ...]:
    return tuple(
        SourceNote(path=rel, reason=reason)
        for rel, reason in _UNMAPPED_CANDIDATES
        if (workspace.story_dir / rel).exists()
    )


def map_v1_workspace(workspace: StoryWorkspace) -> ImportPlan:
    """把 V1 工作区映射为 V2 dry-run 报告与提案；不写 Repository、不改源文件。"""
    drafts: list[_Draft] = []
    drafts.extend(_map_character(c) for c in list_characters(workspace))
    drafts.extend(_map_clue(c) for c in load_ledger(workspace.clues_path, V1Clue))
    drafts.extend(_map_foreshadow(f) for f in load_ledger(workspace.foreshadows_path, V1Foreshadow))
    drafts.extend(_map_method(m) for m in load_ledger(workspace.method_path, V1Method))
    drafts.extend(_map_timeline(t) for t in load_ledger(workspace.timeline_path, V1Timeline))
    drafts.extend(_map_outline(o) for o in load_ledger(workspace.outline_path, V1Outline))

    entities = tuple(draft.mapping for draft in drafts)
    proposal = tuple(draft.change for draft in drafts if draft.change is not None)
    report = ImportReport(
        workspace=workspace.slug,
        entities=entities,
        expected_artifacts=sum(entity.artifact_count for entity in entities if entity.planned),
        expected_relations=0,
        unmapped_sources=_unmapped_sources(workspace),
    )
    return ImportPlan(report=report, proposal=proposal)
