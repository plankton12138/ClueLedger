"""模块装卸与库发布工具（0.8 F3 收口）。

契约：装卸只改本作品装配，必须精确到版本或绑定；库发布只改全局库，不自动装入。
两者都要求本轮范围登记作者原话；一般写作或库搜索授权不能代替。装配提交后本轮结束。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hastings.library import EntryDraft, ModuleLibrary
from hastings.library.bindings import AssemblyService
from hastings.workspace.agent import RunContext, build_tools
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.module_assembly import AssemblyStore
from hastings.workspace.service import WriteSession
from tau_agent.tools import AgentTool


def _setup(
    tmp_path: Path, *, author_text: str, bind: bool = False
) -> tuple[Workspace, dict[str, AgentTool], ModuleLibrary, dict[str, Any]]:
    library = ModuleLibrary(tmp_path / "模块库", cache_file=tmp_path / "cache" / "catalog.json")
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    created = library.create_module(
        kind="characters",
        name="主角团",
        entries=[EntryDraft("lin", "character", "林静", "法医，旧识。")],
        request_key="create-key-1",
    )
    module = created["module"]  # type: ignore[index]
    module_id = str(module["module_id"])
    revision_id = str(module["head"])
    if bind:
        AssemblyService(library, workspace).bind(
            module_id=module_id,
            revision_id=revision_id,
            label="主角团",
            expected_digest=None,
            request_key="bind-key-1",
        )
    run = RunContext(
        workspace,
        WriteSession(workspace, instruction=author_text),
        author_messages=((1, author_text),),
        assembly=AssemblyStore(workspace.root).read(),
        library=library,
        state_root=tmp_path / "state",
    )
    tools = {tool.name: tool for tool in build_tools(workspace, run)}
    facts = {"module_id": module_id, "revision_id": revision_id}
    return workspace, tools, library, facts


async def _call(tool: AgentTool, **arguments: Any) -> dict[str, Any]:
    result = await tool.execute("call-1", arguments)
    body = json.loads(result.text)
    if result.terminate:
        body["_terminated"] = True
    return body


def _binding_id(workspace: Workspace) -> str:
    return AssemblyStore(workspace.root).read().bindings[0].id


@pytest.mark.anyio
async def test_attach_needs_registered_scope(tmp_path: Path) -> None:
    _, tools, _, facts = _setup(tmp_path, author_text="写第一章。")

    with pytest.raises(WorkspaceError) as blocked:
        await _call(
            tools["attach_module"],
            module_id=facts["module_id"],
            revision_id=facts["revision_id"],
        )
    assert blocked.value.code == "TASK_SCOPE_REQUIRED_CONFLICT"


@pytest.mark.anyio
async def test_attach_rejects_version_outside_the_scope(tmp_path: Path) -> None:
    _, tools, _, facts = _setup(tmp_path, author_text="把主角团模块装进这个作品。")
    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="装入主角团",
        authorization="把主角团模块装进这个作品",
        module_grants=[
            {
                "action": "attach",
                "module_id": facts["module_id"],
                "revision_id": facts["revision_id"],
            }
        ],
    )

    with pytest.raises(WorkspaceError) as blocked:
        await _call(
            tools["attach_module"],
            module_id=facts["module_id"],
            revision_id="rev_other",
        )
    assert blocked.value.code == "TASK_SCOPE_DENIED_CONFLICT"


@pytest.mark.anyio
async def test_attach_binds_the_exact_version_and_ends_the_turn(tmp_path: Path) -> None:
    workspace, tools, _, facts = _setup(tmp_path, author_text="把主角团模块装进这个作品。")
    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="装入主角团",
        authorization="把主角团模块装进这个作品",
        module_grants=[
            {
                "action": "attach",
                "module_id": facts["module_id"],
                "revision_id": facts["revision_id"],
            }
        ],
    )

    body = await _call(
        tools["attach_module"],
        module_id=facts["module_id"],
        revision_id=facts["revision_id"],
        label="主角团",
    )

    assert body["attached"] is True
    assert body["_terminated"] is True
    bindings = AssemblyStore(workspace.root).read().bindings
    assert [(item.module.module_id, item.module.revision_id) for item in bindings] == [
        (facts["module_id"], facts["revision_id"])
    ]


@pytest.mark.anyio
async def test_detach_needs_the_registered_binding(tmp_path: Path) -> None:
    workspace, tools, _, _ = _setup(tmp_path, author_text="把主角团模块卸下。", bind=True)
    binding_id = _binding_id(workspace)
    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="卸下主角团",
        authorization="把主角团模块卸下",
        module_grants=[{"action": "detach", "binding_id": binding_id}],
    )

    body = await _call(tools["detach_module"], binding_id=binding_id)

    assert body["detached"] is True
    assert body["_terminated"] is True
    assert AssemblyStore(workspace.root).read().bindings == ()


@pytest.mark.anyio
async def test_publish_requires_author_publish_request(tmp_path: Path) -> None:
    _, tools, _, _ = _setup(tmp_path, author_text="写第一章。")
    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="改写第一章",
        authorization="写第一章",
        grants=[{"action": "update", "path": "正文/0001 雨夜.md"}],
    )

    with pytest.raises(WorkspaceError) as blocked:
        await _call(
            tools["publish_module"],
            kind="characters",
            name="新的角色",
            entries=[{"entry_id": "lin", "kind": "character", "title": "林静"}],
        )
    assert blocked.value.code == "TASK_SCOPE_DENIED_CONFLICT"


@pytest.mark.anyio
async def test_publish_adds_a_revision_to_an_existing_module(tmp_path: Path) -> None:
    _, tools, library, facts = _setup(tmp_path, author_text="把主角团的新人物发布到模块库。")
    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="发布新人物",
        authorization="把主角团的新人物发布到模块库",
        library_publish=True,
    )

    body = await _call(
        tools["publish_module"],
        module_id=facts["module_id"],
        entries=[{"entry_id": "zhou", "kind": "character", "title": "周岚", "body": "助手。"}],
    )

    assert body["created"] is False
    assert body["revision_id"] != facts["revision_id"]
    assert library.module(facts["module_id"]).head == body["revision_id"]


@pytest.mark.anyio
async def test_publish_creates_a_module_without_touching_the_assembly(tmp_path: Path) -> None:
    workspace, tools, library, _ = _setup(tmp_path, author_text="把这份人物设定发布到模块库。")
    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="发布人物设定",
        authorization="把这份人物设定发布到模块库",
        library_publish=True,
    )

    body = await _call(
        tools["publish_module"],
        kind="characters",
        name="新的角色",
        entries=[
            {
                "entry_id": "lin",
                "kind": "character",
                "title": "林静",
                "body": "法医。",
            }
        ],
    )

    assert body["created"] is True
    assert body["revision_id"]
    modules = library.modules(include_deleted=False)["items"]
    assert any(item["name"] == "新的角色" for item in modules)  # type: ignore[index]
    assert AssemblyStore(workspace.root).read().bindings == ()
