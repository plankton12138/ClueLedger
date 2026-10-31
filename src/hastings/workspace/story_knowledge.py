"""角色经历与读者揭示分开；只使用明确登记的实际事件先后。"""

from __future__ import annotations

from collections.abc import Iterable

from hastings.workspace.story_models import KnowledgeChange, StoryVariant


def event_ancestors(variant: StoryVariant) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {key: set() for key in variant.nodes}
    for relation in variant.event_times:
        if relation.after_node_id in result and relation.before_node_id in result:
            result[relation.after_node_id].add(relation.before_node_id)
    changed = True
    while changed:
        changed = False
        for node_id, parents in result.items():
            expanded = parents | {ancestor for parent in parents for ancestor in result[parent]}
            if expanded != parents:
                result[node_id] = expanded
                changed = True
    return result


def effective_knowledge(variant: StoryVariant, node_ids: Iterable[str]) -> list[KnowledgeChange]:
    """所选事件结束时已生效的知识；未登记先后时只包含同场知识。"""
    before = event_ancestors(variant)
    visible = set(node_ids)
    visible |= {parent for node_id in tuple(visible) for parent in before.get(node_id, set())}
    return [
        change
        for node in variant.nodes.values()
        for change in node.knowledge_changes
        if change.effective_event_node_id in visible
    ]


def validate_knowledge(variant: StoryVariant) -> None:
    before = event_ancestors(variant)
    for node in variant.nodes.values():
        for change in node.knowledge_changes:
            effective = change.effective_event_node_id
            for source in change.source_node_ids:
                if source == effective and change.source_kind == "observation":
                    continue
                if source not in before[effective]:
                    raise ValueError(
                        f"节点 {node.id}／角色 {change.subject_id}：知情来源 {source} "
                        f"未登记在生效事件 {effective} 之前；"
                        "请补充实际时间关系或核对来源，不要移动生效时点"
                    )
