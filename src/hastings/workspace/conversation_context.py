"""会话上下文恢复（0.4 V04-10）。

同一会话要能接着聊，但事件日志里存的不是完整的模型对话：工具参数只留定位信息、
工具结果只留摘录、思考内容完全没有保存。直接把日志拼成模型历史会造出「有工具调用
没有工具结果」的残缺请求，换一个供应商还会带回不该复用的思考签名。

所以跨轮只回放两样东西：

- **作者说过的提示**（``turn_started.text``）；
- **模型公开的回复**（``assistant_message.text``）。

其余事实通过应用自己的说明补上：每一轮实际是完成、失败、停止还是中断，动了哪些文件，
历史有没有记录。这段说明进系统提示，不冒充作者的要求；``run_finished`` 与模型自述冲突时
以实际执行事实为准。涉及原文时模型自己重新 ``read_file``——文件是共享的，旧工具结果
本来也不该被当成当前正文。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tau_agent.messages import AgentMessage, AssistantMessage, TextContent, UserMessage

__all__ = ["execution_facts", "replay_public_messages"]

_STATUS_LABEL = {
    "completed": "完成",
    "partial": "部分完成",
    "failed": "失败",
    "stopped": "作者停止",
    "interrupted": "中断",
}

_RECORDING_LABEL = {
    "committed": "历史已记录",
    "no_change": "没有改动",
    "unrecorded": "历史未记录",
}

#: 执行事实最多带多少轮；更早的轮次只说明「还有更早的讨论」。
_MAX_FACT_TURNS = 20
_FACT_PROMPT_LIMIT = 60


def replay_public_messages(events: Sequence[dict[str, Any]]) -> list[AgentMessage]:
    """把事件还原成用户提示与公开回复；工具过程与流式增量不回放。

    一轮里可能有多条正式公开文本（多次模型响应，例如先说明再动手）。它们在本轮内
    合并成一条 assistant 消息：上下文看到的仍是「作者说一句、模型答一段」，
    既不会把同一次回复拆成两条，也不会漏掉任何一段。
    """
    messages: list[AgentMessage] = []
    reply: list[str] = []

    def flush() -> None:
        if reply:
            messages.append(
                AssistantMessage(content=[TextContent(text="\n\n".join(reply))], stop_reason="stop")
            )
            reply.clear()

    for event in events:
        kind = event.get("type")
        text = event.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        if kind == "turn_started":
            # 新一轮开始：上一轮的公开文本先落成一条 assistant 消息。
            flush()
            messages.append(UserMessage(content=text))
        elif kind == "assistant_message":
            reply.append(text)
    flush()
    return messages


def execution_facts(events: Sequence[dict[str, Any]]) -> str:
    """每一轮的实际执行结果，写进系统提示供模型判断「上次到底成了没有」。"""
    turns: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for event in events:
        kind = event.get("type")
        if kind == "turn_started":
            current = {"prompt": event.get("text") or "", "result": None}
            turns.append(current)
        elif kind == "run_finished" and current is not None and current["result"] is None:
            current["result"] = event
    finished = [turn for turn in turns if turn["result"] is not None]
    if not finished:
        return ""
    lines: list[str] = []
    if len(finished) > _MAX_FACT_TURNS:
        omitted = len(finished) - _MAX_FACT_TURNS
        lines.append(f"（更早还有 {omitted} 轮讨论，未在这里逐条列出。）")
        finished = finished[-_MAX_FACT_TURNS:]
    for turn in finished:
        result = turn["result"]
        prompt = " ".join(str(turn["prompt"]).split())[:_FACT_PROMPT_LIMIT]
        status = _STATUS_LABEL.get(str(result.get("status")), str(result.get("status")))
        recording = _RECORDING_LABEL.get(
            str(result.get("recording")), str(result.get("recording"))
        )
        paths = result.get("changed_paths") or []
        changed = "、".join(str(path) for path in paths) if paths else "没有改动文件"
        lines.append(f"- 「{prompt}」：{status}；{changed}；{recording}")
    return "\n".join(lines)
