"""v1.1 Q1：Agent 结构文档工具——授权走任务范围，门禁在 structure_io。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hastings.workspace.agent import RunContext, build_tools
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import STRUCTURE_PATH, Workspace
from hastings.workspace.git import GitRepo
from hastings.workspace.service import WriteSession
from tau_agent.tools import AgentTool

GOOD = "## 铁律\n第三人称限制视角。\n\n## 路线\n暴雪之夜逐步收束。\n"


async def _call(tool: AgentTool, **arguments: Any) -> dict[str, Any]:
    result = await tool.execute("call-1", arguments)
    return json.loads(result.text)


async def _grant_structure(tools: dict[str, AgentTool]) -> None:
    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="起步起草全书结构",
        authorization="就这样开始",
        grants=[
            {"action": action, "path": STRUCTURE_PATH}
            for action in ("create", "update")
        ],
    )


def _setup(tmp_path: Path) -> tuple[Workspace, dict[str, AgentTool]]:
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    GitRepo(workspace.root).init()
    session = WriteSession(workspace, instruction="起步")
    run = RunContext(
        workspace,
        session,
        author_messages=((1, "就这样开始"),),
        state_root=None,
    )
    tools = {tool.name: tool for tool in build_tools(workspace, run)}
    return workspace, tools


@pytest.mark.anyio
async def test_read_structure_missing_is_empty(tmp_path: Path) -> None:
    _, tools = _setup(tmp_path)
    payload = await _call(tools["read_structure"])
    assert payload["exists"] is False
    assert payload["text"] == "" and payload["digest"] is None


@pytest.mark.anyio
async def test_write_structure_requires_task_scope(tmp_path: Path) -> None:
    _, tools = _setup(tmp_path)
    with pytest.raises(WorkspaceError) as exc:
        await _call(tools["write_structure"], text=GOOD)
    assert exc.value.code == "TASK_SCOPE_REQUIRED_CONFLICT"
    assert not (tmp_path / "雪山旅馆" / STRUCTURE_PATH).exists()


@pytest.mark.anyio
async def test_write_structure_create_and_update(tmp_path: Path) -> None:
    workspace, tools = _setup(tmp_path)
    await _grant_structure(tools)
    created = await _call(tools["write_structure"], text=GOOD)
    assert created["path"] == STRUCTURE_PATH
    readback = await _call(tools["read_structure"])
    assert readback["exists"] and readback["digest"] == created["digest"]

    # 改写必须先读（读取前提），再带 digest。
    with pytest.raises(WorkspaceError):
        await _call(tools["write_structure"], text=GOOD + "\n## 骨架\n1. 抵达。\n")
    updated = await _call(
        tools["write_structure"],
        text=GOOD + "\n## 骨架\n1. 抵达。\n",
        digest=created["digest"],
    )
    assert updated["digest"] != created["digest"]
    assert "## 骨架" in workspace.read(STRUCTURE_PATH).text


@pytest.mark.anyio
async def test_write_structure_create_requires_marker(tmp_path: Path) -> None:
    _, tools = _setup(tmp_path)
    await _grant_structure(tools)
    with pytest.raises(WorkspaceError) as exc:
        await _call(tools["write_structure"], text="# 我的作品\n## 铁律\n规则\n")
    assert exc.value.code == "STRUCTURE_MARKER_REQUIRED"


@pytest.mark.anyio
async def test_write_structure_budget_conflict(tmp_path: Path) -> None:
    workspace, tools = _setup(tmp_path)
    await _grant_structure(tools)
    created = await _call(tools["write_structure"], text=GOOD)
    await _call(tools["read_structure"])
    with pytest.raises(WorkspaceError) as exc:
        await _call(
            tools["write_structure"],
            text="## 铁律\n规则\n" + "x" * 1700,
            digest=created["digest"],
        )
    assert exc.value.code == "STRUCTURE_BUDGET_CONFLICT"
    assert workspace.read(STRUCTURE_PATH).digest == created["digest"]
