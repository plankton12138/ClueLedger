"""outline 第六账本单测（P8 T3）：状态机 / 章号唯一 / 泛化读写 / 确认门接线。"""

from pathlib import Path

import pytest

from hastings.bible.models import OutlineEntry
from hastings.bible.state_machine import (
    INITIAL_STATUS,
    InvalidTransitionError,
    validate_initial,
    validate_transition,
)
from hastings.bible.store import StoryWorkspace, load_ledger, save_story_doc
from hastings.bible.story_state import StoryDoc
from hastings.tools.bible_tools import create_bible_tools
from hastings.tools.errors import ToolInputError
from hastings.tools.method_tools import create_method_tools
from hastings.tools.story_tools import create_story_tools
from tau_agent import AgentTool
from tau_agent.types import JSONValue


@pytest.fixture
def workspace(tmp_path: Path) -> StoryWorkspace:
    workspace = StoryWorkspace(root=tmp_path / "stories", slug="demo")
    # 非 intake 阶段（写路径可用）：章纲经 bible_upsert 落盘，受 intake 写门约束。
    save_story_doc(workspace, StoryDoc(slug="demo", stage="bible_drafting"))
    return workspace


@pytest.fixture
def tools(workspace: StoryWorkspace) -> dict[str, AgentTool]:
    tool_list = create_bible_tools(workspace) + create_story_tools(workspace)
    tool_list += create_method_tools(workspace)
    return {tool.name: tool for tool in tool_list}


async def _call(tool: AgentTool, arguments: dict[str, JSONValue]) -> object:
    return await tool.execute("call-1", arguments)


def _upsert_args(name: str, chapter: int, **extra: object) -> dict[str, JSONValue]:
    data: dict[str, JSONValue] = {"name": name, "chapter": chapter}
    data.update(extra)  # type: ignore[arg-type]
    return {"entity_type": "outline", "data": data}


# ---------- 状态机（第六账本：planned → written → revised） ----------


def test_outline_initial_status_is_planned() -> None:
    assert INITIAL_STATUS["outline"] == "planned"
    validate_initial("outline", "planned")  # 不抛即通过


def test_outline_rejects_non_initial_status() -> None:
    with pytest.raises(InvalidTransitionError, match="初始状态"):
        validate_initial("outline", "written")


def test_outline_legal_transitions() -> None:
    validate_transition("outline", "planned", "written")
    validate_transition("outline", "written", "revised")


def test_outline_rejects_skip_and_terminal() -> None:
    with pytest.raises(InvalidTransitionError, match="planned → revised"):
        validate_transition("outline", "planned", "revised")
    with pytest.raises(InvalidTransitionError, match="终态"):
        validate_transition("outline", "revised", "planned")


# ---------- 泛化读写（注册表零 if-else 扩展） ----------


@pytest.mark.anyio
async def test_upsert_outline_writes_ledger(
    tools: dict[str, AgentTool], workspace: StoryWorkspace
) -> None:
    result = await _call(
        tools["bible_upsert"],
        _upsert_args(
            "案发之夜",
            7,
            act="第二幕",
            beats=["暴风雪封路", "发现尸体"],
            plant_plans=["保温瓶的位置"],
            end_hook="雪地上的第二行脚印",
        ),
    )

    assert "案发之夜" in result.text
    entries = load_ledger(workspace.outline_path, OutlineEntry)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.name == "案发之夜"
    assert entry.chapter == 7
    assert entry.status == "planned"
    assert entry.beats == ["暴风雪封路", "发现尸体"]
    assert entry.plant_plans == ["保温瓶的位置"]


@pytest.mark.anyio
async def test_query_outline_lists_summary(
    tools: dict[str, AgentTool], workspace: StoryWorkspace
) -> None:
    del workspace
    await _call(tools["bible_upsert"], _upsert_args("案发之夜", 7, act="第二幕"))
    await _call(tools["bible_upsert"], _upsert_args("不在场证明崩塌", 12))

    result = await _call(tools["bible_query"], {"entity_type": "outline"})

    assert result.details == {"count": 2, "names": ["案发之夜", "不在场证明崩塌"]}
    assert "案发之夜" in result.text and "ch7" in result.text
    assert "第二幕" in result.text


@pytest.mark.anyio
async def test_query_outline_by_name_returns_yaml(
    tools: dict[str, AgentTool], workspace: StoryWorkspace
) -> None:
    del workspace
    await _call(tools["bible_upsert"], _upsert_args("案发之夜", 7, end_hook="第二行脚印"))

    result = await _call(tools["bible_query"], {"entity_type": "outline", "name": "案发之夜"})

    assert "end_hook" in result.text and "第二行脚印" in result.text


