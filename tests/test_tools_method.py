"""method_lock / method_unlock 单测：全链路 + upsert 锁定拦截 + 审计断言（P2 T4）。"""

from collections.abc import Mapping
from pathlib import Path

import pytest

from hastings.bible.models import Method
from hastings.bible.store import StoryWorkspace, load_ledger
from hastings.tools.bible_tools import create_bible_tools
from hastings.tools.errors import ToolInputError
from hastings.tools.method_tools import create_method_tools
from tau_agent import AgentTool
from tau_agent.types import JSONValue


@pytest.fixture
def workspace(tmp_path: Path) -> StoryWorkspace:
    return StoryWorkspace(root=tmp_path / "stories", slug="demo")


@pytest.fixture
def tools(workspace: StoryWorkspace) -> dict[str, AgentTool]:
    created = create_bible_tools(workspace) + create_method_tools(workspace)
    return {tool.name: tool for tool in created}


async def _call(tool: AgentTool, arguments: Mapping[str, JSONValue]) -> object:
    return await tool.execute("call-1", arguments)


async def _draft_method(tools: dict[str, AgentTool]) -> None:
    await _call(
        tools["bible_upsert"],
        {
            "entity_type": "method",
            "data": {"name": "密室诡计", "description": "冰锥密室", "time_window": "第3天晚"},
        },
    )


def _load_single(workspace: StoryWorkspace) -> Method:
    methods = load_ledger(workspace.method_path, Method)
    assert len(methods) == 1
    return methods[0]


# ---------- method_lock ----------


@pytest.mark.anyio
async def test_lock_drafts_to_locked_with_history(
    tools: dict[str, AgentTool], workspace: StoryWorkspace
) -> None:
    await _draft_method(tools)
    result = await _call(
        tools["method_lock"],
        {"name": "密室诡计", "chapter": "ch01", "reason": "诡测定稿"},
    )

    assert "已锁定" in result.text
    method = _load_single(workspace)
    assert method.status == "locked"
    assert [h.action for h in method.history] == ["locked"]
    assert method.history[0].chapter == "ch01"
    assert method.history[0].reason == "诡测定稿"
    assert method.history[0].timestamp  # ISO 时间戳非空


@pytest.mark.anyio
async def test_lock_rejects_unknown_method(tools: dict[str, AgentTool]) -> None:
    with pytest.raises(ToolInputError, match="不存在"):
        await _call(tools["method_lock"], {"name": "幽灵诡计", "chapter": "ch01", "reason": "x"})


@pytest.mark.anyio
async def test_lock_rejects_already_locked(tools: dict[str, AgentTool]) -> None:
    await _draft_method(tools)
    await _call(tools["method_lock"], {"name": "密室诡计", "chapter": "ch01", "reason": "定稿"})

    with pytest.raises(ToolInputError, match="已是 locked"):
        await _call(tools["method_lock"], {"name": "密室诡计", "chapter": "ch02", "reason": "重复"})


@pytest.mark.anyio
async def test_lock_rejects_revealed_terminal(tools: dict[str, AgentTool]) -> None:
    await _draft_method(tools)
    await _call(tools["method_lock"], {"name": "密室诡计", "chapter": "ch01", "reason": "定稿"})
    await _call(
        tools["bible_upsert"],
        {
            "entity_type": "method",
            "data": {
                "name": "密室诡计",
                "description": "冰锥密室",
                "time_window": "第3天晚",
                "status": "revealed",
            },
        },
    )

    with pytest.raises(ToolInputError, match="revealed"):
        await _call(tools["method_lock"], {"name": "密室诡计", "chapter": "ch02", "reason": "x"})


@pytest.mark.anyio
async def test_lock_requires_non_empty_params(tools: dict[str, AgentTool]) -> None:
    await _draft_method(tools)

    with pytest.raises(ToolInputError, match="reason"):
        await _call(tools["method_lock"], {"name": "密室诡计", "chapter": "ch01", "reason": ""})
    with pytest.raises(ToolInputError, match="chapter"):
        await _call(tools["method_lock"], {"name": "密室诡计", "reason": "缺章节"})


# ---------- method_unlock ----------


@pytest.mark.anyio
async def test_unlock_returns_to_drafted_with_history(
    tools: dict[str, AgentTool], workspace: StoryWorkspace
) -> None:
    await _draft_method(tools)
    await _call(tools["method_lock"], {"name": "密室诡计", "chapter": "ch01", "reason": "定稿"})

    result = await _call(tools["method_unlock"], {"name": "密室诡计", "reason": "时间线对不上"})

    assert "已解锁" in result.text
    method = _load_single(workspace)
    assert method.status == "drafted"
    assert [h.action for h in method.history] == ["locked", "unlocked"]
    assert method.history[1].reason == "时间线对不上"
    assert method.history[1].chapter == ""  # 解锁不记章节
    assert method.history[1].timestamp


@pytest.mark.anyio
async def test_unlock_rejects_drafted_method(tools: dict[str, AgentTool]) -> None:
    await _draft_method(tools)

    with pytest.raises(ToolInputError, match="drafted"):
        await _call(tools["method_unlock"], {"name": "密室诡计", "reason": "未锁定"})


