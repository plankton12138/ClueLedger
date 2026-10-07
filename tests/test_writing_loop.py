"""写作 loop 集成测试（T6，Phase 1 核心交付）。

用 FakeProvider 预录一段完整剧本——"创建两个角色并写第一章"——然后断言三层契约：
① 事件序列；② tmp_path 下真实落盘（2 张角色 YAML + 1 章正文）且内容可解析；③ 消息历史。

这是从 tau 上游学到的最重要范式：Agent 行为可以像纯函数一样确定性测试。
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

from hastings.bible.models import Character
from hastings.bible.store import StoryWorkspace, list_characters
from hastings.prompts import build_system_prompt
from hastings.tools.bible_tools import create_bible_tools
from hastings.tools.chapter_tools import create_chapter_tools
from tau_agent import (
    AgentHarness,
    AgentHarnessConfig,
    AssistantMessage,
    ToolCall,
    ToolResultMessage,
)
from tau_agent.events import MessageStartEvent, ToolExecutionEndEvent
from tau_agent.provider_events import (
    AssistantDoneEvent,
    AssistantStartEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ToolCallEndEvent,
)
from tau_ai import FakeProvider

PROMPT = "创建两个角色并写第一章"
POIROT = {"name": "波洛", "identity": "比利时侦探", "secret": "曾因未破案离职"}
CUBE = {"name": "奎勒", "identity": "列车乘务长", "motivation": "掩盖十二年前的旧案"}
CHAPTER_TITLE = "雪封之夜"
CHAPTER_BODY = "雪从午夜开始落。到三点十七分，列车已经完全被封在白色里。"


def _assistant_stream(*, call: ToolCall | None, text: str, reason: str) -> list[Any]:
    events: list[Any] = [AssistantStartEvent(partial=AssistantMessage(model="fake"))]
    if text:
        events.append(TextStartEvent(content_index=0, partial=AssistantMessage(model="fake")))
        events.append(
            TextDeltaEvent(
                content_index=0,
                delta=text,
                partial=AssistantMessage(content=text, model="fake"),
            )
        )
        events.append(
            TextEndEvent(
                content_index=0,
                content=text,
                partial=AssistantMessage(content=text, model="fake"),
            )
        )
    if call is not None:
        events.append(
            ToolCallEndEvent(
                content_index=1,
                tool_call=call,
                partial=AssistantMessage(content=[call], model="fake"),
            )
        )
    message: AssistantMessage = (
        AssistantMessage(content=[call], stop_reason="toolUse")
        if call is not None
        else AssistantMessage(content=text, stop_reason="stop")
    )
    events.append(AssistantDoneEvent(reason=reason, message=message))
    return events


def _scripted_provider() -> FakeProvider:
    poirot = ToolCall(
        id="call-1", name="bible_upsert", arguments={"entity_type": "character", "data": POIROT}
    )
    cube = ToolCall(
        id="call-2", name="bible_upsert", arguments={"entity_type": "character", "data": CUBE}
    )
    query = ToolCall(id="call-3", name="bible_query", arguments={"entity_type": "character"})
    write = ToolCall(
        id="call-4",
        name="write_chapter",
        arguments={"chapter": 1, "title": CHAPTER_TITLE, "content": CHAPTER_BODY},
    )
    closing = "案卷已开卷：波洛与奎勒已建档，第一章已落盘。"
    return FakeProvider(
        [
            _assistant_stream(call=poirot, text="先建第一张角色卡。", reason="toolUse"),
            _assistant_stream(call=cube, text="再建第二张。", reason="toolUse"),
            _assistant_stream(call=query, text="核对账本。", reason="toolUse"),
            _assistant_stream(call=write, text="动笔写第一章。", reason="toolUse"),
            _assistant_stream(call=None, text=closing, reason="stop"),
        ]
    )


@pytest.fixture
def workspace(tmp_path: Path) -> StoryWorkspace:
    return StoryWorkspace(root=tmp_path, slug="orient-express")


@pytest.fixture
def harness(workspace: StoryWorkspace) -> AgentHarness:
    tools = create_bible_tools(workspace) + create_chapter_tools(workspace)
    return AgentHarness(
        AgentHarnessConfig(
            provider=_scripted_provider(),
            model="fake",
            system=build_system_prompt(tools),
            tools=tools,
        )
    )


@pytest.mark.anyio
async def test_writing_loop_event_sequence(harness: AgentHarness) -> None:
    events = [event async for event in harness.prompt(PROMPT)]
    types = [event.type for event in events]

    # 五个模型回合，每个工具回合都是 start → update → end → tool_start → tool_end
    assert types[0] == "agent_start"
    assert types[-1] == "agent_end"
    assert types.count("turn_start") == 5
    assert types.count("turn_end") == 5
    assert types.count("tool_execution_start") == 4
    assert types.count("tool_execution_end") == 4
    assert types.count("message_start") == 5 + 4 + 1  # assistant + toolResult + user prompt

    # 工具执行顺序就是剧本顺序
    tool_names = [event.tool_name for event in events if isinstance(event, ToolExecutionEndEvent)]
    assert tool_names == ["bible_upsert", "bible_upsert", "bible_query", "write_chapter"]
    assert all(not event.is_error for event in events if isinstance(event, ToolExecutionEndEvent))


@pytest.mark.anyio
async def test_writing_loop_persists_workspace_files(
    harness: AgentHarness, workspace: StoryWorkspace
) -> None:
    async for _ in harness.prompt(PROMPT):
        pass

    # 两张角色卡，YAML 可解析且字段正确
    characters = list_characters(workspace)
    assert [c.name for c in characters] == ["奎勒", "波洛"]
    poirot = next(c for c in characters if c.name == "波洛")
    assert Character.model_validate(poirot.model_dump()) == poirot
    raw = yaml.safe_load((workspace.characters_dir / "波洛.yaml").read_text(encoding="utf-8"))
    assert raw["identity"] == "比利时侦探"

    # 一章正文，标题由工具生成
    chapter = workspace.chapters_dir / "ch01.md"
    assert chapter.exists()
    text = chapter.read_text(encoding="utf-8")
    assert text.startswith(f"# 第1章 {CHAPTER_TITLE}\n")
    assert CHAPTER_BODY in text


@pytest.mark.anyio
async def test_writing_loop_message_history(harness: AgentHarness) -> None:
    async for _ in harness.prompt(PROMPT):
        pass

    assert [m.role for m in harness.messages] == [
        "user",
        "assistant",
        "toolResult",
        "assistant",
        "toolResult",
        "assistant",
        "toolResult",
        "assistant",
        "toolResult",
        "assistant",
    ]
    results = [m for m in harness.messages if isinstance(m, ToolResultMessage)]
    assert [r.tool_name for r in results] == [
        "bible_upsert",
        "bible_upsert",
        "bible_query",
        "write_chapter",
    ]
    assert "角色「波洛」已写入" in results[0].text
    assert "第1章已写入" in results[3].text
    assert all(r.is_error is False for r in results)


@pytest.mark.anyio
async def test_writing_loop_rejects_repeated_chapter_without_force(
    harness: AgentHarness, workspace: StoryWorkspace
) -> None:
    """覆盖保护：同一章二次写入必须被工具拦下（错误喂回模型，不崩 loop）。"""

    async for _ in harness.prompt(PROMPT):
        pass
    assert (workspace.chapters_dir / "ch01.md").exists()

    # 再发一次相同请求：write_chapter 这次会命中覆盖保护
    tools = create_bible_tools(workspace) + create_chapter_tools(workspace)
    replay = AgentHarness(
        AgentHarnessConfig(
            provider=_single_write_provider(),
            model="fake",
            system=build_system_prompt(tools),
            tools=tools,
        )
    )
    events = [event async for event in replay.prompt("重写第一章")]

    ends = [event for event in events if isinstance(event, ToolExecutionEndEvent)]
    assert len(ends) == 1
    assert ends[0].is_error is True
    assert "force=true" in ends[0].result.text
    # 正文未被篡改
    assert CHAPTER_BODY in (workspace.chapters_dir / "ch01.md").read_text(encoding="utf-8")


def _single_write_provider() -> FakeProvider:
    write = ToolCall(
        id="call-9",
        name="write_chapter",
        arguments={"chapter": 1, "title": "重写", "content": "这段不该写进去。"},
    )
    return FakeProvider(
        [
            _assistant_stream(call=write, text="再写一次第一章。", reason="toolUse"),
            _assistant_stream(call=None, text="好的，需要 force=true。", reason="stop"),
        ]
    )


@pytest.mark.anyio
async def test_first_message_start_is_user_prompt(harness: AgentHarness) -> None:
    events = [event async for event in harness.prompt(PROMPT)]
    message_starts = [event for event in events if isinstance(event, MessageStartEvent)]

    assert message_starts[0].message.role == "user"
