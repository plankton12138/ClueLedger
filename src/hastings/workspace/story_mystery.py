"""悬疑视图的只读投影：实际时间、线索生命周期与知情。

视图只整理已有事实：``event_times`` 决定横轴，``clue_uses`` 与 ``clue_plans``
聚合线索，``knowledge_changes`` 提供侦探/助手等主体的认知。它不推断真相，
不把角色说法写成作者真相，也不产生新的写入路径。
"""

from __future__ import annotations

import heapq
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from hastings.workspace.story_clue_catalog import StoryClueCatalog
from hastings.workspace.story_models import ClueUse, StoryNode, StoryVariant
from hastings.workspace.story_structure import structure_diagnostics

__all__ = ["build_mystery_view"]

_ROLE_ORDER = ("plant", "develop", "misdirect", "reinterpret", "resolve")


def _order_key(
    variant: StoryVariant,
    stage_order: Mapping[str, int],
    node: StoryNode,
) -> tuple[int, bool, int, str]:
    return (
        stage_order.get(node.stage_id or "", len(stage_order) + 1),
        node.reveal_order is None,
        node.reveal_order or 0,
        node.id,
    )


def _actual_order(
    variant: StoryVariant,
) -> tuple[
    list[str],
    dict[str, list[str]],
    dict[str, list[str]],
    set[str],
]:
    """按实际时间排序；未登记节点按阶段与叙述顺序追加。

    只对有实际时间关系的节点做拓扑排序；没有关系的节点不参与，
    以免把 ``reveal_order`` 伪装成实际时间。
    """
    node_ids = set(variant.nodes)
    stage_order = {stage.id: stage.order for stage in variant.stages.values()}
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for relation in variant.event_times:
        before = relation.before_node_id
        after = relation.after_node_id
        if before not in node_ids or after not in node_ids or before == after:
            continue
        key = (before, after)
        if key in seen:
            continue
        seen.add(key)
        pairs.append(key)

    adjacency: dict[str, list[str]] = defaultdict(list)
    indegree: dict[str, int] = {}
    registered: set[str] = set()
    for before, after in pairs:
        adjacency[before].append(after)
        indegree[after] = indegree.get(after, 0) + 1
        indegree.setdefault(before, 0)
        registered.update((before, after))

    queue: list[tuple[int, bool, int, str]] = [
        _order_key(variant, stage_order, variant.nodes[node_id])
        for node_id in registered
        if indegree[node_id] == 0
    ]
    heapq.heapify(queue)
    ordered: list[str] = []
    while queue:
        _, _, _, node_id = heapq.heappop(queue)
        ordered.append(node_id)
        for target in adjacency[node_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                heapq.heappush(
                    queue,
                    _order_key(variant, stage_order, variant.nodes[target]),
                )

    if len(ordered) < len(registered):
        # 循环关系不应让视图失败：把剩余节点按叙述顺序接在后面。
        ordered.extend(
            sorted(
                registered - set(ordered),
                key=lambda node_id: _order_key(variant, stage_order, variant.nodes[node_id]),
            )
        )
    ordered.extend(
        sorted(
            node_ids - registered,
            key=lambda node_id: _order_key(variant, stage_order, variant.nodes[node_id]),
        )
    )

    before_ids: dict[str, list[str]] = defaultdict(list)
    after_ids: dict[str, list[str]] = defaultdict(list)
    for before, after in pairs:
        after_ids[before].append(after)
        before_ids[after].append(before)
    return ordered, before_ids, after_ids, registered


def _use_view(use: ClueUse, catalog: StoryClueCatalog) -> dict[str, Any]:
    fact = catalog.get(use.clue_id)
    return {
        "clue_id": use.clue_id,
        "title": fact.title if fact is not None else use.clue_id,
        "roles": list(use.roles),
        "presentation": use.presentation,
        "mode": use.mode,
        "speaker_id": use.speaker_id,
        "expected_interpretation": use.expected_interpretation,
    }


def _clues_view(
    variant: StoryVariant,
    catalog: StoryClueCatalog,
    order_index: Mapping[str, int],
) -> list[dict[str, Any]]:
    used: dict[str, list[tuple[str, ClueUse]]] = defaultdict(list)
    for node in variant.nodes.values():
        for use in node.clue_uses:
            used[use.clue_id].append((node.id, use))

    clues: list[dict[str, Any]] = []
    for clue_id in sorted(set(variant.clue_plans) | set(used)):
        fact = catalog.get(clue_id)
        plan = variant.clue_plans.get(clue_id) or (fact.plan if fact is not None else None)
        entries = sorted(
            used.get(clue_id, []),
            key=lambda pair: (
                order_index.get(pair[0], len(order_index)),
                pair[0],
                pair[1].clue_id,
            ),
        )
        roles = [role for role in _ROLE_ORDER if any(role in use.roles for _, use in entries)]
        roles.extend(
            sorted({role for _, use in entries for role in use.roles if role not in _ROLE_ORDER})
        )
        uses = [
            {
                **_use_view(use, catalog),
                "node_id": node_id,
                "node_title": variant.nodes[node_id].title,
            }
            for node_id, use in entries
        ]
        clues.append(
            {
                "clue_id": clue_id,
                "title": fact.title if fact is not None else clue_id,
                "roles": roles,
                "author_truth": plan.author_truth if plan is not None else None,
                "intention": plan.intention if plan is not None else "",
                "resolution_intent": (plan.resolution_intent if plan is not None else "undecided"),
                "has_identity": bool(fact is not None and fact.has_identity),
                "uses": uses,
            }
        )
    return clues


def _subjects_view(variant: StoryVariant, order_index: Mapping[str, int]) -> list[dict[str, Any]]:
    subjects: dict[str, dict[str, Any]] = {}
    for node in variant.nodes.values():
        for change in node.knowledge_changes:
            entry = subjects.setdefault(
                change.subject_id,
                {
                    "subject_id": change.subject_id,
                    "claims": 0,
                    "stances": set(),
                    "node_ids": set(),
                },
            )
            entry["claims"] += 1
            entry["stances"].add(change.stance)
            entry["node_ids"].add(node.id)
    return [
        {
            "subject_id": entry["subject_id"],
            "claims": entry["claims"],
            "stances": sorted(entry["stances"]),
            "node_ids": sorted(
                entry["node_ids"],
                key=lambda node_id: order_index.get(node_id, len(order_index)),
            ),
        }
        for _, entry in sorted(subjects.items())
    ]


def build_mystery_view(variant: StoryVariant, catalog: StoryClueCatalog) -> dict[str, Any]:
    order, before_ids, after_ids, registered = _actual_order(variant)
    order_index = {node_id: index for index, node_id in enumerate(order)}
    clues = _clues_view(variant, catalog, order_index)
    labels = {clue["clue_id"]: clue["title"] for clue in clues}
    diagnostics = structure_diagnostics(variant, labels)
    subjects = _subjects_view(variant, order_index)

    nodes: list[dict[str, Any]] = []
    for node_id in order:
        node = variant.nodes[node_id]
        nodes.append(
            {
                "id": node.id,
                "time_status": ("registered" if node_id in registered else "unregistered"),
                "before_ids": before_ids.get(node_id, []),
                "after_ids": after_ids.get(node_id, []),
                "clue_uses": [
                    {
                        **_use_view(use, catalog),
                        "node_id": node.id,
                        "node_title": node.title,
                    }
                    for use in node.clue_uses
                ],
                "knowledge_changes": [
                    change.model_dump(mode="json") for change in node.knowledge_changes
                ],
            }
        )

    total = len(variant.nodes)
    if total == 0 or len(registered) >= total:
        state = "complete"
    elif not registered:
        state = "unregistered"
    else:
        state = "partial"
    knowledge_count = sum(len(node.knowledge_changes) for node in variant.nodes.values())
    return {
        "variant_id": variant.id,
        "revision": variant.revision,
        "order": order,
        "time_status": {
            "state": state,
            "registered": len(registered),
            "unregistered": total - len(registered),
            "total": total,
        },
        "nodes": nodes,
        "clues": clues,
        "subjects": subjects,
        "diagnostics": diagnostics,
        "counts": {
            "nodes": total,
            "clues": len(clues),
            "event_times": len(variant.event_times),
            "knowledge_changes": knowledge_count,
            "subjects": len(subjects),
        },
    }
