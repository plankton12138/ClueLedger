"""bible_query 工具单测（V1 弃用后唯一存留的 bible 工具，只读）。

写路径（bible_upsert）已随 V1 弃用删除；播种经 store 的 save_*（夹具级手段，
无生产调用方）。
"""

from collections.abc import Mapping
from pathlib import Path

import pytest

from hastings.bible.models import Character, Clue
from hastings.bible.store import StoryWorkspace, save_character, save_ledger
from hastings.tools.bible_tools import create_bible_tools
from hastings.tools.errors import ToolInputError
from tau_agent import AgentTool
from tau_agent.types import JSONValue


@pytest.fixture
def workspace(tmp_path: Path) -> StoryWorkspace:
    return StoryWorkspace(root=tmp_path / "stories", slug="demo")


@pytest.fixture
def tools(workspace: StoryWorkspace) -> dict[str, AgentTool]:
    return {tool.name: tool for tool in create_bible_tools(workspace)}


async def _call(tool: AgentTool, arguments: Mapping[str, JSONValue]) -> object:
    return await tool.execute("call-1", arguments)


# ---------- bible_query ----------


@pytest.mark.anyio
async def test_query_empty_bible(tools: dict[str, AgentTool]) -> None:
    result = await _call(tools["bible_query"], {"entity_type": "character"})

    assert "为空" in result.text
    assert result.details == {"count": 0, "names": []}


@pytest.mark.anyio
async def test_query_lists_all_characters(
    tools: dict[str, AgentTool], workspace: StoryWorkspace
) -> None:
    save_character(workspace, Character(name="波洛", motivation="真相"))
    save_character(workspace, Character(name="马普尔", motivation="好奇"))

    result = await _call(tools["bible_query"], {"entity_type": "character"})

    assert "波洛" in result.text
    assert "马普尔" in result.text
    assert result.details == {"count": 2, "names": ["波洛", "马普尔"]}


@pytest.mark.anyio
async def test_query_by_name_returns_yaml(
    tools: dict[str, AgentTool], workspace: StoryWorkspace
) -> None:
    save_character(workspace, Character(name="波洛", identity="退休侦探"))

    result = await _call(tools["bible_query"], {"entity_type": "character", "name": "波洛"})

    assert "identity: 退休侦探" in result.text
    assert isinstance(result.details, dict) and result.details["found"] is True


@pytest.mark.anyio
async def test_query_missing_name_returns_hint_not_error(tools: dict[str, AgentTool]) -> None:
    result = await _call(tools["bible_query"], {"entity_type": "character", "name": "不存在"})

    assert "不存在" in result.text
    assert result.details == {"found": False, "name": "不存在"}


@pytest.mark.anyio
async def test_query_rejects_unknown_entity_type(tools: dict[str, AgentTool]) -> None:
    with pytest.raises(ToolInputError, match="可选值"):
        await _call(tools["bible_query"], {"entity_type": "plot"})


@pytest.mark.anyio
async def test_query_rejects_non_string_name(tools: dict[str, AgentTool]) -> None:
    with pytest.raises(ToolInputError, match="字符串"):
        await _call(tools["bible_query"], {"entity_type": "character", "name": 3})


@pytest.mark.anyio
async def test_query_ledger_empty_returns_guidance_not_error(
    tools: dict[str, AgentTool],
) -> None:
    """泄压阀：空账本 ≠ error，返回引导性提示。"""
    result = await _call(tools["bible_query"], {"entity_type": "method"})

    assert "为空" in result.text
    assert result.details == {"count": 0, "names": []}


@pytest.mark.anyio
async def test_query_ledger_lists_summaries(
    tools: dict[str, AgentTool], workspace: StoryWorkspace
) -> None:
    save_ledger(
        workspace.clues_path,
        [
            Clue(name="染血的信", content="血迹", chapter="ch02"),
            Clue(name="半张车票", content="撕角", points_to="管家"),
        ],
    )

    result = await _call(tools["bible_query"], {"entity_type": "clue"})

    assert "染血的信" in result.text and "半张车票" in result.text
    assert "planned" in result.text  # 摘要行带状态，模型对账不查全文
    assert result.details == {"count": 2, "names": ["染血的信", "半张车票"]}


# ---------- 元信息 ----------


def test_tools_carry_prompt_guidelines(tools: dict[str, AgentTool]) -> None:
    for tool in tools.values():
        assert tool.prompt_guidelines, f"{tool.name} 缺少 prompt_guidelines"
        assert tool.render_call is not None


def test_only_query_tool_exists(tools: dict[str, AgentTool]) -> None:
    """V1 弃用断言：bible 工具只剩 bible_query，写工具不再存在。"""
    assert set(tools) == {"bible_query"}


def test_query_render_call(tools: dict[str, AgentTool]) -> None:
    render = tools["bible_query"].render_call
    assert render is not None
    assert render({"entity_type": "character", "name": "波洛"}) == "查询角色 波洛"
    assert render({"entity_type": "clue"}) == "列出全部线索"
