"""回合规速守卫测试（P10 T4，D3/L4）。

两层：① StageAdvanceGuard 纯单元（计数/重置/rollback 不限次）；
② FakeProvider 集成——单次 prompt 内第二次 advance 被 before_tool_call 拒绝。
"""

from pathlib import Path
from typing import Any

import pytest

from hastings.bible.models import Character, Method, OutlineEntry
from hastings.bible.store import StoryWorkspace, save_character, save_ledger
from hastings.manuscript.advance_guard import REJECTION, StageAdvanceGuard
from hastings.tools.story_tools import create_story_tools
from tau_agent import AgentHarness, AgentHarnessConfig, AssistantMessage, ToolCall
from tau_agent.events import AgentStartEvent, ToolExecutionEndEvent
from tau_agent.provider_events import AssistantDoneEvent, AssistantStartEvent
from tau_ai import FakeProvider


def _advance(reason: str) -> ToolCall:
    return ToolCall(
        id=f"adv-{reason}", name="story_stage", arguments={"action": "advance", "reason": reason}
    )


def _rollback(reason: str) -> ToolCall:
    return ToolCall(
        id=f"rb-{reason}", name="story_stage", arguments={"action": "rollback", "reason": reason}
    )


def _turn(call: ToolCall | None, text: str, reason: str) -> list[Any]:
    if call is None:
        message = AssistantMessage(content=text, stop_reason="stop")
    else:
        message = AssistantMessage(content=[call], stop_reason="toolUse")
    return [
        AssistantStartEvent(partial=AssistantMessage(model="fake")),
        AssistantDoneEvent(reason=reason, message=message),
    ]


@pytest.mark.anyio
async def test_guard_allows_first_advance_blocks_second() -> None:
    guard = StageAdvanceGuard()
    guard.on_event(AgentStartEvent())
    assert await guard.before_tool_call(_advance("第一次")) == (False, None)
    blocked, message = await guard.before_tool_call(_advance("第二次"))
    assert blocked is True
    assert message is not None
    assert "本回合已推进过一次" in message
    assert "为什么错" in message and "怎样算对" in message


@pytest.mark.anyio
async def test_guard_resets_on_agent_start() -> None:
    guard = StageAdvanceGuard()
    guard.on_event(AgentStartEvent())
    await guard.before_tool_call(_advance("第一次"))
    guard.on_event(AgentStartEvent())  # 新一轮 prompt
    assert await guard.before_tool_call(_advance("新一轮第一次")) == (False, None)


@pytest.mark.anyio
async def test_guard_does_not_limit_rollback_or_other_tools() -> None:
    guard = StageAdvanceGuard()
    guard.on_event(AgentStartEvent())
    # rollback 不限次
    for i in range(3):
        assert await guard.before_tool_call(_rollback(f"r{i}")) == (False, None)
    # 非 story_stage 工具不受影响
    other = ToolCall(id="x", name="bible_query", arguments={"entity_type": "character"})
    assert await guard.before_tool_call(other) == (False, None)


@pytest.mark.anyio
async def test_second_advance_rejected_in_single_prompt(tmp_path: Path) -> None:
    """集成：一次 prompt 内连推两次 → 第二次 is_error 且文案含三要素。"""
    workspace = StoryWorkspace(root=tmp_path, slug="demo")
    tools = create_story_tools(workspace)
    provider = FakeProvider(
        [
            _turn(_advance("作者确认开始建账"), "推进到起草。", "toolUse"),
            _turn(_advance("再推一格"), "继续推进。", "toolUse"),
            _turn(None, "本轮完成。", "stop"),
        ]
    )
    harness = AgentHarness(
        AgentHarnessConfig(provider=provider, model="fake", system="", tools=tools)
    )
    guard = StageAdvanceGuard()
    harness.subscribe(guard.on_event)
    harness.config.before_tool_call = guard.before_tool_call

    events = [event async for event in harness.prompt("先推进到 bible_drafting，再推一格")]
    ends = [event for event in events if isinstance(event, ToolExecutionEndEvent)]
    assert [e.tool_name for e in ends] == ["story_stage", "story_stage"]
    assert ends[0].is_error is False
    assert "阶段已推进" in ends[0].result.text
    assert ends[1].is_error is True
    assert "本回合已推进过一次" in ends[1].result.text
    # 阶段只推进了一格（intake → bible_drafting）
    from hastings.bible.store import load_story_doc

    assert load_story_doc(workspace).stage == "bible_drafting"


@pytest.mark.anyio
async def test_advance_allowed_again_next_prompt(tmp_path: Path) -> None:
    """跨 prompt 计数重置：第二轮的 advance 不被上一轮次数牵连。

    两轮各推进一格合法边：intake→bible_drafting、bible_drafting→
    bible_confirmed（确认门前置先由 store 层补齐，避开工具业务门干扰）。
    """
    workspace = StoryWorkspace(root=tmp_path, slug="demo")
    workspace.ensure_dirs()
    save_character(workspace, Character(name="波洛", identity="侦探"))
    save_character(workspace, Character(name="黑斯廷斯", identity="叙事者"))
    save_ledger(workspace.method_path, [Method(name="毒杀诡计", status="locked")])
    save_ledger(workspace.outline_path, [OutlineEntry(name="案发之夜", chapter=1)])
    tool = create_story_tools(workspace)[0]
    harness = AgentHarness(
        AgentHarnessConfig(
            provider=FakeProvider(
                [
                    _turn(_advance("第一轮推进"), "推进。", "toolUse"),
                    _turn(None, "完。", "stop"),
                    _turn(_advance("第二轮推进"), "再推进。", "toolUse"),
                    _turn(None, "完。", "stop"),
                ]
            ),
            model="fake",
            system="",
            tools=[tool],
        )
    )
    guard = StageAdvanceGuard()
    harness.subscribe(guard.on_event)
    harness.config.before_tool_call = guard.before_tool_call

    first = [e async for e in harness.prompt("推进")]
    second = [e async for e in harness.prompt("再推进")]
    first_ends = [e for e in first if isinstance(e, ToolExecutionEndEvent)]
    second_ends = [e for e in second if isinstance(e, ToolExecutionEndEvent)]
    assert first_ends[0].is_error is False
    assert second_ends[0].is_error is False  # 计数已重置，新回合 advance 放行


def test_rejection_constant_is_three_elements() -> None:
    assert "被拒" in REJECTION
    assert "为什么错" in REJECTION
    assert "怎样算对" in REJECTION
