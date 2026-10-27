"""0.9 C2：轮内请求裁剪。

轮内压缩不调用摘要模型——它只丢弃较早的工具往返，最近一轮与当前请求必须原样保留。
切点只能落在完整单元边界上：assistant 的每个工具调用都要带着它的结果一起走，
否则供应商会收到「有调用没有结果」的残缺请求。
"""

from __future__ import annotations

import pytest

from hastings.workspace.compaction import trim_request_messages
from tau_agent.messages import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)


def _call(call_id: str, name: str = "read_file") -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text="读一下"), ToolCall(id=call_id, name=name, arguments={})],
        stop_reason="toolUse",
    )


def _result(call_id: str, text: str = "正文" * 100) -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id=call_id, tool_name="read_file", content=[TextContent(text=text)]
    )


def _history() -> list:
    return [
        UserMessage(content="写第一章"),
        _call("c1"),
        _result("c1"),
        _call("c2"),
        _result("c2"),
        _call("c3"),
        _result("c3"),
        UserMessage(content="现在改成第三人称"),
    ]


def test_trims_older_units_and_keeps_the_latest() -> None:
    trimmed = trim_request_messages(_history(), keep_recent_tokens=50)

    assert trimmed is not None
    assert trimmed[-1].role == "user"
    # 最后一次工具往返一定保留：那是当前请求的上下文。
    assert any(getattr(message, "tool_call_id", "") == "c3" for message in trimmed)


def test_never_leaves_a_call_without_its_result() -> None:
    trimmed = trim_request_messages(_history(), keep_recent_tokens=50)

    assert trimmed is not None
    calls = {
        block.id
        for message in trimmed
        if isinstance(message, AssistantMessage)
        for block in message.tool_calls
    }
    results = {
        message.tool_call_id for message in trimmed if isinstance(message, ToolResultMessage)
    }
    assert calls == results


def test_incomplete_unit_blocks_trimming() -> None:
    """只有调用没有结果时不能裁：宁可超限，也不发一份残缺请求。"""
    broken = [
        UserMessage(content="写第一章"),
        _call("c1"),
        _call("c2"),
        _result("c1"),
        UserMessage(content="继续"),
    ]

    assert trim_request_messages(broken, keep_recent_tokens=1) is None


def test_orphan_result_blocks_trimming() -> None:
    orphan = [
        UserMessage(content="写第一章"),
        _result("c9"),
        UserMessage(content="继续"),
    ]

    assert trim_request_messages(orphan, keep_recent_tokens=1) is None


def test_nothing_to_trim_when_the_budget_is_large() -> None:
    assert trim_request_messages(_history(), keep_recent_tokens=100_000) is None


def test_single_unit_is_left_alone() -> None:
    assert trim_request_messages([UserMessage(content="只写一句")], keep_recent_tokens=1) is None


def test_current_author_prompt_survives_many_tool_units() -> None:
    prompt = UserMessage(content="只修改第一章，保留第一人称")
    messages = [prompt, _call("old"), _result("old"), _call("new"), _result("new")]
    trimmed = trim_request_messages(messages, keep_recent_tokens=1)
    assert trimmed is not None
    assert trimmed[0] is prompt
    assert {getattr(m, "tool_call_id", "") for m in trimmed} >= {"new"}
    assert not any(getattr(m, "tool_call_id", "") == "old" for m in trimmed)


def test_large_current_prompt_does_not_discard_latest_tool_evidence() -> None:
    messages = [
        _call("old"),
        _result("old"),
        _call("new"),
        _result("new"),
        UserMessage(content="只做讨论" * 1000),
    ]
    trimmed = trim_request_messages(messages, keep_recent_tokens=1)
    assert trimmed is not None
    assert any(getattr(m, "tool_call_id", "") == "new" for m in trimmed)


@pytest.mark.parametrize("keep", [0, -1])
def test_non_positive_budget_does_nothing(keep: int) -> None:
    assert trim_request_messages(_history(), keep_recent_tokens=keep) is None
