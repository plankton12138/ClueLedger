"""备选方案的整案与片段采用：先把要复制什么算清楚，再作为一次确定的变更提交。

两条路径的区别只在「谁来做判断」：

- ``exact``：这里能做，把选中范围与它的必需前提复制到目标方案；对象在目标里已存在且
  内容不同就算冲突，不静默覆盖。
- ``assist``：结构先按同一计划确定性提交，随后由接口受理一次可见操作，让模型按依赖
  补查与改写正文；结构没有变化时不启动模型。

采用只复制结构。正文关联（``bindings``）指向的是某个方案自己的正文副本，复制到目标方案
会让关联指向不存在的段落，所以这里不复制，并在结果里说明。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.story_models import (
    AddClue,
    AddConstraint,
    AddNode,
    AddThread,
    Connect,
    EventTimeRelation,
    SetEventTime,
    StoryOperation,
    StoryVariant,
    UnsetEventTime,
    UpdateClue,
    UpdateConstraint,
    UpdateNode,
)

__all__ = [
    "AdoptionConflict",
    "AdoptionPlan",
    "adoption_plan",
]

ObjectKind = Literal["node", "clue", "thread", "constraint", "edge", "event_time"]


@dataclass(frozen=True, slots=True)
class AdoptionConflict:
    """目标方案里已有同 ID 但内容不同的对象；采用不会静默覆盖它。"""

    kind: ObjectKind
    id: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "id": self.id, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class AdoptionPlan:
    source: str
    target: str
    node_ids: tuple[str, ...] = ()
    operations: tuple[StoryOperation, ...] = ()
    copied: dict[str, list[str]] = field(default_factory=dict)
    unchanged: list[str] = field(default_factory=list)
    #: 为了支撑所选节点额外带入的对象：作者只选了一个节点，线索与约定也要跟过来。
    pulled_in: list[str] = field(default_factory=list)
    #: 指向未选节点的连接：不复制，也不当成失败。
    skipped_edges: list[str] = field(default_factory=list)
    #: 跨边界连接：一端在所选范围、另一端是目标里已有的对象；已复制并在收据里单列。
    boundary_edges: list[str] = field(default_factory=list)
    #: 整案覆盖时被移除的目标实际时间关系。
    removed_event_times: list[str] = field(default_factory=list)
    #: 两端都不在本次范围、或端点不在目标里的实际时间关系。
    skipped_event_times: list[str] = field(default_factory=list)
    conflicts: tuple[AdoptionConflict, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.operations and not self.conflicts

    def summary(self) -> dict[str, object]:
        return {
            "source": self.source,
            "target": self.target,
            "node_ids": list(self.node_ids),
            "copied": {name: list(items) for name, items in self.copied.items()},
            "unchanged": list(self.unchanged),
            "pulled_in": list(self.pulled_in),
            "skipped_edges": list(self.skipped_edges),
            "boundary_edges": list(self.boundary_edges),
            "removed_event_times": list(self.removed_event_times),
            "skipped_event_times": list(self.skipped_event_times),
            "conflicts": [item.to_dict() for item in self.conflicts],
            "operations": len(self.operations),
        }


def _closure(
    source: StoryVariant, node_ids: tuple[str, ...]
) -> tuple[set[str], set[str], set[str], set[str]]:
    """选中节点 → 它用到的线索、所属分支、适用约定；空选择表示整个方案。"""
    if not node_ids:
        return (
            set(source.nodes),
            set(source.clues),
            set(source.threads),
            set(source.constraints),
        )
    missing = [node_id for node_id in node_ids if node_id not in source.nodes]
    if missing:
        raise WorkspaceError(
            code="STORY_ADOPTION_INVALID",
            where="story.adoption",
            why=f"源方案里没有这些节点：{'、'.join(sorted(missing))}",
            fix="刷新构思后重新选择要采用的内容",
            details={"node_ids": sorted(missing)},
        )
    nodes = set(node_ids)
    clues: set[str] = set()
    threads: set[str] = set()
    for node_id in sorted(nodes):
        node = source.nodes[node_id]
        clues.update(use.clue_id for use in node.clue_uses if use.clue_id in source.clues)
        threads.update(thread_id for thread_id in node.thread_ids if thread_id in source.threads)
    subjects = {
        use.speaker_id for node_id in nodes for use in source.nodes[node_id].clue_uses
    } | {
        change.subject_id
        for node_id in nodes
        for change in source.nodes[node_id].knowledge_changes
    }
    constraints = {
        constraint.id
        for constraint in source.constraints.values()
        if constraint.scope.kind == "book"
        or (constraint.scope.kind == "node" and constraint.scope.target_id in nodes)
        or (constraint.scope.kind == "character" and constraint.scope.target_id in subjects)
    }
    return nodes, clues, threads, constraints


def _diff(
    *,
    incoming: object,
    existing: object | None,
    add: StoryOperation,
    update: StoryOperation | None,
    replace: bool,
) -> tuple[StoryOperation | None, str | None]:
    """一个对象的处理决定：新增、跳过（已一致）或冲突。"""
    if existing is None:
        return add, None
    if existing == incoming:
        return None, "unchanged"
    if replace and update is not None:
        return update, "replaced"
    return None, "conflict"


def adoption_plan(
    source: StoryVariant,
    target: StoryVariant,
    *,
    node_ids: tuple[str, ...] = (),
    replace: bool = False,
) -> AdoptionPlan:
    """算出把 ``source`` 的选中范围复制到 ``target`` 需要的操作；不写任何文件。"""
    if source.id == target.id:
        raise WorkspaceError(
            code="STORY_ADOPTION_INVALID",
            where="story.adoption",
            why="源方案与目标方案是同一个",
            fix="选择另一个备选方案再采用",
            details={"variant_id": source.id},
        )
    nodes, clues, threads, constraints = _closure(source, node_ids)
    selected_nodes = set(nodes)
    fragment = bool(node_ids)
    pulled_in: list[str] = []
    if fragment:
        pulled_in = sorted(
            [f"clue:{item}" for item in clues]
            + [f"thread:{item}" for item in threads]
            + [f"constraint:{item}" for item in constraints]
        )
    operations: list[StoryOperation] = []
    conflicts: list[AdoptionConflict] = []
    copied: dict[str, list[str]] = {
        "nodes": [],
        "clues": [],
        "threads": [],
        "constraints": [],
        "edges": [],
        "event_times": [],
    }
    plural = {"node": "nodes", "clue": "clues", "thread": "threads", "constraint": "constraints"}
    unchanged: list[str] = []

    def collect(
        kind: ObjectKind,
        incoming: object,
        existing: object | None,
        add: StoryOperation,
        update: StoryOperation | None,
        label: str,
    ) -> None:
        operation, decision = _diff(
            incoming=incoming,
            existing=existing,
            add=add,
            update=update,
            replace=replace,
        )
        if decision == "conflict":
            conflicts.append(
                AdoptionConflict(
                    kind=kind,
                    id=label,
                    reason="目标方案里已有同 ID 但内容不同的对象",
                )
            )
            return
        if decision == "unchanged":
            unchanged.append(f"{kind}:{label}")
            return
        if operation is not None:
            operations.append(operation)
            if kind in plural:
                copied[plural[kind]].append(label)

    for clue_id in sorted(clues):
        clue = source.clues[clue_id]
        collect(
            "clue",
            clue,
            target.clues.get(clue_id),
            AddClue(clue=clue),
            UpdateClue(clue_id=clue_id, clue=clue),
            clue_id,
        )
    for thread_id in sorted(threads):
        thread = source.threads[thread_id]
        # 分支只有新增与删除，没有更新操作：内容不同按冲突处理。
        collect(
            "thread",
            thread,
            target.threads.get(thread_id),
            AddThread(thread=thread),
            None,
            thread_id,
        )
    for constraint_id in sorted(constraints):
        constraint = source.constraints[constraint_id]
        collect(
            "constraint",
            constraint,
            target.constraints.get(constraint_id),
            AddConstraint(constraint=constraint),
            UpdateConstraint(constraint_id=constraint_id, constraint=constraint),
            constraint_id,
        )
    for node_id in sorted(nodes):
        node = source.nodes[node_id]
        collect(
            "node",
            node,
            target.nodes.get(node_id),
            AddNode(node=node),
            UpdateNode(node_id=node_id, node=node),
            node_id,
        )

    # 连接属于「所选内容及其获准依赖」：两端都在范围内的照常复制；一端在范围内的
    # 是跨边界关系，复制并在收据里单列；两端都在范围外的既有关系保持原样。
    present = set(target.nodes) | nodes
    skipped_edges: list[str] = []
    boundary_edges: list[str] = []
    for edge in sorted(source.edges.values(), key=lambda item: item.id):
        if edge.from_id not in present or edge.to_id not in present:
            skipped_edges.append(edge.id)
            continue
        if fragment and edge.from_id not in selected_nodes and edge.to_id not in selected_nodes:
            # 两端都不在本次范围：与目标一致就是没变化；确实不同也不复制，列入未覆盖。
            if target.edges.get(edge.id) == edge:
                unchanged.append(f"edge:{edge.id}")
            else:
                skipped_edges.append(edge.id)
            continue
        operation, decision = _diff(
            incoming=edge,
            existing=target.edges.get(edge.id),
            add=Connect(edge=edge),
            update=Connect(edge=edge),
            replace=replace,
        )
        if decision == "conflict":
            conflicts.append(
                AdoptionConflict(
                    kind="edge",
                    id=edge.id,
                    reason="目标方案里已有同 ID 但两端不同的连接",
                )
            )
        elif decision == "unchanged":
            unchanged.append(f"edge:{edge.id}")
        elif operation is not None:
            operations.append(operation)
            copied["edges"].append(edge.id)
            if fragment and (
                edge.from_id not in selected_nodes or edge.to_id not in selected_nodes
            ):
                boundary_edges.append(edge.id)
                pulled_in.append(f"edge:{edge.id}")

    # 实际事件时间：与揭示顺序分别校验，只复制与所选内容相关的关系。
    target_pairs = {(row.before_node_id, row.after_node_id) for row in target.event_times}
    source_pairs = {(row.before_node_id, row.after_node_id) for row in source.event_times}
    unsets: list[StoryOperation] = []
    sets: list[StoryOperation] = []
    unset_pairs: set[tuple[str, str]] = set()
    removed_event_times: list[str] = []
    skipped_event_times: list[str] = []
    for relation in sorted(
        source.event_times, key=lambda row: (row.before_node_id, row.after_node_id)
    ):
        pair = (relation.before_node_id, relation.after_node_id)
        label = f"{pair[0]}->{pair[1]}"
        if pair in target_pairs:
            unchanged.append(f"event_time:{label}")
            continue
        if pair[0] not in present or pair[1] not in present:
            skipped_event_times.append(label)
            continue
        if fragment and pair[0] not in selected_nodes and pair[1] not in selected_nodes:
            skipped_event_times.append(label)
            continue
        reverse = (pair[1], pair[0])
        if reverse in target_pairs:
            if not replace:
                conflicts.append(
                    AdoptionConflict(
                        kind="event_time",
                        id=label,
                        reason="目标方案里已有相反的实际时间关系",
                    )
                )
                continue
            unsets.append(
                UnsetEventTime(before_node_id=reverse[0], after_node_id=reverse[1])
            )
            unset_pairs.add(reverse)
        sets.append(SetEventTime(relation=EventTimeRelation(
            before_node_id=pair[0], after_node_id=pair[1]
        )))
        copied["event_times"].append(label)
        if fragment and (pair[0] not in selected_nodes or pair[1] not in selected_nodes):
            pulled_in.append(f"event_time:{label}")

    # 整案覆盖时，目标里与来源不一致的实际时间关系按来源收口；片段采用不动范围外关系。
    if not fragment:
        for pair in sorted(target_pairs - source_pairs):
            if pair in unset_pairs:
                continue
            if pair[0] not in nodes or pair[1] not in nodes:
                continue
            if replace:
                unsets.append(
                    UnsetEventTime(before_node_id=pair[0], after_node_id=pair[1])
                )
                removed_event_times.append(f"{pair[0]}->{pair[1]}")
            else:
                # 不覆盖时保留目标的旧关系，但要在收据里说明它没有跟着来源走。
                skipped_event_times.append(f"{pair[0]}->{pair[1]}")

    operations.extend(unsets)
    operations.extend(sets)

    return AdoptionPlan(
        source=source.id,
        target=target.id,
        node_ids=tuple(sorted(nodes)),
        operations=tuple(operations),
        copied=copied,
        unchanged=unchanged,
        pulled_in=pulled_in,
        skipped_edges=skipped_edges,
        boundary_edges=boundary_edges,
        removed_event_times=removed_event_times,
        skipped_event_times=skipped_event_times,
        conflicts=tuple(conflicts),
    )


