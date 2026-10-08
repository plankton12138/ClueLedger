"""FakeProvider 剧本共享助手（server 层测试用）。

P1 在 test_writing_loop.py 建立了"预录事件剧本"范式；server 层测试
（test_server_session / test_server_ws）说同一种剧本语言，故抽到本模块。
"""

from __future__ import annotations

from typing import Any

from tau_agent import AssistantMessage, ToolCall
from tau_agent.provider_events import (
    AssistantDoneEvent,
    AssistantStartEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ToolCallEndEvent,
)


def assistant_stream(*, call: ToolCall | None, text: str, reason: str) -> list[Any]:
    """一个模型回合的完整 provider 事件剧本（可带一个工具调用）。"""
    events: list[Any] = [AssistantStartEvent(partial=AssistantMessage(model="fake"))]
    if text:
        events.append(TextStartEvent(content_index=0, partial=AssistantMessage(model="fake")))
        events.append(
            TextDeltaEvent(
                content_index=0,
                delta=text,
                partial=AssistantMessage(content=text, model="fake"),
            )
        )
        events.append(
            TextEndEvent(
                content_index=0,
                content=text,
                partial=AssistantMessage(content=text, model="fake"),
            )
        )
    if call is not None:
        events.append(
            ToolCallEndEvent(
                content_index=1,
                tool_call=call,
                partial=AssistantMessage(content=[call], model="fake"),
            )
        )
    message: AssistantMessage = (
        AssistantMessage(content=[call], stop_reason="toolUse")
        if call is not None
        else AssistantMessage(content=text, stop_reason="stop")
    )
    events.append(AssistantDoneEvent(reason=reason, message=message))
    return events


def closing(text: str) -> list[Any]:
    """无工具调用的总结回合（正常收尾）。"""
    return assistant_stream(call=None, text=text, reason="stop")


def character_upsert(call_id: str, name: str, identity: str) -> ToolCall:
    """bible_upsert 角色卡的工具调用。"""
    return ToolCall(
        id=call_id,
        name="bible_upsert",
        arguments={"entity_type": "character", "data": {"name": name, "identity": identity}},
    )
