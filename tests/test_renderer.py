"""PlainRenderer 测试（T5）。

用 FakeProvider 驱动真实 agent loop 产生事件流，再喂渲染器并捕获 stdout 断言：
文本增量按序出现、工具行格式正确、错误工具调用有 error 行、未知事件被显式记录。
"""

from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hastings.bible.store import StoryWorkspace
from hastings.cli import PlainRenderer
from hastings.tools.chapter_tools import create_chapter_tools
from tau_agent import (
    AgentHarness,
    AgentHarnessConfig,
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    TextContent,
    ToolCall,
)
from tau_agent.events import (
    AgentStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
)
from tau_agent.provider_events import (
    AssistantDoneEvent,
    AssistantStartEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ToolCallEndEvent,
)
from tau_ai import FakeProvider


def _stream_text(text: str) -> list[Any]:
    return [
        TextStartEvent(content_index=0, partial=AssistantMessage(model="fake")),
        TextDeltaEvent(
            content_index=0,
            delta=text,
            partial=AssistantMessage(content=text, model="fake"),
        ),
        TextEndEvent(
            content_index=0,
            content=text,
            partial=AssistantMessage(content=text, model="fake"),
        ),
    ]


def _text_updates(text: str) -> list[MessageUpdateEvent]:
    """把 provider 文本事件包装成 harness 对外发出的 message_update 事件。"""
    return [
        MessageUpdateEvent(
            message=AssistantMessage(content=text, model="fake"),
            assistant_message_event=event,
        )
        for event in _stream_text(text)
    ]


def test_renderer_prints_text_deltas_in_order(
    capsys: pytest.CaptureFixture[str],
) -> None:
    renderer = PlainRenderer({})

    for event in _text_updates("雪封之夜。"):
        renderer.handle(event)

    assert capsys.readouterr().out == "雪封之夜。"


def test_renderer_prints_tool_line_with_render_call(
    capsys: pytest.CaptureFixture[str],
) -> None:
    tool = create_chapter_tools(StoryWorkspace(root=Path("."), slug="tmp"))[0]
    renderer = PlainRenderer({tool.name: tool})

    renderer.handle(
        ToolExecutionStartEvent(
            tool_call_id="call-1",
            tool_name="write_chapter",
            args={"chapter": 3, "content": "正文", "force": False},
        )
    )

    assert capsys.readouterr().out == "[tool] write_chapter 写入第3章（2 字）\n"


def test_renderer_falls_back_to_tool_name_without_renderer(
    capsys: pytest.CaptureFixture[str],
) -> None:
    renderer = PlainRenderer({})

    renderer.handle(
        ToolExecutionStartEvent(tool_call_id="call-1", tool_name="bible_upsert", args={})
    )

    assert capsys.readouterr().out == "[tool] bible_upsert\n"


def test_renderer_prints_error_line_format(
    capsys: pytest.CaptureFixture[str],
) -> None:
    renderer = PlainRenderer({})

    renderer.handle(
        ToolExecutionEndEvent(
            tool_call_id="call-1",
            tool_name="write_chapter",
            result=AgentToolResult(content=[TextContent(text="ch01.md 已存在；请传 force=true")]),
            is_error=True,
        )
    )

    out = capsys.readouterr().out
    assert out.startswith("[tool error] write_chapter ")
    assert "force=true" in out


def test_renderer_ignores_successful_tool_end(
    capsys: pytest.CaptureFixture[str],
) -> None:
    renderer = PlainRenderer({})

    renderer.handle(
        ToolExecutionEndEvent(
            tool_call_id="call-2",
            tool_name="write_chapter",
            result=AgentToolResult(content=[TextContent(text="第1章已写入 ch01.md")]),
            is_error=False,
        )
    )

    assert capsys.readouterr().out == ""


def test_renderer_records_unhandled_event_types_explicitly() -> None:
    renderer = PlainRenderer({})
    unknown = SimpleNamespace(type="mystery_event")

    renderer.handle(unknown)  # type: ignore[arg-type]
    renderer.handle(AgentStartEvent())  # 已知事件：被显式忽略，不计入

    # 未知事件不是静默吞掉：类型被记录，便于调试视图排查
    assert renderer.unhandled_event_types == ("mystery_event",)


@pytest.mark.anyio
async def test_renderer_consumes_real_harness_event_stream(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Any,
) -> None:
    """端到端：FakeProvider 事件流 → harness → 渲染器 → stdout。"""
    workspace = StoryWorkspace(root=tmp_path, slug="demo")
    tools = create_chapter_tools(workspace)
    by_name: Mapping[str, AgentTool] = {tool.name: tool for tool in tools}
    call = ToolCall(
        id="call-1",
        name="write_chapter",
        arguments={"chapter": 1, "title": "雪封之夜", "content": "雪从午夜开始落。"},
    )
    provider = FakeProvider(
        [
            [
                AssistantStartEvent(partial=AssistantMessage(model="fake")),
                *_stream_text("我先把第一章写入案卷。"),
                ToolCallEndEvent(
                    content_index=1,
                    tool_call=call,
                    partial=AssistantMessage(content=[call], model="fake"),
                ),
                AssistantDoneEvent(
                    reason="toolUse",
                    message=AssistantMessage(content=[call], stop_reason="toolUse"),
                ),
            ],
            [
                AssistantStartEvent(partial=AssistantMessage(model="fake")),
                *_stream_text("第一章已落盘。"),
                AssistantDoneEvent(
                    reason="stop",
                    message=AssistantMessage(content="第一章已落盘。", stop_reason="stop"),
                ),
            ],
        ]
    )
    harness = AgentHarness(
        AgentHarnessConfig(
            provider=provider,
            model="fake",
            system="你是悬疑小说主编。",
            tools=list(tools),
        )
    )
    renderer = PlainRenderer(by_name)

    async for event in harness.prompt("写第一章"):
        renderer.handle(event)

    out = capsys.readouterr().out
    assert "我先把第一章写入案卷。" in out
    assert "[tool] write_chapter 写入第1章（8 字）" in out
    assert "第一章已落盘。" in out
    assert (workspace.chapters_dir / "ch01.md").exists()
    assert renderer.unhandled_event_types == ()