@pytest.mark.anyio
async def test_outline_status_transitions_via_upsert(
    tools: dict[str, AgentTool], workspace: StoryWorkspace
) -> None:
    """章纲状态沿状态机推进：planned → written → revised 经 upsert 合法。"""
    await _call(tools["bible_upsert"], _upsert_args("案发之夜", 7))
    await _call(tools["bible_upsert"], _upsert_args("案发之夜", 7, status="written"))
    await _call(tools["bible_upsert"], _upsert_args("案发之夜", 7, status="revised"))

    (entry,) = load_ledger(workspace.outline_path, OutlineEntry)
    assert entry.status == "revised"


# ---------- 章号唯一性（工具层校验，name 之外的第二键） ----------


@pytest.mark.anyio
async def test_upsert_outline_rejects_duplicate_chapter(
    tools: dict[str, AgentTool], workspace: StoryWorkspace
) -> None:
    del workspace
    await _call(tools["bible_upsert"], _upsert_args("案发之夜", 7))

    with pytest.raises(ToolInputError) as exc_info:
        await _call(tools["bible_upsert"], _upsert_args("不在场证明崩塌", 7))
    msg = str(exc_info.value)
    assert "章号 7" in msg and "案发之夜" in msg
    assert "为什么错" in msg and "怎样算对" in msg


@pytest.mark.anyio
async def test_upsert_outline_same_name_overwrite_allows_same_chapter(
    tools: dict[str, AgentTool], workspace: StoryWorkspace
) -> None:
    """同名覆盖写是更新而非冲突：章号保持不变时放行。"""
    await _call(tools["bible_upsert"], _upsert_args("案发之夜", 7, act="第二幕"))
    await _call(
        tools["bible_upsert"],
        _upsert_args("案发之夜", 7, act="第三幕", end_hook="新的钩子"),
    )

    (entry,) = load_ledger(workspace.outline_path, OutlineEntry)
    assert entry.act == "第三幕"
    assert entry.end_hook == "新的钩子"


@pytest.mark.anyio
async def test_upsert_outline_same_name_can_move_chapter(
    tools: dict[str, AgentTool], workspace: StoryWorkspace
) -> None:
    """同名条目把章号改到空位是合法重排。"""
    await _call(tools["bible_upsert"], _upsert_args("案发之夜", 7))
    await _call(tools["bible_upsert"], _upsert_args("案发之夜", 8))

    entries = load_ledger(workspace.outline_path, OutlineEntry)
    assert [e.chapter for e in entries] == [8]


@pytest.mark.anyio
async def test_upsert_outline_rejects_missing_chapter(tools: dict[str, AgentTool]) -> None:
    with pytest.raises(ToolInputError, match="chapter"):
        await _call(tools["bible_upsert"], {"entity_type": "outline", "data": {"name": "x"}})


@pytest.mark.anyio
async def test_upsert_outline_rejects_invalid_status_jump(
    tools: dict[str, AgentTool], workspace: StoryWorkspace
) -> None:
    del workspace
    await _call(tools["bible_upsert"], _upsert_args("案发之夜", 7))
    with pytest.raises(ToolInputError, match="planned → revised"):
        await _call(tools["bible_upsert"], _upsert_args("案发之夜", 7, status="revised"))


# ---------- 确认门接线（_derive_prereqs 的 outlines 计数） ----------


@pytest.mark.anyio
async def test_confirm_gate_passes_with_outline(
    tools: dict[str, AgentTool], workspace: StoryWorkspace
) -> None:
    """补齐角色/手法/章纲后，确认门放行——outline 计数来自账本实况。"""
    await _call(tools["bible_upsert"], _upsert_args("案发之夜", 7))
    for name in ("波洛", "马普尔"):
        await _call(tools["bible_upsert"], {"entity_type": "character", "data": {"name": name}})
    await _call(
        tools["bible_upsert"],
        {"entity_type": "method", "data": {"name": "保温瓶诡计", "description": "隔热层藏机关"}},
    )
    await _call(
        tools["method_lock"],
        {"name": "保温瓶诡计", "chapter": "ch7", "reason": "线索链闭合，作者确认"},
    )
    await _call(tools["story_stage"], {"action": "advance", "reason": "intake 完成"})
    result = await _call(tools["story_stage"], {"action": "advance", "reason": "作者确认 Bible"})

    assert "bible_confirmed" in result.text
