"""写作 loop 集成测试（T6，Phase 1 核心交付）。

用 FakeProvider 预录一段完整剧本——"创建两个角色并写第一章"——然后断言三层契约：
① 事件序列；② tmp_path 下真实落盘（2 张角色 YAML + 1 章正文）且内容可解析；③ 消息历史。

这是从 tau 上游学到的最重要范式：Agent 行为可以像纯函数一样确定性测试。
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

from hastings.bible.models import Character, Clue, Foreshadow, Method
from hastings.bible.store import StoryWorkspace, list_characters, load_ledger
from hastings.manuscript.write_guard import EditMarkRegistry
from hastings.prompts import build_system_prompt
from hastings.tools.bible_tools import create_bible_tools
from hastings.tools.chapter_tools import create_chapter_tools
from hastings.tools.method_tools import create_method_tools
from hastings.tools.timeline_tools import create_timeline_tools
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


def _reread_provider() -> FakeProvider:
    """P4 T3 集成剧本：读回第 1 章原文 → 写第 2 章（读回通道进 agent loop）。"""
    read = ToolCall(id="call-1", name="read_chapter", arguments={"chapter": 1})
    write = ToolCall(
        id="call-2",
        name="write_chapter",
        arguments={"chapter": 2, "title": "第二夜", "content": "夜更深了。"},
    )

    def tool_turn(call: ToolCall) -> list[Any]:
        return [
            AssistantStartEvent(partial=AssistantMessage(model="fake")),
            ToolCallEndEvent(
                content_index=1, tool_call=call, partial=AssistantMessage(model="fake")
            ),
            AssistantDoneEvent(
                reason="toolUse", message=AssistantMessage(content=[call], stop_reason="toolUse")
            ),
        ]

    closing = AssistantDoneEvent(
        reason="stop", message=AssistantMessage(content="第二章已落笔。", stop_reason="stop")
    )
    return FakeProvider(
        [
            tool_turn(read),
            tool_turn(write),
            [AssistantStartEvent(partial=AssistantMessage(model="fake")), closing],
        ]
    )


@pytest.mark.anyio
async def test_writing_loop_rereads_chapter_before_next(tmp_path: Path) -> None:
    """读回通道：第 1 章正文经 read_chapter 回到上下文，第 2 章衔接落盘。"""
    workspace = StoryWorkspace(root=tmp_path, slug="orient-express")
    tools = create_bible_tools(workspace) + create_chapter_tools(workspace)
    reread_harness = AgentHarness(
        AgentHarnessConfig(
            provider=_reread_provider(),
            model="fake",
            system=build_system_prompt(tools),
            tools=tools,
        )
    )

    # 前置：第 1 章已存在
    workspace.ensure_dirs()
    (workspace.chapters_dir / "ch01.md").write_text(
        "# 第1章 雪封之夜\n\n雪从午夜开始落。\n", encoding="utf-8"
    )

    events = [event async for event in reread_harness.prompt("核对第1章后写第2章")]

    tool_ends = [e for e in events if isinstance(e, ToolExecutionEndEvent)]
    assert [e.tool_name for e in tool_ends] == ["read_chapter", "write_chapter"]
    # read_chapter 的结果正文回到 agent 上下文（toolResult 消息可见原文）
    read_result = tool_ends[0]
    assert "雪从午夜开始落" in read_result.result.text
    assert not read_result.is_error
    # 第 2 章落盘，第 1 章未被改动
    assert "夜更深了" in (workspace.chapters_dir / "ch02.md").read_text(encoding="utf-8")
    assert "雪从午夜开始落" in (workspace.chapters_dir / "ch01.md").read_text(encoding="utf-8")


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


# --- P2 T6：五账本全流程剧本 ---
# 剧本照抄 P1 范式，只扩内容："建角色、登记线索与伏笔、起草手法并锁定，
# 然后写第一章"，章后把伏笔对账为 planted，最后跑时间线检测收尾。


FLOW_PROMPT = "建角色、登记线索与伏笔、起草手法并锁定，然后写第一章"
FLOW_DETECTIVE = {"name": "沈砚", "identity": "退休警探", "secret": "十年前因怀表案引咎辞职"}
FLOW_SUSPECT = {"name": "顾眉", "motivation": "掩盖十年前的挪用案"}
FLOW_CLUE = {
    "name": "停摆的怀表",
    "content": "死者口袋里的怀表停在 21:47",
    "chapter": "ch01",
    "points_to": "顾眉",
    "is_genuine": True,
}
FLOW_FORESHADOW = {
    "name": "旅客名单缺页",
    "content": "旅客名单被撕去了一页",
    "plant_chapter": "ch01",
    "plant_mode": "hint",
    "expected_resolve_chapter": "ch05",
}
FLOW_METHOD = {
    "name": "怀表调时诡计",
    "description": "凶手提前拨停怀表，伪造死亡时刻",
    "time_window": "第1天 21:00-23:00",
    "requirements": ["怀表在死者上衣口袋", "凶手案发前接触过车厢"],
    "alibi_claims": [
        {
            "person": "顾眉",
            "day": 1,
            "t_start": "21:00",
            "t_end": "23:00",
            "claim": "声称整晚在餐厅打牌",
        }
    ],
}
FLOW_LOCK_REASON = "诡计定稿，开写前冻结"
FLOW_CHAPTER_TITLE = "雪夜停摆"
FLOW_CHAPTER_BODY = "怀表停在二十一点四十七分。沈砚合上表盖：死亡时刻是被人拨出来的。"


def _full_flow_provider() -> FakeProvider:
    detective = ToolCall(
        id="call-f1",
        name="bible_upsert",
        arguments={"entity_type": "character", "data": FLOW_DETECTIVE},
    )
    suspect = ToolCall(
        id="call-f2",
        name="bible_upsert",
        arguments={"entity_type": "character", "data": FLOW_SUSPECT},
    )
    clue = ToolCall(
        id="call-f3", name="bible_upsert", arguments={"entity_type": "clue", "data": FLOW_CLUE}
    )
    foreshadow = ToolCall(
        id="call-f4",
        name="bible_upsert",
        arguments={"entity_type": "foreshadow", "data": FLOW_FORESHADOW},
    )
    method = ToolCall(
        id="call-f5", name="bible_upsert", arguments={"entity_type": "method", "data": FLOW_METHOD}
    )
    lock = ToolCall(
        id="call-f6",
        name="method_lock",
        arguments={"name": "怀表调时诡计", "chapter": "ch01", "reason": FLOW_LOCK_REASON},
    )
    write = ToolCall(
        id="call-f7",
        name="write_chapter",
        arguments={"chapter": 1, "title": FLOW_CHAPTER_TITLE, "content": FLOW_CHAPTER_BODY},
    )
    planted = ToolCall(
        id="call-f8",
        name="bible_upsert",
        # 章后对账：upsert 是覆盖写，必须全量回传，只把 status 推进为 planted
        arguments={"entity_type": "foreshadow", "data": {**FLOW_FORESHADOW, "status": "planted"}},
    )
    check = ToolCall(id="call-f9", name="timeline_check", arguments={})
    closing = (
        "全流程完毕：角色建档，线索与伏笔登记，"
        "手法已锁定，第一章落盘，伏笔对账为 planted，时间线自洽。"
    )
    return FakeProvider(
        [
            _assistant_stream(call=detective, text="先建侦探。", reason="toolUse"),
            _assistant_stream(call=suspect, text="再建嫌疑人。", reason="toolUse"),
            _assistant_stream(call=clue, text="登记线索。", reason="toolUse"),
            _assistant_stream(call=foreshadow, text="登记伏笔。", reason="toolUse"),
            _assistant_stream(call=method, text="起草手法。", reason="toolUse"),
            _assistant_stream(call=lock, text="手法定稿，锁定。", reason="toolUse"),
            _assistant_stream(call=write, text="动笔写第一章。", reason="toolUse"),
            _assistant_stream(call=planted, text="章后对账：伏笔已埋。", reason="toolUse"),
            _assistant_stream(call=check, text="跑一次时间线检测。", reason="toolUse"),
            _assistant_stream(call=None, text=closing, reason="stop"),
        ]
    )


@pytest.fixture
def full_harness(workspace: StoryWorkspace) -> AgentHarness:
    tools = (
        create_bible_tools(workspace)
        + create_chapter_tools(workspace)
        + create_method_tools(workspace)
        + create_timeline_tools(workspace)
    )
    return AgentHarness(
        AgentHarnessConfig(
            provider=_full_flow_provider(),
            model="fake",
            system=build_system_prompt(tools),
            tools=tools,
        )
    )


FLOW_TOOL_NAMES = [
    "bible_upsert",
    "bible_upsert",
    "bible_upsert",
    "bible_upsert",
    "bible_upsert",
    "method_lock",
    "write_chapter",
    "bible_upsert",
    "timeline_check",
]


@pytest.mark.anyio
async def test_full_flow_event_sequence(full_harness: AgentHarness) -> None:
    events = [event async for event in full_harness.prompt(FLOW_PROMPT)]
    types = [event.type for event in events]

    # 十个模型回合：九个工具回合 + 一个总结回合
    assert types[0] == "agent_start"
    assert types[-1] == "agent_end"
    assert types.count("turn_start") == 10
    assert types.count("turn_end") == 10
    assert types.count("tool_execution_start") == 9
    assert types.count("tool_execution_end") == 9
    assert types.count("message_start") == 10 + 9 + 1  # assistant + toolResult + user prompt

    tool_names = [event.tool_name for event in events if isinstance(event, ToolExecutionEndEvent)]
    assert tool_names == FLOW_TOOL_NAMES
    assert all(not event.is_error for event in events if isinstance(event, ToolExecutionEndEvent))


@pytest.mark.anyio
async def test_full_flow_persists_ledgers(
    full_harness: AgentHarness, workspace: StoryWorkspace
) -> None:
    async for _ in full_harness.prompt(FLOW_PROMPT):
        pass

    # 两张角色卡照旧落盘
    assert [c.name for c in list_characters(workspace)] == ["沈砚", "顾眉"]

    # 线索账本：初始状态 planned
    clues = load_ledger(workspace.clues_path, Clue)
    assert len(clues) == 1
    assert clues[0].status == "planned"
    assert clues[0].is_genuine is True

    # 伏笔账本：章后对账迁移到 planted，且覆盖写后全量字段仍在
    foreshadows = load_ledger(workspace.foreshadows_path, Foreshadow)
    assert len(foreshadows) == 1
    assert foreshadows[0].status == "planted"
    assert foreshadows[0].content == FLOW_FORESHADOW["content"]
    assert foreshadows[0].expected_resolve_chapter == "ch05"

    # 手法账本：经 method_lock 迁移到 locked，history 含锁定审计记录
    methods = load_ledger(workspace.method_path, Method)
    assert len(methods) == 1
    assert methods[0].status == "locked"
    assert [(h.action, h.chapter) for h in methods[0].history] == [("locked", "ch01")]

    # 一章正文
    chapter = workspace.chapters_dir / "ch01.md"
    assert chapter.exists()
    assert chapter.read_text(encoding="utf-8").startswith(f"# 第1章 {FLOW_CHAPTER_TITLE}\n")

    # timeline_check 只读检测，不落盘
    assert not workspace.timeline_path.exists()


@pytest.mark.anyio
async def test_full_flow_message_history(full_harness: AgentHarness) -> None:
    async for _ in full_harness.prompt(FLOW_PROMPT):
        pass

    assert [m.role for m in full_harness.messages] == [
        "user",
        *("assistant", "toolResult") * 9,
        "assistant",
    ]
    results = [m for m in full_harness.messages if isinstance(m, ToolResultMessage)]
    assert [r.tool_name for r in results] == FLOW_TOOL_NAMES
    assert "手法「怀表调时诡计」已锁定" in results[5].text
    assert "第1章已写入" in results[6].text
    assert "status=planted" in results[7].text
    assert "时间线自洽" in results[8].text
    assert all(r.is_error is False for r in results)


# --- P5 T3（D4）：作者草稿锁 → write_chapter is_error 喂回模型 ---


def _locked_chapter_provider() -> FakeProvider:
    """剧本：agent 试图抢写被作者锁定的第 3 章 → 被拒 → 收尾。"""
    write = ToolCall(
        id="call-g1",
        name="write_chapter",
        arguments={"chapter": 3, "title": "抢写", "content": "趁作者没保存改掉这段。"},
    )
    return FakeProvider(
        [
            _assistant_stream(call=write, text="我把第3章改成十点零七分。", reason="toolUse"),
            _assistant_stream(
                call=None, text="第3章正被作者手动编辑，等作者保存或放弃后我再动笔。", reason="stop"
            ),
        ]
    )


@pytest.mark.anyio
async def test_write_chapter_rejected_while_author_holds_mark(tmp_path: Path) -> None:
    """作者锁 3 章 → write_chapter(force=true) is_error，三要素文案喂回模型。"""
    workspace = StoryWorkspace(root=tmp_path, slug="orient-express")
    marks = EditMarkRegistry()
    marks.acquire("ch03")
    tools = create_bible_tools(workspace) + create_chapter_tools(workspace, edit_marks=marks)
    harness = AgentHarness(
        AgentHarnessConfig(
            provider=_locked_chapter_provider(),
            model="fake",
            system=build_system_prompt(tools),
            tools=tools,
        )
    )

    events = [event async for event in harness.prompt("把第3章的时间改成十点零七分")]

    ends = [event for event in events if isinstance(event, ToolExecutionEndEvent)]
    assert [e.tool_name for e in ends] == ["write_chapter"]
    assert ends[0].is_error is True
    assert "第3章正在被作者手动编辑" in ends[0].result.text
    assert "为什么错" in ends[0].result.text
    assert "怎样算对" in ends[0].result.text
    # 作者锁下正文未被篡改
    assert not (workspace.chapters_dir / "ch03.md").exists()
