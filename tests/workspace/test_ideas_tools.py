"""灵感专用工具与回合材料（0.8 C）。

灵感默认不进入目录、搜索或上下文；只有作者本轮选中的修订可以读取，明确要求检索时
才可用搜索，明确说「记下来」时才允许保存。下一轮不继承上一轮的搜索许可。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hastings.workspace.agent import RunContext, build_tools
from hastings.workspace.context import ideas_section
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.ideas import IdeaStore
from hastings.workspace.materials import ContextRequest, freeze_context
from hastings.workspace.service import WriteSession
from tau_agent.tools import AgentTool


def _workspace(tmp_path: Path) -> Workspace:
    workspace = Workspace.create(tmp_path / "book")
    workspace.write("正文/0001 雨夜.md", "# 雨夜\n\n侦探抵达旅馆。\n", expected_digest=None)
    return workspace


def _run(workspace: Workspace, *texts: str) -> RunContext:
    return RunContext(
        workspace,
        WriteSession(workspace),
        author_messages=tuple((index + 1, text) for index, text in enumerate(texts)),
        turn_id="turn-idea-1",
    )


async def _call(tool: AgentTool, **arguments: Any) -> dict[str, Any]:
    result = await tool.execute("call-1", arguments)
    return json.loads(result.text)


def test_selected_idea_freezes_its_revision_and_body(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    idea = IdeaStore(workspace).create(
        title="旧识", body="老板娘认识死者。", request_key="idea-key-1"
    )

    snapshot = freeze_context(
        workspace,
        ContextRequest(idea_references=((idea["idea_id"], idea["revision_id"]),)),
    )

    assert len(snapshot.ideas) == 1
    assert snapshot.ideas[0].body == "老板娘认识死者。"
    assert "老板娘认识死者。" in ideas_section(snapshot)
    assert snapshot.identity()["idea_references"] == [[idea["idea_id"], idea["revision_id"]]]


def test_deleted_idea_cannot_be_selected(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = IdeaStore(workspace)
    idea = store.create(title="旧识", body="文本。", request_key="idea-key-1")
    deleted = store.trash(
        idea["idea_id"],
        expected_digest=str(idea["digest"]),
        expected_revision=str(idea["revision_id"]),
        request_key="idea-key-2",
    )

    with pytest.raises(WorkspaceError) as excinfo:
        freeze_context(
            workspace,
            ContextRequest(idea_references=((deleted["idea_id"], deleted["revision_id"]),)),
        )

    assert excinfo.value.code == "CONTEXT_SOURCE_NOT_FOUND"


@pytest.mark.anyio
async def test_agent_can_read_only_the_selected_idea(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = IdeaStore(workspace)
    selected = store.create(title="旧识", body="老板娘认识死者。", request_key="idea-key-1")
    other = store.create(title="无关", body="另一条笔记。", request_key="idea-key-2")
    run = _run(workspace, "只讨论选中的那条。")
    snapshot = freeze_context(
        workspace,
        ContextRequest(idea_references=((selected["idea_id"], selected["revision_id"]),)),
    )
    run.idea_references = snapshot.ideas
    tools = {tool.name: tool for tool in build_tools(workspace, run)}

    read = await _call(
        tools["read_idea"],
        idea_id=selected["idea_id"],
        revision_id=selected["revision_id"],
    )
    assert read["body"] == "老板娘认识死者。"

    with pytest.raises(WorkspaceError) as denied:
        await _call(tools["read_idea"], idea_id=other["idea_id"])
    assert denied.value.code == "IDEA_ACCESS_DENIED"


@pytest.mark.anyio
async def test_search_requires_an_explicit_request_and_expires_next_turn(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    idea = IdeaStore(workspace).create(
        title="蓝鲸旅馆", body="房卡可以打开后门。", request_key="idea-key-1"
    )
    run = _run(workspace, "帮我找灵感，有没有房卡相关的内容？")
    tools = {tool.name: tool for tool in build_tools(workspace, run)}

    found = await _call(tools["search_ideas"], query="房卡")
    assert found["items"][0]["idea_id"] == idea["idea_id"]
    read = await _call(
        tools["read_idea"],
        idea_id=idea["idea_id"],
        revision_id=found["items"][0]["head_revision_id"],
    )
    assert "房卡" in read["body"]

    next_turn = _run(workspace, "继续写下一段。")
    next_tools = {tool.name: tool for tool in build_tools(workspace, next_turn)}
    with pytest.raises(WorkspaceError) as denied:
        await _call(next_tools["search_ideas"], query="房卡")
    assert denied.value.code == "IDEA_SEARCH_DENIED"


@pytest.mark.anyio
async def test_save_idea_requires_the_author_to_ask_for_it(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    tools = {tool.name: tool for tool in build_tools(workspace, _run(workspace, "继续写。"))}
    with pytest.raises(WorkspaceError) as denied:
        await _call(tools["save_idea"], body="门后有第三个人。")
    assert denied.value.code == "IDEA_SAVE_DENIED"

    asking = _run(workspace, "把「门后有第三个人」记下来。")
    asking_tools = {tool.name: tool for tool in build_tools(workspace, asking)}
    saved = await _call(asking_tools["save_idea"], title="第三人", body="门后有第三个人。")
    assert saved["idea_id"]

    stored = IdeaStore(workspace).get(saved["idea_id"])
    assert stored["body"] == "门后有第三个人。"
