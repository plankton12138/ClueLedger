"""在应用边界记录实际模型请求，不修改便携 agent 层。"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Mapping
from typing import Any

from hastings.server.model_limits import ModelLimits
from hastings.workspace.activity import ActivityStore, redact
from hastings.workspace.compaction import trim_request_messages
from hastings.workspace.context_usage import (
    budget_state,
    count_tokens,
    estimate_request,
    wire_overhead,
)
from hastings.workspace.errors import WorkspaceError
from tau_agent.messages import AgentMessage, AssistantMessage
from tau_agent.provider import CancellationToken, ModelProvider
from tau_agent.provider_events import AssistantDoneEvent, AssistantErrorEvent, AssistantMessageEvent
from tau_agent.tools import AgentTool

#: 供应商对「请求超出上下文」的常见措辞。只认明确指向长度/容量的句子，
#: 不把超时、限流或输出截断当成溢出，否则重试会白花一次费用。
_OVERFLOW_HINTS = (
    "context_length_exceeded",
    "maximum context length",
    "context window",
    "prompt is too long",
    "input is too long",
    "too many tokens",
    "reduce the length",
    "request too large",
    "token count exceeds",
    "上下文长度",
    "超出上下文",
    "token 数量",
)


def _is_context_overflow(message: str | None) -> bool:
    text = (message or "").lower()
    return bool(text) and any(hint in text for hint in _OVERFLOW_HINTS)


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


def _request_estimate(
    *,
    identity: str,
    system: str,
    messages: list[AgentMessage],
    definitions: list[dict[str, Any]],
    limits: ModelLimits | None,
    source_revisions: Mapping[str, str],
) -> dict[str, object]:
    """这一轮请求的 token 估算与容量判断。

    与字符数分开记：字符数只说明「装了多少字」，用它判断装不装得下会算错，
    而估算要给出的是与模型窗口可比的数字。
    """
    message_wire = json.dumps(
        [message.model_dump(mode="json") for message in messages],
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    tool_wire = json.dumps(definitions, ensure_ascii=False, separators=(",", ":"), default=str)
    estimate = estimate_request(
        model_identity=identity,
        system=system,
        tool_wire=tool_wire,
        message_wire=message_wire,
        source_revisions=source_revisions,
        protocol_tokens=wire_overhead(messages=len(messages), tools=len(definitions)),
        count=count_tokens,
    )
    return {
        "model": identity,
        "total_tokens": estimate.total,
        "system_tokens": estimate.system_tokens,
        "tool_tokens": estimate.tool_tokens,
        "message_tokens": estimate.message_tokens,
        "protocol_tokens": estimate.protocol_tokens,
        "message_count": len(messages),
        "tool_overhead_tokens": wire_overhead(messages=0, tools=len(definitions)),
        "method": estimate.method,
        "fingerprint": estimate.request_fingerprint,
        "budget": budget_state(total_tokens=estimate.total, limits=limits) if limits else "unknown",
        "context_window_tokens": limits.context_window_tokens if limits else None,
        "input_limit_tokens": limits.input_limit() if limits else None,
        "output_reserve_tokens": limits.output_reserve_tokens if limits else None,
        "safety_tokens": limits.safety_tokens if limits else None,
        # 容量出处：界面展开明细时显示，避免把目录值当成作者填的。
        "capacity_source": limits.source if limits else "unknown",
        "limits_revision": limits.revision if limits else "",
    }


class RecordedProvider:
    def __init__(
        self,
        provider: ModelProvider,
        store: ActivityStore,
        emit: Callable[[str, dict[str, Any]], None],
        *,
        stage: str,
        identity: str = "",
        limits: ModelLimits | None = None,
        source_revisions: Mapping[str, str] | None = None,
        keep_recent_tokens: int | None = None,
        enforce_budget: bool = False,
        before_request: Callable[
            [list[AgentMessage], str, list[dict[str, Any]], CancellationToken | None],
            Awaitable[list[AgentMessage]],
        ]
        | None = None,
    ) -> None:
        self.provider, self.store, self.emit, self.stage = provider, store, emit, stage
        #: 本轮实际的模型身份（连接:模型）；用于让估算只对同一模型成立。
        self.identity = identity
        #: 受理时冻结的容量；未知时预算只能是 unknown，不能按零处理。
        self.limits = limits
        #: 装配结果与资料版本，参与估算指纹。
        self.source_revisions = dict(source_revisions or {})
        #: 超预算时保留的近期规模；None 表示不裁剪（轮内裁剪由调用方开启）。
        self.keep_recent_tokens = keep_recent_tokens
        self.capture = store.settings()["capture_inputs"]
        self.before_request = before_request
        self.enforce_budget = enforce_budget

    def _trim_if_needed(
        self,
        messages: list[AgentMessage],
        system: str,
        definitions: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """超预算时裁掉较早的完整工具往返；不超预算或无法裁剪时返回 ``None``。

        窗口未知就不裁：那时没有可比的边界，裁多裁少都是猜。
        """
        if self.keep_recent_tokens is None or self.limits is None:
            return None
        limit = self.limits.input_limit()
        if limit is None:
            return None
        before = _request_estimate(
            identity=self.identity,
            system=system,
            messages=messages,
            definitions=definitions,
            limits=self.limits,
            source_revisions=self.source_revisions,
        )
        total = before["total_tokens"]
        if not isinstance(total, int) or total <= limit:
            return None
        trimmed = trim_request_messages(messages, keep_recent_tokens=self.keep_recent_tokens)
        if trimmed is None:
            return None
        after = _request_estimate(
            identity=self.identity,
            system=system,
            messages=trimmed,
            definitions=definitions,
            limits=self.limits,
            source_revisions=self.source_revisions,
        )
        return {
            "messages": trimmed,
            "estimate": after,
            "dropped_messages": len(messages) - len(trimmed),
            "tokens_before": before["total_tokens"],
            "tokens_after": after["total_tokens"],
        }

    def _overflow_retry(
        self,
        messages: list[AgentMessage],
        working: list[AgentMessage],
        system: str,
        definitions: list[dict[str, Any]],
        operation: str,
        error_text: str | None,
    ) -> list[AgentMessage] | None:
        """供应商明确拒绝上下文时，保留更少的近期消息并重发一次。

        重试只收紧一次：若连最新一轮也放不下，就没有余地，错误照常上抛，避免用无限
        重试掩盖真实的容量问题。窗口未知也可以重试——拒绝本身就是「装不下」的证据，
        不需要先猜到确切容量。
        """
        if self.keep_recent_tokens is None:
            return None
        keep = max(1000, self.keep_recent_tokens // 2)
        trimmed = trim_request_messages(messages, keep_recent_tokens=keep)
        if trimmed is None or len(trimmed) >= len(working):
            return None
        before = _request_estimate(
            identity=self.identity,
            system=system,
            messages=working,
            definitions=definitions,
            limits=self.limits,
            source_revisions=self.source_revisions,
        )
        after = _request_estimate(
            identity=self.identity,
            system=system,
            messages=trimmed,
            definitions=definitions,
            limits=self.limits,
            source_revisions=self.source_revisions,
        )
        self.emit(
            "model_request_retried",
            {
                "operation_id": operation,
                "reason": "context_overflow",
                "message": (error_text or "")[:300],
                "tokens_before": before["total_tokens"],
                "tokens_after": after["total_tokens"],
                "dropped_messages": len(messages) - len(trimmed),
                "stage": self.stage,
                "request_estimate": after,
            },
        )
        return trimmed

    async def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
        session_id: str | None = None,
    ) -> AsyncGenerator[AssistantMessageEvent, None]:
        operation = uuid.uuid4().hex
        definitions = [
            {"name": tool.name, "description": tool.description, "parameters": tool.parameters}
            for tool in tools
        ]
        # 只在请求副本上裁剪：harness 手里的消息列表保持原样，下一轮重新判断。
        working = list(messages)
        if self.before_request is not None:
            working = await self.before_request(working, system, definitions, signal)
        estimate = _request_estimate(
            identity=self.identity or model,
            system=system,
            messages=working,
            definitions=definitions,
            limits=self.limits,
            source_revisions=self.source_revisions,
        )
        trim = self._trim_if_needed(working, system, definitions)
        if trim is not None:
            working, estimate = trim.pop("messages"), trim.pop("estimate")
        limit = self.limits.input_limit() if self.limits else None
        if (
            (self.before_request is not None or self.enforce_budget)
            and limit is not None
            and isinstance(estimate["total_tokens"], int)
            and estimate["total_tokens"] > limit
        ):
            raise WorkspaceError(
                code="CONTEXT_REQUIRED_INPUT_TOO_LARGE",
                where="conversation.context",
                why="当前请求与必要工具结果超过模型输入预算",
                fix="缩小本次处理范围，或选择容量更大的模型；已保存的改动仍然保留",
            )
        snapshot = None
        if self.capture:
            snapshot = self.store.attachment(
                {
                    "model": model,
                    "system": system,
                    "messages": [_message(m) for m in working],
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
                "request_size": _request_size(system, working, definitions),
                "request_estimate": estimate,
                **({"request_trim": trim} if trim else {}),
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
                messages=working,
                tools=tools,
                signal=signal,
                session_id=session_id,
            )
            retried = False
            while True:
                retry = False
                async for event in _interruptible_stream(source, signal):
                    # 溢出只可能发生在首个内容之前；已经输出过内容就不再重试，
                    # 否则调用方会收到两段互相矛盾的半成品。
                    if (
                        not retried
                        and first is None
                        and isinstance(event, AssistantErrorEvent)
                        and event.reason == "error"
                        and _is_context_overflow(event.error.error_message)
                    ):
                        trimmed = self._overflow_retry(
                            working,
                            working,
                            system,
                            definitions,
                            operation,
                            event.error.error_message,
                        )
                        if trimmed is not None:
                            retried = True
                            retry = True
                            working = trimmed
                            break
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
                    if isinstance(event, AssistantDoneEvent | AssistantErrorEvent):
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
                if not retry:
                    break
                source = self.provider.stream_response(
                    model=model,
                    system=system,
                    messages=working,
                    tools=tools,
                    signal=signal,
                    session_id=session_id,
                )
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
