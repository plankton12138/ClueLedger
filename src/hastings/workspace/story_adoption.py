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
    StoryVariant,
    UpdateClue,
    UpdateConstraint,
    UpdateNode,
)

__all__ = [
    "AdoptionConflict",
    "AdoptionPlan",
    "adoption_plan",
]

ObjectKind = Literal["node", "clue", "thread", "constraint", "edge"]


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
    operations: tuple[object, ...] = ()
    copied: dict[str, list[str]] = field(default_factory=dict)
    unchanged: list[str] = field(default_factory=list)
    #: 为了支撑所选节点额外带入的对象：作者只选了一个节点，线索与约定也要跟过来。
    pulled_in: list[str] = field(default_factory=list)
    #: 指向未选节点的连接：不复制，也不当成失败。
    skipped_edges: list[str] = field(default_factory=list)
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
    add: object,
    update: object | None,
    replace: bool,
) -> tuple[object | None, str | None]:
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
    pulled_in: list[str] = []
    if node_ids:
        pulled_in = sorted(
            [f"clue:{item}" for item in clues]
            + [f"thread:{item}" for item in threads]
            + [f"constraint:{item}" for item in constraints]
        )
    operations: list[object] = []
    conflicts: list[AdoptionConflict] = []
    copied: dict[str, list[str]] = {"nodes": [], "clues": [], "threads": [], "constraints": []}
    plural = {"node": "nodes", "clue": "clues", "thread": "threads", "constraint": "constraints"}
    unchanged: list[str] = []

    def collect(
        kind: ObjectKind,
        incoming: object,
        existing: object | None,
        add: object,
        update: object | None,
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

    # 连接只在两端都已存在于目标（含本次新增）时才可能复制。
    present = set(target.nodes) | nodes
    skipped_edges: list[str] = []
    for edge in sorted(source.edges.values(), key=lambda item: item.id):
        if edge.from_id not in present or edge.to_id not in present:
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

    return AdoptionPlan(
        source=source.id,
        target=target.id,
        node_ids=tuple(sorted(nodes)),
        operations=tuple(operations),
        copied=copied,
        unchanged=unchanged,
        pulled_in=pulled_in,
        skipped_edges=skipped_edges,
        conflicts=tuple(conflicts),
    )


