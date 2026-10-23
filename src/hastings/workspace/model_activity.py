"""在应用边界记录实际模型请求，不修改便携 agent 层。"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

from hastings.workspace.activity import ActivityStore, redact
from tau_agent.messages import AgentMessage
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
                "input_captured": self.capture,
            },
        )
        started = time.monotonic()
        first: int | None = None
        status, reason, usage, error = "interrupted", "", None, None
        try:
            async for event in self.provider.stream_response(
                model=model,
                system=system,
                messages=messages,
                tools=tools,
                signal=signal,
                session_id=session_id,
            ):
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
                yield event
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
