"""可判定的结构诊断；描述缺项，不把分支数量当成文学质量。"""

from __future__ import annotations

from typing import Any

from hastings.workspace.story_models import StoryVariant


def structure_diagnostics(variant: StoryVariant) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if len(variant.threads) < 2:
        return issues
    for thread in variant.threads.values():
        members = [
            n
            for n in variant.nodes.values()
            if n.primary_thread_id == thread.id and not n.is_convergence
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
                    "related_ids": [n.id for n in members],
                    "message": f"故事线「{thread.title}」"
                    + ("缺少主要推进节点；" if not members else "")
                    + ("、".join(missing) + "未定" if missing else ""),
                    "suggestion": "安排该线自己的关键变化，再核对它对其他线的影响",
                }
            )
    next_edges = [e for e in variant.edges.values() if e.kind == "next"]
    incoming = {key: 0 for key in variant.nodes}
    outgoing = dict(incoming)
    for edge in next_edges:
        incoming[edge.to_id] += 1
        outgoing[edge.from_id] += 1
    if (
        len(variant.nodes) > 2
        and len(next_edges) == len(variant.nodes) - 1
        and max(incoming.values(), default=0) <= 1
        and max(outgoing.values(), default=0) <= 1
    ):
        issues.append(
            {
                "code": "STORY_SINGLE_CHAIN",
                "object_id": variant.id,
                "related_ids": sorted(variant.nodes),
                "message": "全部节点仍由推进边串成一条路径",
                "suggestion": "核对发生先后与推进依赖，保留真实关系，不为图形添加虚构分支",
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
