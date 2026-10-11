"""会话持久化桥测试（P4 T1）。

JsonlSessionStorage 是 tau 现成组件，本模块验证三件事：
① Windows + asyncio 下文件锁探路（append/read_all 最小闭环）；
② attach 后 message_end 事件落盘、load 后消息历史一致（含 user 消息）；
③ 坏文件泄压阀：改名备份 + 中文指引，不静默丢。
P9 T5 追加 archive_session_file 归档语义测试。
"""

import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from hastings.bible.store import StoryWorkspace
from hastings.prompts import build_system_prompt
from hastings.session_persist import (
    archive_session_file,
    attach_session_storage,
    load_session_messages,
    quarantine_session_file,
    restore_and_attach,
    session_file,
)
from hastings.session_store import activate_session, active_session_id, create_session
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


# --- archive_session_file（P9 T5，D4）：归档保留、原文件删除、撞名加序号 ---


def test_archive_session_file_returns_none_when_missing(session_path: Path) -> None:
    assert archive_session_file(session_path) is None


def test_archive_session_file_copies_content_then_removes_original(session_path: Path) -> None:
    session_path.write_text("旧会话内容", encoding="utf-8")

    archived = archive_session_file(session_path)

    assert archived is not None
    assert re.fullmatch(r"session-\d{8}-\d{6}\.jsonl", archived.name) is not None
    assert archived.read_text(encoding="utf-8") == "旧会话内容"
    assert not session_path.exists()


def test_archive_session_file_same_second_collision_gets_suffix(
    session_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同秒内两次归档：第二份加 -1 序号，两份内容各自保留。"""

    class FrozenDateTime:
        @classmethod
        def now(cls) -> datetime:
            return datetime(2026, 1, 2, 3, 4, 5)

    monkeypatch.setattr("hastings.session_persist.datetime", FrozenDateTime)

    session_path.write_text("第一版", encoding="utf-8")
    first = archive_session_file(session_path)
    assert first is not None and first.name == "session-20260102-030405.jsonl"

    session_path.write_text("第二版", encoding="utf-8")
    second = archive_session_file(session_path)
    assert second is not None and second.name == "session-20260102-030405-1.jsonl"
    assert first.read_text(encoding="utf-8") == "第一版"
    assert second.read_text(encoding="utf-8") == "第二版"


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
async def test_abort_repair_messages_are_persisted(
    make_harness: Callable[[], AgentHarness], session_path: Path
) -> None:
    """abort 后 harness 合成的 toolResult + repair 消息同样走 message_end → 落盘
    （计划风险表第 2 行：message_end 之外的定稿路径不得漏记）。"""
    from tau_agent.events import ToolExecutionStartEvent

    harness = make_harness()

    def cancel_on_tool_start(event: Any) -> None:
        if isinstance(event, ToolExecutionStartEvent):
            harness.cancel()

    unsubscribe = harness.subscribe(cancel_on_tool_start)
    attach_session_storage(harness, JsonlSessionStorage(session_path))
    async for _ in harness.prompt("创建角色波洛"):
        pass
    unsubscribe()

    # 中止后：user → assistant(toolUse) → toolResult(错误) → repair assistant
    roles = [m.role for m in harness.messages]
    assert roles[0] == "user"
    assert "toolResult" in roles
    assert roles[-1] == "assistant"  # repair 定稿消息存在，不是停在半截 toolResult
    aborted = [m for m in harness.messages if m.role == "toolResult"]
    assert any("aborted" in str(m.content).lower() for m in aborted)

    # 且全部进 jsonl（与 harness 内存一致，重启可回放）
    entries = await JsonlSessionStorage(session_path).read_all()
    assert [e.message for e in entries] == list(harness.messages)


@pytest.mark.anyio
async def test_persist_io_failure_warns_and_does_not_kill_stream(
    make_harness: Callable[[], AgentHarness],
    session_path: Path,
    capsys: Any,
) -> None:
    """落盘 IO 故障（OSError）→ stderr 中文告警，写作流不中断（R3-⑥ 泄压阀）。"""

    class BrokenStorage:
        async def append(self, entry: MessageEntry) -> None:
            raise OSError("disk full")

    harness = make_harness()
    attach_session_storage(harness, BrokenStorage())  # type: ignore[arg-type]
    async for _ in harness.prompt("创建角色波洛"):
        pass

    # 写作流正常走完（FakeProvider 两回合都在），告警三要素上屏
    assert len(harness.messages) > 0
    err = capsys.readouterr().err
    assert "落盘失败" in err
    assert "disk full" in err
    assert "怎样算对" in err


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


@pytest.mark.anyio
async def test_restore_and_attach_switches_active_session(
    tmp_path: Path, make_harness: Callable[[], AgentHarness]
) -> None:
    """切换活跃会话：re-attach 换文件换历史，空会话不残留旧消息，旧会话保留可回看。"""
    workspace = StoryWorkspace(root=tmp_path, slug="orient-express")
    workspace.ensure_dirs()
    first = make_harness()
    unsubscribe = await restore_and_attach(workspace, first)
    async for _ in first.prompt("创建角色波洛"):
        pass
    assert len(first.messages) > 0
    first_messages = first.messages
    first_id = active_session_id(workspace)

    created = create_session(workspace)
    unsubscribe()  # 断开旧会话的存储/索引订阅
    second = make_harness()
    unsubscribe = await restore_and_attach(workspace, second)
    assert second.messages == ()  # 空会话：不得残留上一会话历史

    # 切回首会话：历史恢复（旧会话文件仍在）
    activate_session(workspace, first_id)
    unsubscribe()
    third = make_harness()
    await restore_and_attach(workspace, third)
    assert third.messages == first_messages
    assert created.id != first_id
