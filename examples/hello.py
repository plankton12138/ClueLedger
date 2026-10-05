"""Hello-world：用 FakeProvider 确定性回放一次带工具调用的完整 agent loop。

运行：uv run python examples/hello.py

这是 Hastings 的"骨架验证"演示：tau 内核在新仓库里跑通完整 agent loop，
全程不触碰真实 LLM——事件流由 FakeProvider 预录回放，因此输出完全确定。
"""

import asyncio
from collections.abc import Mapping

from tau_agent import (
    AgentHarness,
    AgentHarnessConfig,
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    MessageEndEvent,
    MessageStartEvent,
    ToolCall,
    ToolCancellationToken,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolUpdateCallback,
)
from tau_agent.provider_events import (
    AssistantDoneEvent,
    AssistantStartEvent,
    ToolCallEndEvent,
)
from tau_agent.types import JSONValue
from tau_ai import FakeProvider

PING_MESSAGE = "东方快车上的第一声汽笛"
FINAL_TEXT = "故事已开卷。"


def make_story_ping() -> AgentTool:
    """最小小说工具：收到 message 原样回答 pong。"""

    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        return AgentToolResult(content=f"pong: {arguments['message']}")

    return AgentTool(
        name="story_ping",
        label="Story Ping",
        description="Ping the story workspace and echo the message back.",
        parameters={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
        execute_fn=execute,
    )


def make_fake_provider(call: ToolCall) -> FakeProvider:
    """预录两段事件流：先发起 story_ping 工具调用，再输出最终文本。"""
    assistant_call = AssistantMessage(content=[call], stop_reason="toolUse")
    assistant_final = AssistantMessage(content=FINAL_TEXT, stop_reason="stop")
    return FakeProvider(
        [
            [
                AssistantStartEvent(partial=AssistantMessage(model="fake")),
                ToolCallEndEvent(
                    content_index=0,
                    tool_call=call,
                    partial=AssistantMessage(content=[call]),
                ),
                AssistantDoneEvent(reason="toolUse", message=assistant_call),
            ],
            [
                AssistantStartEvent(partial=AssistantMessage(model="fake")),
                AssistantDoneEvent(reason="stop", message=assistant_final),
            ],
        ]
    )


def describe(event: object) -> str:
    """把事件渲染成一行可读摘要。"""
    if isinstance(event, MessageStartEvent | MessageEndEvent):
        return f"{event.message.role}: {getattr(event.message, 'text', '')}"
    if isinstance(event, ToolExecutionStartEvent):
        return f"{event.tool_name}({event.args})"
    if isinstance(event, ToolExecutionEndEvent):
        return f"{event.tool_name} -> {event.result.text} (is_error={event.is_error})"
    return ""


async def main() -> None:
    call = ToolCall(id="call-1", name="story_ping", arguments={"message": PING_MESSAGE})
    harness = AgentHarness(
        AgentHarnessConfig(
            provider=make_fake_provider(call),
            model="fake",
            system="你是悬疑小说主编。",
            tools=[make_story_ping()],
        )
    )

    async for event in harness.prompt("开始写第一章"):
        line = describe(event)
        print(f"{event.type:<22} {line}")


if __name__ == "__main__":
    asyncio.run(main())
