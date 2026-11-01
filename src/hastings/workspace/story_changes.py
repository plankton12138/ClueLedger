"""构思变更的应用、反向操作与对象摘要（1.0.1 P1）。

这里只处理结构，不碰正文文件，也不判断语义是否成立。三条规则值得单独记住：

* 一次 ``StoryChangeSet`` 是一个整体：先按顺序改一份数据副本，最后统一重建模型并做
  无环校验。中间状态可以暂时不一致，例如同一个变更里先删节点再删指向它的线索。
* 删除节点只连带关系（边与实际时间），作者写下的内容不静默丢弃：线索回收、知情、
  正文关联仍指向它时报错，要求在同一个变更里一并处理。
* 撤销不是整本回滚。提交时记下反向操作与「改动过的对象 → 提交后摘要」，撤销前重查
  这些对象；只要其中一个后来被改过就冲突，其余无关编辑原样保留。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, cast

from pydantic import BaseModel, ValidationError

from hastings.workspace.story_models import (
    AcknowledgeAttention,
    AddConstraint,
    AddNode,
    AddStage,
    AddThread,
    AttentionReceipt,
    BindProse,
    ClearAttentionReceipt,
    CluePlanInput,
    ConfirmAlignment,
    Connect,
    CreateClueIdentity,
    Disconnect,
    EventTimeRelation,
    RecordAssessment,
    RemoveAssessment,
    RemoveClueIdentity,
    RemoveCluePlan,
    RemoveClueRelation,
    RemoveConstraint,
    RemoveNode,
    RemoveStage,
    RemoveThread,
    RenameClueIdentity,
    SetEventTime,
    SetTimeOrigin,
    StoryChangeSet,
    StoryCluePlan,
    StoryOperation,
    StoryVariant,
    UnbindProse,
    UnsetEventTime,
    UpdateConstraint,
    UpdateNode,
    UpdateStage,
    UpdateThread,
    UpsertCluePlan,
    UpsertClueRelation,
    validate_story_variant,
)

__all__ = [
    "apply_change_set",
    "diff_variant",
    "object_digest",
    "touched_objects",
    "variant_digests",
]


class StoryInvalid(ValueError):
    """结构本身能判断的失败：标识冲突、目标不存在、悬空引用、循环。

    调用层把它映射成 ``STORY_GRAPH_INVALID``，不把内部异常文本直接当接口消息。
    """


def apply_change_set(variant: StoryVariant, change: StoryChangeSet) -> StoryVariant:
    """把变更应用到方案副本上，返回新方案；``variant`` 本身不被修改。"""
    data = variant.model_dump(mode="json")
    for operation in change.operations:
        _apply(data, operation)
    try:
        result = StoryVariant.model_validate(data)
        validate_story_variant(result)
    except ValueError as exc:
        raise StoryInvalid(_first_line(exc)) from exc
    return result


def _apply(data: dict[str, Any], operation: StoryOperation) -> None:
    stages = cast(dict[str, Any], data["stages"])
    nodes = cast(dict[str, Any], data["nodes"])
    clue_plans = cast(dict[str, Any], data["clue_plans"])
    threads = cast(dict[str, Any], data["threads"])
    constraints = cast(dict[str, Any], data["constraints"])
    edges = cast(dict[str, Any], data["edges"])
    bindings = cast(dict[str, Any], data["bindings"])
    clue_relations = cast(dict[str, Any], data["clue_relations"])
    attention_receipts = cast(dict[str, Any], data["attention_receipts"])
    assessments = cast(dict[str, Any], data["assessments"])
    event_times = cast(list[dict[str, Any]], data["event_times"])

    if isinstance(operation, AddStage):
        _require_new(stages, operation.stage.id, "阶段")
        stages[operation.stage.id] = operation.stage.model_dump(mode="json")
    elif isinstance(operation, UpdateStage):
        _require_existing(stages, operation.stage_id, "阶段")
        stages[operation.stage_id] = operation.stage.model_dump(mode="json")
    elif isinstance(operation, RemoveStage):
        _require_existing(stages, operation.stage_id, "阶段")
        affected = [
            node["id"] for node in nodes.values() if node.get("stage_id") == operation.stage_id
        ]
        if affected:
            raise StoryInvalid(
                "删除阶段前，先在同一变更中调整或解除这些节点的阶段：" + "、".join(affected)
            )
        del stages[operation.stage_id]
    elif isinstance(operation, AddNode):
        _require_new(nodes, operation.node.id, "节点")
        nodes[operation.node.id] = operation.node.model_dump(mode="json")
    elif isinstance(operation, UpdateNode):
        _require_existing(nodes, operation.node_id, "节点")
        nodes[operation.node_id] = operation.node.model_dump(mode="json")
    elif isinstance(operation, RemoveNode):
        _require_existing(nodes, operation.node_id, "节点")
        del nodes[operation.node_id]
        for key in [key for key, edge in edges.items() if _touches(edge, operation.node_id)]:
            del edges[key]
        event_times[:] = [
            row
            for row in event_times
            if operation.node_id not in (row["before_node_id"], row["after_node_id"])
        ]
        # 暂缓记录属于提醒决定，不是作者内容，随节点一起消失。
        attention_receipts.pop(operation.node_id, None)
    elif isinstance(operation, (CreateClueIdentity, RenameClueIdentity, RemoveClueIdentity)):
        # 身份由账本保存；故事侧只负责引用解析和最终一致性检查。
        pass
    elif isinstance(operation, UpsertCluePlan):
        plan = StoryCluePlan(clue_id=operation.clue_id, **operation.plan.model_dump(mode="json"))
        clue_plans[operation.clue_id] = plan.model_dump(mode="json")
    elif isinstance(operation, RemoveCluePlan):
        _require_existing(clue_plans, operation.clue_id, "线索计划")
        del clue_plans[operation.clue_id]
    elif isinstance(operation, AddThread):
        _require_new(threads, operation.thread.id, "分支")
        threads[operation.thread.id] = operation.thread.model_dump(mode="json")
    elif isinstance(operation, UpdateThread):
        _require_existing(threads, operation.thread_id, "分支")
        threads[operation.thread_id] = operation.thread.model_dump(mode="json")
    elif isinstance(operation, RemoveThread):
        _require_existing(threads, operation.thread_id, "分支")
        del threads[operation.thread_id]
        for node in nodes.values():
            members = cast(list[str], node["thread_ids"])
            members[:] = [value for value in members if value != operation.thread_id]
    elif isinstance(operation, Connect):
        _require_new(edges, operation.edge.id, "关系")
        edges[operation.edge.id] = operation.edge.model_dump(mode="json")
    elif isinstance(operation, Disconnect):
        _require_existing(edges, operation.edge_id, "关系")
        del edges[operation.edge_id]
    elif isinstance(operation, AddConstraint):
        _require_new(constraints, operation.constraint.id, "约定")
        constraints[operation.constraint.id] = operation.constraint.model_dump(mode="json")
    elif isinstance(operation, UpdateConstraint):
        _require_existing(constraints, operation.constraint_id, "约定")
        constraints[operation.constraint_id] = operation.constraint.model_dump(mode="json")
    elif isinstance(operation, RemoveConstraint):
        _require_existing(constraints, operation.constraint_id, "约定")
        del constraints[operation.constraint_id]
    elif isinstance(operation, SetEventTime):
        row = {
            "before_node_id": operation.relation.before_node_id,
            "after_node_id": operation.relation.after_node_id,
        }
        if row not in event_times:
            event_times.append(row)
    elif isinstance(operation, UnsetEventTime):
        target = {
            "before_node_id": operation.before_node_id,
            "after_node_id": operation.after_node_id,
        }
        if target not in event_times:
            raise StoryInvalid("要删除的实际时间关系不存在")
        event_times.remove(target)
    elif isinstance(operation, BindProse):
        _require_new(bindings, operation.binding.id, "正文关联")
        bindings[operation.binding.id] = operation.binding.model_dump(mode="json")
    elif isinstance(operation, UnbindProse):
        _require_existing(bindings, operation.binding_id, "正文关联")
        del bindings[operation.binding_id]
    elif isinstance(operation, RecordAssessment):
        _require_new(assessments, operation.assessment.id, "核对记录")
        assessments[operation.assessment.id] = operation.assessment.model_dump(mode="json")
    elif isinstance(operation, RemoveAssessment):
        _require_existing(assessments, operation.assessment_id, "核对记录")
        del assessments[operation.assessment_id]
    elif isinstance(operation, UpsertClueRelation):
        new_relation = operation.relation
        wanted = new_relation.symmetric_key()
        for other_id, other in clue_relations.items():
            if other_id != new_relation.id and _relation_key(other) == wanted:
                raise StoryInvalid(f"线索关系已存在：{other_id}")
        stored = new_relation
        if new_relation.kind != "supports":
            # 无方向关系按端点排序保存，两种提交顺序得到同一条记录。
            first, second = sorted((new_relation.from_clue_id, new_relation.to_clue_id))
            stored = new_relation.model_copy(update={"from_clue_id": first, "to_clue_id": second})
        clue_relations[new_relation.id] = stored.model_dump(mode="json")
    elif isinstance(operation, RemoveClueRelation):
        _require_existing(clue_relations, operation.relation_id, "线索关系")
        del clue_relations[operation.relation_id]
    elif isinstance(operation, AcknowledgeAttention):
        _require_existing(nodes, operation.node_id, "节点")
        previous = attention_receipts.get(operation.node_id)
        receipt = AttentionReceipt(
            node_id=operation.node_id,
            fingerprint=operation.fingerprint,
            noted_at=str(previous["noted_at"]) if previous else "",
        )
        attention_receipts[operation.node_id] = receipt.model_dump(mode="json")
    elif isinstance(operation, ClearAttentionReceipt):
        attention_receipts.pop(operation.node_id, None)
    elif isinstance(operation, SetTimeOrigin):
        data["time_origin_label"] = operation.label
    elif isinstance(operation, ConfirmAlignment):
        for alignment in operation.alignments:
            binding = bindings.get(alignment.binding_id)
            if binding is None:
                raise StoryInvalid(f"正文关联不存在：{alignment.binding_id}")
            if binding.get("node_id") != operation.node_id:
                raise StoryInvalid(f"正文关联不属于该节点：{alignment.binding_id}")
            binding["contract_digest"] = alignment.contract_digest
            binding["span_digest"] = alignment.span_digest
    else:  # pragma: no cover - 联合类型穷尽；新增操作忘记分支时这里会报错
        raise StoryInvalid(f"未知的结构操作：{type(operation).__name__}")


def diff_variant(source: StoryVariant, target: StoryVariant) -> list[StoryOperation]:
    """生成把 ``source`` 变成 ``target`` 的操作序列；撤销与影响分析共用。

    顺序按依赖排，不是按对象类型排：先断开关系与正文关联，再补新对象，最后删节点。
    这样每一步的存在性检查都成立——删节点会连带删掉它的边，反过来的顺序会让后面
    的 ``disconnect`` 找不到目标。

    只比较内容，不比较修订号：修订号由提交生成，不属于对象内容。
    """
    added_stages, updated_stages, removed_stages = _partition(source.stages, target.stages)
    added_nodes, updated_nodes, removed_nodes = _partition(source.nodes, target.nodes)
    added_plans, updated_plans, removed_plans = _partition(source.clue_plans, target.clue_plans)
    added_threads, updated_threads, removed_threads = _partition(source.threads, target.threads)
    added_rules, updated_rules, removed_rules = _partition(source.constraints, target.constraints)
    added_edges, updated_edges, removed_edges = _partition(source.edges, target.edges)
    added_binds, updated_binds, removed_binds = _partition(source.bindings, target.bindings)
    added_relations, updated_relations, removed_relations = _partition(
        source.clue_relations, target.clue_relations
    )
    added_receipts, updated_receipts, removed_receipts = _partition(
        source.attention_receipts, target.attention_receipts
    )
    added_checks, updated_checks, removed_checks = _partition(
        source.assessments, target.assessments
    )
    source_times = {(row.before_node_id, row.after_node_id) for row in source.event_times}
    target_times = {(row.before_node_id, row.after_node_id) for row in target.event_times}

    operations: list[StoryOperation] = []
    operations.extend(Disconnect(edge_id=key) for key in removed_edges)
    operations.extend(
        UnsetEventTime(before_node_id=pair[0], after_node_id=pair[1])
        for pair in sorted(source_times - target_times)
    )
    operations.extend(UnbindProse(binding_id=key) for key in removed_binds)
    operations.extend(RemoveClueRelation(relation_id=key) for key in removed_relations)
    operations.extend(RemoveAssessment(assessment_id=key) for key in removed_checks)
    operations.extend(AddStage(stage=target.stages[key]) for key in added_stages)
    operations.extend(AddThread(thread=target.threads[key]) for key in added_threads)
    operations.extend(AddConstraint(constraint=target.constraints[key]) for key in added_rules)
    operations.extend(
        UpsertCluePlan(
            clue_id=key,
            plan=CluePlanInput(
                kind=target.clue_plans[key].kind,
                author_truth=target.clue_plans[key].author_truth,
                intention=target.clue_plans[key].intention,
                resolution_intent=target.clue_plans[key].resolution_intent,
            ),
        )
        for key in added_plans
    )
    operations.extend(AddNode(node=target.nodes[key]) for key in added_nodes)
    operations.extend(UpdateNode(node_id=key, node=target.nodes[key]) for key in updated_nodes)
    operations.extend(
        UpsertCluePlan(
            clue_id=key,
            plan=CluePlanInput(
                kind=target.clue_plans[key].kind,
                author_truth=target.clue_plans[key].author_truth,
                intention=target.clue_plans[key].intention,
                resolution_intent=target.clue_plans[key].resolution_intent,
            ),
        )
        for key in updated_plans
    )
    operations.extend(
        UpsertClueRelation(relation=target.clue_relations[key]) for key in added_relations
    )
    operations.extend(
        UpsertClueRelation(relation=target.clue_relations[key]) for key in updated_relations
    )
    operations.extend(
        AcknowledgeAttention(
            node_id=key,
            fingerprint=target.attention_receipts[key].fingerprint,
        )
        for key in added_receipts
    )
    operations.extend(
        AcknowledgeAttention(
            node_id=key,
            fingerprint=target.attention_receipts[key].fingerprint,
        )
        for key in updated_receipts
    )
    operations.extend(UpdateStage(stage_id=key, stage=target.stages[key]) for key in updated_stages)
    operations.extend(
        UpdateThread(thread_id=key, thread=target.threads[key]) for key in updated_threads
    )
    operations.extend(
        UpdateConstraint(constraint_id=key, constraint=target.constraints[key])
        for key in updated_rules
    )
    for key in updated_binds:
        operations.append(UnbindProse(binding_id=key))
        operations.append(BindProse(binding=target.bindings[key]))
    operations.extend(BindProse(binding=target.bindings[key]) for key in added_binds)
    for key in updated_checks:
        operations.append(RemoveAssessment(assessment_id=key))
        operations.append(RecordAssessment(assessment=target.assessments[key]))
    operations.extend(RecordAssessment(assessment=target.assessments[key]) for key in added_checks)
    for key in updated_edges:
        operations.append(Disconnect(edge_id=key))
        operations.append(Connect(edge=target.edges[key]))
    operations.extend(Connect(edge=target.edges[key]) for key in added_edges)
    operations.extend(
        SetEventTime(relation=EventTimeRelation(before_node_id=pair[0], after_node_id=pair[1]))
        for pair in sorted(target_times - source_times)
    )
    operations.extend(ClearAttentionReceipt(node_id=key) for key in removed_receipts)
    if source.time_origin_label != target.time_origin_label:
        operations.append(SetTimeOrigin(label=target.time_origin_label))
    operations.extend(RemoveNode(node_id=key) for key in removed_nodes)
    operations.extend(RemoveCluePlan(clue_id=key) for key in removed_plans)
    operations.extend(RemoveThread(thread_id=key) for key in removed_threads)
    operations.extend(RemoveStage(stage_id=key) for key in removed_stages)
    operations.extend(RemoveConstraint(constraint_id=key) for key in removed_rules)
    return operations


def _partition[T: BaseModel](
    source: dict[str, T], target: dict[str, T]
) -> tuple[list[str], list[str], list[str]]:
    """按标识把两个版本拆成「新增 / 内容变化 / 删除」三份键。"""
    added = [key for key in target if key not in source]
    updated = [
        key
        for key in target
        if key in source
        and source[key].model_dump(mode="json") != target[key].model_dump(mode="json")
    ]
    removed = [key for key in source if key not in target]
    return added, updated, removed


def object_digest(value: object) -> str:
    """对象内容摘要；用于判断「提交之后有没有人再改过它」。"""
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def variant_digests(variant: StoryVariant) -> dict[str, str]:
    """按对象拆开的内容摘要表；撤销前与当前状态逐项比对。"""
    digests: dict[str, str] = {}
    for key, stage in variant.stages.items():
        digests[f"stage:{key}"] = object_digest(stage.model_dump(mode="json"))
    for key, node in variant.nodes.items():
        digests[f"node:{key}"] = object_digest(node.model_dump(mode="json"))
    for key, plan in variant.clue_plans.items():
        digests[f"clue_plan:{key}"] = object_digest(plan.model_dump(mode="json"))
    for key, thread in variant.threads.items():
        digests[f"thread:{key}"] = object_digest(thread.model_dump(mode="json"))
    for key, constraint in variant.constraints.items():
        digests[f"constraint:{key}"] = object_digest(constraint.model_dump(mode="json"))
    for key, edge in variant.edges.items():
        digests[f"edge:{key}"] = object_digest(edge.model_dump(mode="json"))
    for key, binding in variant.bindings.items():
        digests[f"binding:{key}"] = object_digest(binding.model_dump(mode="json"))
    for key, clue_relation in variant.clue_relations.items():
        digests[f"clue_relation:{key}"] = object_digest(clue_relation.model_dump(mode="json"))
    for key, receipt in variant.attention_receipts.items():
        digests[f"attention:{key}"] = object_digest(receipt.model_dump(mode="json"))
    for key, assessment in variant.assessments.items():
        digests[f"assessment:{key}"] = object_digest(assessment.model_dump(mode="json"))
    for row in variant.event_times:
        digests[f"time:{row.before_node_id}>{row.after_node_id}"] = object_digest(
            [row.before_node_id, row.after_node_id]
        )
    return digests


def touched_objects(before: StoryVariant, after: StoryVariant) -> dict[str, str]:
    """本次改动涉及的对象及提交后的摘要；撤销只保护这些对象。"""
    prior = variant_digests(before)
    current = variant_digests(after)
    changed = {key: digest for key, digest in current.items() if prior.get(key) != digest}
    for key in prior:
        if key not in current:
            # 被删除的对象也要保护：撤销时若它已经被重新建出来，说明后来又有编辑。
            changed[key] = ""
    return changed


def _require_new(items: dict[str, Any], key: str, label: str) -> None:
    if key in items:
        raise StoryInvalid(f"{label}已经存在：{key}")


def _require_existing(items: dict[str, Any], key: str, label: str) -> None:
    if key not in items:
        raise StoryInvalid(f"{label}不存在：{key}")


def _touches(edge: dict[str, Any], node_id: str) -> bool:
    return node_id in (edge.get("from_id"), edge.get("to_id"))


def _relation_key(row: dict[str, Any]) -> tuple[str, str, str]:
    """已存关系的去重键；与 ``ClueRelation.symmetric_key`` 同一规则。"""
    first, second = str(row["from_clue_id"]), str(row["to_clue_id"])
    if row["kind"] != "supports":
        first, second = sorted((first, second))
    return str(row["kind"]), first, second


def _first_line(exc: ValueError) -> str:
    """把 pydantic 的多行校验输出压成一句可展示的原因。"""
    if isinstance(exc, ValidationError):
        errors = exc.errors()
        if errors:
            return str(errors[0].get("msg", "")).removeprefix("Value error, ").strip()
    text = str(exc).strip()
    return text.splitlines()[0] if text else "结构校验失败"
