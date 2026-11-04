"""结构任务的模型反馈与作者摘要；预算不进入作者摘要。"""

from __future__ import annotations

import json
from typing import Any

from tau_agent.messages import TextContent
from tau_agent.tools import AgentToolResult

#: 剩余回合降到这个数就不再开新批次：先把已改的落定，再回复作者。
BUDGET_NOTICE_ROUNDS = 4


def author_summary(result: AgentToolResult) -> str:
    if isinstance(result.details, dict) and "author_summary" in result.details:
        return str(result.details["author_summary"])
    try:
        payload = json.loads(result.text)
        if isinstance(payload, dict) and isinstance(payload.get("summary"), str):
            return str(payload["summary"])
    except (ValueError, TypeError):
        pass
    return result.text


def budget_feedback(result: AgentToolResult, *, remaining: int) -> AgentToolResult:
    """把剩余回合写进工具返回。

    模型看不到回合上限，只能从工具返回判断还剩多少步；写在这里比写在系统提示里准确，
    因为它反映的是这一轮已经用掉多少，而不是一个恒定数字。作者摘要保持工具原本的说法。
    """
    try:
        payload = json.loads(result.text)
    except (ValueError, TypeError):
        return result
    if not isinstance(payload, dict):
        return result
    budget: dict[str, Any] = {"remaining_model_rounds": remaining}
    if remaining <= BUDGET_NOTICE_ROUNDS:
        budget["notice"] = (
            f"本轮只剩 {remaining} 个模型回合：不要再开始新的修改批次，"
            "先落定当前改动并回复作者；未完成的部分留给下一轮。"
        )
    payload["execution_budget"] = budget
    details: dict[str, Any] = dict(result.details) if isinstance(result.details, dict) else {}
    details["execution_budget"] = budget
    # 界面照旧显示工具原本的说法：有 summary 用 summary，没有才是整段返回。
    summary = payload.get("summary")
    details.setdefault("author_summary", summary if isinstance(summary, str) else result.text)
    return result.model_copy(
        update={
            "content": [
                TextContent(text=json.dumps(payload, ensure_ascii=False)),
                *[block for block in result.content if not isinstance(block, TextContent)],
            ],
            "details": details,
        }
    )


def structure_feedback(
    name: str,
    result: AgentToolResult,
    failed: bool,
    available: set[str],
    budget: dict[str, int],
) -> AgentToolResult:
    original = result.text
    try:
        payload = json.loads(original)
    except (ValueError, TypeError):
        payload = None
    if not isinstance(payload, dict):
        payload = {"ok": not failed, "message": original}
    summary = str(payload.get("summary") or original)
    if name not in available:
        summary = (
            f"[TOOL_NOT_FOUND] 构思生成：调用的工具 {name} 不存在。"
            "建议：使用 read_story 读取草稿版本，将 draft_revision 作为 update_story 的参数提交。"
        )
        payload = {
            "ok": False,
            "code": "TOOL_NOT_FOUND",
            "summary": summary,
            "available_tools": sorted(available),
        }
    payload["execution_budget"] = budget
    details: dict[str, Any] = dict(result.details) if isinstance(result.details, dict) else {}
    details.update({"author_summary": summary, "execution_budget": budget})
    if "code" in payload:
        details["code"] = payload["code"]
    return result.model_copy(
        update={
            "content": [
                TextContent(text=json.dumps(payload, ensure_ascii=False)),
                *[block for block in result.content if not isinstance(block, TextContent)],
            ],
            "details": details,
        }
    )
