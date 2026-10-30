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
from typing import Literal, cast

import httpx

from hastings.prompts import tools as prompt_tools
from hastings.prompts.tasks import PROBE_PROMPT, PROBE_SYSTEM, probe_tool_prompt
from hastings.prompts.tools import PROBE_TOOL_DESCRIPTION
from hastings.server.model_limits import (
    DEFAULT_PROJECT_INPUT_TOKENS,
    LimitProfile,
    ModelCapacity,
    ModelLimits,
    resolve_model_limits,
)
from hastings.server.provider_runtime import (
    ManagedModelProvider,
    ProfileLike,
    build_provider,
    is_loopback_url,
)
from hastings.workspace.model_activity import request_estimate
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
PROBE_TOOL_VALUE = "pong"


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """一次模型发现的结果；``complete`` 为假表示分页没走完或结果被截断。

    ``limits`` 是供应商在列表里明确给出的容量（例如 OpenRouter 的 context_length、
    Google 的 inputTokenLimit）。列表只有名字时不编造容量，这一项就是空的。
    """

    models: list[str] = field(default_factory=list)
    limits: dict[str, ModelCapacity] = field(default_factory=dict)
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
    key = _bearer(profile)
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


@dataclass(frozen=True, slots=True)
class _Entries:
    """一次响应里的模型条目：ID 与供应商给出的容量分开收集。"""

    models: list[str]
    limits: dict[str, ModelCapacity]


async def _discover_openai(client: httpx.AsyncClient, base: str, key: str) -> DiscoveryResult:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    return await _paged(
        client,
        url=f"{base}/models",
        headers=headers,
        extract=lambda payload: _entries_from_data(payload),
    )


async def _discover_anthropic(client: httpx.AsyncClient, base: str, key: str) -> DiscoveryResult:
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    models: list[str] = []
    limits: dict[str, ModelCapacity] = {}
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
            return _finish(models, limits, complete=False, code=code, message=message)
        if response.status_code >= 400:
            code, message = _http_failure(response.status_code, action="获取模型列表")
            return _finish(models, limits, complete=False, code=code, message=message)
        payload = _json(response)
        entries = _entries_from_data(payload)
        models.extend(entries.models)
        limits.update(entries.limits)
        if not payload.get("has_more"):
            return _finish(models, limits, complete=complete)
        last_id = payload.get("last_id")
        after = last_id if isinstance(last_id, str) and last_id else ""
        if not after:
            complete = False
            break
    return _finish(models, limits, complete=False)


async def _discover_google(client: httpx.AsyncClient, base: str, key: str) -> DiscoveryResult:
    models: list[str] = []
    limits: dict[str, ModelCapacity] = {}
    token: str | None = None
    for _ in range(MAX_PAGES):
        params: dict[str, str | int] = {"key": key, "pageSize": PAGE_SIZE}
        if token:
            params["pageToken"] = token
        try:
            response = await client.get(f"{base}/models", params=params)
        except Exception as exc:
            code, message = _transport_failure(exc, action="获取模型列表")
            return _finish(models, limits, complete=False, code=code, message=message)
        if response.status_code >= 400:
            code, message = _http_failure(response.status_code, action="获取模型列表")
            return _finish(models, limits, complete=False, code=code, message=message)
        payload = _json(response)
        raw = payload.get("models")
        if isinstance(raw, list):
            entries = _google_entries(raw)
            models.extend(entries.models)
            limits.update(entries.limits)
        next_token = payload.get("nextPageToken")
        token = next_token if isinstance(next_token, str) and next_token else None
        if not token:
            return _finish(models, limits, complete=True)
    return _finish(models, limits, complete=False)


