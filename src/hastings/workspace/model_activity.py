"""在应用边界记录实际模型请求，不修改便携 agent 层。"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

from hastings.workspace.activity import ActivityStore, redact
from tau_agent.messages import AgentMessage, AssistantMessage
from tau_agent.provider import CancellationToken, ModelProvider
from tau_agent.provider_events import AssistantDoneEvent, AssistantErrorEvent, AssistantMessageEvent
from tau_agent.tools import AgentTool


def _message(message: AgentMessage) -> dict[str, Any]:
    raw = message.model_dump(mode="json")
    content = raw.get("content")
    if isinstance(content, list):
        raw["content"] = [
            ({"type": "image", "omitted": True} if block.get("type") == "image" else block)
            for block in content
            if block.get("type") != "thinking"
        ]
    return dict(redact(raw))


def _chars(payload: object) -> int:
    """应用侧序列化字符数；只是可复算的估算，不冒充供应商 token。"""
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _request_size(
    system: str, messages: list[AgentMessage], definitions: list[dict[str, Any]]
) -> dict[str, object]:
    message_chars = sum(_chars(message.model_dump(mode="json")) for message in messages)
    tool_chars = _chars(definitions)
    return {
        "method": "json_char_count",
        "estimated": True,
        "system_chars": len(system),
        "message_chars": message_chars,
        "tool_chars": tool_chars,
        "total_chars": len(system) + message_chars + tool_chars,
    }


class RecordedProvider:
    def __init__(
        self,
        provider: ModelProvider,
        store: ActivityStore,
        emit: Callable[[str, dict[str, Any]], None],
        *,
        stage: str,
    ) -> None:
        self.provider, self.store, self.emit, self.stage = provider, store, emit, stage
        self.capture = store.settings()["capture_inputs"]

    async def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
        session_id: str | None = None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        operation = uuid.uuid4().hex
        definitions = [
            {"name": tool.name, "description": tool.description, "parameters": tool.parameters}
            for tool in tools
        ]
        snapshot = None
        if self.capture:
            snapshot = self.store.attachment(
                {
                    "model": model,
                    "system": system,
                    "messages": [_message(m) for m in messages],
                    "tools": definitions,
                }
            )
        common = {"operation_id": operation, "stage": self.stage, "model": model}
        self.emit(
            "model_request_started",
            {
                **common,
                "input_detail": snapshot,
                "system_chars": len(system),
                "message_count": len(messages),
                "tools": [t.name for t in tools],
                "request_size": _request_size(system, messages, definitions),
                "input_captured": self.capture,
            },
        )
        started = time.monotonic()
        first: int | None = None
        status, reason, usage, error = "interrupted", "", None, None
        partial = AssistantMessage(model=model)
        terminal = False
        try:
            source = self.provider.stream_response(
                model=model,
                system=system,
                messages=messages,
                tools=tools,
                signal=signal,
                session_id=session_id,
            )
            async for event in _interruptible_stream(source, signal):
                # 开始事件只有响应壳，不能算首个输出。
                if first is None and getattr(event, "type", "") in (
                    "text_delta",
                    "thinking_delta",
                    "toolcall_delta",
                    "text_end",
                    "thinking_end",
                    "toolcall_end",
                    "done",
                ):
                    first = int((time.monotonic() - started) * 1000)
                if isinstance(event, (AssistantDoneEvent, AssistantErrorEvent)):
                    terminal = True
                    message = (
                        event.message if isinstance(event, AssistantDoneEvent) else event.error
                    )
                    reason = message.stop_reason
                    status = (
                        "stopped"
                        if reason == "aborted"
                        else "failed"
                        if reason == "error"
                        else "truncated"
                        if reason == "length"
                        else "completed"
                    )
                    # 上游 Usage 的默认 0 不代表供应商确实提供了用量。
                    if message.usage.model_fields_set:
                        usage = message.usage.model_dump(mode="json", exclude={"cost"})
                    if message.error_message:
                        error = message.error_message
                else:
                    partial = event.partial
                yield event
            if signal is not None and signal.is_cancelled() and not terminal:
                reason, status = "aborted", "stopped"
                yield AssistantErrorEvent(
                    reason="aborted",
                    error=partial.model_copy(update={"stop_reason": "aborted"}),
                )
        except BaseException as exc:
            status = "stopped" if signal and signal.is_cancelled() else "failed"
            error = str(exc) or type(exc).__name__
            raise
        finally:
            if signal and signal.is_cancelled():
                status = "stopped"
            self.emit(
                "model_request_finished",
                {
                    **common,
                    "status": status,
                    "stop_reason": reason,
                    "usage": usage,
                    "message": error,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "first_output_ms": first,
                },
            )


async def _interruptible_stream(
    source: AsyncIterator[AssistantMessageEvent], signal: CancellationToken | None
) -> AsyncIterator[AssistantMessageEvent]:
    """只中断等待模型数据的任务；不取消可能正在保存内容的工具。

    协议令牌只有同步检查接口，因此至多每 50ms 检查一次。等待响应头、首段内容、
    后续流或供应商重试时，都不依赖下一段网络数据才能响应停止。
    """
    if signal is None:
        async for event in source:
            yield event
        return
    iterator = source.__aiter__()
    pending: asyncio.Future[AssistantMessageEvent] | None = None
    try:
        while not signal.is_cancelled():
            pending = asyncio.ensure_future(anext(iterator))
            while not pending.done():
                await asyncio.wait((pending,), timeout=0.05)
                if signal.is_cancelled():
                    return
            try:
                event = pending.result()
            except StopAsyncIteration:
                return
            pending = None
            if signal.is_cancelled():
                return
            yield event
    finally:
        if pending is not None:
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        close = getattr(iterator, "aclose", None)
        if close is not None:
            await close()
