"""模型发现与连接测试（V04-9B）。

契约：
- 四类协议各自按官方接口取列表：认证头、路径与分页参数不同，不能混成一种。
- 「没有列表接口」「认证失败」「限流」「网络故障」是不同结果，不能都降级成「没有模型」。
- 响应测试真的发一次模型请求；工具测试要求完整往返（工具名、参数、结果回传、最终回复）。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Any

import httpx
import pytest

from hastings.server.model_probe import (
    PROBE_TOOL_NAME,
    discover_models,
    probe_response,
    probe_tool,
)
from hastings.server.provider_config import ProviderProfile
from tau_agent.messages import AssistantMessage, TextContent, ToolCall
from tau_agent.provider_events import AssistantDoneEvent, AssistantErrorEvent, AssistantStartEvent


def _profile(
    api: str = "openai-completions", base_url: str = "https://api.example.test/v1"
) -> ProviderProfile:
    return ProviderProfile(
        name="conn",
        base_url=base_url,
        api_key="sk-test",
        default_model="m1",
        api=api,  # type: ignore[arg-type]
    )


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _transport(handler: Any, calls: list[httpx.Request]) -> httpx.MockTransport:
    def _handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    return httpx.MockTransport(_handle)


# -- 模型发现：协议差异 -----------------------------------------------------


def test_openai_style_discovery_reads_ids_with_bearer_auth() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "m1"}, {"id": "m2"}, {"id": "m1"}]})

    result = _run(
        discover_models(_profile(), discovery="openai-models", transport=_transport(handler, calls))
    )

    assert result.models == ["m1", "m2"]
    assert result.complete is True
    assert result.code is None
    assert result.limits == {}
    assert str(calls[0].url) == "https://api.example.test/v1/models"
    assert calls[0].headers["authorization"] == "Bearer sk-test"


def test_openai_style_discovery_reads_reported_capacity() -> None:
    """网关给出窗口与输出上限时一并带回；转发商更小的窗口优先。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "m1",
                        "context_length": 128_000,
                        "top_provider": {
                            "context_length": 64_000,
                            "max_completion_tokens": 8_192,
                        },
                    }
                ]
            },
        )

    result = _run(
        discover_models(_profile(), discovery="openai-models", transport=_transport(handler, []))
    )

    capacity = result.limits["m1"]
    assert capacity.context_window_tokens == 64_000
    assert capacity.max_output_tokens == 8_192


