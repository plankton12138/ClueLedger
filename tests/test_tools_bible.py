"""bible_upsert / bible_query 工具单测：正常路径 + 每条约束的非法输入。"""

from collections.abc import Mapping
from pathlib import Path

import pytest

from hastings.bible.store import StoryWorkspace, list_characters
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


# ---------- bible_upsert ----------


@pytest.mark.anyio
async def test_upsert_saves_character_file(
    tools: dict[str, AgentTool], workspace: StoryWorkspace
) -> None:
    result = await _call(
        tools["bible_upsert"],
        {
            "entity_type": "character",
            "data": {"name": "波洛", "identity": "退休侦探", "relationships": ["信任黑斯廷斯"]},
        },
    )

    assert "波洛" in result.text
    assert isinstance(result.details, dict)
    assert result.details["name"] == "波洛"
    saved = list_characters(workspace)
    assert [c.name for c in saved] == ["波洛"]
    assert saved[0].relationships == ["信任黑斯廷斯"]


@pytest.mark.anyio
async def test_upsert_same_name_overwrites(
    tools: dict[str, AgentTool], workspace: StoryWorkspace
) -> None:
    await _call(
        tools["bible_upsert"],
        {"entity_type": "character", "data": {"name": "波洛", "identity": "退休侦探"}},
    )
    await _call(
        tools["bible_upsert"],
        {"entity_type": "character", "data": {"name": "波洛", "identity": "比利时侦探"}},
    )

    saved = list_characters(workspace)
    assert len(saved) == 1
    assert saved[0].identity == "比利时侦探"


@pytest.mark.anyio
async def test_upsert_rejects_unsupported_entity_type(tools: dict[str, AgentTool]) -> None:
    with pytest.raises(ToolInputError, match="Phase 2"):
        await _call(tools["bible_upsert"], {"entity_type": "clue", "data": {"name": "x"}})


@pytest.mark.anyio
async def test_upsert_rejects_non_object_data(tools: dict[str, AgentTool]) -> None:
    with pytest.raises(ToolInputError, match="data"):
        await _call(tools["bible_upsert"], {"entity_type": "character", "data": "波洛"})


@pytest.mark.anyio
async def test_upsert_rejects_missing_name(tools: dict[str, AgentTool]) -> None:
    with pytest.raises(ToolInputError, match="name"):
        await _call(tools["bible_upsert"], {"entity_type": "character", "data": {}})


@pytest.mark.anyio
async def test_upsert_rejects_path_traversal_name(tools: dict[str, AgentTool]) -> None:
    with pytest.raises(ToolInputError, match="Invalid entity name"):
        await _call(
            tools["bible_upsert"],
            {"entity_type": "character", "data": {"name": "../evil"}},
        )


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
    del workspace
    for name in ["波洛", "马普尔"]:
        await _call(
            tools["bible_upsert"],
            {"entity_type": "character", "data": {"name": name, "motivation": "真相"}},
        )

    result = await _call(tools["bible_query"], {"entity_type": "character"})

    assert "波洛" in result.text
    assert "马普尔" in result.text
    assert result.details == {"count": 2, "names": ["波洛", "马普尔"]}


@pytest.mark.anyio
async def test_query_by_name_returns_yaml(tools: dict[str, AgentTool]) -> None:
    await _call(
        tools["bible_upsert"],
        {"entity_type": "character", "data": {"name": "波洛", "identity": "退休侦探"}},
    )

    result = await _call(tools["bible_query"], {"entity_type": "character", "name": "波洛"})

    assert "identity: 退休侦探" in result.text
    assert isinstance(result.details, dict) and result.details["found"] is True


@pytest.mark.anyio
async def test_query_missing_name_returns_hint_not_error(tools: dict[str, AgentTool]) -> None:
    result = await _call(tools["bible_query"], {"entity_type": "character", "name": "不存在"})

    assert "不存在" in result.text
    assert result.details == {"found": False, "name": "不存在"}


@pytest.mark.anyio
async def test_query_rejects_unsupported_entity_type(tools: dict[str, AgentTool]) -> None:
    with pytest.raises(ToolInputError, match="Phase 2"):
        await _call(tools["bible_query"], {"entity_type": "clue"})


# ---------- 元信息 ----------


def test_tools_carry_prompt_guidelines(tools: dict[str, AgentTool]) -> None:
    for tool in tools.values():
        assert tool.prompt_guidelines, f"{tool.name} 缺少 prompt_guidelines"
        assert tool.render_call is not None


def test_upsert_render_call(tools: dict[str, AgentTool]) -> None:
    render = tools["bible_upsert"].render_call
    assert render is not None
    assert render({"entity_type": "character", "data": {"name": "波洛"}}) == "写入角色 波洛"
    assert render({"entity_type": "character"}) == "写入角色"
