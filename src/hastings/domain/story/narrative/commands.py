"""Narrative 领域命令（Phase 13 T13.3b；modules/story-domain.md §2.2/§3/§4）。

结构为 Series → Book → Arc → Chapter → Scene → Beat。节点顺序用可重排
`order_key`，不把「第几章」当身份：移动只改 parent/order_key，Artifact ID 不变
（不变量 10）。

命令是纯函数 `fn(catalog, new_id, ...) -> DomainChange`，写路径只经 DomainChange
返回，由 application handler 交给 Guard 与 Commit Service。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from hastings.domain.repository.requests import RelationChange
from hastings.domain.story import error_codes as ec
from hastings.domain.story import kinds
from hastings.domain.story.catalog import StoryCatalog, StoryEntry
from hastings.domain.story.changes import (
    CERTAINTY_INFERRED,
    DomainChange,
    ImpactSeed,
    RelationSpec,
)
from hastings.domain.story.errors import violation
from hastings.domain.story.narrative.models import (
    Beat,
    DisclosureSpec,
    NarrativeNode,
    StateChange,
)
from hastings.domain.story.refs import (
    NewId,
    check_revision,
    create_change,
    new_artifact_id,
    require_ref,
    revise_change,
)

__all__ = [
    "create_beat",
    "create_node",
    "delete_setup",
    "move_beat",
    "revise_beat_purpose",
]

# 结构链：每种节点允许的父级类型（Series 为根，无父级）
_NODE_PARENT_KIND: dict[str, str | None] = {
    kinds.KIND_SERIES: None,
    kinds.KIND_BOOK: kinds.KIND_SERIES,
    kinds.KIND_ARC: kinds.KIND_BOOK,
    kinds.KIND_CHAPTER: kinds.KIND_ARC,
    kinds.KIND_SCENE: kinds.KIND_CHAPTER,
}


def _check_order_key(
    catalog: StoryCatalog,
    kind: str,
    parent_id: str | None,
    order_key: str,
    *,
    cmd: str,
    exclude_id: str | None = None,
) -> None:
    """同一父级下 order_key 必须唯一；空 order_key 表示未排序，跳过检查。"""
    if not order_key:
        return
    for entry in catalog.by_kind(kind):
        if entry.artifact_id == exclude_id:
            continue
        if entry.content.get("parent_id") != parent_id:
            continue
        if entry.content.get("order_key") == order_key:
            raise violation(
                ec.STORY_INVALID_COMMAND,
                f"{cmd}.order_key",
                f"同级下已存在 order_key={order_key!r} 的对象 {entry.artifact_id}",
                "换一个 order_key（如上一位与下一位之间插值）；顺序是排序键，不是身份",
                order_key=order_key,
                parent_id=parent_id,
                conflicting=entry.artifact_id,
            )


def create_node(
    catalog: StoryCatalog,
    new_id: NewId,
    *,
    kind: str,
    title: str = "",
    parent_id: str | None = None,
    order_key: str = "",
) -> DomainChange:
    """创建结构节点；父级必须存在且类型符合结构链（Series 无父级）。"""
    if kind not in kinds.NARRATIVE_NODE_KINDS:
        raise violation(
            ec.STORY_INVALID_COMMAND,
            "CreateNode.kind",
            f"节点类型 {kind!r} 不是结构节点",
            f"改用以下之一：{sorted(kinds.NARRATIVE_NODE_KINDS)}",
            kind=kind,
            allowed=sorted(kinds.NARRATIVE_NODE_KINDS),
        )
    expected_parent = _NODE_PARENT_KIND[kind]
    if expected_parent is None:
        if parent_id is not None:
            raise violation(
                ec.STORY_INVALID_COMMAND,
                "CreateNode.parent_id",
                f"{kind!r} 是结构根，不应有父级（收到 {parent_id!r}）",
                "把 parent_id 置空；一个项目只有一个 Series 根",
                parent_id=parent_id,
            )
    else:
        if parent_id is None:
            raise violation(
                ec.STORY_MISSING_PRECONDITION,
                "CreateNode.parent_id",
                f"{kind!r} 必须有父级",
                f"提供类型为 {expected_parent!r} 的 parent_id",
                expected_parent=expected_parent,
            )
        require_ref(catalog, parent_id, (expected_parent,), cmd="CreateNode", field="parent_id")

    _check_order_key(catalog, kind, parent_id, order_key, cmd="CreateNode")
    artifact_id = new_artifact_id(catalog, new_id, kind, cmd="CreateNode")
    node = NarrativeNode(
        node_id=artifact_id, kind=kind, title=title, parent_id=parent_id, order_key=order_key
    )
    change = create_change(artifact_id, kind, node.to_content())
    return DomainChange(
        artifact_changes=(change,), summary=f"新建 {kind} {title!r}（{artifact_id}）"
    )


def create_beat(
    catalog: StoryCatalog,
    new_id: NewId,
    *,
    parent_id: str,
    title: str = "",
    purpose: str = "",
    pov_character_id: str = "",
    participants: Iterable[str] = (),
    preconditions: Iterable[str] = (),
    state_changes: Iterable[StateChange] = (),
    reader_disclosures: Iterable[DisclosureSpec] = (),
    setups: Iterable[str] = (),
    payoffs: Iterable[str] = (),
    depends_on: Iterable[str] = (),
    constraints: Iterable[str] = (),
    order_key: str = "",
) -> DomainChange:
    """创建节拍（状态折叠的最小单位）；引用与结构边在同一事务内建立。

    所有 Iterable 参数先物化为 tuple：它们既用于引用校验，又要写入 Beat 内容与
    关系边，若在校验循环里消费生成器，后续使用会拿到空序列（T13.10 评审修正）。
    """
    participant_ids = tuple(participants)
    precondition_ids = tuple(preconditions)
    state_change_specs = tuple(state_changes)
    setup_ids = tuple(setups)
    payoff_ids = tuple(payoffs)
    depend_ids = tuple(depends_on)
    constraint_ids = tuple(constraints)
    disclosures = tuple(reader_disclosures)

    require_ref(catalog, parent_id, kinds.NARRATIVE_NODE_KINDS, cmd="CreateBeat", field="parent_id")
    if pov_character_id:
        require_ref(
            catalog,
            pov_character_id,
            (kinds.KIND_CHARACTER,),
            cmd="CreateBeat",
            field="pov_character_id",
        )
    for participant_id in participant_ids:
        require_ref(
            catalog, participant_id, (kinds.KIND_CHARACTER,), cmd="CreateBeat", field="participants"
        )
    for change_spec in state_change_specs:
        if change_spec.character_id:
            require_ref(
                catalog,
                change_spec.character_id,
                (kinds.KIND_CHARACTER,),
                cmd="CreateBeat",
                field="state_changes.character_id",
            )
    for setup_id in setup_ids:
        require_ref(catalog, setup_id, (kinds.KIND_FORESHADOW,), cmd="CreateBeat", field="setups")
    for payoff_id in payoff_ids:
        require_ref(catalog, payoff_id, (kinds.KIND_FORESHADOW,), cmd="CreateBeat", field="payoffs")
    for depend_id in depend_ids:
        require_ref(catalog, depend_id, (kinds.KIND_BEAT,), cmd="CreateBeat", field="depends_on")
    for spec in disclosures:
        require_ref(
            catalog,
            spec.fact_id,
            (kinds.KIND_TRUTH_FACT,),
            cmd="CreateBeat",
            field="reader_disclosures",
        )
    for constraint_id in constraint_ids:
        require_ref(
            catalog,
            constraint_id,
            (kinds.KIND_WORLD_RULE,),
            cmd="CreateBeat",
            field="constraints",
        )

    _check_order_key(catalog, kinds.KIND_BEAT, parent_id, order_key, cmd="CreateBeat")

    artifact_id = new_artifact_id(catalog, new_id, kinds.KIND_BEAT, cmd="CreateBeat")
    beat = Beat(
        beat_id=artifact_id,
        parent_id=parent_id,
        title=title,
        purpose=purpose,
        pov_character_id=pov_character_id,
        participants=participant_ids,
        preconditions=precondition_ids,
        state_changes=state_change_specs,
        reader_disclosures=disclosures,
        setups=setup_ids,
        payoffs=payoff_ids,
        depends_on=depend_ids,
        constraints=constraint_ids,
        order_key=order_key,
    )

    relations: list[RelationChange] = []
    relations.extend(
        RelationSpec("add", artifact_id, kinds.REL_SETS_UP, setup_id, {"from": "beat"}).to_change()
        for setup_id in setup_ids
    )
    relations.extend(
        RelationSpec(
            "add", artifact_id, kinds.REL_PAYS_OFF, payoff_id, {"from": "beat"}
        ).to_change()
        for payoff_id in payoff_ids
    )
    relations.extend(
        RelationSpec(
            "add", artifact_id, kinds.REL_REQUIRES, depend_id, {"from": "depends_on"}
        ).to_change()
        for depend_id in depend_ids
    )
    relations.extend(
        RelationSpec(
            "add",
            artifact_id,
            kinds.REL_REVEALS,
            spec.fact_id,
            {"audience": spec.audience, "interpretation": spec.interpretation},
        ).to_change()
        for spec in disclosures
    )
    # 世界规则约束本 Beat（modules/story-domain.md §4）：主动方是规则，source=规则、
    # target=beat，与「motivates=角色驱动 Scene」同向。
    relations.extend(
        RelationSpec(
            "add", constraint_id, kinds.REL_CONSTRAINS, artifact_id, {"from": "beat"}
        ).to_change()
        for constraint_id in constraint_ids
    )
    # Beat 内联改变角色状态（与 record_knowledge_change 同向：source=beat、
    # target=状态持有者）；无 character_id 的自由状态无从定位持有者，不产边。
    for change_spec in state_change_specs:
        if change_spec.character_id is None:
            continue
        relations.append(
            RelationSpec(
                "add",
                artifact_id,
                kinds.REL_CHANGES_STATE,
                change_spec.character_id,
                {
                    "kind": change_spec.kind,
                    "value": change_spec.value,
                    "target_id": change_spec.target_id,
                },
            ).to_change()
        )
    change = create_change(artifact_id, kinds.KIND_BEAT, beat.to_content())
    return DomainChange(
        artifact_changes=(change,),
        relation_changes=tuple(relations),
        summary=f"新建节拍 {title or purpose!r}（{artifact_id}）",
    )


def move_beat(
    catalog: StoryCatalog,
    new_id: NewId,
    *,
    beat_id: str,
    parent_id: str | None = None,
    order_key: str | None = None,
    expected_revision: str | None = None,
) -> DomainChange:
    """移动节拍：只改 parent/order_key，Artifact ID 不变（不变量 10）。"""
    entry = require_ref(catalog, beat_id, (kinds.KIND_BEAT,), cmd="MoveBeat", field="beat_id")
    check_revision(entry, expected_revision, cmd="MoveBeat")
    current = Beat.from_content(entry.content)

    new_parent = current.parent_id if parent_id is None else parent_id
    if parent_id is not None:
        require_ref(
            catalog, parent_id, kinds.NARRATIVE_NODE_KINDS, cmd="MoveBeat", field="parent_id"
        )
    new_order = current.order_key if order_key is None else order_key
    if new_parent == current.parent_id and new_order == current.order_key:
        return DomainChange(summary=f"节拍 {beat_id} 位置未变化（合法空结果）")

    _check_order_key(
        catalog,
        kinds.KIND_BEAT,
        new_parent,
        new_order,
        cmd="MoveBeat",
        exclude_id=beat_id,
    )
    content = replace(current, parent_id=new_parent, order_key=new_order).to_content()
    seeds = (
        ImpactSeed(
            target_kind=kinds.KIND_BEAT,
            target_id=beat_id,
            reason="节拍位置变化，状态折叠顺序与前置条件需复核",
            certainty=CERTAINTY_INFERRED,
        ),
    )
    return DomainChange(
        artifact_changes=(revise_change(entry, content),),
        impact_seeds=seeds,
        summary=f"移动节拍 {beat_id} 到 {new_parent}（{new_order!r}）",
    )


def revise_beat_purpose(
    catalog: StoryCatalog,
    new_id: NewId,
    *,
    beat_id: str,
    purpose: str,
    expected_revision: str | None = None,
) -> DomainChange:
    """改变节拍作用；产生正文与下游结构的影响种子（语义分析留待 Phase 15）。"""
    entry = require_ref(
        catalog, beat_id, (kinds.KIND_BEAT,), cmd="ReviseBeatPurpose", field="beat_id"
    )
    check_revision(entry, expected_revision, cmd="ReviseBeatPurpose")
    current = Beat.from_content(entry.content)
    if purpose == current.purpose:
        return DomainChange(summary=f"节拍 {beat_id} 作用未变化（合法空结果）")

    content = replace(current, purpose=purpose).to_content()
    seeds = [
        ImpactSeed(
            target_kind=kinds.KIND_BEAT,
            target_id=beat_id,
            reason="节拍作用变化，正文与上下游结构需复核",
            certainty=CERTAINTY_INFERRED,
        )
    ]
    seeds.extend(
        ImpactSeed(
            target_kind=kinds.KIND_BEAT,
            target_id=downstream.artifact_id,
            reason=f"上游节拍 {beat_id} 作用变化，本节的依赖前提需复核",
            certainty=CERTAINTY_INFERRED,
        )
        for downstream in _beats_depending_on(catalog, beat_id)
    )
    return DomainChange(
        artifact_changes=(revise_change(entry, content),),
        impact_seeds=tuple(seeds),
        summary=f"修改节拍 {beat_id} 作用",
    )


def delete_setup(
    catalog: StoryCatalog,
    new_id: NewId,
    *,
    beat_id: str,
    setup_id: str,
) -> DomainChange:
    """撤销某节拍对某伏笔的建立；回收方进入复核清单（stale 由 T13.5 规则判定）。

    最后一个建立方被撤销后，引用该伏笔的 Payoff 即为断裂（STORY_ORPHAN_PAYOFF）。
    """
    entry = require_ref(catalog, beat_id, (kinds.KIND_BEAT,), cmd="DeleteSetup", field="beat_id")
    current = Beat.from_content(entry.content)
    if setup_id not in current.setups:
        return DomainChange(summary=f"节拍 {beat_id} 未建立伏笔 {setup_id}（合法空结果）")

    remaining = tuple(s for s in current.setups if s != setup_id)
    content = replace(current, setups=remaining).to_content()
    relation = RelationSpec(
        "remove", beat_id, kinds.REL_SETS_UP, setup_id, {"from": "beat"}
    ).to_change()
    seeds = [
        ImpactSeed(
            target_kind=kinds.KIND_BEAT,
            target_id=beat_id,
            reason=f"撤销对伏笔 {setup_id} 的建立，前瞻/回收关系需复核",
            certainty=CERTAINTY_INFERRED,
        )
    ]
    seeds.extend(
        ImpactSeed(
            target_kind=kinds.KIND_BEAT,
            target_id=payoff.artifact_id,
            reason=f"其回收的伏笔 {setup_id} 已失去建立方，Payoff 可能 stale",
            certainty=CERTAINTY_INFERRED,
        )
        for payoff in _beats_paying_off(catalog, setup_id)
    )
    return DomainChange(
        artifact_changes=(revise_change(entry, content),),
        relation_changes=(relation,),
        impact_seeds=tuple(seeds),
        summary=f"撤销节拍 {beat_id} 对伏笔 {setup_id} 的建立",
    )


def _beats_depending_on(catalog: StoryCatalog, beat_id: str) -> tuple[StoryEntry, ...]:
    result: list[StoryEntry] = []
    for entry in catalog.beats():
        if beat_id in Beat.from_content(entry.content).depends_on:
            result.append(entry)
    return tuple(result)


def _beats_paying_off(catalog: StoryCatalog, setup_id: str) -> tuple[StoryEntry, ...]:
    result: list[StoryEntry] = []
    for entry in catalog.beats():
        if setup_id in Beat.from_content(entry.content).payoffs:
            result.append(entry)
    return tuple(result)
