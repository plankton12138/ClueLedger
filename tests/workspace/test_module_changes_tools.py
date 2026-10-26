"""模块内容的准备与应用工具（0.8）。

契约：先用 set_task_scope 登记 module_content_grants；prepare 只写任务进度，
apply 保存内容并一次提交装配；成功后本轮进入收尾，不能再继续创作写入。
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
) -> tuple[Workspace, dict[str, AgentTool], ModuleLibrary, dict[str, Any], list[tuple[str, dict]]]:
    library = ModuleLibrary(tmp_path / "模块库", cache_file=tmp_path / "cache" / "catalog.json")
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    facts: dict[str, Any] = {}
    if bind:
        created = library.create_module(
            kind="characters",
            name="主角团",
            entries=[
                EntryDraft("lin", "character", "林静", "法医，旧识。旧识。"),
                EntryDraft("shen", "character", "沈舟", "侦探。"),
            ],
            request_key="create-characters-1",
        )
        module = created["module"]  # type: ignore[index]
        revision = created["revision"]  # type: ignore[index]
        view = AssemblyService(library, workspace).bind(
            module_id=str(module["module_id"]),
            revision_id=str(revision["revision_id"]),
            label="主角团",
            expected_digest=None,
            request_key="bind-characters-1",
        )
        facts = {
            "module_id": str(module["module_id"]),
            "revision_id": str(revision["revision_id"]),
            "binding_id": str(view["bindings"][0]["id"]),
        }
    events: list[tuple[str, dict]] = []
    run = RunContext(
        workspace,
        WriteSession(workspace, instruction=author_text),
        author_messages=((1, author_text),),
        assembly=AssemblyStore(workspace.root).read(),
        library=library,
        state_root=tmp_path / "state",
        turn_id="turn-module-changes",
        emit=lambda kind, payload: events.append((kind, payload)),
    )
    tools = {tool.name: tool for tool in build_tools(workspace, run)}
    return workspace, tools, library, facts, events


async def _call(tool: AgentTool, **arguments: Any) -> dict[str, Any]:
    result = await tool.execute("call-1", arguments)
    return json.loads(result.text)


@pytest.mark.anyio
async def test_prepare_and_apply_two_new_modules(tmp_path: Path) -> None:
    author = "先撰写人物小传和聚集前提，确定死者与真凶。"
    workspace, tools, library, _, events = _setup(tmp_path, author_text=author)
    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="撰写并保存人物小传与聚集前提",
        authorization="先撰写人物小传",
        module_content_grants=[
            {"action": "create", "slot": "cast", "purpose": "人物小传"},
            {"action": "create", "slot": "premise", "purpose": "聚集与调查前提"},
        ],
    )

    prepared = await _call(
        tools["prepare_module_changes"],
        operations=[
            {
                "operation_id": "cast",
                "action": "create",
                "name": "人物小传",
                "body": "# 核心嫌疑人\n\n七名客人。",
            },
            {
                "operation_id": "premise",
                "action": "create",
                "name": "聚集与调查前提",
                "body": "暴雪封山，侦探被困。",
            },
        ],
    )
    assert prepared["status"] == "prepared"
    assert AssemblyStore(workspace.root).read().bindings == ()

    applied = await _call(tools["apply_module_changes"], change_set_id=prepared["change_set_id"])

    assert applied["applied"] is True
    bindings = AssemblyStore(workspace.root).read().bindings
    assert [binding.label for binding in bindings] == ["人物小传", "聚集与调查前提"]
    assert len({binding.module.module_id for binding in bindings}) == 2
    assert len(library.modules()["items"]) == 2  # type: ignore[index]
    assert [kind for kind, _ in events] == [
        "task_scope_set",
        "module_changes_prepared",
        "module_content_saved",
        "module_content_saved",
        "module_changes_applied",
    ]


@pytest.mark.anyio
async def test_prepare_rejects_a_slot_outside_the_registered_scope(tmp_path: Path) -> None:
    author = "先撰写人物小传。"
    _, tools, _, _, _ = _setup(tmp_path, author_text=author)
    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="撰写人物小传",
        authorization="先撰写人物小传",
        module_content_grants=[{"action": "create", "slot": "cast"}],
    )

    with pytest.raises(WorkspaceError) as denied:
        await _call(
            tools["prepare_module_changes"],
            operations=[
                {"operation_id": "world", "action": "create", "name": "世界", "body": "内容"}
            ],
        )
    assert denied.value.code == "TASK_SCOPE_DENIED_CONFLICT"


@pytest.mark.anyio
async def test_apply_rechecks_the_current_scope(tmp_path: Path) -> None:
    author = "先撰写人物小传。"
    _, tools, _, _, _ = _setup(tmp_path, author_text=author)
    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="撰写人物小传",
        authorization="先撰写人物小传",
        grants=[{"action": "update", "path": "正文/0001 新章.md"}],
        module_content_grants=[{"action": "create", "slot": "cast"}],
    )
    prepared = await _call(
        tools["prepare_module_changes"],
        operations=[{"operation_id": "cast", "action": "create", "name": "人物", "body": "内容"}],
    )
    # 重新登记只能收窄：去掉内容授权后，已经准备的内容也不能应用。
    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="只改第一章",
        authorization="先撰写人物小传",
        grants=[{"action": "update", "path": "正文/0001 新章.md"}],
    )

    with pytest.raises(WorkspaceError) as denied:
        await _call(tools["apply_module_changes"], change_set_id=prepared["change_set_id"])
    assert denied.value.code == "TASK_SCOPE_DENIED_CONFLICT"


@pytest.mark.anyio
async def test_update_reads_the_exact_revision_then_applies(tmp_path: Path) -> None:
    author = "把医生改成姐姐，其余不变。"
    workspace, tools, library, facts, _ = _setup(tmp_path, author_text=author, bind=True)
    record = await _call(
        tools["read_record"],
        module_id=facts["module_id"],
        revision_id=facts["revision_id"],
        entry_id="lin",
    )
    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="修改林静的称呼",
        authorization="把医生改成姐姐",
        module_content_grants=[
            {
                "action": "update",
                "binding_id": facts["binding_id"],
                "module_id": facts["module_id"],
                "base_revision": facts["revision_id"],
            }
        ],
    )
    prepared = await _call(
        tools["prepare_module_changes"],
        operations=[
            {
                "operation_id": "lin-update",
                "action": "update",
                "binding_id": facts["binding_id"],
                "module_id": facts["module_id"],
                "base_revision": facts["revision_id"],
                "edits": [
                    {
                        "entry_id": "lin",
                        "base_digest": record["digest"],
                        "find": "法医，旧识。",
                        "replace": "死者的姐姐。",
                    }
                ],
            }
        ],
    )
    applied = await _call(tools["apply_module_changes"], change_set_id=prepared["change_set_id"])

    assert applied["applied"] is True
    binding = AssemblyStore(workspace.root).read().binding(facts["binding_id"])
    assert binding is not None
    assert binding.module.revision_id != facts["revision_id"]
    assert library.module(facts["module_id"]).head == facts["revision_id"]
    updated, _ = library.storage.read_entry(facts["module_id"], binding.module.revision_id, "lin")
    assert "死者的姐姐。" in updated
    assert (
        "沈舟"
        in library.storage.read_entry(facts["module_id"], binding.module.revision_id, "shen")[0]
    )


@pytest.mark.anyio
async def test_apply_enters_finalizing_and_blocks_further_writes(tmp_path: Path) -> None:
    author = "先撰写人物小传。"
    workspace, tools, _, _, _ = _setup(tmp_path, author_text=author)
    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="撰写人物小传",
        authorization="先撰写人物小传",
        module_content_grants=[{"action": "create", "slot": "cast"}],
    )
    prepared = await _call(
        tools["prepare_module_changes"],
        operations=[{"operation_id": "cast", "action": "create", "name": "人物", "body": "内容"}],
    )
    await _call(tools["apply_module_changes"], change_set_id=prepared["change_set_id"])

    with pytest.raises(WorkspaceError) as blocked:
        await _call(tools["write_file"], path="正文/0001 新章.md", text="正文")
    assert blocked.value.code == "TASK_FINALIZING_CONFLICT"
    assert AssemblyStore(workspace.root).read().bindings
