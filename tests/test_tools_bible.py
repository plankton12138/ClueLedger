"""bible_upsert / bible_query 工具单测：五账本正常路径 + 每条约束的非法输入。"""

from collections.abc import Mapping
from pathlib import Path

import pytest

from hastings.bible.models import Clue, Foreshadow, TimelineEntry
from hastings.bible.store import (
    StoryWorkspace,
    list_characters,
    load_ledger,
)
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


# --- schema 自描述（F1 走查修复）：描述带类型形状 + 最小正确示例 ---
#
# 走查发现模型对同一 schema 错误原样重试 7-16 次才改行为（relationships 传成
# 字符串 ×7、requirements ×1、timeline 缺 name ×16）。缓解手段：类型形状直接
# 写进参数描述，最小正确示例进 prompt_guidelines——让模型在首次调用前就看到对的样子。


def test_upsert_data_description_documents_list_field_shapes(
    tools: dict[str, AgentTool],
) -> None:
    data_desc = tools["bible_upsert"].parameters["properties"]["data"]["description"]

    assert "字符串列表" in data_desc
    assert "relationships" in data_desc
    assert "requirements" in data_desc
    assert "participants" in data_desc


def test_upsert_data_description_requires_name_for_all_entities(
    tools: dict[str, AgentTool],
) -> None:
    """name 必填须覆盖全部六类实体——timeline ×16 缺 name 报错的根因锚点。"""
    data_desc = tools["bible_upsert"].parameters["properties"]["data"]["description"]

    assert "name" in data_desc
    assert "timeline" in data_desc
    assert "必填" in data_desc


def test_upsert_guidelines_carry_minimal_correct_example(
    tools: dict[str, AgentTool],
) -> None:
    guidelines = "\n".join(tools["bible_upsert"].prompt_guidelines)

    assert "示例" in guidelines
    assert "字符串列表" in guidelines  # 列表字段别传成字符串的最小提醒


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
async def test_upsert_rejects_unknown_entity_type(tools: dict[str, AgentTool]) -> None:
    with pytest.raises(ToolInputError, match="可选值"):
        await _call(tools["bible_upsert"], {"entity_type": "plot", "data": {"name": "x"}})


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
async def test_query_rejects_unknown_entity_type(tools: dict[str, AgentTool]) -> None:
    with pytest.raises(ToolInputError, match="可选值"):
        await _call(tools["bible_query"], {"entity_type": "plot"})


# ---------- 五账本泛化（P2 T3） ----------


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("entity_type", "data", "yaml_fragment"),
    [
        (
            "clue",
            {"name": "染血的信", "content": "边缘干涸血迹", "chapter": "ch02"},
            "content: 边缘干涸血迹",
        ),
        (
            "foreshadow",
            {"name": "停摆的挂钟", "content": "指针停在 21:47", "plant_chapter": "ch01"},
            "plant_chapter: ch01",
        ),
        ("method", {"name": "密室诡计", "description": "延时反锁"}, "description: 延时反锁"),
        ("timeline", {"name": "发现尸体", "day": 1, "chapter": "ch01"}, "day: 1"),
    ],
)
async def test_upsert_query_ledger_roundtrip(
    tools: dict[str, AgentTool],
    entity_type: str,
    data: dict[str, JSONValue],
    yaml_fragment: str,
) -> None:
    await _call(tools["bible_upsert"], {"entity_type": entity_type, "data": data})

    result = await _call(tools["bible_query"], {"entity_type": entity_type, "name": data["name"]})

    assert yaml_fragment in result.text
    assert isinstance(result.details, dict) and result.details["found"] is True


@pytest.mark.anyio
async def test_upsert_status_transition_moves_ledger(
    tools: dict[str, AgentTool], workspace: StoryWorkspace
) -> None:
    """planned → planted 合法迁移落盘；迁移后旧值不被覆盖回退。"""
    await _call(
        tools["bible_upsert"],
        {"entity_type": "foreshadow", "data": {"name": "挂钟", "content": "停摆"}},
    )
    await _call(
        tools["bible_upsert"],
        {"entity_type": "foreshadow", "data": {"name": "挂钟", "status": "planted"}},
    )

    loaded = load_ledger(workspace.foreshadows_path, Foreshadow)
    assert [f.status for f in loaded] == ["planted"]


@pytest.mark.anyio
async def test_upsert_rejects_illegal_transition_with_legal_targets(
    tools: dict[str, AgentTool], workspace: StoryWorkspace
) -> None:
    """planted → planned 非法；错误消息列出合法去向，且落盘不被破坏。"""
    await _call(
        tools["bible_upsert"],
        {"entity_type": "foreshadow", "data": {"name": "挂钟", "content": "停摆"}},
    )
    await _call(
        tools["bible_upsert"],
        {"entity_type": "foreshadow", "data": {"name": "挂钟", "status": "planted"}},
    )

    with pytest.raises(ToolInputError, match="合法去向") as exc_info:
        await _call(
            tools["bible_upsert"],
            {"entity_type": "foreshadow", "data": {"name": "挂钟", "status": "planned"}},
        )
    assert "echoed" in str(exc_info.value) and "resolved" in str(exc_info.value)
    assert load_ledger(workspace.foreshadows_path, Foreshadow)[0].status == "planted"