@pytest.mark.anyio
async def test_unlock_requires_reason(tools: dict[str, AgentTool]) -> None:
    await _draft_method(tools)
    await _call(tools["method_lock"], {"name": "密室诡计", "chapter": "ch01", "reason": "定稿"})

    with pytest.raises(ToolInputError, match="reason"):
        await _call(tools["method_unlock"], {"name": "密室诡计"})


# ---------- 与 bible_upsert 的锁定拦截协同 ----------


@pytest.mark.anyio
async def test_upsert_rejects_editing_locked_method(
    tools: dict[str, AgentTool], workspace: StoryWorkspace
) -> None:
    await _draft_method(tools)
    await _call(tools["method_lock"], {"name": "密室诡计", "chapter": "ch01", "reason": "定稿"})

    with pytest.raises(ToolInputError, match="method_unlock"):
        await _call(
            tools["bible_upsert"],
            {
                "entity_type": "method",
                "data": {"name": "密室诡计", "description": "改成撬锁入室"},
            },
        )
    # 拒绝后账本原样：内容仍是被锁版本
    method = _load_single(workspace)
    assert method.description == "冰锥密室"
    assert method.status == "locked"


@pytest.mark.anyio
async def test_upsert_rejects_locked_to_drafted(
    tools: dict[str, AgentTool], workspace: StoryWorkspace
) -> None:
    """locked→drafted 这条边只归 method_unlock，upsert 不得绕行。"""
    await _draft_method(tools)
    await _call(tools["method_lock"], {"name": "密室诡计", "chapter": "ch01", "reason": "定稿"})

    with pytest.raises(ToolInputError, match="method_unlock"):
        await _call(
            tools["bible_upsert"],
            {"entity_type": "method", "data": {"name": "密室诡计", "status": "drafted"}},
        )
    assert _load_single(workspace).status == "locked"


@pytest.mark.anyio
async def test_upsert_locked_allows_noop_and_reveal(
    tools: dict[str, AgentTool], workspace: StoryWorkspace
) -> None:
    await _draft_method(tools)
    await _call(tools["method_lock"], {"name": "密室诡计", "chapter": "ch01", "reason": "定稿"})

    # no-op 覆盖写（字段全同）放行
    result = await _call(
        tools["bible_upsert"],
        {
            "entity_type": "method",
            "data": {"name": "密室诡计", "description": "冰锥密室", "time_window": "第3天晚"},
        },
    )
    assert "已写入" in result.text

    # 全量回传 + 仅 status=revealed 放行（locked→revealed 是唯一经 upsert 的出边）
    await _call(
        tools["bible_upsert"],
        {
            "entity_type": "method",
            "data": {
                "name": "密室诡计",
                "description": "冰锥密室",
                "time_window": "第3天晚",
                "status": "revealed",
            },
        },
    )
    method = _load_single(workspace)
    assert method.status == "revealed"
    assert method.description == "冰锥密室"  # 全量回传，内容未被覆盖写清空


@pytest.mark.anyio
async def test_upsert_locked_rejects_reveal_with_field_change(
    tools: dict[str, AgentTool],
) -> None:
    """借 revealed 之名夹带内容修改，必须整体被拒。"""
    await _draft_method(tools)
    await _call(tools["method_lock"], {"name": "密室诡计", "chapter": "ch01", "reason": "定稿"})

    with pytest.raises(ToolInputError, match="method_unlock"):
        await _call(
            tools["bible_upsert"],
            {
                "entity_type": "method",
                "data": {"name": "密室诡计", "description": "顺手改内容", "status": "revealed"},
            },
        )


# ---------- 全链路（计划 T4 验收路径） ----------


@pytest.mark.anyio
async def test_full_chain_lock_edit_unlock_relock_reveal(
    tools: dict[str, AgentTool], workspace: StoryWorkspace
) -> None:
    await _draft_method(tools)
    await _call(tools["method_lock"], {"name": "密室诡计", "chapter": "ch01", "reason": "定稿"})
    with pytest.raises(ToolInputError):
        await _call(
            tools["bible_upsert"],
            {"entity_type": "method", "data": {"name": "密室诡计", "description": "x"}},
        )
    await _call(tools["method_unlock"], {"name": "密室诡计", "reason": "动机不成立"})
    await _call(
        tools["bible_upsert"],
        {"entity_type": "method", "data": {"name": "密室诡计", "description": "撬锁入室"}},
    )
    await _call(tools["method_lock"], {"name": "密室诡计", "chapter": "ch02", "reason": "二次定稿"})
    await _call(
        tools["bible_upsert"],
        {
            "entity_type": "method",
            "data": {"name": "密室诡计", "description": "撬锁入室", "status": "revealed"},
        },
    )

    method = _load_single(workspace)
    assert method.status == "revealed"
    assert method.description == "撬锁入室"
    assert [h.action for h in method.history] == ["locked", "unlocked", "locked"]
    assert [h.chapter for h in method.history] == ["ch01", "", "ch02"]
