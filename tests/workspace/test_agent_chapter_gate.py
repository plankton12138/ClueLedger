"""1.0.9 P6：Agent 正文写入只受全书构思门禁约束。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hastings.workspace.agent import RunContext, build_tools
from hastings.workspace.chapter_write_policy import ChapterWritePolicy
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.git import GitRepo
from hastings.workspace.service import WriteSession
from hastings.workspace.story_store import EMPTY_REVISION, StoryStore
from hastings.workspace.writing_actions import save_book_intent
from tau_agent.tools import AgentTool


async def _call(tool: AgentTool, **arguments: Any) -> dict[str, Any]:
    result = await tool.execute("call-1", arguments)
    return json.loads(result.text)


async def _grant(tools: dict[str, AgentTool], *paths: str) -> None:
    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="改写这一章",
        authorization="写第一章",
        grants=[
            {"action": action, "path": path}
            for path in paths
            for action in ("create", "update", "delete")
        ],
    )


def _setup(tmp_path: Path) -> tuple[Workspace, StoryStore, dict[str, AgentTool]]:
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    GitRepo(workspace.root).init()
    state_root = str(tmp_path / "state")
    store = StoryStore(Path(state_root), workspace, format_version=4)
    workspace.chapter_policy = ChapterWritePolicy(store)
    session = WriteSession(workspace, instruction="改写第一章")
    run = RunContext(
        workspace,
        session,
        author_messages=((1, "改写第一章，加入湿手套。"),),
        state_root=Path(state_root),
    )
    tools = {tool.name: tool for tool in build_tools(workspace, run)}
    return workspace, store, tools


@pytest.mark.anyio
async def test_agent_write_requires_book_intent(tmp_path: Path) -> None:
    workspace, store, tools = _setup(tmp_path)
    path = "正文/0001 第一章.md"
    await _grant(tools, path)
    with pytest.raises(WorkspaceError) as exc:
        await _call(tools["write_file"], path=path, text="# 第一章\n\n林砚走进旅馆。\n")
    assert exc.value.code == "BOOK_INTENT_REQUIRED"
    assert not (workspace.root / path).exists()

    save_book_intent(
        store,
        text="暴雪封山的旅馆发生命案。",
        expected_plan_revision=EMPTY_REVISION,
        request_key="book",
    )
    await _call(tools["write_file"], path=path, text="# 第一章\n\n林砚走进旅馆。\n")
    assert (workspace.root / path).exists()

    view = workspace.read(path)
    await _grant(tools, path)
    await _call(tools["read_file"], path=path)
    edited = await _call(
        tools["edit_file"],
        path=path,
        old_text="林砚走进旅馆。",
        new_text="林砚走进旅馆，看见湿手套。",
        digest=view.digest,
    )
    assert edited["digest"] != view.digest
    assert "湿手套" in workspace.read(path).text
