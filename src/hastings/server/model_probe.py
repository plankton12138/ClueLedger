"""模型列表读取与连接测试（V04-9B）。

三件事彼此独立，界面必须分开显示：

- **模型发现**：读供应商的列表接口。成功、合法空列表、没有该接口、认证失败、限流、
  网络故障是不同事实——把「key 不对」显示成「这家没有模型」会让作者去改一个没问题的
  地址。
- **响应测试**：真的发一句短请求，要求返回非空文本。``/models`` 返回 200 不能代替
  这一步：列表能读到、模型调不通是最常见的一种情况。
- **工具测试**：提供一个只回固定值的 ``connection_probe`` 工具，验证「模型发出工具
  调用 → 结果回传 → 最终回复」的完整往返。没发起工具调用记为本次未通过，不上升为
  「该模型永远不支持工具」。

请求都用短生命周期实例，结束即关闭；不开额外重试——一次点击不该悄悄产生多次付费调用。
测试不读作品、不写文件、不建 Git：它们只回答「这条连接现在能不能用」。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

import httpx

from hastings.server.provider_runtime import (
    ManagedModelProvider,
    ProfileLike,
    build_provider,
    is_loopback_url,
)
from tau_agent.messages import (
    AgentMessage,
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from tau_agent.tools import AgentTool, AgentToolResult
from tau_ai.events import AssistantDoneEvent, AssistantErrorEvent, TextDeltaEvent

__all__ = [
    "DISCOVERY_TIMEOUT_SECONDS",
    "PROBE_TOOL_NAME",
    "RESPONSE_TIMEOUT_SECONDS",
    "TOOL_TIMEOUT_SECONDS",
    "DiscoveryResult",
    "ProbeResult",
    "discover_models",
    "probe_response",
    "probe_tool",
]

DISCOVERY_TIMEOUT_SECONDS = 15.0
RESPONSE_TIMEOUT_SECONDS = 30.0
TOOL_TIMEOUT_SECONDS = 60.0
#: 分页上限：列表接口理论上可以无限翻页，作者的一次点击必须有终点。
MAX_PAGES = 10
PAGE_SIZE = 100

PROBE_TOOL_NAME = "connection_probe"
PROBE_PROMPT = "请只回答两个字：你好。"
PROBE_TOOL_PROMPT = (
    f"调用 {PROBE_TOOL_NAME} 工具，参数 echo 传「ping」，然后根据工具结果用一句话说明你收到了什么。"
)
PROBE_TOOL_VALUE = "pong"


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """一次模型发现的结果；``complete`` 为假表示分页没走完或结果被截断。"""

    models: list[str] = field(default_factory=list)
    complete: bool = True
    code: str | None = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.code is None


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """一次连接测试的结果：响应与工具是两条独立事实。"""

    model: str
    response: Literal["passed", "failed", "untested"] = "untested"
    tool_call: Literal["passed", "failed", "untested"] = "untested"
    code: str | None = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.code is None


# -- 失败分类 ---------------------------------------------------------------


def _http_failure(status: int, *, action: str) -> tuple[str, str]:
    """HTTP 状态 → 稳定错误码 + 面向作者的一句话（不附厂商原始响应）。"""
    if status in (401, 403):
        return (
            "MODEL_AUTH_FAILED",
            f"{action}失败：认证没有通过。检查 API Key 是否正确、是否属于这家服务。",
        )
    if status == 404:
        return (
            "MODEL_ENDPOINT_NOT_FOUND",
            f"{action}失败：这个地址没有对应的接口。确认基础地址与协议是否匹配。",
        )
    if status == 429:
        return "MODEL_RATE_LIMITED", f"{action}失败：请求被限流，稍后再试。"
    if status >= 500:
        return "MODEL_PROVIDER_ERROR", f"{action}失败：供应商服务异常（{status}）。"
    return "MODEL_REQUEST_FAILED", f"{action}失败：供应商拒绝了请求（{status}）。"


def _transport_failure(exc: Exception, *, action: str) -> tuple[str, str]:
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return "MODEL_TIMEOUT", f"{action}超时，未在限定时间内完成。"
    return "MODEL_NETWORK_FAILED", f"{action}失败：连不上这个地址（{type(exc).__name__}）。"


def _client(
    base_url: str, timeout: float, transport: httpx.AsyncBaseTransport | None
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout,
        transport=transport,
        # 本机兼容服务不经系统代理；其余服务沿用系统代理设置。
        trust_env=not is_loopback_url(base_url),
    )


# -- 模型发现 ---------------------------------------------------------------


async def discover_models(
    profile: ProfileLike,
    *,
    discovery: str | None,
    connection_id: str | None = None,
    timeout: float = DISCOVERY_TIMEOUT_SECONDS,
    transport: httpx.AsyncBaseTransport | None = None,
) -> DiscoveryResult:
    """按策略读取模型列表；没有列表接口时明确返回「只能手填」。"""
    if discovery is None:
        return DiscoveryResult(
            models=[],
            code="MODEL_DISCOVERY_UNSUPPORTED",
            message="这家服务没有可用的模型列表接口；请手动填写模型 ID。",
        )
    key = profile.resolved_api_key()
    base = profile.base_url.rstrip("/")
    try:
        async with _client(base, timeout, transport) as client:
            if discovery == "anthropic-models":
                return await _discover_anthropic(client, base, key)
            if discovery == "google-models":
                return await _discover_google(client, base, key)
            return await _discover_openai(client, base, key)
    except Exception as exc:  # 网络层失败：给原因，不降级成「没有模型」
        code, message = _transport_failure(exc, action="获取模型列表")
        return DiscoveryResult(models=[], complete=False, code=code, message=message)


async def _discover_openai(client: httpx.AsyncClient, base: str, key: str) -> DiscoveryResult:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    return await _paged(
        client,
        url=f"{base}/models",
        headers=headers,
        extract=lambda payload: _ids_from_data(payload),
    )


async def _discover_anthropic(client: httpx.AsyncClient, base: str, key: str) -> DiscoveryResult:
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    models: list[str] = []
    after: str | None = None
    complete = True
    for _ in range(MAX_PAGES):
        params: dict[str, str | int] = {"limit": PAGE_SIZE}
        if after:
            params["after_id"] = after
        try:
            response = await client.get(f"{base}/models", headers=headers, params=params)
        except Exception as exc:
            code, message = _transport_failure(exc, action="获取模型列表")
            return DiscoveryResult(models=models, complete=False, code=code, message=message)
        if response.status_code >= 400:
            code, message = _http_failure(response.status_code, action="获取模型列表")
            return DiscoveryResult(models=models, complete=False, code=code, message=message)
        payload = _json(response)
        models.extend(_ids_from_data(payload))
        if not payload.get("has_more"):
            return DiscoveryResult(models=_dedupe(models), complete=complete)
        last_id = payload.get("last_id")
        after = last_id if isinstance(last_id, str) and last_id else ""
        if not after:
            complete = False
            break
    return DiscoveryResult(models=_dedupe(models), complete=False)


async def _discover_google(client: httpx.AsyncClient, base: str, key: str) -> DiscoveryResult:
    models: list[str] = []
    token: str | None = None
    for _ in range(MAX_PAGES):
        params: dict[str, str | int] = {"key": key, "pageSize": PAGE_SIZE}
        if token:
            params["pageToken"] = token
        try:
            response = await client.get(f"{base}/models", params=params)
        except Exception as exc:
            code, message = _transport_failure(exc, action="获取模型列表")
            return DiscoveryResult(models=models, complete=False, code=code, message=message)
        if response.status_code >= 400:
            code, message = _http_failure(response.status_code, action="获取模型列表")
            return DiscoveryResult(models=models, complete=False, code=code, message=message)
        payload = _json(response)
        raw = payload.get("models")
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    name = item.get("name")
                    if isinstance(name, str) and name:
                        # Google 用 models/<id> 作资源名；调用时只认 <id>。
                        models.append(name.removeprefix("models/"))
        next_token = payload.get("nextPageToken")
        token = next_token if isinstance(next_token, str) and next_token else None
        if not token:
            return DiscoveryResult(models=_dedupe(models), complete=True)
    return DiscoveryResult(models=_dedupe(models), complete=False)


async def _paged(
    client: httpx.AsyncClient,
    *,
    url: str,
    headers: Mapping[str, str],
    extract: Callable[[dict[str, object]], list[str]],
) -> DiscoveryResult:
    try:
        response = await client.get(url, headers=headers)
    except Exception as exc:
        code, message = _transport_failure(exc, action="获取模型列表")
        return DiscoveryResult(models=[], complete=False, code=code, message=message)
    if response.status_code >= 400:
        code, message = _http_failure(response.status_code, action="获取模型列表")
        return DiscoveryResult(models=[], code=code, message=message)
    return DiscoveryResult(models=_dedupe(extract(_json(response))), complete=True)


def _json(response: httpx.Response) -> dict[str, object]:
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def _ids_from_data(payload: dict[str, object]) -> list[str]:
    raw = payload.get("data")
    if not isinstance(raw, list):
        return []
    ids: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            identifier = item.get("id")
            if isinstance(identifier, str) and identifier:
                ids.append(identifier)
    return ids


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


# -- 连接测试 ---------------------------------------------------------------

ProviderFactory = Callable[[ProfileLike], ManagedModelProvider]


async def probe_response(
    profile: ProfileLike,
    model: str,
    *,
    timeout: float = RESPONSE_TIMEOUT_SECONDS,
    factory: ProviderFactory | None = None,
) -> ProbeResult:
    """发一句固定短句，要求非空文本回复。"""
    if not model:
        return ProbeResult(model=model, code="MODEL_NOT_SELECTED", message="先选择要测试的模型。")
    provider = (factory or _build)(profile)
    try:
        text = await asyncio.wait_for(
            _collect_text(provider, model, PROBE_PROMPT, tools=[]), timeout=timeout
        )
    except TimeoutError:
        return ProbeResult(
            model=model,
            response="failed",
            code="MODEL_TIMEOUT",
            message="响应测试超时，未在限定时间内完成。",
        )
    except _ProbeFailure as failure:
        return ProbeResult(
            model=model, response="failed", code=failure.code, message=failure.message
        )
    finally:
        await _close(provider)
    if not text.strip():
        return ProbeResult(
            model=model,
            response="failed",
            code="MODEL_EMPTY_RESPONSE",
            message="模型返回了空内容；换一个模型或检查该模型是否可用。",
        )
    return ProbeResult(model=model, response="passed", message="响应测试通过。")


async def probe_tool(
    profile: ProfileLike,
    model: str,
    *,
    timeout: float = TOOL_TIMEOUT_SECONDS,
    factory: ProviderFactory | None = None,
) -> ProbeResult:
    """验证工具名、参数、结果回传与最终回复的完整往返（最多两次模型响应）。"""
    if not model:
        return ProbeResult(model=model, code="MODEL_NOT_SELECTED", message="先选择要测试的模型。")
    provider = (factory or _build)(profile)
    tool = _probe_tool()
    try:
        first = await asyncio.wait_for(
            _collect_message(provider, model, PROBE_TOOL_PROMPT, tools=[tool]), timeout=timeout
        )
        call = _first_tool_call(first)
        if call is None:
            return ProbeResult(
                model=model,
                tool_call="failed",
                code="MODEL_TOOL_NOT_CALLED",
                message="模型没有发起工具调用，本次未通过；这不代表该模型永远不支持工具。",
            )
        if call.name != PROBE_TOOL_NAME:
            return ProbeResult(
                model=model,
                tool_call="failed",
                code="MODEL_TOOL_MISMATCH",
                message=f"模型调用了预期之外的工具：{call.name}。",
            )
        tool_result = ToolResultMessage(
            tool_call_id=call.id,
            tool_name=call.name,
            content=[TextContent(text=PROBE_TOOL_VALUE)],
        )
        final = await asyncio.wait_for(
            _collect_message(
                provider,
                model,
                PROBE_TOOL_PROMPT,
                tools=[tool],
                history=[UserMessage(content=PROBE_TOOL_PROMPT), first, tool_result],
            ),
            timeout=timeout,
        )
    except TimeoutError:
        return ProbeResult(
            model=model,
            tool_call="failed",
            code="MODEL_TIMEOUT",
            message="工具测试超时，未在限定时间内完成。",
        )
    except _ProbeFailure as failure:
        return ProbeResult(
            model=model, tool_call="failed", code=failure.code, message=failure.message
        )
    finally:
        await _close(provider)
    if not _text_of(final).strip():
        return ProbeResult(
            model=model,
            tool_call="failed",
            code="MODEL_EMPTY_RESPONSE",
            message="工具结果回传后没有得到最终回复。",
        )
    return ProbeResult(model=model, tool_call="passed", message="工具测试通过。")


class _ProbeFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _build(profile: ProfileLike) -> ManagedModelProvider:
    """探针用自己的短预算：不开重试，按测试类型给定超时。"""
    return build_provider(profile, worker=False, max_retries=0)


async def _close(provider: ManagedModelProvider) -> None:
    close = getattr(provider, "aclose", None)
    if close is not None:
        await close()


async def _collect_text(
    provider: ManagedModelProvider, model: str, prompt: str, *, tools: list[AgentTool]
) -> str:
    message = await _collect_message(provider, model, prompt, tools=tools)
    return _text_of(message)


async def _collect_message(
    provider: ManagedModelProvider,
    model: str,
    prompt: str,
    *,
    tools: list[AgentTool],
    history: list[AgentMessage] | None = None,
) -> AssistantMessage:
    messages: list[AgentMessage] = list(history) if history else [UserMessage(content=prompt)]
    text: list[str] = []
    failed: str | None = None
    result: AssistantMessage | None = None
    async for event in provider.stream_response(
        model=model,
        system="你是一个简洁的连接测试助手，只回答被要求的最小内容。",
        messages=messages,
        tools=tools,
    ):
        if isinstance(event, TextDeltaEvent):
            text.append(event.delta)
        elif isinstance(event, AssistantErrorEvent):
            failed = event.error.error_message or "厂商没有返回可用回复"
        elif isinstance(event, AssistantDoneEvent):
            result = event.message
    if failed:
        raise _ProbeFailure("MODEL_CALL_FAILED", f"模型调用失败：{failed}")
    if result is None:
        # 没有结束事件时用已收到的文本拼一条，避免把「流断了」说成成功。
        return AssistantMessage(content=[TextContent(text="".join(text))], stop_reason="error")
    return result


def _text_of(message: AssistantMessage) -> str:
    return "".join(block.text for block in message.content if isinstance(block, TextContent))


def _first_tool_call(message: AssistantMessage) -> ToolCall | None:
    for block in message.content:
        if isinstance(block, ToolCall):
            return block
    return None


def _probe_tool() -> AgentTool:
    """只回固定值的白名单工具：不碰作品、不写文件、不建立 Git。"""

    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, object],
        signal: object | None = None,
        on_update: object | None = None,
    ) -> AgentToolResult:
        del tool_call_id, arguments, signal, on_update
        return AgentToolResult(content=[TextContent(text=PROBE_TOOL_VALUE)])

    return AgentTool(
        name=PROBE_TOOL_NAME,
        label="连接探针",
        description="连接测试用的固定工具：无论收到什么参数，都返回 pong。",
        parameters={
            "type": "object",
            "properties": {"echo": {"type": "string", "description": "任意字符串"}},
            "additionalProperties": False,
        },
        execute_fn=execute,
        execution_mode="sequential",
    )
