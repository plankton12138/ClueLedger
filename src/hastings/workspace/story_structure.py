"""可判定的结构诊断；描述缺项，不把分支数量当成文学质量。

诊断分三类：

* 故事线是否完整；
* 节点是否被一条推进边串成主路径；
* 实际时间、线索生命周期与读者读法是否留下了可读的缺项。

全部只读，不替作者补真相，也不阻止有意保留的单线或开放结局。
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from hastings.workspace.story_models import StoryVariant

#: 线索角色进入诊断时的固定顺序，保证同一份结构的诊断稳定。
_ROLE_ORDER = ("plant", "develop", "misdirect", "reinterpret", "resolve")


def _longest_next_path(variant: StoryVariant) -> list[str]:
    nodes = set(variant.nodes)
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in variant.edges.values():
        if edge.kind == "next" and edge.from_id in nodes and edge.to_id in nodes:
            adjacency[edge.from_id].append(edge.to_id)
    memo: dict[str, list[str]] = {}
    visiting: set[str] = set()

    def walk(node_id: str) -> list[str]:
        if node_id in memo:
            return memo[node_id]
        if node_id in visiting:
            # 推进边本应在写入时拒绝环；这里保守停止，不让诊断本身失败。
            return [node_id]
        visiting.add(node_id)
        best = [node_id]
        for target in adjacency[node_id]:
            candidate = [node_id, *walk(target)]
            if len(candidate) > len(best):
                best = candidate
        visiting.remove(node_id)
        memo[node_id] = best
        return best

    return max((walk(node_id) for node_id in variant.nodes), key=len, default=[])


def _event_time_cycle(variant: StoryVariant) -> bool:
    nodes = set(variant.nodes)
    adjacency: dict[str, list[str]] = defaultdict(list)
    indegree = {node_id: 0 for node_id in nodes}
    involved: set[str] = set()
    for relation in variant.event_times:
        before = relation.before_node_id
        after = relation.after_node_id
        if before not in nodes or after not in nodes or before == after:
            continue
        adjacency[before].append(after)
        indegree[after] += 1
        involved.update((before, after))
    queue = [node_id for node_id in involved if indegree[node_id] == 0]
    visited = 0
    while queue:
        node_id = queue.pop()
        visited += 1
        for target in adjacency[node_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    return visited < len(involved)


def _clue_label(clue_id: str, labels: Mapping[str, str] | None) -> str:
    return labels.get(clue_id, clue_id) if labels else clue_id


def structure_diagnostics(
    variant: StoryVariant,
    labels: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """返回可判定的结构缺项；``labels`` 只用于把线索 ID 换成可读标题。"""
    issues: list[dict[str, Any]] = []

    if len(variant.threads) >= 2:
        for thread in variant.threads.values():
            members = [
                node
                for node in variant.nodes.values()
                if node.primary_thread_id == thread.id and not node.is_convergence
            ]
            missing = [
                label
                for label, value in (
                    ("目标", thread.goal),
                    ("阻力", thread.obstacle),
                    ("收束", thread.intended_resolution),
                )
                if not value
            ]
            if not members or missing:
                issues.append(
                    {
                        "code": "STORY_THREAD_INCOMPLETE",
                        "object_id": thread.id,
                        "related_ids": [node.id for node in members],
                        "message": f"故事线「{thread.title}」"
                        + ("缺少主要推进节点；" if not members else "")
                        + ("、".join(missing) + "未定" if missing else ""),
                        "suggestion": "安排该线自己的关键变化，再核对它对其他线的影响",
                    }
                )

    node_ids = set(variant.nodes)
    next_edges = [
        edge
        for edge in variant.edges.values()
        if edge.kind == "next" and edge.from_id in node_ids and edge.to_id in node_ids
    ]
    incoming = {key: 0 for key in variant.nodes}
    outgoing = dict(incoming)
    for edge in next_edges:
        incoming[edge.to_id] += 1
        outgoing[edge.from_id] += 1
    exact_single_chain = (
        len(variant.nodes) > 2
        and len(next_edges) == len(variant.nodes) - 1
        and max(incoming.values(), default=0) <= 1
        and max(outgoing.values(), default=0) <= 1
    )
    if exact_single_chain:
        issues.append(
            {
                "code": "STORY_SINGLE_CHAIN",
                "object_id": variant.id,
                "related_ids": sorted(variant.nodes),
                "message": "全部节点仍由推进边串成一条路径",
                "suggestion": "核对发生先后与推进依赖，保留真实关系，不为图形添加虚构分支",
            }
        )

    longest_path = _longest_next_path(variant)
    if len(variant.nodes) >= 4 and not exact_single_chain:
        threshold = max(4, math.ceil(len(variant.nodes) * 0.6))
        if len(longest_path) >= threshold:
            issues.append(
                {
                    "code": "STORY_MAIN_CHAIN_LONG",
                    "object_id": variant.id,
                    "related_ids": longest_path,
                    "message": (
                        f"最长推进链覆盖 {len(longest_path)}/{len(variant.nodes)} 个节点，"
                        "其余故事线没有形成独立推进"
                    ),
                    "suggestion": (
                        "先核对实际时间与推进依赖，再给各线安排自己的关键变化和汇合；"
                        "如果故事本来就是单线，在阶段摘要或汇合处写明原因"
                    ),
                }
            )

    registered = {
        node_id
        for relation in variant.event_times
        for node_id in (relation.before_node_id, relation.after_node_id)
        if node_id in node_ids and relation.before_node_id != relation.after_node_id
    }
    if len(variant.nodes) > 1:
        if not registered:
            issues.append(
                {
                    "code": "STORY_TIMELINE_EMPTY",
                    "object_id": variant.id,
                    "related_ids": sorted(variant.nodes),
                    "message": f"{len(variant.nodes)} 个节点还没有登记实际时间",
                    "suggestion": "为案发窗口和关键节点补 set_event_time；不要把 next 当成实际时间",
                }
            )
        elif len(registered) < len(variant.nodes):
            issues.append(
                {
                    "code": "STORY_TIMELINE_PARTIAL",
                    "object_id": variant.id,
                    "related_ids": sorted(node_ids - registered),
                    "message": (
                        f"实际时间只覆盖 {len(registered)}/{len(variant.nodes)} 个节点，"
                        "其余节点会按叙述顺序追加"
                    ),
                    "suggestion": "补齐实际先后；未登记节点会在悬疑视图横轴末尾标注“时间未登记”",
                }
            )
    if _event_time_cycle(variant):
        issues.append(
            {
                "code": "STORY_TIMELINE_CYCLE",
                "object_id": variant.id,
                "related_ids": sorted(registered),
                "message": "实际时间关系存在循环，无法得到完整时间轴",
                "suggestion": "检查 event_times 的 before / after，保留可解释的实际先后",
            }
        )

    knowledge_nodes = [node.id for node in variant.nodes.values() if node.knowledge_changes]
    clue_nodes = [node.id for node in variant.nodes.values() if node.clue_uses]
    if clue_nodes and not knowledge_nodes:
        issues.append(
            {
                "code": "STORY_KNOWLEDGE_MISSING",
                "object_id": variant.id,
                "related_ids": sorted(clue_nodes),
                "message": "还没有登记任何角色知情；悬疑视图的侦探/助手层会显示未登记",
                "suggestion": (
                    "在关键线索节点写 knowledge_changes，记录谁知道或相信什么，"
                    "以及 effective_event_node_id"
                ),
            }
        )

    uses_by_clue: dict[str, list[tuple[str, Any]]] = defaultdict(list)
    for node in variant.nodes.values():
        for use in node.clue_uses:
            uses_by_clue[use.clue_id].append((node.id, use))
    for clue_id in sorted(set(variant.clue_plans) | set(uses_by_clue)):
        plan = variant.clue_plans.get(clue_id)
        entries = uses_by_clue.get(clue_id, [])
        uses = [use for _, use in entries]
        related_node_ids = sorted({node_id for node_id, _ in entries})
        label = _clue_label(clue_id, labels)
        roles = {role for use in uses for role in use.roles}
        if not uses:
            issues.append(
                {
                    "code": "STORY_CLUE_UNUSED",
                    "object_id": clue_id,
                    "related_ids": [],
                    "message": f"线索「{label}」有计划但没有使用点",
                    "suggestion": "把线索接入至少一个节点，或删除未使用计划",
                }
            )
            continue
        if not roles & {"resolve", "reinterpret"}:
            issues.append(
                {
                    "code": "STORY_CLUE_LIFECYCLE_INCOMPLETE",
                    "object_id": clue_id,
                    "related_ids": related_node_ids,
                    "message": f"线索「{label}」只有埋设或推进，没有回收或重新解释",
                    "suggestion": "为线索安排回收或重新解释；保留开放结局时在计划里写明原因",
                }
            )
        if roles & {"misdirect", "reinterpret"} and not any(
            use.expected_interpretation.strip() for use in uses
        ):
            issues.append(
                {
                    "code": "STORY_CLUE_READING_MISSING",
                    "object_id": clue_id,
                    "related_ids": related_node_ids,
                    "message": f"线索「{label}」承担误导或重新解释，但没有写读者先相信什么",
                    "suggestion": "补写 expected_interpretation：这条线索想让读者先得出什么结论",
                }
            )
        if (
            plan is not None
            and plan.resolution_intent == "explain"
            and not (plan.author_truth and plan.author_truth.strip())
        ):
            issues.append(
                {
                    "code": "STORY_CLUE_TRUTH_MISSING",
                    "object_id": clue_id,
                    "related_ids": related_node_ids,
                    "message": f"线索「{label}」计划解释，但作者真相未定",
                    "suggestion": "补作者真相，或把回收方式改为“保留开放/未定”",
                }
            )
    return issues


def requires_multiline_check(text: str, variant: StoryVariant) -> bool:
    if any(word in text for word in ("单线", "只要一条线", "只有一条线")):
        return False
    return any(word in text for word in ("多线", "多条故事线", "并行", "交织")) or (
        len(variant.threads) > 1
        and any(word in text for word in ("创建构思", "开始构思", "生成构思", "建立构思"))
    )
