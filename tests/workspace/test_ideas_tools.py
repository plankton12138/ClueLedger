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

    next_turn = _run(workspace, "帮我找灵感，有没有房卡相关的内容？", "继续写下一段。")
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


@pytest.mark.anyio
@pytest.mark.parametrize("denial", ["不要", "不用", "禁止", "不需要", "暂不", "别"])
async def test_idea_tools_reject_explicit_denial_without_side_effects(
    tmp_path: Path, denial: str
) -> None:
    workspace = _workspace(tmp_path)
    store = IdeaStore(workspace)
    store.create(title="房卡", body="后门。", request_key="idea-denial-seed")
    before = {path: path.read_bytes() for path in store.root.glob("*.json")}
    run = _run(workspace, "帮我搜索灵感，把想法记下来。", f"{denial}搜索灵感，{denial}记下来。")
    tools = {tool.name: tool for tool in build_tools(workspace, run)}

    for name, arguments, code in (
        ("search_ideas", {"query": "房卡"}, "IDEA_SEARCH_DENIED"),
        ("save_idea", {"body": "未授权的新想法。"}, "IDEA_SAVE_DENIED"),
    ):
        with pytest.raises(WorkspaceError) as denied:
            await _call(tools[name], **arguments)
        assert denied.value.code == code
    assert not run.idea_visible
    assert {path: path.read_bytes() for path in store.root.glob("*.json")} == before


@pytest.mark.anyio
async def test_current_turn_text_overrides_historical_idea_permissions(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    run = _run(workspace, "帮我找灵感，把这个记下来。")
    run.current_author_text = "继续写下一段。"
    tools = {tool.name: tool for tool in build_tools(workspace, run)}
    for name, arguments in (
        ("search_ideas", {"query": "房卡"}),
        ("save_idea", {"body": "未授权的新想法。"}),
    ):
        with pytest.raises(WorkspaceError):
            await _call(tools[name], **arguments)


@pytest.mark.anyio
async def test_idea_search_and_save_permissions_are_independent(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    run = _run(workspace, "帮我找灵感，但不要记下来。")
    tools = {tool.name: tool for tool in build_tools(workspace, run)}
    assert (await _call(tools["search_ideas"], query="房卡"))["items"] == []
    with pytest.raises(WorkspaceError) as denied:
        await _call(tools["save_idea"], body="新想法。")
    assert denied.value.code == "IDEA_SAVE_DENIED"

    run = _run(workspace, "不要搜索灵感，把「后门有人」记下来。")
    tools = {tool.name: tool for tool in build_tools(workspace, run)}
    assert (await _call(tools["save_idea"], body="后门有人。"))["idea_id"]
    with pytest.raises(WorkspaceError) as denied:
        await _call(tools["search_ideas"], query="后门")
    assert denied.value.code == "IDEA_SEARCH_DENIED"


@pytest.mark.anyio
async def test_quoted_idea_content_does_not_deny_a_save_request(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    run = _run(workspace, "把「侦探不能是凶手，不要搜索灵感」记下来。")
    tools = {tool.name: tool for tool in build_tools(workspace, run)}
    saved = await _call(tools["save_idea"], body="侦探不能是凶手，不要搜索灵感")
    assert IdeaStore(workspace).get(saved["idea_id"])["body"] == "侦探不能是凶手，不要搜索灵感"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("text", "tool", "arguments"),
    [
        ("只讨论「搜索灵感」这几个字。", "search_ideas", {"query": "房卡"}),
        ("只讨论「记下来」这几个字。", "save_idea", {"body": "不应保存"}),
    ],
)
async def test_quoted_action_words_do_not_authorize_idea_tools(
    tmp_path: Path, text: str, tool: str, arguments: dict[str, str]
) -> None:
    workspace = _workspace(tmp_path)
    tools = {tool.name: tool for tool in build_tools(workspace, _run(workspace, text))}
    with pytest.raises(WorkspaceError):
        await _call(tools[tool], **arguments)


def test_accepting_a_deleted_entry_is_rejected_even_for_an_older_live_revision(
    tmp_path: Path,
) -> None:
    """条目当前已删除时，旧的可读修订也不能被新受理；已受理的轮次不受影响。"""
    workspace = _workspace(tmp_path)
    store = IdeaStore(workspace)
    created = store.create(body="第一版。", request_key="idea-freeze-1")
    edited = store.update(
        str(created["idea_id"]),
        expected_digest=str(created["digest"]),
        expected_revision=str(created["revision_id"]),
        body="第二版。",
        request_key="idea-freeze-2",
    )
    store.trash(
        str(created["idea_id"]),
        expected_digest=str(edited["digest"]),
        expected_revision=str(edited["revision_id"]),
        request_key="idea-freeze-3",
    )

    with pytest.raises(WorkspaceError) as excinfo:
        freeze_context(
            workspace,
            ContextRequest(idea_references=((created["idea_id"], created["revision_id"]),)),
        )
    assert excinfo.value.code == "CONTEXT_SOURCE_NOT_FOUND"
    assert "已删除" in excinfo.value.why


@pytest.mark.anyio
async def test_one_turn_can_save_several_ideas_with_separate_operations(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    run = _run(workspace, "把这两句都记下来。")
    tools = {tool.name: tool for tool in build_tools(workspace, run)}
    save = tools["save_idea"]

    first = json.loads((await save.execute("call-a", {"body": "第一句。"})).text)
    second = json.loads((await save.execute("call-b", {"body": "第二句。"})).text)
    assert first["idea_id"] != second["idea_id"]

    # 同一次调用的重试复用同一操作身份，不新增记录。
    retry = json.loads((await save.execute("call-a", {"body": "第一句。"})).text)
    assert retry["idea_id"] == first["idea_id"]
    assert IdeaStore(workspace).list()["total"] == 2


@pytest.mark.anyio
async def test_saved_idea_keeps_the_original_text(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    run = _run(workspace, "把这句话记下来。")
    tools = {tool.name: tool for tool in build_tools(workspace, run)}
    body = "  前后都有空格，中间有\n换行 🎭  "
    saved = json.loads((await tools["save_idea"].execute("call-1", {"body": body})).text)

    view = IdeaStore(workspace).get(str(saved["idea_id"]))
    assert view["body"] == body
    assert view["title"] == ""


def test_reference_view_carries_a_short_preview(tmp_path: Path) -> None:
    """受理事件里带的是短预览，不是内部标识：界面据此显示引用内容。"""
    workspace = _workspace(tmp_path)
    body = "  灯塔在退潮时才会露出第二层台阶。 " + "很长的补充。" * 40
    idea = IdeaStore(workspace).create(body=body, request_key="idea-preview-1")
    snapshot = freeze_context(
        workspace,
        ContextRequest(idea_references=((idea["idea_id"], idea["revision_id"]),)),
    )

    view = snapshot.to_dict()["idea_revisions"][0]
    assert str(view["preview"]).startswith("灯塔在退潮时才会露出第二层台阶。")
    assert len(str(view["preview"])) <= 49
    # 提示词里用预览当标题，而不是内部标识。
    section = ideas_section(snapshot)
    assert "灯塔在退潮时" in section
