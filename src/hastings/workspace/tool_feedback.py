"""结构任务的模型反馈与作者摘要；预算不进入作者摘要。"""

from __future__ import annotations

import json
from typing import Any

from tau_agent.messages import TextContent
from tau_agent.tools import AgentToolResult


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
