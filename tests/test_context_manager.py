"""上下文估算与两档压缩测试（P10 T8，D8/L9）。

分层：① 估算器纯函数（中文换算 / usage 锚定 / 字符 fallback / 阈值）；
② 第一档剥离纯函数（keep-window 边界 / tool_call_id 配对 / 结果消息不动）；
③ 两档压缩端到端（摘要成功 → 替换 + 归档 + 事件；摘要失败 → 泄压阀）；
④ 同步剥离 strip_oversized_context（内存态、不落盘）。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from hastings.bible.store import StoryWorkspace
from hastings.context_manager import (
    COMPACTION_SUMMARY_PREFIX,
    CompactionResult,
    build_token_usage,
    compact_context,
    compaction_threshold,
    count_context_tokens,
    estimate_message_tokens,
    estimate_text_tokens,
    provider_context_tokens,
    record_token_usage,
    strip_chapter_args,
    strip_oversized_context,
)
from hastings.events_store import EventStore, events_file
from hastings.session_persist import load_session_messages, session_file
from hastings.settings import StorySettings, save_settings
from tau_agent import AgentHarness, AgentHarnessConfig, AssistantMessage
from tau_agent.messages import (
    AgentMessage,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from tau_agent.provider_events import TextDeltaEvent
from tau_agent.session import MessageEntry
from tau_agent.session.jsonl import entry_to_json_line
from tau_ai import FakeProvider


def _harness(
    messages: Sequence[AgentMessage] = (),
    *,
    provider: FakeProvider | None = None,
) -> AgentHarness:
    return AgentHarness(
        AgentHarnessConfig(
            provider=provider if provider is not None else FakeProvider([]),
            model="fake",
            system="",
            tools=[],
        ),
        messages=messages,
    )


def _workspace(tmp_path: Path, *, window: int = 128_000) -> StoryWorkspace:
    workspace = StoryWorkspace(root=tmp_path, slug="demo")
    save_settings(workspace, StorySettings(context_window_tokens=window))
    return workspace


def _write_message(chapter: int, content: str, call_id: str = "c1") -> AssistantMessage:
    return AssistantMessage(
        model="fake",
        content=[
            ToolCall(
                id=call_id, name="write_chapter", arguments={"chapter": chapter, "content": content}
            )
        ],
    )


def _summary_stream(text: str) -> list[Any]:
    return [
        TextDeltaEvent(
            content_index=0,
            delta=text,
            partial=AssistantMessage(content=text, model="fake"),
        )
    ]


# ---------- ① 估算器 ----------


def test_estimate_text_tokens_chinese_calibration() -> None:
    assert estimate_text_tokens("") == 0
    assert estimate_text_tokens("a") == 1  # 向上取整，下限 1
    assert estimate_text_tokens("abcd") == 2  # 4 字符 / 2
    assert estimate_text_tokens("中文中文") == 2  # 4 字符 / 2
    assert estimate_text_tokens("正" * 100) == 50


def test_provider_context_tokens_prefers_total_then_sum() -> None:
    assert (
        provider_context_tokens(AssistantMessage(model="m", usage=Usage(total_tokens=123))) == 123
    )
    assert (
        provider_context_tokens(AssistantMessage(model="m", usage=Usage(input=10, output=5))) == 15
    )


def test_count_context_tokens_anchors_on_provider_usage() -> None:
    anchor = AssistantMessage(model="m", usage=Usage(total_tokens=1000))
    trailing = UserMessage(content="续写" * 10)
    estimate = count_context_tokens(system="", messages=(anchor, trailing), tools=())

    assert estimate.uses_provider_usage is True
    assert estimate.source == "provider_usage"
    assert estimate.provider_tokens == 1000
    assert estimate.trailing_tokens == estimate_message_tokens(trailing)
    assert estimate.total_tokens == 1000 + estimate_message_tokens(trailing)


def test_count_context_tokens_falls_back_to_chars() -> None:
    messages = (UserMessage(content="正" * 200),)
    estimate = count_context_tokens(system="s" * 100, messages=messages, tools=())

    assert estimate.uses_provider_usage is False
    assert estimate.source == "chars"
    assert estimate.total_tokens == estimate_text_tokens("s" * 100) + estimate_message_tokens(
        messages[0]
    )


def test_build_token_usage_reads_usage_and_window(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, window=32_000)
    assistant = AssistantMessage(
        model="m", content="正文", usage=Usage(input=100, output=40, total_tokens=140)
    )
    harness = _harness((UserMessage(content="写"), assistant))

    usage = build_token_usage(harness, workspace)

    assert usage.window_tokens == 32_000
    assert usage.input_tokens == 100
    assert usage.output_tokens == 40
    assert usage.total_tokens == 140
    # context_tokens 与唯一计数源一致
    assert (
        usage.context_tokens
        == count_context_tokens(
            system=harness.config.system, messages=harness.messages, tools=()
        ).total_tokens
    )


@pytest.mark.anyio
async def test_record_token_usage_appends_event(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    assistant = AssistantMessage(model="m", content="hi", usage=Usage(input=5, output=3))
    harness = _harness((assistant,))
    store = EventStore(events_file(workspace))

    usage = await record_token_usage(store, harness, workspace)

    assert usage.total_tokens == 8
    page = await store.read()
    assert page.records[0].type == "token_usage"
    assert page.records[0].data["input"] == 5
    assert page.records[0].data["context_tokens"] == usage.context_tokens


def test_compaction_threshold_reserves_headroom() -> None:
    assert compaction_threshold(0) is None  # 未配置 → 不触发
    assert compaction_threshold(-5) is None
    assert compaction_threshold(32_768) == 32_768 - 16_384
    assert compaction_threshold(100) == 1  # 小窗口下限恒为 1


# ---------- ② 第一档剥离（纯函数） ----------


def test_strip_respects_keep_window_boundary() -> None:
    write = _write_message(1, "正文" * 50, call_id="c1")
    result = ToolResultMessage(tool_call_id="c1", tool_name="write_chapter", content="已写入")
    recent = UserMessage(content="继续写第2章")
    messages = (write, result, recent)

    # keep-window 覆盖全部 → 不剥离，序列原样
    keep_all = strip_chapter_args(messages, keep_recent_tokens=10_000_000)
    assert keep_all.stripped_chapters == ()
    assert keep_all.messages == messages

    # keep-window 只留尾部 → 早前 write_chapter 正文被替换为占位符
    stripped = strip_chapter_args(messages, keep_recent_tokens=1)
    assert stripped.stripped_chapters == (1,)


def test_strip_preserves_tool_call_id_pairing() -> None:
    write = _write_message(3, "正文" * 50, call_id="call-42")
    result = ToolResultMessage(tool_call_id="call-42", tool_name="write_chapter", content="已写入")
    messages = (write, result)

    stripped = strip_chapter_args(messages, keep_recent_tokens=1)

    assistant = stripped.messages[0]
    assert isinstance(assistant, AssistantMessage)
    call = assistant.tool_calls[0]
    assert call.id == "call-42"  # id 保留 → call/result 配对不破
    assert call.arguments["chapter"] == 3
    assert call.arguments["content"] != "正文" * 50
    assert "已落盘" in str(call.arguments["content"])
    assert "read_chapter" in str(call.arguments["content"])

    # 紧跟的结果消息原样保留，配对 id 未变
    result_after = stripped.messages[1]
    assert isinstance(result_after, ToolResultMessage)
    assert result_after.tool_call_id == "call-42"


def test_strip_reduces_estimate_for_chinese_body() -> None:
    write = _write_message(1, "正文" * 500)
    messages = (write,)

    before = estimate_message_tokens(write)
    after = estimate_message_tokens(strip_chapter_args(messages, keep_recent_tokens=1).messages[0])

    assert after < before  # 长中文正文替换为占位符后估算显著下降


def test_strip_ignores_non_write_tool_calls() -> None:
    other = AssistantMessage(
        model="fake",
        content=[ToolCall(id="c9", name="read_chapter", arguments={"chapter": 1})],
    )
    stripped = strip_chapter_args((other,), keep_recent_tokens=1)

    assert stripped.stripped_chapters == ()
    assert stripped.messages == (other,)


# ---------- ③ 两档压缩端到端 ----------


@pytest.mark.anyio
async def test_compact_below_threshold_is_noop(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, window=128_000)
    harness = _harness((UserMessage(content="你好"),))

    result = await compact_context(harness, workspace)

    assert result.compacted is False
    assert result.reason == "below_threshold"
    assert len(harness.messages) == 1


@pytest.mark.anyio
async def test_summary_failure_is_relief_valve(tmp_path: Path, capsys: Any) -> None:
    workspace = _workspace(tmp_path, window=20_000)  # 阈值 3616
    big = UserMessage(content="正" * 8000)  # 约 4004 token > 阈值
    harness = _harness((big,), provider=FakeProvider([]))  # 摘要返回空流

    result = await compact_context(harness, workspace)

    assert result.compacted is False
    assert result.reason == "summary_failed"
    assert harness.messages == (big,)  # 未替换、写作流继续
    err = capsys.readouterr().err
    assert "为什么错" in err and "怎样算对" in err  # 三要素告警
    assert not session_file(workspace).exists()  # 失败不落盘


@pytest.mark.anyio
async def test_manual_compact_with_nothing_to_strip(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, window=128_000)
    harness = _harness((UserMessage(content="短消息"),))

    result = await compact_context(harness, workspace, manual=True)

    assert result.compacted is False
    assert result.reason == "nothing_to_strip"


@pytest.mark.anyio
async def test_compact_summarizes_persists_and_archives(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, window=20_000)  # 阈值 3616
    workspace.ensure_dirs()
    old = UserMessage(content="旧会话")
    session_file(workspace).write_text(
        entry_to_json_line(MessageEntry(message=old)), encoding="utf-8"
    )

    big = UserMessage(content="正" * 8000)
    provider = FakeProvider([_summary_stream("## 作品与阶段\n[暴雨山庄]")])
    harness = _harness((big,), provider=provider)

    result = await compact_context(harness, workspace)

    assert isinstance(result, CompactionResult)
    assert result.compacted is True
    assert result.reason == "auto"
    assert result.summarized is True
    assert result.archived is not None
    # 归档保留全量历史（P10 D13 后活跃会话文件在 sessions/ 下，归档为同目录兄弟）
    assert (session_file(workspace).parent / result.archived).exists()

    # 压缩后 = 摘要 user 消息 + keep-recent 窗口
    assert len(harness.messages) == 2
    head = harness.messages[0]
    assert isinstance(head, UserMessage)
    assert head.text.startswith(COMPACTION_SUMMARY_PREFIX)
    assert harness.messages[1] == big

    # 重连恢复即压缩后形态
    reloaded = await load_session_messages(session_file(workspace))
    assert reloaded.messages == harness.messages

    # 压缩事件落账（可观测性是事件契约的一部分）
    page = await EventStore(events_file(workspace)).read()
    assert any(record.type == "session_compact" for record in page.records)


# ---------- ④ 同步剥离（刷新点内存态，不落盘） ----------


def test_strip_oversized_context_strips_in_memory(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, window=20_000)
    content = "正" * 50_000  # 约 25000 token，超过 keep-window 20000
    harness = _harness((_write_message(3, content),))

    assert strip_oversized_context(harness, workspace) is True

    after = harness.messages[0]
    assert isinstance(after, AssistantMessage)
    call = after.tool_calls[0]
    assert call.id == "c1"
    assert call.arguments["content"] != content
    assert "已落盘" in str(call.arguments["content"])
    assert not session_file(workspace).exists()  # 同步剥离不落盘


def test_strip_oversized_context_silent_below_threshold(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, window=128_000)
    harness = _harness((UserMessage(content="短"),))

    assert strip_oversized_context(harness, workspace) is False