def test_google_discovery_reads_input_and_output_limits() -> None:
    """Google 分别公布输入与输出上限；窗口取两者之和，输入上限单独保留。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-a",
                        "inputTokenLimit": 1_048_576,
                        "outputTokenLimit": 65_536,
                    }
                ]
            },
        )

    result = _run(
        discover_models(
            _profile("google-generative-ai"),
            discovery="google-models",
            transport=_transport(handler, []),
        )
    )

    capacity = result.limits["gemini-a"]
    assert capacity.context_window_tokens == 1_114_112
    assert capacity.max_input_tokens == 1_048_576
    assert capacity.max_output_tokens == 65_536


def test_anthropic_discovery_paginates_with_last_id() -> None:
    calls: list[httpx.Request] = []
    pages = [
        {"data": [{"id": "claude-a"}], "has_more": True, "last_id": "claude-a"},
        {"data": [{"id": "claude-b"}], "has_more": False},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages[len(calls) - 1])

    result = _run(
        discover_models(
            _profile("anthropic-messages"),
            discovery="anthropic-models",
            transport=_transport(handler, calls),
        )
    )

    assert result.models == ["claude-a", "claude-b"]
    assert calls[0].headers["x-api-key"] == "sk-test"
    assert calls[0].headers["anthropic-version"]
    assert "after_id" not in calls[0].url.params
    assert calls[1].url.params["after_id"] == "claude-a"


def test_google_discovery_strips_resource_prefix_and_follows_token() -> None:
    calls: list[httpx.Request] = []
    pages = [
        {"models": [{"name": "models/gemini-a"}], "nextPageToken": "t2"},
        {"models": [{"name": "models/gemini-b"}]},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages[len(calls) - 1])

    result = _run(
        discover_models(
            _profile("google-generative-ai", "https://generativelanguage.test/v1beta"),
            discovery="google-models",
            transport=_transport(handler, calls),
        )
    )

    assert result.models == ["gemini-a", "gemini-b"]
    assert calls[0].url.params["key"] == "sk-test"
    assert calls[1].url.params["pageToken"] == "t2"


def test_preset_without_list_endpoint_returns_unsupported() -> None:
    result = _run(discover_models(_profile(), discovery=None))

    assert result.code == "MODEL_DISCOVERY_UNSUPPORTED"
    assert "手动" in result.message
    assert result.models == []


def test_empty_list_is_a_valid_answer_not_an_error() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    result = _run(
        discover_models(_profile(), discovery="openai-models", transport=_transport(handler, calls))
    )

    assert result.models == []
    assert result.code is None


def test_discovery_truncates_after_page_budget() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        # 永远还有下一页：一次点击必须有终点。
        return httpx.Response(200, json={"models": [{"name": "models/x"}], "nextPageToken": "more"})

    result = _run(
        discover_models(
            _profile("google-generative-ai"),
            discovery="google-models",
            transport=_transport(handler, calls),
        )
    )

    assert result.complete is False
    assert len(calls) == 10


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "MODEL_AUTH_FAILED"),
        (403, "MODEL_AUTH_FAILED"),
        (404, "MODEL_ENDPOINT_NOT_FOUND"),
        (429, "MODEL_RATE_LIMITED"),
        (500, "MODEL_PROVIDER_ERROR"),
    ],
)
def test_discovery_keeps_distinct_failure_reasons(status: int, code: str) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "nope"})

    result = _run(
        discover_models(_profile(), discovery="openai-models", transport=_transport(handler, calls))
    )

    assert result.code == code
    assert "没有模型" not in result.message


def test_discovery_reports_network_failure_not_empty_catalog() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("拒绝连接", request=request)

    result = _run(
        discover_models(_profile(), discovery="openai-models", transport=_transport(handler, []))
    )

    assert result.code == "MODEL_NETWORK_FAILED"
    assert result.models == []


def test_discovery_reports_timeout_separately() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("超时", request=request)

    result = _run(
        discover_models(_profile(), discovery="openai-models", transport=_transport(handler, []))
    )

    assert result.code == "MODEL_TIMEOUT"


# -- 受控 HTTP 服务：真实 socket 往返 ---------------------------------------


class _ModelsHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        body = json.dumps({"data": [{"id": "local-model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


def test_discovery_against_controlled_local_service() -> None:
    server = HTTPServer(("127.0.0.1", 0), _ModelsHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}/v1"
        result = _run(discover_models(_profile(base_url=base), discovery="openai-models"))
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert result.models == ["local-model"]
    assert result.code is None


# -- 连接测试 ---------------------------------------------------------------


class _FakeProvider:
    """按脚本返回模型消息；第二次调用可以看到历史（用于验证工具结果回传）。"""

    def __init__(self, messages: list[AssistantMessage], *, delay: float = 0.0) -> None:
        self.messages = messages
        self.delay = delay
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def stream_response(self, **kwargs: Any) -> AsyncIterator[Any]:
        self.calls.append(kwargs)
        message = self.messages[min(len(self.calls) - 1, len(self.messages) - 1)]

        async def generator() -> AsyncIterator[Any]:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield AssistantStartEvent(partial=AssistantMessage())
            yield AssistantDoneEvent(reason="stop", message=message)

        return generator()

    async def aclose(self) -> None:
        self.closed = True


def _done(text: str) -> AssistantMessage:
    return AssistantMessage(content=[TextContent(text=text)], stop_reason="stop")


def test_response_probe_requires_real_text() -> None:
    provider = _FakeProvider([_done("你好")])

    result = _run(probe_response(_profile(), "m1", factory=lambda _profile: provider))

    assert result.response == "passed"
    assert result.code is None
    assert provider.closed is True
    assert provider.calls[0]["tools"] == []


def test_response_probe_empty_text_is_a_failure() -> None:
    provider = _FakeProvider([_done("")])

    result = _run(probe_response(_profile(), "m1", factory=lambda _profile: provider))

    assert result.response == "failed"
    assert result.code == "MODEL_EMPTY_RESPONSE"


def test_response_probe_surfaces_vendor_error() -> None:
    provider = _FakeProvider([])

    def stream_response(**_: Any) -> AsyncIterator[Any]:
        async def generator() -> AsyncIterator[Any]:
            yield AssistantStartEvent(partial=AssistantMessage())
            yield AssistantErrorEvent(
                reason="error",
                error=AssistantMessage(
                    content=[], stop_reason="error", error_message="invalid api key"
                ),
            )

        return generator()

    provider.stream_response = stream_response  # type: ignore[method-assign]

    result = _run(probe_response(_profile(), "m1", factory=lambda _profile: provider))

    assert result.response == "failed"
    assert result.code == "MODEL_CALL_FAILED"
    assert "invalid api key" in result.message


def test_response_probe_times_out() -> None:
    provider = _FakeProvider([_done("你好")], delay=0.5)

    result = _run(probe_response(_profile(), "m1", timeout=0.05, factory=lambda _profile: provider))

    assert result.code == "MODEL_TIMEOUT"
    assert provider.closed is True


def test_tool_probe_verifies_full_round_trip() -> None:
    call = ToolCall(id="call-1", name=PROBE_TOOL_NAME, arguments={"echo": "ping"})
    provider = _FakeProvider(
        [
            AssistantMessage(content=[call], stop_reason="toolUse"),
            _done("收到了 pong"),
        ]
    )

    result = _run(probe_tool(_profile(), "m1", factory=lambda _profile: provider))

    assert result.tool_call == "passed"
    assert result.code is None
    # 第一次请求带上了探针工具，第二次请求带回了工具结果。
    assert [tool.name for tool in provider.calls[0]["tools"]] == [PROBE_TOOL_NAME]
    second_messages = provider.calls[1]["messages"]
    assert [type(message).__name__ for message in second_messages] == [
        "UserMessage",
        "AssistantMessage",
        "ToolResultMessage",
    ]
    tool_result = second_messages[-1]
    assert tool_result.tool_call_id == "call-1"
    assert tool_result.content[0].text == "pong"


def test_tool_probe_without_tool_call_is_untested_not_impossible() -> None:
    provider = _FakeProvider([_done("我直接回答了，没有调用工具")])

    result = _run(probe_tool(_profile(), "m1", factory=lambda _profile: provider))

    assert result.tool_call == "failed"
    assert result.code == "MODEL_TOOL_NOT_CALLED"
    assert "不代表" in result.message


def test_tool_probe_rejects_unknown_tool_name() -> None:
    provider = _FakeProvider(
        [
            AssistantMessage(
                content=[ToolCall(id="c", name="write_file", arguments={})],
                stop_reason="toolUse",
            ),
            _done("好"),
        ]
    )

    result = _run(probe_tool(_profile(), "m1", factory=lambda _profile: provider))

    assert result.code == "MODEL_TOOL_MISMATCH"


def test_tool_probe_requires_final_reply() -> None:
    call = ToolCall(id="call-1", name=PROBE_TOOL_NAME, arguments={"echo": "ping"})
    provider = _FakeProvider(
        [
            AssistantMessage(content=[call], stop_reason="toolUse"),
            _done(""),
        ]
    )

    result = _run(probe_tool(_profile(), "m1", factory=lambda _profile: provider))

    assert result.code == "MODEL_EMPTY_RESPONSE"


def test_probe_without_model_is_rejected_before_calling() -> None:
    result = _run(probe_response(_profile(), "", factory=lambda _profile: pytest.fail("不该构造")))

    assert result.code == "MODEL_NOT_SELECTED"


def test_probe_tool_definition_is_read_only() -> None:
    """测试工具只有固定返回值，不接触作品目录。"""
    from hastings.server.model_probe import _probe_tool

    tool = _probe_tool()

    assert tool.name == PROBE_TOOL_NAME
    assert tool.parameters["additionalProperties"] is False
    result = _run(tool.execute("id", {"echo": "x"}))
    assert result.content[0].text == "pong"  # type: ignore[union-attr]
