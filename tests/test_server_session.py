"""HastingsSession 服务层测试（P3 T2）。

协议无关核心：不 import fastapi，纯 asyncio + FakeProvider 剧本驱动。
时序控制不用 sleep/竞态：prompt() 同步置位 is_running（harness 语义），
steer/follow_up/cancel 经订阅 listener 在事件点确定性触发。
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from fake_scripts import assistant_stream, character_upsert, closing
from hastings.bible.store import StoryWorkspace
from hastings.prompts import build_system_prompt
from hastings.server.hastings_session import HastingsSession
from hastings.server.session import AgentBusyError
from hastings.settings import StorySettings, save_settings
from hastings.tools.bible_tools import create_bible_tools
from tau_agent import AgentHarness, AgentHarnessConfig
from tau_agent.events import AgentEvent
from tau_ai import FakeProvider

STEER_TEXT = "记得他是比利时人"
FOLLOW_UP_TEXT = "再补一条细节"


@pytest.fixture
def workspace(tmp_path: Path) -> StoryWorkspace:
    return StoryWorkspace(root=tmp_path, slug="orient-express")


def _session(workspace: StoryWorkspace, *extra_turns: list[Any]) -> HastingsSession:
    tools = create_bible_tools(workspace)
    harness = AgentHarness(
        AgentHarnessConfig(
            provider=FakeProvider(
                [
                    assistant_stream(
                        call=character_upsert("call-s1", "波洛", "比利时侦探"),
                        text="先建档。",
                        reason="toolUse",
                    ),
                    *extra_turns,
                ]
            ),
            model="fake",
            system=build_system_prompt(tools),
            tools=tools,
        )
    )
    return HastingsSession(harness=harness, workspace=workspace)


async def _drain(stream: AsyncIterator[AgentEvent]) -> list[AgentEvent]:
    return [event async for event in stream]


@pytest.mark.anyio
async def test_prompt_streams_events_and_running_lifecycle(
    workspace: StoryWorkspace,
) -> None:
    session = _session(workspace, closing("建档完成。"))
    assert session.is_running is False
    assert session.slug == "orient-express"
    assert session.model == "fake"
    assert session.tool_names == ("bible_upsert", "bible_query")

    stream = session.prompt("建一个角色")
    assert session.is_running is True  # prompt() 同步置位，无需等首个事件

    events = await _drain(stream)
    assert session.is_running is False
    assert events[0].type == "agent_start"
    assert events[-1].type == "agent_end"


@pytest.mark.anyio
async def test_prompt_while_running_raises_busy_with_guidance(
    workspace: StoryWorkspace,
) -> None:
    session = _session(workspace, closing("建档完成。"))

    stream = session.prompt("建一个角色")
    with pytest.raises(AgentBusyError) as exc_info:
        session.prompt("再来一个")
    message = str(exc_info.value)
    # 三要素：哪里错（prompt 被拒）+ 为什么错（单流）+ 怎样算对（三个替代动作）
    assert "被拒绝" in message
    assert "同时只能" in message
    assert "steer" in message and "follow_up" in message and "abort" in message

    await _drain(stream)


@pytest.mark.anyio
async def test_steer_injects_message_into_running_flow(
    workspace: StoryWorkspace,
) -> None:
    session = _session(workspace, closing("建档完成。"))

    def on_start(event: AgentEvent) -> None:
        if event.type == "agent_start":
            session.steer(STEER_TEXT)

    unsubscribe = session.subscribe(on_start)
    await _drain(session.prompt("建一个角色"))
    unsubscribe()

    user_messages = [m for m in session.messages if m.role == "user"]
    assert len(user_messages) == 2  # 原 prompt + steer 插话
    assert any(STEER_TEXT in m.text for m in user_messages)


@pytest.mark.anyio
async def test_follow_up_queues_chained_continuation(
    workspace: StoryWorkspace,
) -> None:
    followup_reply = assistant_stream(call=None, text="已补上怀表细节。", reason="stop")
    session = _session(workspace, closing("建档完成。"), followup_reply)

    def on_start(event: AgentEvent) -> None:
        if event.type == "agent_start":
            session.follow_up(FOLLOW_UP_TEXT)

    unsubscribe = session.subscribe(on_start)
    events = await _drain(session.prompt("建一个角色"))
    unsubscribe()

    # 链式续跑发生在同一个 run 里：follow_up 轮结束后才发唯一的 agent_end
    assert [e.type for e in events].count("agent_end") == 1
    user_messages = [m for m in session.messages if m.role == "user"]
    assert len(user_messages) == 2  # 原 prompt + follow_up 追加
    assert "怀表" in session.messages[-1].text


@pytest.mark.anyio
async def test_cancel_aborts_pending_tool_execution(workspace: StoryWorkspace) -> None:
    session = _session(workspace, closing("建档完成。"))

    def on_tool_start(event: AgentEvent) -> None:
        if event.type == "tool_execution_start":
            session.cancel()

    unsubscribe = session.subscribe(on_tool_start)
    await _drain(session.prompt("建一个角色"))
    unsubscribe()

    # 取消接线生效：信号在工具执行前被检查，该工具以 abort 错误收场
    # （loop 语义：信号只在工具执行前检查，FakeProvider 不消费信号，故后续
    # 回合照常跑——会话层只验证 cancel() 确实到达了当前 run 的 signal）
    last_tool_result = next(m for m in reversed(session.messages) if m.role == "toolResult")
    assert last_tool_result.is_error is True
    assert "Operation aborted" in last_tool_result.text


@pytest.mark.anyio
async def test_subscribe_fanout_and_unsubscribe(workspace: StoryWorkspace) -> None:
    session = _session(workspace, closing("建档完成。"))
    live: list[str] = []
    detached: list[str] = []

    def live_listener(event: AgentEvent) -> None:
        live.append(event.type)

    def detached_listener(event: AgentEvent) -> None:
        detached.append(event.type)

    unsubscribe_detached = session.subscribe(detached_listener)
    unsubscribe_detached()
    session.subscribe(live_listener)

    await _drain(session.prompt("建一个角色"))

    assert "agent_start" in live and "agent_end" in live
    assert detached == []  # 退订后不再收到任何事件


# ---------- edit_marks 接线（P5 T3，D4）：session 注册表与工具闭包共享 ----------


@pytest.mark.anyio
async def test_from_workspace_wires_edit_marks_into_chapter_tools(
    workspace: StoryWorkspace,
) -> None:
    """from_workspace 装配的 write_chapter 与 session.edit_marks 是同一注册表。"""
    from hastings.tools.errors import ToolInputError

    session = HastingsSession.from_workspace(workspace, provider=FakeProvider([]), model="fake")
    write_tool = next(t for t in session.harness.config.tools if t.name == "write_chapter")

    session.edit_marks.acquire("ch02")

    with pytest.raises(ToolInputError, match="正在被作者手动编辑"):
        await write_tool.execute("call-1", {"chapter": 2, "content": "抢写。"})


def test_direct_construction_gets_fresh_registry(workspace: StoryWorkspace) -> None:
    """直接构造（无注册表实参）得到空注册表——CLI 等单 agent 场景锁永不触发。"""
    session = _session(workspace)

    assert session.edit_marks.mark_of("ch01") is None
    assert session.edit_marks.acquire("ch01").holder == "user"


# ---------- 写作语言热生效（P6，D3） ----------


def test_apply_writing_language_rebuilds_system_prompt(workspace: StoryWorkspace) -> None:
    """改语言即重建 config.system——下一轮对话读到新 prompt，无需重建会话。"""
    session = _session(workspace)
    assert "中文" in session.harness.config.system

    session.apply_writing_language("English")

    assert "English" in session.harness.config.system
    assert "中文" not in session.harness.config.system


def test_from_workspace_reads_saved_language(workspace: StoryWorkspace) -> None:
    """from_workspace 装配时读 settings.yaml 的写作语言注入 system prompt。"""
    save_settings(workspace, StorySettings(writing_language="日本語"))

    session = HastingsSession.from_workspace(workspace, provider=FakeProvider([]), model="fake")

    assert "日本語" in session.harness.config.system
