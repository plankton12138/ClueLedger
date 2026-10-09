"""会话持久化桥测试（P4 T1）。

JsonlSessionStorage 是 tau 现成组件，本模块验证三件事：
① Windows + asyncio 下文件锁探路（append/read_all 最小闭环）；
② attach 后 message_end 事件落盘、load 后消息历史一致（含 user 消息）；
③ 坏文件泄压阀：改名备份 + 中文指引，不静默丢。
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from hastings.bible.store import StoryWorkspace
from hastings.prompts import build_system_prompt
from hastings.session_persist import (
    attach_session_storage,
    load_session_messages,
    quarantine_session_file,
    restore_and_attach,
    session_file,
)
from hastings.tools.bible_tools import create_bible_tools
from tau_agent import AgentHarness, AgentHarnessConfig, AssistantMessage, ToolCall, UserMessage
from tau_agent.events import MessageEndEvent
from tau_agent.provider_events import AssistantDoneEvent, AssistantStartEvent, ToolCallEndEvent
from tau_agent.session import JsonlSessionStorage, MessageEntry, ModelChangeEntry
from tau_ai import FakeProvider

POIROT = {"name": "波洛", "identity": "比利时侦探", "secret": "无"}


def _scripted_provider() -> FakeProvider:
    call = ToolCall(
        id="call-1", name="bible_upsert", arguments={"entity_type": "character", "data": POIROT}
    )

    def stream(*, call: ToolCall | None, text: str, reason: str) -> list[Any]:
        events: list[Any] = [AssistantStartEvent(partial=AssistantMessage(model="fake"))]
        message: AssistantMessage = (
            AssistantMessage(content=[call], stop_reason="toolUse")
            if call is not None
            else AssistantMessage(content=text, stop_reason="stop")
        )
        if call is not None:
            events.append(
                ToolCallEndEvent(
                    content_index=1, tool_call=call, partial=AssistantMessage(model="fake")
                )
            )
        events.append(AssistantDoneEvent(reason=reason, message=message))
        return events

    return FakeProvider(
        [
            stream(call=call, text="建角色。", reason="toolUse"),
            stream(call=None, text="已建档。", reason="stop"),
        ]
    )


@pytest.fixture
def session_path(tmp_path: Path) -> Path:
    return tmp_path / "session.jsonl"


@pytest.fixture
def make_harness(tmp_path: Path) -> Callable[[], AgentHarness]:
    def factory() -> AgentHarness:
        workspace = StoryWorkspace(root=tmp_path, slug="orient-express")
        tools = create_bible_tools(workspace)
        return AgentHarness(
            AgentHarnessConfig(
                provider=_scripted_provider(),
                model="fake",
                system=build_system_prompt(tools),
                tools=tools,
            )
        )

    return factory


@pytest.mark.anyio
async def test_storage_roundtrip_on_windows(session_path: Path) -> None:
    """探路用例：msvcrt 文件锁 + asyncio 下 append/read_all 闭环（风险表第 1 行）。"""
    storage = JsonlSessionStorage(session_path)
    entry = MessageEntry(message=UserMessage(content="你好"))
    await storage.append(entry)
    loaded = await storage.read_all()
    assert [e.id for e in loaded] == [entry.id]


@pytest.mark.anyio
async def test_attach_persists_message_end(
    make_harness: Callable[[], AgentHarness], session_path: Path
) -> None:
    harness = make_harness()
    attach_session_storage(harness, JsonlSessionStorage(session_path))
    async for _ in harness.prompt("创建角色波洛"):
        pass

    assert session_path.exists()
    # user prompt + 每回合 assistant + 每个工具结果——全部定稿消息都在
    assert len(harness.messages) > 0
    storage = JsonlSessionStorage(session_path)
    entries = await storage.read_all()
    assert [e.message for e in entries] == list(harness.messages)


@pytest.mark.anyio
async def test_load_restores_messages_across_restart(
    make_harness: Callable[[], AgentHarness], session_path: Path
) -> None:
    original = make_harness()
    attach_session_storage(original, JsonlSessionStorage(session_path))
    async for _ in original.prompt("创建角色波洛"):
        pass

    restarted = make_harness()
    result = await load_session_messages(session_path)
    assert result.warning is None
    assert result.messages == original.messages

    restarted.replace_messages(result.messages)
    assert restarted.messages == original.messages


@pytest.mark.anyio
async def test_load_skips_non_message_entries(session_path: Path) -> None:
    storage = JsonlSessionStorage(session_path)
    await storage.append(MessageEntry(message=UserMessage(content="第一题")))
    await storage.append(ModelChangeEntry(model="gpt-x"))
    result = await load_session_messages(session_path)
    assert [m.role for m in result.messages] == ["user"]


@pytest.mark.anyio
async def test_load_quarantines_corrupt_file(session_path: Path) -> None:
    """坏行 → SessionJsonlError → 改名备份 + 中文指引 + 空会话继续（泄压阀）。"""
    session_path.write_text("not-a-json-line\n", encoding="utf-8")
    result = await load_session_messages(session_path)
    assert result.messages == ()
    assert result.warning is not None
    assert "备份" in result.warning
    assert not session_path.exists()  # 原路径已让位
    backups = list(session_path.parent.glob("session.jsonl.bad-*"))
    assert len(backups) == 1


def test_quarantine_missing_file_is_noop(session_path: Path) -> None:
    assert quarantine_session_file(session_path) is None


@pytest.mark.anyio
async def test_message_end_subscription_off_switch(
    make_harness: Callable[[], AgentHarness], session_path: Path
) -> None:
    """退订后不再写入——attach 返回值就是 unsubscribe（契约与 harness.subscribe 一致）。"""
    harness = make_harness()
    unsubscribe = attach_session_storage(harness, JsonlSessionStorage(session_path))
    unsubscribe()
    async for _ in harness.prompt("创建角色波洛"):
        pass
    assert not session_path.exists()


# 确认 MessageEndEvent 是持久化挂点（防上游改名后静默失联）
def test_message_end_event_has_message_field() -> None:
    event = MessageEndEvent(message=UserMessage(content="x"))
    assert event.message.content == "x"


# ---------- T2：restore_and_attach 组合接线（CLI 与 server 共用） ----------


@pytest.mark.anyio
async def test_restore_and_attach_survives_restart_and_keeps_appending(
    tmp_path: Path, make_harness: Callable[[], AgentHarness]
) -> None:
    workspace = StoryWorkspace(root=tmp_path, slug="orient-express")

    # 第一轮：空会话启动 → 跑两回合 → 退出
    first = make_harness()
    await restore_and_attach(workspace, first)
    async for _ in first.prompt("创建角色波洛"):
        pass
    count_after_first = len(first.messages)
    assert count_after_first > 0

    # 第二轮（模拟重启）：消息恢复一致，且新对话继续追加进同一份 jsonl
    second = make_harness()
    await restore_and_attach(workspace, second)
    assert second.messages == first.messages
    async for _ in second.prompt("继续核对账本"):
        pass

    reloaded = await load_session_messages(session_file(workspace))
    assert len(reloaded.messages) > count_after_first
    assert reloaded.messages == second.messages


@pytest.mark.anyio
async def test_restore_and_attach_surfaces_corruption_warning(
    tmp_path: Path, make_harness: Callable[[], AgentHarness]
) -> None:
    workspace = StoryWorkspace(root=tmp_path, slug="orient-express")
    workspace.ensure_dirs()
    session_file(workspace).write_text("broken\n", encoding="utf-8")
    warnings: list[str] = []
    harness = make_harness()
    await restore_and_attach(workspace, harness, on_warning=warnings.append)
    assert len(warnings) == 1
    assert "备份" in warnings[0]
    assert harness.messages == ()
    # 告警之后 attach 仍然生效——以空会话继续正常写作
    async for _ in harness.prompt("创建角色波洛"):
        pass
    reloaded = await load_session_messages(session_file(workspace))
    assert len(reloaded.messages) == len(harness.messages)
