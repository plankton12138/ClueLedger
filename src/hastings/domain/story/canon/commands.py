"""Canon 领域命令（Phase 13 T13.3a；modules/story-domain.md §3/§5）。

命令是纯函数 `fn(catalog, new_id, ...) -> DomainChange`：不调用
CommitRepository、不读数据库。application handler 负责取 Snapshot、构造
StoryCatalog、执行命令、把类型化违规转换为应用错误信封，再交给 Guard 与
Commit Service。

四透镜落地：
- 硬约束放工具层（此层）：类型不匹配、必填缺失、revision 过期、ID 冲突一律拒绝，
  并给出「哪里错 / 为什么错 / 怎样算对」三要素；
- 泄压阀：值未变化、作用未变化属合法空结果（返回空 DomainChange，不抛异常）；
- 错误消息是喂给模型的 API：字段级定位（cmd.field）原样透传。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from hastings.domain.repository.requests import RelationChange
from hastings.domain.story import error_codes as ec
from hastings.domain.story import kinds
from hastings.domain.story.canon.models import (
    CHARACTER_ROLES,
    Character,
    Clue,
    MysteryTruth,
    RedHerring,
)
from hastings.domain.story.catalog import StoryCatalog, StoryEntry
from hastings.domain.story.changes import (
    CERTAINTY_INFERRED,
    DomainChange,
    ImpactSeed,
    RelationSpec,
)
from hastings.domain.story.errors import violation
from hastings.domain.story.mystery.models import KNOWLEDGE_MODES, KnowledgeState
from hastings.domain.story.narrative.models import Beat
from hastings.domain.story.refs import (
    NewId,
    check_revision,
    create_change,
    new_artifact_id,
    require_ref,
    revise_change,
)
from hastings.domain.story.schema import require_str

__all__ = [
    "change_character_role",
    "create_character",
    "define_mystery_truth",
    "plan_clue",
    "plan_red_herring",
    "record_knowledge_change",
    "revise_character",
    "supersede_canon_fact",
]

# 驱动情节走向的角色：其目标构成 motivates 边；其余角色不驱动
_DRIVING_ROLES = frozenset(("protagonist", "antagonist", "accomplice"))

_FACT_KINDS = (kinds.KIND_TRUTH_FACT, kinds.KIND_MYSTERY_TRUTH)


# --------------------------------------------------------------------------
# 命令私有助手
# --------------------------------------------------------------------------
def _ensure_role(role: str, *, cmd: str) -> None:
    if role not in CHARACTER_ROLES:
        raise violation(
            ec.STORY_INVALID_COMMAND,
            f"{cmd}.role",
            f"角色作用 {role!r} 不是受支持的值",
            f"改用以下之一：{sorted(CHARACTER_ROLES)}",
            role=role,
            allowed=sorted(CHARACTER_ROLES),
        )


def _character_dependents(catalog: StoryCatalog, character_id: str) -> tuple[StoryEntry, ...]:
    """依赖该角色的节拍：以角色为视角，或把角色列为参与者。"""
    result: list[StoryEntry] = []
    for entry in catalog.beats():
        beat = Beat.from_content(entry.content)
        if beat.pov_character_id == character_id or character_id in beat.participants:
            result.append(entry)
    return tuple(result)


# --------------------------------------------------------------------------
# 命令
# --------------------------------------------------------------------------
def create_character(
    catalog: StoryCatalog,
    new_id: NewId,
    *,
    name: str,
    identity: str = "",
    goal: str = "",
    need: str = "",
    secret: str = "",
    arc: str = "",
    role: str = "support",
    traits: Iterable[str] = (),
) -> DomainChange:
    """新建角色。ID 由 new_id 生成且必须唯一；同名允许共存（按名查询须消歧）。"""
    _ensure_role(role, cmd="CreateCharacter")
    model = Character(
        name=require_str(name, field="name", where="canon.character"),
        identity=identity,
        goal=goal,
        need=need,
        secret=secret,
        arc=arc,
        role=role,
        traits=tuple(traits),
    )
    artifact_id = new_artifact_id(catalog, new_id, kinds.KIND_CHARACTER, cmd="CreateCharacter")
    change = create_change(artifact_id, kinds.KIND_CHARACTER, model.to_content())
    return DomainChange(
        artifact_changes=(change,), summary=f"新建角色 {model.name}（{artifact_id}）"
    )


def revise_character(
    catalog: StoryCatalog,
    new_id: NewId,
    *,
    character_id: str,
    expected_revision: str | None = None,
    name: str | None = None,
    identity: str | None = None,
    goal: str | None = None,
    need: str | None = None,
    secret: str | None = None,
    arc: str | None = None,
    role: str | None = None,
    traits: Iterable[str] | None = None,
) -> DomainChange:
    """部分修改角色：只覆盖显式提供的字段，未提供字段保持不变（不变量 2）。"""
    entry = require_ref(
        catalog, character_id, (kinds.KIND_CHARACTER,), cmd="ReviseCharacter", field="character_id"
    )
    check_revision(entry, expected_revision, cmd="ReviseCharacter")

    updates: dict[str, Any] = {}
    if name is not None:
        updates["name"] = require_str(name, field="name", where="canon.character")
    for field_name, value in (
        ("identity", identity),
        ("goal", goal),
        ("need", need),
        ("secret", secret),
        ("arc", arc),
    ):
        if value is not None:
            updates[field_name] = value
    if role is not None:
        _ensure_role(role, cmd="ReviseCharacter")
        updates["role"] = role
    if traits is not None:
        updates["traits"] = tuple(traits)

    current = Character.from_content(entry.content)
    merged = replace(current, **updates) if updates else current
    content = merged.to_content()
    if content == entry.content:
        return DomainChange(summary=f"角色 {character_id} 内容未变化（合法空结果）")
    return DomainChange(
        artifact_changes=(revise_change(entry, content),),
        summary=f"修订角色 {merged.name}（{character_id}）",
    )


def change_character_role(
    catalog: StoryCatalog,
    new_id: NewId,
    *,
    character_id: str,
    role: str,
    expected_revision: str | None = None,
) -> DomainChange:
    """改变角色在故事中的作用；同步维护 motivates 边并产生影响种子。"""
    entry = require_ref(
        catalog,
        character_id,
        (kinds.KIND_CHARACTER,),
        cmd="ChangeCharacterRole",
        field="character_id",
    )
    check_revision(entry, expected_revision, cmd="ChangeCharacterRole")
    _ensure_role(role, cmd="ChangeCharacterRole")

    current = Character.from_content(entry.content)
    if role == current.role:
        return DomainChange(summary=f"角色 {character_id} 作用未变化（合法空结果）")

    content = replace(current, role=role).to_content()
    dependents = _character_dependents(catalog, character_id)
    driving = role in _DRIVING_ROLES
    action = "add" if driving else "remove"
    relations = tuple(
        RelationSpec(
            action=action,
            source=character_id,
            relation_type=kinds.REL_MOTIVATES,
            target=dependent.artifact_id,
            metadata={"role": role},
        ).to_change()
        for dependent in dependents
    )
    seeds = tuple(
        ImpactSeed(
            target_kind=dependent.kind,
            target_id=dependent.artifact_id,
            reason=f"角色 {character_id} 作用改为 {role}，该节拍的动机与状态需复核",
            certainty=CERTAINTY_INFERRED,
        )
        for dependent in dependents
    )
    return DomainChange(
        artifact_changes=(revise_change(entry, content),),
        relation_changes=relations,
        impact_seeds=seeds,
        summary=f"角色 {character_id} 作用改为 {role}（影响 {len(dependents)} 个节拍）",
    )


def define_mystery_truth(
    catalog: StoryCatalog,
    new_id: NewId,
    *,
    culprit_id: str,
    motive: str = "",
    method: str = "",
    cover_up: str = "",
    accomplice_ids: Iterable[str] = (),
    true_timeline: Iterable[str] = (),
) -> DomainChange:
    """建立案件真相：凶手、协助者、时间线引用必须有效。"""
    require_ref(
        catalog, culprit_id, (kinds.KIND_CHARACTER,), cmd="DefineMysteryTruth", field="culprit_id"
    )
    accomplices = tuple(accomplice_ids)
    for accomplice_id in accomplices:
        require_ref(
            catalog,
            accomplice_id,
            (kinds.KIND_CHARACTER,),
            cmd="DefineMysteryTruth",
            field="accomplice_ids",
        )
    timeline = tuple(true_timeline)
    for event_id in timeline:
        require_ref(
            catalog,
            event_id,
            (kinds.KIND_TIMELINE_EVENT,),
            cmd="DefineMysteryTruth",
            field="true_timeline",
        )

    model = MysteryTruth(
        culprit_id=culprit_id,
        motive=motive,
        method=method,
        cover_up=cover_up,
        accomplice_ids=accomplices,
        true_timeline=timeline,
    )
    artifact_id = new_artifact_id(
        catalog, new_id, kinds.KIND_MYSTERY_TRUTH, cmd="DefineMysteryTruth"
    )
    change = create_change(artifact_id, kinds.KIND_MYSTERY_TRUTH, model.to_content())
    return DomainChange(artifact_changes=(change,), summary=f"建立案件真相（{artifact_id}）")


def plan_clue(
    catalog: StoryCatalog,
    new_id: NewId,
    *,
    statement: str,
    source: str = "",
    points_to: Iterable[str] = (),
    necessity: bool = False,
    disclosure_plan: Iterable[str] = (),
) -> DomainChange:
    """规划真实线索，并为每个指向对象产生 requires 边（modules §4）。"""
    statement = require_str(statement, field="statement", where="canon.clue")
    facts = tuple(points_to)
    for fact_id in facts:
        require_ref(catalog, fact_id, _FACT_KINDS, cmd="PlanClue", field="points_to")
    plan = tuple(disclosure_plan)
    for beat_id in plan:
        require_ref(catalog, beat_id, (kinds.KIND_BEAT,), cmd="PlanClue", field="disclosure_plan")

    model = Clue(
        statement=statement,
        source=source,
        points_to=facts,
        necessity=necessity,
        disclosure_plan=plan,
    )
    artifact_id = new_artifact_id(catalog, new_id, kinds.KIND_CLUE, cmd="PlanClue")
    change = create_change(artifact_id, kinds.KIND_CLUE, model.to_content())
    relations = tuple(
        RelationSpec(
            action="add",
            source=artifact_id,
            relation_type=kinds.REL_REQUIRES,
            target=fact_id,
            metadata={"from": "clue"},
        ).to_change()
        for fact_id in facts
    )
    return DomainChange(
        artifact_changes=(change,),
        relation_changes=relations,
        summary=f"规划线索 {statement!r}（{artifact_id}）",
    )


def plan_red_herring(
    catalog: StoryCatalog,
    new_id: NewId,
    *,
    false_explanation: str,
    basis: str,
    resolution: str = "",
) -> DomainChange:
    """规划误导项；必须有真实的人物或世界原因（不变量 8）。"""
    model = RedHerring(
        false_explanation=require_str(
            false_explanation, field="false_explanation", where="canon.red_herring"
        ),
        basis=require_str(basis, field="basis", where="canon.red_herring"),
        resolution=resolution,
    )
    artifact_id = new_artifact_id(catalog, new_id, kinds.KIND_RED_HERRING, cmd="PlanRedHerring")
    change = create_change(artifact_id, kinds.KIND_RED_HERRING, model.to_content())
    return DomainChange(
        artifact_changes=(change,),
        summary=f"规划误导 {model.false_explanation!r}（{artifact_id}）",
    )


def record_knowledge_change(
    catalog: StoryCatalog,
    new_id: NewId,
    *,
    character_id: str,
    fact_id: str,
    mode: str = "unaware",
    at_beat_id: str | None = None,
    note: str = "",
) -> DomainChange:
    """记录某角色在某节拍对某事实的认知；产生 beat→角色 的 changes_state 边。

    提前知情（knows 早于真相揭示）由确定性规则判定（T13.5），此处只忠实记录
    作者意图；misbelieves 是合法认知，不在此处拦截。
    """
    require_ref(
        catalog,
        character_id,
        (kinds.KIND_CHARACTER,),
        cmd="RecordKnowledgeChange",
        field="character_id",
    )
    require_ref(
        catalog, fact_id, (kinds.KIND_TRUTH_FACT,), cmd="RecordKnowledgeChange", field="fact_id"
    )
    if mode not in KNOWLEDGE_MODES:
        raise violation(
            ec.STORY_INVALID_COMMAND,
            "RecordKnowledgeChange.mode",
            f"认知模式 {mode!r} 不是受支持的值",
            f"改用以下之一：{sorted(KNOWLEDGE_MODES)}",
            mode=mode,
            allowed=sorted(KNOWLEDGE_MODES),
        )
    if at_beat_id is not None:
        require_ref(
            catalog, at_beat_id, (kinds.KIND_BEAT,), cmd="RecordKnowledgeChange", field="at_beat_id"
        )

    model = KnowledgeState(
        character_id=character_id, fact_id=fact_id, mode=mode, at_beat_id=at_beat_id, note=note
    )
    artifact_id = new_artifact_id(
        catalog, new_id, kinds.KIND_KNOWLEDGE, cmd="RecordKnowledgeChange"
    )
    change = create_change(artifact_id, kinds.KIND_KNOWLEDGE, model.to_content())
    relations: tuple[RelationChange, ...] = ()
    if at_beat_id is not None:
        relations = (
            RelationSpec(
                action="add",
                source=at_beat_id,
                relation_type=kinds.REL_CHANGES_STATE,
                target=character_id,
                metadata={"fact_id": fact_id, "mode": mode},
            ).to_change(),
        )
    return DomainChange(
        artifact_changes=(change,),
        relation_changes=relations,
        summary=f"记录认知：{character_id} 对 {fact_id} 转为 {mode}（{artifact_id}）",
    )


def supersede_canon_fact(
    catalog: StoryCatalog,
    new_id: NewId,
    *,
    old_id: str,
    content: dict[str, Any],
    reason: str = "",
) -> DomainChange:
    """用新事实替代旧事实：创建替代对象 + supersedes 边，旧对象保留（不变量 3）。

    查询视图据此把「被替代」的旧事实排除出「有效事实」，历史修订交由 Repository
    的 Revision 链保留，领域层不做物理删除。
    """
    old = catalog.require(old_id, where="SupersedeCanonFact.old_id")
    if old.kind not in kinds.CANON_KINDS:
        raise violation(
            ec.STORY_INVALID_COMMAND,
            "SupersedeCanonFact.old_id",
            f"{old_id!r} 的类型是 {old.kind!r}，不是可作为 Canon 事实替代的对象",
            "SupersedeCanonFact 只替代 Canon 事实；叙事结构与认知请用对应命令修订",
            artifact_id=old_id,
            kind=old.kind,
        )
    artifact_id = new_artifact_id(catalog, new_id, old.kind, cmd="SupersedeCanonFact")
    change = create_change(artifact_id, old.kind, dict(content))
    relation = RelationSpec(
        action="add",
        source=artifact_id,
        relation_type=kinds.REL_SUPERSEDES,
        target=old_id,
        metadata={"reason": reason},
    ).to_change()
    seeds = (
        ImpactSeed(
            target_kind=old.kind,
            target_id=old_id,
            reason="该事实已被替代，引用它的下游对象需复核",
            certainty=CERTAINTY_INFERRED,
        ),
    )
    return DomainChange(
        artifact_changes=(change,),
        relation_changes=(relation,),
        impact_seeds=seeds,
        summary=f"以 {artifact_id} 替代 {old_id}（旧事实保留）",
    )
