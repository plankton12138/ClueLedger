"""工具返回里的回合预算。

回合上限按模型请求计数，一次工具执行要再请求一次模型才算一轮。模型看不到这个数字时，
会把一处一处的小修改拆成十几次调用，最后在收尾前耗尽预算。这里钉两件事：每次工具返回
都报告剩余回合；临近上限时明确要求收尾。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hastings.workspace.agent import RunContext, build_harness
from hastings.workspace.files import Workspace
from hastings.workspace.git import GitRepo
from hastings.workspace.service import WriteSession
from hastings.workspace.tool_feedback import author_summary, budget_feedback
from tau_agent.messages import AssistantMessage, TextContent, ToolCall, ToolResultMessage
from tau_agent.provider_events import AssistantDoneEvent, AssistantStartEvent
from tau_agent.tools import AgentToolResult


class _ScriptedProvider:
    """按脚本逐次回复，并记下模型实际收到的消息。"""

    def __init__(self, turns: list[list[Any]]) -> None:
        self.turns = turns
        self.calls = 0
        self.requests: list[dict[str, Any]] = []

    def stream_response(
        self, *, system: str = "", messages: list[Any] | None = None, **rest: Any
    ) -> Any:
        index = self.calls
        self.calls += 1
        self.requests.append({"messages": list(messages or [])})

        async def generator() -> Any:
            yield AssistantStartEvent(partial=AssistantMessage())
            if index < len(self.turns):
                for event in self.turns[index]:
                    yield event

        return generator()


def _tool_turn(*calls: ToolCall) -> list[Any]:
    return [
        AssistantDoneEvent(
            reason="toolUse",
            message=AssistantMessage(content=list(calls), stop_reason="toolUse"),
        )
    ]


def _text_turn(text: str) -> list[Any]:
    return [
        AssistantDoneEvent(
            reason="stop",
            message=AssistantMessage(content=[TextContent(text=text)], stop_reason="stop"),
        )
    ]


def _read_chapter() -> list[Any]:
    return _tool_turn(ToolCall(id="c1", name="read_file", arguments={"path": "正文/0001 雨夜.md"}))


def _setup(tmp_path: Path) -> tuple[Workspace, RunContext]:
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    GitRepo(workspace.root).init()
    workspace.write("正文/0001 雨夜.md", "# 雨夜\n\n雪很大。\n", expected_digest=None)
    session = WriteSession(workspace, instruction="读第一章")
    return workspace, RunContext(workspace, session, author_messages=((1, "读一下第一章"),))


def _tool_payload(provider: _ScriptedProvider, *, tool: str) -> dict[str, Any]:
    message = next(
        item
        for item in provider.requests[1]["messages"]
        if isinstance(item, ToolResultMessage) and item.tool_name == tool
    )
    return json.loads(message.content[0].text)


@pytest.mark.anyio
async def test_tool_result_reports_remaining_model_rounds(tmp_path: Path) -> None:
    workspace, run = _setup(tmp_path)
    provider = _ScriptedProvider(
        [
            _read_chapter(),
            _text_turn("读完了。"),
        ]
    )
    harness = build_harness(
        provider=provider, model="fake", workspace=workspace, run=run, max_turns=4
    )

    async for _ in harness.prompt("读一下第一章"):
        pass

    payload = _tool_payload(provider, tool="read_file")
    assert payload["execution_budget"]["remaining_model_rounds"] == 3


@pytest.mark.anyio
async def test_low_budget_adds_a_closing_notice(tmp_path: Path) -> None:
    workspace, run = _setup(tmp_path)
    provider = _ScriptedProvider(
        [
            _read_chapter(),
            _text_turn("读完了。"),
        ]
    )
    harness = build_harness(
        provider=provider, model="fake", workspace=workspace, run=run, max_turns=2
    )

    async for _ in harness.prompt("读一下第一章"):
        pass

    payload = _tool_payload(provider, tool="read_file")
    assert payload["execution_budget"]["remaining_model_rounds"] == 1
    assert "不要再开始新的修改批次" in payload["execution_budget"]["notice"]


def test_budget_feedback_keeps_the_author_facing_summary() -> None:
    """预算只进模型可见的返回；作品日志里仍是工具原本的说法。"""
    result = AgentToolResult(
        content=[
            TextContent(text=json.dumps({"summary": "读完了", "chars": 12}, ensure_ascii=False))
        ],
        details={"summary": "读完了", "chars": 12},
    )

    updated = budget_feedback(result, remaining=9)

    payload = json.loads(updated.text)
    assert payload["execution_budget"]["remaining_model_rounds"] == 9
    assert payload["summary"] == "读完了"
    assert author_summary(updated) == "读完了"
