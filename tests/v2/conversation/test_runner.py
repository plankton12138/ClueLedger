"""ChatRunner 端口契约测试（Phase 19.3 T19.3c）。

Provider 流式适配的行为钉子：文本增量透传、内部推理不下发（计划 §19.3「不显示
内部推理文本」）、取消即停（服务端取消 token，不只是断开）、输出预算截断、
失败分类（空文本 / 授权失败 / 连接失败 / 超时各自有稳定 code）。

用可控 FakeProvider 驱动 ``stream_response`` 事件序列，不访问网络。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from hastings.application.errors import EnvironmentFailure, HastingsError
from hastings.application.ports.chat import ChatRun
from hastings.domain.conversation import error_codes as ec
from hastings.infrastructure.conversations.runner import (
    ProviderChatRunner,
    TurnCancellationToken,
)
from tau_agent.messages import AssistantMessage
from tau_agent.provider_events import (
    AssistantDoneEvent,
    AssistantErrorEvent,
    AssistantStartEvent,
    TextDeltaEvent,
    TextStartEvent,
    ThinkingDeltaEvent,
    ThinkingStartEvent,
)


class FakeProvider:
    """按脚本吐事件；``events`` 为 (延迟秒, 事件) 序列。"""

    def __init__(self, events: list[tuple[float, Any]] | None = None) -> None:
        self._events = events or []
        self.closed = False

    def stream_response(self, **kwargs: Any) -> Any:
        async def iterator() -> Any:
            for delay, event in self._events:
                if delay:
                    await asyncio.sleep(delay)
                yield event

        return iterator()

    async def aclose(self) -> None:
        self.closed = True


def _run(**kwargs: Any) -> ChatRun:
    defaults: dict[str, Any] = {
        "model": "test-model",
        "system": "你是写作伙伴。",
        "history": (("author", "你好"), ("assistant", "在")),
        "cancelled": lambda: False,
    }
    defaults.update(kwargs)
    return ChatRun(**defaults)


def _text_events(*deltas: str) -> list[tuple[float, Any]]:
    events: list[tuple[float, Any]] = [(0, AssistantStartEvent(partial=AssistantMessage()))]
    first = True
    for delta in deltas:
        if first:
            events.append((0, TextStartEvent(content_index=0, partial=AssistantMessage())))
            first = False
        events.append(
            (0, TextDeltaEvent(content_index=0, delta=delta, partial=AssistantMessage()))
        )
    events.append(
        (0, AssistantDoneEvent(reason="stop", message=AssistantMessage()))
    )
    return events


@pytest.mark.anyio
async def test_streams_text_deltas_in_order_and_returns_final_text() -> None:
    provider = FakeProvider(_text_events("你好", "，作者", "。"))
    runner = ProviderChatRunner(lambda: provider)
    seen: list[str] = []

    async def sink(delta: str) -> None:
        seen.append(delta)

    final = await runner.stream(_run(), sink)
    assert final == "你好，作者。"
    assert seen == ["你好", "，作者", "。"]


@pytest.mark.anyio
async def test_thinking_deltas_never_reach_the_sink() -> None:
    events = [
        (0, AssistantStartEvent(partial=AssistantMessage())),
        (0, ThinkingStartEvent(content_index=0, partial=AssistantMessage())),
        (0, ThinkingDeltaEvent(content_index=0, delta="内部推理", partial=AssistantMessage())),
        (0, TextStartEvent(content_index=1, partial=AssistantMessage())),
        (0, TextDeltaEvent(content_index=1, delta="正文", partial=AssistantMessage())),
        (0, AssistantDoneEvent(reason="stop", message=AssistantMessage())),
    ]
    provider = FakeProvider(events)
    runner = ProviderChatRunner(lambda: provider)
    seen: list[str] = []

    async def sink(delta: str) -> None:
        seen.append(delta)

    final = await runner.stream(_run(), sink)
    assert seen == ["正文"]
    assert final == "正文"


@pytest.mark.anyio
async def test_cancellation_token_stops_the_stream_and_returns_partial_text() -> None:
    events = _text_events("第一段") + [(0.3, TextDeltaEvent(content_index=0, delta="迟到分片",
                                                           partial=AssistantMessage()))]
    provider = FakeProvider(events)
    token = TurnCancellationToken()
    runner = ProviderChatRunner(lambda: provider)

    async def sink(delta: str) -> None:
        if delta == "第一段":
            token.cancel()

    final = await runner.stream(_run(cancelled=token.is_cancelled), sink)
    assert final == "第一段"


@pytest.mark.anyio
async def test_empty_completion_is_a_distinct_failure() -> None:
    provider = FakeProvider([(0, AssistantDoneEvent(reason="stop", message=AssistantMessage()))])
    runner = ProviderChatRunner(lambda: provider)

    async def sink(delta: str) -> None:
        del delta

    with pytest.raises(HastingsError) as excinfo:
        await runner.stream(_run(), sink)
    assert excinfo.value.code == ec.CHAT_EMPTY_COMPLETION
    assert excinfo.value.why
    assert excinfo.value.fix


@pytest.mark.anyio
async def test_provider_error_event_is_classified_as_provider_failure() -> None:
    provider = FakeProvider(
        [(0, AssistantErrorEvent(reason="error", error=AssistantMessage()))]
    )
    runner = ProviderChatRunner(lambda: provider)

    async def sink(delta: str) -> None:
        del delta

    with pytest.raises(HastingsError) as excinfo:
        await runner.stream(_run(), sink)
    assert excinfo.value.code == ec.CHAT_PROVIDER_FAILED


@pytest.mark.anyio
async def test_connection_error_is_classified_as_unavailable() -> None:
    class BrokenProvider(FakeProvider):
        def stream_response(self, **kwargs: Any) -> Any:
            async def iterator() -> Any:
                raise OSError("connection refused")
                yield  # pragma: no cover

            return iterator()

    runner = ProviderChatRunner(lambda: BrokenProvider())

    async def sink(delta: str) -> None:
        del delta

    with pytest.raises(HastingsError) as excinfo:
        await runner.stream(_run(), sink)
    assert excinfo.value.code == ec.CHAT_PROVIDER_UNAVAILABLE


@pytest.mark.anyio
async def test_total_timeout_fails_with_turn_timeout() -> None:
    events = [(0.5, TextDeltaEvent(content_index=0, delta="慢", partial=AssistantMessage()))]
    provider = FakeProvider(events)
    runner = ProviderChatRunner(lambda: provider, total_timeout=0.05)

    async def sink(delta: str) -> None:
        del delta

    with pytest.raises(HastingsError) as excinfo:
        await runner.stream(_run(), sink)
    assert excinfo.value.code == ec.CHAT_TURN_TIMEOUT


@pytest.mark.anyio
async def test_first_token_timeout_is_separate_from_total_timeout() -> None:
    # 总预算足够，但首段迟迟不来：单独的首段等待上限先触发。
    events = [(1.0, TextDeltaEvent(content_index=0, delta="迟到", partial=AssistantMessage()))]
    provider = FakeProvider(events)
    runner = ProviderChatRunner(lambda: provider, first_token_timeout=0.05, total_timeout=30)

    async def sink(delta: str) -> None:
        del delta

    with pytest.raises(HastingsError) as excinfo:
        await runner.stream(_run(), sink)
    assert excinfo.value.code == ec.CHAT_FIRST_TOKEN_TIMEOUT


@pytest.mark.anyio
async def test_output_budget_truncates_and_stops_the_stream() -> None:
    provider = FakeProvider(_text_events("甲" * 30, "乙" * 30))
    runner = ProviderChatRunner(lambda: provider, max_output_chars=40)
    seen: list[str] = []

    async def sink(delta: str) -> None:
        seen.append(delta)

    final = await runner.stream(_run(), sink)
    assert len(final) == 40
    assert final == "甲" * 30 + "乙" * 10
    assert "".join(seen) == final


@pytest.mark.anyio
async def test_history_is_sent_as_user_and_assistant_messages() -> None:
    captured: dict[str, Any] = {}

    class CapturingProvider(FakeProvider):
        def stream_response(self, **kwargs: Any) -> Any:
            captured.update(kwargs)
            return super().stream_response(**kwargs)

    provider = CapturingProvider(_text_events("好"))
    runner = ProviderChatRunner(lambda: provider)

    async def sink(delta: str) -> None:
        del delta

    await runner.stream(_run(), sink)
    messages = captured["messages"]
    assert [type(m).__name__ for m in messages] == ["UserMessage", "AssistantMessage"]
    assert messages[0].content == "你好"
    assert isinstance(messages[1].content, list)
    assert messages[1].content[0].text == "在"
    assert captured["model"] == "test-model"
    assert captured["system"] == "你是写作伙伴。"
    assert captured["tools"] == []


@pytest.mark.anyio
async def test_refs_are_appended_to_the_last_user_turn() -> None:
    captured: dict[str, Any] = {}

    class CapturingProvider(FakeProvider):
        def stream_response(self, **kwargs: Any) -> Any:
            captured.update(kwargs)
            return super().stream_response(**kwargs)

    provider = CapturingProvider(_text_events("好"))
    runner = ProviderChatRunner(lambda: provider)
    run = _run(
        history=(("author", "结合这段讨论"),),
        refs=("[未保存摘录] 主角在雨夜相遇",),
    )

    async def sink(delta: str) -> None:
        del delta

    await runner.stream(run, sink)
    content = captured["messages"][-1].content
    assert isinstance(content, str)
    assert "结合这段讨论" in content
    assert "引用材料" in content
    assert "未保存摘录" in content


def test_cancellation_token_is_a_cancellationToken() -> None:
    token = TurnCancellationToken()
    assert not token.is_cancelled()
    token.cancel()
    assert token.is_cancelled()


@pytest.mark.anyio
async def test_timeout_raises_environment_failure() -> None:
    events = [
        (0.5, TextDeltaEvent(content_index=0, delta="慢", partial=AssistantMessage()))
    ]
    provider = FakeProvider(events)
    runner = ProviderChatRunner(lambda: provider, total_timeout=0.05)

    async def sink(delta: str) -> None:
        del delta

    with pytest.raises(EnvironmentFailure):
        await runner.stream(_run(), sink)
