"""建章提案工具测试（v1.1 M5，Q3-E）。

契约（q0-contracts 第 6 节）：模型在对话中提议建章（含可选标题），作者确认后走
既有建章事务；重复请求键幂等；失败回滚不留半登记。确定性门槛：作者本轮原话
必须明确确认建章，确认引文必须与原话一致。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hastings.workspace.agent import RunContext, build_tools
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.git import GitRepo
from hastings.workspace.prose_ledger import LEDGER_FILENAME, load_ledger
from hastings.workspace.service import WriteSession
from hastings.workspace.story_context import StoryContextSnapshot
from hastings.workspace.story_store import EMPTY_REVISION, MAIN_VARIANT_ID, StoryStore
from hastings.workspace.writing_actions import save_book_intent
from tau_agent.tools import AgentTool

_AUTHOR_CONFIRMED = "好，建吧，标题叫阁楼对峙"
_AUTHOR_NOT_CONFIRMED = "先别建，继续讨论人物"


async def _acall(tool: AgentTool, **arguments: Any) -> dict[str, Any]:
    result = await tool.execute("call-1", arguments)
    return json.loads(result.text)


def _setup(
    tmp_path: Path, *, author_text: str = _AUTHOR_CONFIRMED
) -> tuple[Workspace, StoryStore, dict[str, AgentTool], str]:
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    GitRepo(workspace.root).init()
    state_root = tmp_path / "state"
    store = StoryStore(state_root, workspace, format_version=4)
    save_book_intent(
        store,
        text="暴雪封山的旅馆发生命案，湿手套先让林砚怀疑管家。",
        expected_plan_revision=EMPTY_REVISION,
        request_key="intent",
    )
    revision = store.require_variant(MAIN_VARIANT_ID).revision
    session = WriteSession(workspace, instruction="建章")
    run = RunContext(
        workspace,
        session,
        author_messages=((1, author_text),),
        state_root=state_root,
        current_author_text=author_text,
        story=StoryContextSnapshot(
            variant_id=MAIN_VARIANT_ID, revision=revision, purpose="write"
        ),
    )
    tools = {tool.name: tool for tool in build_tools(workspace, run)}
    return workspace, store, tools, revision


_ARGS = {
    "expected_plan_revision": "",
    "request_key": "chapter:prop:1",
    "confirmation": "好，建吧",
}


@pytest.mark.anyio
async def test_create_chapter_with_confirmation(tmp_path: Path) -> None:
    workspace, store, tools, revision = _setup(tmp_path)
    payload = await _acall(
        tools["create_chapter"],
        title="阁楼对峙",
        expected_plan_revision=revision,
        request_key="chapter:prop:1",
        confirmation="好，建吧",
    )
    assert payload["chapter_id"]
    assert (workspace.root / payload["path"]).exists()
    plan = store.require_variant(MAIN_VARIANT_ID).chapter(payload["chapter_id"])
    assert plan is not None and plan.title == "阁楼对峙"
    # 骨架只有标题：台账不登记（对账护栏的事实基础）。
    assert load_ledger(workspace.root / LEDGER_FILENAME).chapters == {}


@pytest.mark.anyio
async def test_same_request_key_is_idempotent(tmp_path: Path) -> None:
    _, _, tools, revision = _setup(tmp_path)
    args = dict(_ARGS, expected_plan_revision=revision, title="阁楼对峙")
    first = await _acall(tools["create_chapter"], **args)
    again = await _acall(tools["create_chapter"], **args)
    assert again["chapter_id"] == first["chapter_id"]
    assert again["change_id"] == first["change_id"]


@pytest.mark.anyio
async def test_unconfirmed_author_text_is_rejected(tmp_path: Path) -> None:
    workspace, _, tools, revision = _setup(tmp_path, author_text=_AUTHOR_NOT_CONFIRMED)
    with pytest.raises(WorkspaceError) as exc:
        await _acall(
            tools["create_chapter"],
            expected_plan_revision=revision,
            request_key="chapter:prop:2",
            confirmation="先别建",
        )
    assert exc.value.code == "CHAPTER_CONFIRMATION_REQUIRED"
    # 失败不留半登记：没有章节计划、没有正文文件。
    assert store_has_no_chapters(workspace)


@pytest.mark.anyio
async def test_confirmation_quote_must_match_author_text(tmp_path: Path) -> None:
    _, _, tools, revision = _setup(tmp_path)
    with pytest.raises(WorkspaceError) as exc:
        await _acall(
            tools["create_chapter"],
            expected_plan_revision=revision,
            request_key="chapter:prop:3",
            confirmation="作者确认过",  # 原话里没有这句话
        )
    assert exc.value.code == "STORY_AUTHORIZATION_CONFLICT"


@pytest.mark.anyio
async def test_title_optional_defaults_to_order(tmp_path: Path) -> None:
    workspace, store, tools, revision = _setup(
        tmp_path, author_text="行，建吧"
    )
    payload = await _acall(
        tools["create_chapter"],
        expected_plan_revision=revision,
        request_key="chapter:prop:4",
        confirmation="建吧",
    )
    plan = store.require_variant(MAIN_VARIANT_ID).chapter(payload["chapter_id"])
    assert plan is not None and plan.title is None
    assert (workspace.root / payload["path"]).read_text(encoding="utf-8").startswith("# 第1章")


def store_has_no_chapters(workspace: Workspace) -> bool:
    return not (workspace.root / "正文").exists() or not any(
        (workspace.root / "正文").glob("*.md")
    )
