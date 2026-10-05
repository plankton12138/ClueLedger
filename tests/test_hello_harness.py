"""Hello-world harness：FakeProvider 确定性回放一次带工具调用的完整 agent loop。

学 tau 上游 test_agent_harness.py 的范式：断言事件契约，而非仅断言"无异常"。
"""

from collections.abc import Mapping

import pytest

from tau_agent import (
    AgentHarness,
    AgentHarnessConfig,
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    MessageStartEvent,
    ToolCall,
    ToolCancellationToken,
    ToolExecutionEndEvent,
    ToolResultMessage,
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


def _story_ping() -> AgentTool:
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


def _fake_provider(call: ToolCall) -> FakeProvider:
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


@pytest.mark.anyio
async def test_hello_harness_runs_full_tool_loop() -> None:
    story_ping = _story_ping()
    call = ToolCall(id="call-1", name="story_ping", arguments={"message": PING_MESSAGE})
    provider = _fake_provider(call)
    harness = AgentHarness(
        AgentHarnessConfig(
            provider=provider,
            model="fake",
            system="你是悬疑小说主编。",
            tools=[story_ping],
        )
    )

    events = [event async for event in harness.prompt("开始写第一章")]

    # 事件序列契约：AgentStart → TurnStart → 消息生命周期 → 工具执行 → TurnEnd → AgentEnd
    assert [event.type for event in events] == [
        "agent_start",
        "turn_start",
        "message_start",  # user prompt
        "message_end",
        "message_start",  # assistant（发起工具调用）
        "message_update",  # tool_call_end 部分消息
        "message_end",
        "tool_execution_start",
        "tool_execution_end",
        "message_start",  # tool_result
        "message_end",
        "turn_end",
        "turn_start",  # 第二轮：模型收到工具结果后给出最终文本
        "message_start",
        "message_end",
        "turn_end",
        "agent_end",
    ]

    starts = [event for event in events if isinstance(event, MessageStartEvent)]
    assert [event.message.role for event in starts] == [
        "user",
        "assistant",
        "toolResult",
        "assistant",
    ]

    # 消息历史含 user / assistant / tool_result 三类
    assert [message.role for message in harness.messages] == [
        "user",
        "assistant",
        "toolResult",
        "assistant",
    ]
    result = next(m for m in harness.messages if isinstance(m, ToolResultMessage))
    assert result.tool_name == "story_ping"
    assert result.text == f"pong: {PING_MESSAGE}"
    assert result.is_error is False

    execution_end = next(e for e in events if isinstance(e, ToolExecutionEndEvent))
    assert execution_end.tool_name == "story_ping"
    assert execution_end.is_error is False

    # FakeProvider 收到的 tools 列表含 story_ping（工具透传）
    # calls 元素为 (model, system, messages, tools) 四元组，索引 3 即 tools（见 tau_ai/fake.py）
    assert len(provider.calls) == 2
    assert provider.calls[0][3] == [story_ping]