async def _paged(
    client: httpx.AsyncClient,
    *,
    url: str,
    headers: Mapping[str, str],
    extract: Callable[[dict[str, object]], _Entries],
) -> DiscoveryResult:
    try:
        response = await client.get(url, headers=headers)
    except Exception as exc:
        code, message = _transport_failure(exc, action="获取模型列表")
        return DiscoveryResult(models=[], complete=False, code=code, message=message)
    if response.status_code >= 400:
        code, message = _http_failure(response.status_code, action="获取模型列表")
        return DiscoveryResult(models=[], code=code, message=message)
    entries = extract(_json(response))
    return _finish(entries.models, entries.limits, complete=True)


def _finish(
    models: list[str],
    limits: dict[str, ModelCapacity],
    *,
    complete: bool = True,
    code: str | None = None,
    message: str = "",
) -> DiscoveryResult:
    """去重后返回；没出现在列表里的容量条目一并丢掉，避免留下孤立事实。"""
    unique = _dedupe(models)
    kept = set(unique)
    return DiscoveryResult(
        models=unique,
        limits={model: capacity for model, capacity in limits.items() if model in kept},
        complete=complete,
        code=code,
        message=message,
    )


def _json(response: httpx.Response) -> dict[str, object]:
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def _entries_from_data(payload: dict[str, object]) -> _Entries:
    """取模型 ID 与供应商给出的容量。

    两种形状都要认，且字段名不同：

    - 普通接口：``data`` 数组，每项 ``id``。
    - 订阅目录：``models`` 数组，每项 ``slug``（不是 ``id``），并带 ``visibility``；
      只有 ``visibility == "list"`` 的模型才该出现在选择器里，顺序沿用服务端的。

    订阅那次「拿到了列表却像是空的」就是这里只认了 ``id``。
    """
    raw = payload.get("data")
    if isinstance(raw, list):
        return _collect_entries(raw, key="id", visible_only=False)
    raw = payload.get("models")
    if isinstance(raw, list):
        return _collect_entries(raw, key="slug", visible_only=True)
    return _Entries(models=[], limits={})


def _collect_entries(raw: list[object], *, key: str, visible_only: bool) -> _Entries:
    models: list[str] = []
    limits: dict[str, ModelCapacity] = {}
    for item in raw:
        if isinstance(item, str):
            # Google 风格的名字带 ``models/`` 前缀；其它来源是裸 ID。
            models.append(item.split("/")[-1] if item else "")
            continue
        if not isinstance(item, dict):
            continue
        if visible_only and item.get("visibility") not in (None, "list"):
            continue
        value = item.get(key) or item.get("id") or item.get("name")
        if not isinstance(value, str) or not value:
            continue
        model = value.split("/")[-1] if "/" in value else value
        models.append(model)
        capacity = _reported_capacity(item)
        if capacity is not None:
            limits[model] = capacity
    return _Entries(models=models, limits=limits)


def _google_entries(raw: list[object]) -> _Entries:
    models: list[str] = []
    limits: dict[str, ModelCapacity] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        # Google 用 models/<id> 作资源名；调用时只认 <id>。
        model = name.removeprefix("models/")
        models.append(model)
        input_limit = _positive_int(item.get("inputTokenLimit"))
        output_limit = _positive_int(item.get("outputTokenLimit"))
        if input_limit is None and output_limit is None:
            continue
        # Google 分别公布输入与输出上限；两者相加才是这个模型的完整窗口。
        window = input_limit + output_limit if input_limit and output_limit else input_limit
        limits[model] = ModelCapacity(
            context_window_tokens=window,
            max_input_tokens=input_limit,
            max_output_tokens=output_limit,
        )
    return _Entries(models=models, limits=limits)