@pytest.mark.anyio
async def test_upsert_new_entity_rejects_non_initial_status(
    tools: dict[str, AgentTool],
) -> None:
    """防直接造出终态绕过流程：新建即 resolved 被拒并指出初始状态。"""
    with pytest.raises(ToolInputError, match="初始状态"):
        await _call(
            tools["bible_upsert"],
            {"entity_type": "clue", "data": {"name": "染血的信", "status": "resolved"}},
        )


@pytest.mark.anyio
async def test_upsert_without_status_keeps_old_value(
    tools: dict[str, AgentTool], workspace: StoryWorkspace
) -> None:
    """更新未传 status → 保持旧值，绝不静默重置回初始状态（D1）。"""
    await _call(
        tools["bible_upsert"],
        {"entity_type": "clue", "data": {"name": "染血的信", "content": "血迹"}},
    )
    await _call(
        tools["bible_upsert"],
        {"entity_type": "clue", "data": {"name": "染血的信", "content": "更多血迹"}},
    )

    loaded = load_ledger(workspace.clues_path, Clue)
    assert loaded[0].status == "planned"
    assert loaded[0].content == "更多血迹"


@pytest.mark.anyio
async def test_upsert_character_arc_uses_state_machine(
    tools: dict[str, AgentTool], workspace: StoryWorkspace
) -> None:
    """character 的状态字段是 arc：flat → transformed 跳级被拒，逐级迁移合法。"""
    await _call(tools["bible_upsert"], {"entity_type": "character", "data": {"name": "波洛"}})

    with pytest.raises(ToolInputError, match="developing"):
        await _call(
            tools["bible_upsert"],
            {"entity_type": "character", "data": {"name": "波洛", "arc": "transformed"}},
        )

    await _call(
        tools["bible_upsert"],
        {"entity_type": "character", "data": {"name": "波洛", "arc": "developing"}},
    )
    assert list_characters(workspace)[0].arc == "developing"


@pytest.mark.anyio
async def test_upsert_timeline_rejects_unregistered_participants(
    tools: dict[str, AgentTool], workspace: StoryWorkspace
) -> None:
    """D8：参与者必须已建档，错误消息指引先建角色（三要素）。"""
    with pytest.raises(ToolInputError, match="建档"):
        await _call(
            tools["bible_upsert"],
            {
                "entity_type": "timeline",
                "data": {"name": "大厅争执", "day": 2, "participants": ["马普尔"]},
            },
        )
    # 建档后通过
    await _call(
        tools["bible_upsert"],
        {"entity_type": "character", "data": {"name": "马普尔", "identity": "老小姐"}},
    )
    result = await _call(
        tools["bible_upsert"],
        {
            "entity_type": "timeline",
            "data": {"name": "大厅争执", "day": 2, "participants": ["马普尔"]},
        },
    )
    assert "大厅争执" in result.text
    entry = load_ledger(workspace.timeline_path, TimelineEntry)[0]
    assert entry.participants == ["马普尔"]


@pytest.mark.anyio
async def test_query_ledger_empty_returns_guidance_not_error(
    tools: dict[str, AgentTool],
) -> None:
    """泄压阀：空账本 ≠ error，返回引导性提示。"""
    result = await _call(tools["bible_query"], {"entity_type": "method"})

    assert "为空" in result.text
    assert result.details == {"count": 0, "names": []}


@pytest.mark.anyio
async def test_query_ledger_lists_summaries(tools: dict[str, AgentTool]) -> None:
    for data in [
        {"name": "染血的信", "content": "血迹", "chapter": "ch02"},
        {"name": "半张车票", "content": "撕角", "points_to": "管家"},
    ]:
        await _call(tools["bible_upsert"], {"entity_type": "clue", "data": data})

    result = await _call(tools["bible_query"], {"entity_type": "clue"})

    assert "染血的信" in result.text and "半张车票" in result.text
    assert "planned" in result.text  # 摘要行带状态，模型对账不查全文
    assert result.details == {"count": 2, "names": ["染血的信", "半张车票"]}


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
    # 泛化：账本类型带中文名，状态迁移带 → 状态
    assert (
        render({"entity_type": "foreshadow", "data": {"name": "F1", "status": "planted"}})
        == "写入伏笔 F1 → planted"
    )
    assert (
        render({"entity_type": "character", "data": {"name": "波洛", "arc": "developing"}})
        == "写入角色 波洛 → developing"
    )
    assert render({"entity_type": "clue", "data": {"name": "C1"}}) == "写入线索 C1"


def test_query_render_call(tools: dict[str, AgentTool]) -> None:
    render = tools["bible_query"].render_call
    assert render is not None
    assert render({"entity_type": "character", "name": "波洛"}) == "查询角色 波洛"
    assert render({"entity_type": "clue"}) == "列出全部线索"
