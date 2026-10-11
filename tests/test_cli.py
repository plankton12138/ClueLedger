"""CLI 装配测试（P8 T5 补齐）：build_harness 与 Web 端同一状态注入行为。

build_harness 是 CLI 冒烟入口的装配点——它必须与 HastingsSession.from_workspace
对齐：system prompt 都带「当前工作区状态」节，否则 CLI 走查观察到的模型行为
（协议遵循、阶段感知）与 Web 端不一致，冒烟结论不可信。
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from hastings.bible.store import StoryWorkspace, save_story_doc
from hastings.bible.story_state import StoryDoc
from hastings.cli import build_harness, compact_command_output, format_sessions, run_repl
from hastings.context_manager import CompactionResult
from hastings.events_store import EventStore, events_file
from hastings.prompts import STATUS_HEADING
from hastings.session_store import active_session_id, list_sessions, load_index
from tau_agent import AssistantMessage
from tau_agent.provider_events import (
    AssistantDoneEvent,
    AssistantStartEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
)
from tau_ai import FakeProvider


def test_build_harness_injects_workspace_status(tmp_path: Path) -> None:
    """CLI 装配的 system prompt 与 Web 端对齐：写作中工作区注入状态节。"""
    workspace = StoryWorkspace(root=tmp_path, slug="demo")
    save_story_doc(workspace, StoryDoc(slug="demo", stage="writing"))

    harness = build_harness(workspace, FakeProvider([]), "fake")

    assert STATUS_HEADING in harness.config.system
    assert "阶段：writing" in harness.config.system


# --- 事件持久化接线（F4 走查修复）：CLI/驱动链路也挂 events.jsonl ---


def _text_stream(text: str) -> Sequence[Any]:
    """纯文本 assistant 流（含一个 delta，用于验证 message_update 不入库）。"""
    return [
        AssistantStartEvent(partial=AssistantMessage(model="fake")),
        TextStartEvent(content_index=0, partial=AssistantMessage(model="fake")),
        TextDeltaEvent(
            content_index=0,
            delta=text,
            partial=AssistantMessage(content=text, model="fake"),
        ),
        TextEndEvent(content_index=0, content=text, partial=AssistantMessage(model="fake")),
        AssistantDoneEvent(reason="stop", message=AssistantMessage(content=text)),
    ]


@pytest.mark.anyio
async def test_build_harness_persists_events_jsonl(tmp_path: Path) -> None:
    """装配点挂 EventStore：CLI/驱动跑一轮后 events.jsonl 有全帧记录。

    走查发现（F4）：事件账本只挂在 Web 端 HastingsSession，CLI 冒烟与
    eval 驱动没有帧级账本，归因只能靠转录文本。装配点在 build_harness——
    run_repl 与 eval driver 都经它装配，一处接线两端生效。
    """
    workspace = StoryWorkspace(root=tmp_path, slug="demo")
    harness = build_harness(workspace, FakeProvider([_text_stream("收到。")]), "fake")

    async for _ in harness.prompt("你好"):
        pass

    page = await EventStore(events_file(workspace)).read()
    assert page.total > 0
    types = {record.type for record in page.records}
    assert "agent_start" in types
    assert "message_end" in types
    assert "agent_end" in types
    assert "message_update" not in types  # 流式中间态不入库（与 Web 端同一策略）


# --- /compact 命令输出（P10 T13，D12）：合法空结果 / 压缩统计 / running 拒绝 ---


@pytest.mark.anyio
async def test_compact_command_noop_reports_next_step(tmp_path: Path) -> None:
    """无可剥离内容 → 「无需压缩」正常文本 + 指明下一步（泄压阀合法空结果）。"""
    workspace = StoryWorkspace(root=tmp_path, slug="demo")
    harness = build_harness(workspace, FakeProvider([]), "fake")

    text = await compact_command_output(harness, workspace)

    assert "无需压缩" in text
    assert "怎样算对" in text


@pytest.mark.anyio
async def test_compact_command_reports_stats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """压缩发生 → 输出剥离章数、前后计数与归档文件名；manual 标志透传。"""
    workspace = StoryWorkspace(root=tmp_path, slug="demo")
    harness = build_harness(workspace, FakeProvider([]), "fake")
    seen: dict[str, bool] = {}

    async def fake_compact(
        harness: object, workspace: object, *, manual: bool = False
    ) -> CompactionResult:
        del harness, workspace
        seen["manual"] = manual
        return CompactionResult(
            True, "manual", 5000, 1200, (1, 2), True, "session-20260101-000000.jsonl"
        )

    monkeypatch.setattr("hastings.cli.compact_context", fake_compact)

    text = await compact_command_output(harness, workspace)

    assert seen["manual"] is True
    assert "剥离 2 章正文参数" in text
    assert "生成前情摘要" in text
    assert "5000 → 1200 token" in text
    assert "已归档 session-20260101-000000.jsonl" in text


@pytest.mark.anyio
async def test_compact_command_running_reports_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """手动压缩撞上写作流 → 「压缩被拒」+ 下一步指引。"""
    workspace = StoryWorkspace(root=tmp_path, slug="demo")
    harness = build_harness(workspace, FakeProvider([]), "fake")

    async def fake_compact(
        harness: object, workspace: object, *, manual: bool = False
    ) -> CompactionResult:
        del harness, workspace, manual
        return CompactionResult(False, "running", 0, 0)

    monkeypatch.setattr("hastings.cli.compact_context", fake_compact)

    text = await compact_command_output(harness, workspace)

    assert "压缩被拒" in text
    assert "写作流进行中" in text
    assert "怎样算对" in text


# --- 会话命令（P10 T14，D13）：/sessions 列出、/session 切换、/new 新建 ---


def test_format_sessions_marks_active(tmp_path: Path) -> None:
    workspace = StoryWorkspace(root=tmp_path, slug="demo")
    active = load_index(workspace).active_id

    text = format_sessions(workspace)

    assert "会话共 1 个" in text
    assert f"* {active}" in text


@pytest.mark.anyio
async def test_run_repl_new_session_command(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    """/new 新建会话：旧会话保留在列表，活跃切到新会话。"""
    workspace = StoryWorkspace(root=tmp_path, slug="demo")
    workspace.ensure_dirs()
    inputs = iter(["/sessions", "/new", "/quit"])
    monkeypatch.setattr("builtins.input", lambda *_: next(inputs))

    await run_repl(workspace, FakeProvider([]), "fake")

    out = capsys.readouterr().out
    assert "会话共 1 个" in out
    assert "已新建会话" in out
    assert len(list_sessions(workspace)) == 2


@pytest.mark.anyio
async def test_run_repl_session_switch_command(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    """/session <id> 切回旧会话：活跃标记随之更新。"""
    workspace = StoryWorkspace(root=tmp_path, slug="demo")
    workspace.ensure_dirs()
    first = load_index(workspace).active_id
    inputs = iter(["/new", f"/session {first}", "/sessions", "/quit"])
    monkeypatch.setattr("builtins.input", lambda *_: next(inputs))

    await run_repl(workspace, FakeProvider([]), "fake")

    out = capsys.readouterr().out
    assert f"已切换到会话 {first}" in out
    assert f"* {first}" in out
    assert active_session_id(workspace) == first