def _reported_capacity(item: Mapping[str, object]) -> ModelCapacity | None:
    """从一条模型记录里读供应商声明的容量；只有名字时返回 ``None``。

    常见两类形状：

    - OpenRouter 一类网关：顶层 ``context_length``，``top_provider`` 里给出
      实际转发商的 ``context_length`` 与 ``max_completion_tokens``。
    - 少数兼容接口：顶层 ``max_input_tokens`` / ``max_output_tokens``。

    只读取明确的字段，不根据模型名推断。
    """
    top = item.get("top_provider")
    top_window = top.get("context_length") if isinstance(top, Mapping) else None
    top_output = top.get("max_completion_tokens") if isinstance(top, Mapping) else None
    # 网关同时给出模型与转发商的窗口时取更小的那个：路由到较小窗口的供应商也装得下。
    window = _smaller(
        _positive_int(item.get("context_length")),
        _positive_int(item.get("context_window")),
        _positive_int(top_window),
    )
    output = _smaller(
        _positive_int(item.get("max_completion_tokens")),
        _positive_int(item.get("max_output_tokens")),
        _positive_int(top_output),
    )
    input_limit = _positive_int(item.get("max_input_tokens"))
    if window is None and input_limit is None and output is None:
        return None
    return ModelCapacity(
        context_window_tokens=window,
        max_input_tokens=input_limit,
        max_output_tokens=output,
    )


def _smaller(*values: int | None) -> int | None:
    known = [value for value in values if value is not None]
    return min(known) if known else None


def _positive_int(value: object) -> int | None:
    """只接受正整数；布尔、字符串与 0 都按未知处理。"""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def _bearer(profile: ProfileLike) -> str:
    """读取请求凭据：订阅连接用访问令牌，其余用 API Key。

    两者在 HTTP 上都是 Bearer，区别只在来源与续期方式；这里只挑值，不管刷新。
    """
    if getattr(profile, "auth_method", "api_key") == "subscription":
        credential = getattr(profile, "subscription", None)
        return credential.access_token if credential is not None else ""
    return profile.resolved_api_key()


# -- 连接测试 ---------------------------------------------------------------

ProviderFactory = Callable[[ProfileLike], ManagedModelProvider]


