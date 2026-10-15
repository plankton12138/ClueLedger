"""timeline_check 工具包装：账本装载 + 章节范围过滤 + 结果渲染（P2 T5）。

硬约束（冲突判定）在 hastings.bible.timeline_check 纯函数里，本模块只做
IO 与呈现。无冲突是合法空结果——正常返回"时间线自洽"，不报 error；
冲突列表同时放 details（结构化，P3 前端数据源），文本行喂回模型。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import cast

from hastings.bible.models import Method, TimelineEntry
from hastings.bible.store import StoryWorkspace, load_ledger
from hastings.bible.timeline_check import TimelineConflict, check_timeline
from hastings.tools.errors import ToolInputError
from tau_agent.messages import TextContent
from tau_agent.tools import AgentTool, AgentToolResult, ToolCancellationToken, ToolUpdateCallback
from tau_agent.types import JSONValue

_CHAPTER_RE = re.compile(r"ch(\d+)", re.IGNORECASE)


def _parse_chapter_param(arguments: Mapping[str, JSONValue], name: str) -> int | None:
    """可选章节参数 'ch02' → 2；给出但格式非法即参数违规。"""
    raw = arguments.get(name)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ToolInputError(f'{name} 必须是形如 "ch02" 的字符串，收到 {type(raw).__name__}')
    matched = _CHAPTER_RE.fullmatch(raw.strip())
    if matched is None:
        raise ToolInputError(
            f"{name}={raw!r} 格式非法。为什么错：章节参数只接受 ch+数字（如 ch02）。"
            '怎样算对：改为 "ch02"，或缺省以检查全部章节。'
        )
    return int(matched.group(1))


def _in_chapter_range(entry: TimelineEntry, lo: int | None, hi: int | None) -> bool:
    matched = _CHAPTER_RE.fullmatch(entry.chapter.strip()) if entry.chapter else None
    if matched is None:
        return lo is None and hi is None  # 无章节归属：仅在未过滤时纳入
    number = int(matched.group(1))
    return (lo is None or number >= lo) and (hi is None or number <= hi)


def _render_call(arguments: Mapping[str, JSONValue]) -> str | None:
    lo, hi = arguments.get("chapter_from"), arguments.get("chapter_to")
    if isinstance(lo, str) and isinstance(hi, str):
        return f"检测时间线冲突（{lo}-{hi}）"
    if isinstance(lo, str):
        return f"检测时间线冲突（{lo} 起）"
    if isinstance(hi, str):
        return f"检测时间线冲突（至 {hi}）"
    return "检测时间线冲突"


def _conflict_details(conflicts: list[TimelineConflict]) -> list[dict[str, JSONValue]]:
    return [
        {
            "rule": c.rule,
            "day": c.day,
            "persons": list(c.persons),
            "entries": list(c.entries),
            "message": c.message,
        }
        for c in conflicts
    ]


def create_timeline_tools(workspace: StoryWorkspace) -> list[AgentTool]:
    """创建绑定 workspace 的 timeline_check 工具。"""

    async def check_execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        lo = _parse_chapter_param(arguments, "chapter_from")
        hi = _parse_chapter_param(arguments, "chapter_to")
        entries = load_ledger(workspace.timeline_path, TimelineEntry)
        methods = load_ledger(workspace.method_path, Method)
        if lo is not None or hi is not None:
            entries = [e for e in entries if _in_chapter_range(e, lo, hi)]

        conflicts = check_timeline(entries, methods)
        details: dict[str, JSONValue] = {
            "checked_entries": len(entries),
            "checked_claims": sum(len(m.alibi_claims) for m in methods),
            "conflict_count": len(conflicts),
            # list 不型变：list[dict[str, JSONValue]] 需显式放宽为 JSONValue
            "conflicts": cast("JSONValue", _conflict_details(conflicts)),
        }
        if not conflicts:
            return AgentToolResult(
                content=[
                    TextContent(
                        text=(
                            f"时间线自洽：检查了 {len(entries)} 条事件、"
                            f"{details['checked_claims']} 条不在场主张，未发现冲突。"
                        )
                    )
                ],
                details=details,
            )

        lines = [f"时间线检测发现 {len(conflicts)} 处冲突："]
        lines.extend(f"- [{c.rule}] {c.message}" for c in conflicts)
        return AgentToolResult(content=[TextContent(text="\n".join(lines))], details=details)

    check = AgentTool(
        name="timeline_check",
        label="检测时间线冲突",
        description=(
            "对时间线账本与手法不在场主张做确定性冲突检测："
            "R0 数据自检（day/时刻格式/跨零点）、R1 人物分身（同角色同日时段重叠）、"
            "R2 不在场破绽（主张与真实事件相交）。无冲突返回时间线自洽。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "chapter_from": {
                    "type": "string",
                    "description": "章节范围下界，如 ch02；缺省从头",
                },
                "chapter_to": {
                    "type": "string",
                    "description": "章节范围上界，如 ch05；缺省到尾",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        execute_fn=check_execute,
        prompt_guidelines=(
            "核对时间线时先跑一次 timeline_check；报出的冲突如需修正，"
            "在 V2 项目经领域命令改时间线或主张，再重跑确认归零。",
        ),
        render_call=_render_call,
    )
    return [check]


__all__ = ["create_timeline_tools"]
