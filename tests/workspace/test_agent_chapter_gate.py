"""1.0.9 P2：Agent 正文写入必须使用服务端的章修订与构思门槛。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hastings.workspace.agent import RunContext, build_tools
from hastings.workspace.chapter_write_policy import ChapterWritePolicy
from hastings.workspace.chapter_writing import start_chapter
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.git import GitRepo
from hastings.workspace.service import WriteSession
from hastings.workspace.story_store import EMPTY_REVISION, MAIN_VARIANT_ID, StoryStore
from hastings.workspace.writing_actions import (
    create_chapter_plan,
    save_book_intent,
    save_chapter_plan,
)
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


def _setup(tmp_path: Path) -> tuple[Workspace, StoryStore, dict[str, AgentTool], str]:
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    GitRepo(workspace.root).init()
    state_root = str(tmp_path / "state")
    store = StoryStore(Path(state_root), workspace, format_version=3)
    workspace.chapter_policy = ChapterWritePolicy(store)
    save_book_intent(
        store,
        text="暴雪封山的旅馆发生命案。",
        expected_plan_revision=EMPTY_REVISION,
        request_key="book",
    )
    variant = store.require_variant(MAIN_VARIANT_ID)
    shell = create_chapter_plan(
        store,
        title="第一章",
        after_chapter_id=None,
        expected_plan_revision=variant.revision,
        request_key="chapter",
    )
    variant = store.require_variant(MAIN_VARIANT_ID)
    plan = save_chapter_plan(
        store,
        chapter_id=shell.chapter_id,
        intent="入住并起疑，湿手套最后出现。",
        title="第一章",
        information_moves=None,
        expected_chapter_revision=variant.chapter(shell.chapter_id).revision,
        expected_plan_revision=variant.revision,
        request_key="plan",
    )
    variant = store.require_variant(MAIN_VARIANT_ID)
    start_chapter(
        store,
        workspace,
        chapter_id=shell.chapter_id,
        expected_story_revision=variant.revision,
        expected_chapter_revision=plan.chapter_revision,
        request_key="start",
    )
    session = WriteSession(workspace, instruction="改写第一章")
    run = RunContext(
        workspace,
        session,
        author_messages=((1, "改写第一章，加入湿手套。"),),
        state_root=Path(state_root),
    )
    tools = {tool.name: tool for tool in build_tools(workspace, run)}
    return workspace, store, tools, shell.chapter_id


@pytest.mark.anyio
async def test_agent_write_uses_current_chapter_revision(tmp_path: Path) -> None:
    workspace, store, tools, chapter_id = _setup(tmp_path)
    path = store.require_variant(MAIN_VARIANT_ID).chapter_plans[chapter_id].prose_path
    assert path is not None
    view = workspace.read(path)

    await _grant(tools, path)
    await _call(tools["read_file"], path=path)
    result = await _call(
        tools["write_file"],
        path=path,
        text=view.text + "\n林砚走进旅馆。\n",
        digest=view.digest,
    )
    assert result["digest"] != view.digest

    variant = store.require_variant(MAIN_VARIANT_ID)
    plan = variant.chapter(chapter_id)
    assert plan is not None
    save_chapter_plan(
        store,
        chapter_id=chapter_id,
        intent="入住、封山并起疑。",
        title="第一章",
        information_moves=None,
        expected_chapter_revision=plan.revision,
        expected_plan_revision=variant.revision,
        request_key="plan:change",
    )
    new_view = workspace.read(path)
    with pytest.raises(WorkspaceError) as exc:
        await _call(
            tools["edit_file"],
            path=path,
            old_text="林砚走进旅馆。",
            new_text="林砚走进旅馆，看见湿手套。",
            digest=new_view.digest,
        )
    assert exc.value.code == "CHAPTER_PLAN_STALE"
    assert "林砚走进旅馆" in workspace.read(path).text


@pytest.mark.anyio
async def test_agent_cannot_create_unplanned_chapter(tmp_path: Path) -> None:
    workspace, _, tools, _ = _setup(tmp_path)
    await _grant(tools, "正文/0002 未构思.md")
    with pytest.raises(WorkspaceError) as exc:
        await _call(tools["write_file"], path="正文/0002 未构思.md", text="# 未构思\n\n正文\n")
    assert exc.value.code == "CHAPTER_PLAN_REQUIRED"
    assert not (workspace.root / "正文" / "0002 未构思.md").exists()
