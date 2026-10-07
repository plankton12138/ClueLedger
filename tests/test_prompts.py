"""System prompt 组装测试（T4）。

断言三节结构 + 工具 guidelines 全量拼接；无工具时不产生空标题节。
"""

from collections.abc import Mapping
from typing import Any

from hastings.prompts import (
    ROLE_HEADING,
    TOOLS_HEADING,
    WORKFLOW_HEADING,
    build_system_prompt,
)
from tau_agent import AgentTool, AgentToolResult
from tau_agent.types import JSONValue


def _tool(name: str, *guidelines: str) -> AgentTool:
    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: Any = None,
        on_update: Any = None,
    ) -> AgentToolResult:
        del tool_call_id, arguments, signal, on_update
        return AgentToolResult(content="ok")

    return AgentTool(
        name=name,
        label=name,
        description=f"{name} 描述",
        parameters={"type": "object", "properties": {}},
        execute_fn=execute,
        prompt_guidelines=guidelines,
    )


def test_system_prompt_contains_role_and_workflow_sections() -> None:
    prompt = build_system_prompt()

    assert ROLE_HEADING in prompt
    assert WORKFLOW_HEADING in prompt
    # 无工具时不出现空的工具节
    assert TOOLS_HEADING not in prompt


def test_system_prompt_mentions_fair_play_rule() -> None:
    prompt = build_system_prompt()

    assert "fair play" in prompt.lower()
    assert "Bible" in prompt


def test_system_prompt_appends_every_tool_guideline() -> None:
    tools = [
        _tool("bible_upsert", "任何角色设定必须先经 bible_upsert 落盘，再写进正文。"),
        _tool("write_chapter", "写章节前，确认出场角色都已用 bible_upsert 落盘。"),
    ]

    prompt = build_system_prompt(tools)

    assert TOOLS_HEADING in prompt
    for tool in tools:
        for guideline in tool.prompt_guidelines:
            assert guideline in prompt


def test_system_prompt_is_deterministic() -> None:
    tools = [_tool("bible_query", "动笔前先 bible_query 核对既有设定。")]

    assert build_system_prompt(tools) == build_system_prompt(tools)