async def probe_response(
    profile: ProfileLike,
    model: str,
    *,
    timeout: float = RESPONSE_TIMEOUT_SECONDS,
    factory: ProviderFactory | None = None,
    on_text: Callable[[str], None] | None = None,
    project_limit: int = DEFAULT_PROJECT_INPUT_TOKENS,
) -> ProbeResult:
    """发一句固定短句，要求非空文本回复。

    连接测试同样是生成请求，因此也走同一份输入预算；固定短句通常远低于上限，
    检查在这里的作用是让「小预算」不至于绕过探针。
    """
    if not model:
        return ProbeResult(model=model, code="MODEL_NOT_SELECTED", message="先选择要测试的模型。")
    limits = _probe_limits(profile, model, project_limit)
    provider = (factory or _build)(profile)
    try:
        text = await asyncio.wait_for(
            _collect_text(
                provider, model, PROBE_PROMPT, tools=[], on_text=on_text, limits=limits
            ),
            timeout=timeout,
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
    on_text: Callable[[str], None] | None = None,
    project_limit: int = DEFAULT_PROJECT_INPUT_TOKENS,
) -> ProbeResult:
    """验证工具名、参数、结果回传与最终回复的完整往返（最多两次模型响应）。"""
    if not model:
        return ProbeResult(model=model, code="MODEL_NOT_SELECTED", message="先选择要测试的模型。")
    limits = _probe_limits(profile, model, project_limit)
    provider = (factory or _build)(profile)
    tool = _probe_tool()
    try:
        first = await asyncio.wait_for(
            _collect_message(
                provider,
                model,
                probe_tool_prompt(PROBE_TOOL_NAME),
                tools=[tool],
                on_text=on_text,
                limits=limits,
            ),
            timeout=timeout,
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
                probe_tool_prompt(PROBE_TOOL_NAME),
                tools=[tool],
                history=[
                    UserMessage(content=probe_tool_prompt(PROBE_TOOL_NAME)),
                    first,
                    tool_result,
                ],
                on_text=on_text,
                limits=limits,
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
    """探针用自己的短预算：不开重试，按测试类型给定超时。

    回复额度仍取写作轮的固定值：预算里的回复预留与实际请求参数必须是同一个数，
    否则探针报出来的预算会比真实请求宽松。
    """
    return build_provider(profile, worker=True, max_retries=0)


async def _close(provider: ManagedModelProvider) -> None:
    close = getattr(provider, "aclose", None)
    if close is not None:
        await close()


async def _collect_text(
    provider: ManagedModelProvider,
    model: str,
    prompt: str,
    *,
    tools: list[AgentTool],
    on_text: Callable[[str], None] | None = None,
    limits: ModelLimits | None = None,
) -> str:
    message = await _collect_message(
        provider, model, prompt, tools=tools, on_text=on_text, limits=limits
    )
    return _text_of(message)


def _probe_limits(
    profile: ProfileLike, model: str, project_limit: int
) -> ModelLimits | None:
    """探针的输入预算：与写作轮次同一套解析，不另立一套口径。"""
    try:
        return resolve_model_limits(
            cast(LimitProfile, profile), model, project_limit=project_limit
        )
    except ValueError:
        # 容量配置本身矛盾时，由发送路径给出统一报错；探针不抢先解释。
        return None


def _definitions(tools: list[AgentTool]) -> list[dict[str, object]]:
    return [
        {"name": tool.name, "description": tool.description, "parameters": tool.parameters}
        for tool in tools
    ]


async def _collect_message(
    provider: ManagedModelProvider,
    model: str,
    prompt: str,
    *,
    tools: list[AgentTool],
    history: list[AgentMessage] | None = None,
    on_text: Callable[[str], None] | None = None,
    limits: ModelLimits | None = None,
) -> AssistantMessage:
    messages: list[AgentMessage] = list(history) if history else [UserMessage(content=prompt)]
    if limits is not None:
        estimate = request_estimate(
            identity=f"{limits.connection_id}:{model}",
            system=PROBE_SYSTEM,
            messages=messages,
            definitions=_definitions(tools),
            limits=limits,
            source_revisions={},
        )
        total = estimate["total_tokens"]
        if isinstance(total, int) and total > limits.input_limit():
            raise _ProbeFailure(
                "CONTEXT_REQUIRED_INPUT_TOO_LARGE",
                "连接测试的请求超过输入预算，没有发出。"
                "建议：调大项目输入上限，或选择容量更大的模型后重试。",
            )
    text: list[str] = []
    failed: str | None = None
    result: AssistantMessage | None = None
    async for event in provider.stream_response(
        model=model,
        system=PROBE_SYSTEM,
        messages=messages,
        tools=tools,
    ):
        if isinstance(event, TextDeltaEvent):
            text.append(event.delta)
            if on_text is not None:
                on_text(event.delta)
        elif isinstance(event, AssistantErrorEvent):
            failed = event.error.error_message or "厂商没有返回可用回复"
        elif isinstance(event, AssistantDoneEvent):
            result = event.message
    if failed:
        raise _ProbeFailure("MODEL_CALL_FAILED", f"模型调用失败：{failed}")
    if result is None:
        # 没有结束事件：连接中断、流被截断，或供应商在流里报了错却没给终止事件。
        # 已经收到的半段文字不能算通过——写作轮次里这就是「写到一半没了」。
        raise _ProbeFailure(
            "MODEL_STREAM_INCOMPLETE",
            "模型响应没有正常结束（连接中断或流被截断），本次不计为通过。"
            "建议：重试；若持续出现，检查网络或该模型的可用性。",
        )
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
        description=PROBE_TOOL_DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "echo": {
                    "type": "string",
                    "description": prompt_tools.PROBE_ECHO_PARAM_DESCRIPTION,
                }
            },
            "additionalProperties": False,
        },
        execute_fn=execute,
        execution_mode="sequential",
    )
