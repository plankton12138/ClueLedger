"""PlainRenderer 测试（T5）。

用 FakeProvider 驱动真实 agent loop 产生事件流，再喂渲染器并捕获 stdout 断言：
文本增量按序出现、工具行格式正确、错误工具调用有 error 行、未知事件被显式记录。
"""

from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hastings.bible.models import Character, Clue, Method
from hastings.bible.store import StoryWorkspace, save_character, save_ledger
from hastings.cli import PlainRenderer, bible_command_output, create_writing_tools, format_bible
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


# --- T7：/bible 直读命令（格式化 + 防崩 REPL） ---


def _seeded_workspace(tmp_path: Any) -> StoryWorkspace:
    workspace = StoryWorkspace(root=tmp_path, slug="bible-cli")
    save_character(workspace, Character(name="沈砚", identity="退休警探"))
    save_ledger(workspace.clues_path, [Clue(name="停摆的怀表", content="停在 21:47")])
    save_ledger(workspace.method_path, [Method(name="怀表调时诡计", status="locked")])
    return workspace


def test_format_bible_overview_covers_all_five_ledgers(tmp_path: Any) -> None:
    workspace = _seeded_workspace(tmp_path)

    out = format_bible(workspace)

    assert "角色账本共 1 条" in out
    assert "- 沈砚 | 退休警探 | 弧光：flat" in out
    assert "- 停摆的怀表 | planned" in out
    assert "- 怀表调时诡计 | locked" in out
    assert "伏笔账本为空" in out
    assert "时间线事件账本为空" in out


def test_format_bible_single_ledger(tmp_path: Any) -> None:
    workspace = _seeded_workspace(tmp_path)

    assert format_bible(workspace, "method") == "手法账本共 1 条：\n- 怀表调时诡计 | locked"


def test_format_bible_unknown_type_lists_valid_values(tmp_path: Any) -> None:
    workspace = _seeded_workspace(tmp_path)

    out = format_bible(workspace, "spell")

    assert "未知账本类型" in out
    assert "character" in out
    assert "foreshadow" in out


def test_bible_command_output_survives_corrupt_yaml(tmp_path: Any) -> None:
    """手改的 YAML 损坏时返回可读错误文本而非抛异常崩 REPL（沿用 R3 #4 模式）。"""
    workspace = _seeded_workspace(tmp_path)
    workspace.clues_path.write_text("::{ not yaml", encoding="utf-8")

    out = bible_command_output(workspace, "clue")

    assert out.startswith("读取 Story Bible 失败")
    assert str(workspace.bible_dir) in out
    # 其它账本不受损坏文件影响
    assert "手法账本共 1 条" in bible_command_output(workspace, "method")


def test_create_writing_tools_covers_ledger_lifecycle_tools(tmp_path: Any) -> None:
    """真实端点工具集：五账本读写 + 锁定/解锁 + 时间线检测全注册。"""
    workspace = _seeded_workspace(tmp_path)

    names = {tool.name for tool in create_writing_tools(workspace)}

    assert {
        "bible_upsert",
        "bible_query",
        "write_chapter",
        "method_lock",
        "method_unlock",
        "timeline_check",
    } <= names
