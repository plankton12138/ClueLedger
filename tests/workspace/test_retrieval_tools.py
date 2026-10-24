"""检索工具（0.8 F3）：只读本轮装配的精确版本，不旁路库或作品范围。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hastings.library import EntryDraft, ModuleLibrary
from hastings.library.bindings import AssemblyService
from hastings.workspace.agent import RunContext, build_tools
from hastings.workspace.disclosure import ObservedEvidence
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.git import GitRepo
from hastings.workspace.module_assembly import AssemblyStore
from hastings.workspace.service import WriteSession
from hastings.workspace.story_index import (
    ChapterProjection,
    EventObservation,
    StoryIndexStore,
    StoryTime,
)
from tau_agent.tools import AgentTool

CHAPTER = "正文/0001 雨夜.md"


async def _call(tool: AgentTool, **arguments: Any) -> dict[str, Any]:
    result = await tool.execute("call-1", arguments)
    return json.loads(result.text)


def _setup(
    tmp_path: Path, *, author_text: str = "写第一章。"
) -> tuple[Workspace, dict[str, AgentTool], ModuleLibrary, dict[str, Any]]:
    library = ModuleLibrary(tmp_path / "模块库", cache_file=tmp_path / "cache" / "catalog.json")
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    workspace.write(CHAPTER, "# 雨夜\n\n林静在书房。\n", expected_digest=None)
    created = library.create_module(
        kind="characters",
        name="主角团",
        entries=[EntryDraft("lin", "character", "林静", "法医，旧识。", ("法医",), {})],
        request_key="create-1",
    )
    library.create_module(
        kind="characters",
        name="另一组",
        entries=[EntryDraft("lin", "character", "林静", "另一个世界。", (), {})],
        request_key="create-2",
    )
    module = created["module"]  # type: ignore[index]
    AssemblyService(library, workspace).bind(
        module_id=str(module["module_id"]),
        revision_id=str(module["head"]),
        label="主角团",
        expected_digest=None,
        request_key="bind-key-1",
    )
    assembly = AssemblyStore(workspace.root).read()
    run = RunContext(
        workspace,
        WriteSession(workspace, instruction="写第一章"),
        author_messages=((1, author_text),),
        assembly=assembly,
        library=library,
        state_root=tmp_path / "state",
    )
    tools = {tool.name: tool for tool in build_tools(workspace, run)}
    facts = {
        "module_id": str(module["module_id"]),
        "revision_id": str(module["head"]),
        "entry_id": "lin",
    }
    return workspace, tools, library, facts


def _publish_event(workspace: Workspace, root: Path) -> None:
    view = workspace.read(CHAPTER)
    quote = "林静在书房。"
    start = view.text.index(quote)
    event = EventObservation(
        text="林静在书房",
        chapter=1,
        mode="narration",
        evidence=(
            ObservedEvidence(
                path=CHAPTER,
                digest=view.digest,
                start=start,
                end=start + len(quote),
                quote=quote,
            ),
        ),
        story_time=StoryTime(label="那晚"),
    )
    projection = ChapterProjection(
        path=CHAPTER,
        digest=view.digest,
        chapter=1,
        summary="林静在书房。",
        events=(event,),
    )
    StoryIndexStore(root).publish(projection, expected_digest=projection.digest)


def test_tools_only_exist_when_a_library_is_configured(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    run = RunContext(workspace, WriteSession(workspace))
    names = {tool.name for tool in build_tools(workspace, run)}
    assert "list_modules" not in names
    assert "query_records" not in names
    assert "read_record" not in names


@pytest.mark.anyio
async def test_list_modules_and_query_records_use_only_the_frozen_assembly(
    tmp_path: Path,
) -> None:
    _, tools, _, facts = _setup(tmp_path)

    modules = await _call(tools["list_modules"])
    assert modules["total"] == 1
    assert modules["modules"][0]["module_id"] == facts["module_id"]

    found = await _call(tools["query_records"], query="林静")
    assert found["items"][0]["module_ref"][1] == facts["module_id"]
    assert found["items"][0]["source"] == "author"
    assert found["metrics"]["candidates"] >= 1

    uninstalled = await _call(
        tools["query_records"], query="林静", module_ids=["mod_0000000000000000"]
    )
    assert uninstalled["items"] == []
    assert any("不在本轮装配" in warning for warning in uninstalled["warnings"])


@pytest.mark.anyio
async def test_query_records_reads_observations_up_to_the_task_chapter(tmp_path: Path) -> None:
    workspace, tools, _, _ = _setup(tmp_path)
    _publish_event(workspace, tmp_path / "state")

    found = await _call(tools["query_records"], query="书房", kinds=["event"])
    assert found["items"][0]["source"] == "observed"
    assert found["items"][0]["kind"] == "event"
    assert "书房" in found["items"][0]["title"]


@pytest.mark.anyio
async def test_record_read_does_not_satisfy_a_required_file_reference(tmp_path: Path) -> None:
    workspace, tools, _, facts = _setup(tmp_path)
    GitRepo(workspace.root).init()
    workspace.write("正文/0002 第二章.md", "# 第二章\n\n新的证词。\n", expected_digest=None)
    await _call(
        tools["register_references"],
        references=[
            {
                "path": "设定集/线索.md",
                "kind": "author_text",
                "quote": "写第一章。",
                "required": True,
            }
        ],
    )
    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="改写第二章",
        authorization="写第一章",
        grants=[{"action": "update", "path": "正文/0002 第二章.md"}],
    )
    chapter = await _call(tools["read_file"], path="正文/0002 第二章.md")
    await _call(
        tools["read_record"],
        module_id=facts["module_id"],
        revision_id=facts["revision_id"],
        entry_id=facts["entry_id"],
    )

    with pytest.raises(WorkspaceError) as blocked:
        await _call(
            tools["write_file"],
            path="正文/0002 第二章.md",
            text="# 第二章\n\n改写。\n",
            digest=chapter["digest"],
        )
    assert blocked.value.code == "REFERENCE_NOT_FULLY_READ_CONFLICT"

    reference = workspace.read("设定集/线索.md")
    await _call(tools["read_file"], path="设定集/线索.md")
    written = await _call(
        tools["write_file"],
        path="正文/0002 第二章.md",
        text="# 第二章\n\n改写。\n",
        digest=chapter["digest"],
    )
    assert written["digest"] != chapter["digest"]
    assert reference.digest  # 参考文件本身没有被写工具改动


@pytest.mark.anyio
async def test_library_search_requires_explicit_author_permission(tmp_path: Path) -> None:
    workspace, tools, _, _ = _setup(tmp_path, author_text="写第一章，从模块库再找一份人物。")

    denied = None
    try:
        await _call(tools["search_module_library"], query="另一组")
    except WorkspaceError as exc:
        denied = exc
    assert denied is not None
    assert denied.code == "MODULE_ACCESS_DENIED"

    await _call(
        tools["set_task_scope"],
        mode="read_only",
        summary="从模块库找一份人物",
        authorization="从模块库再找一份人物",
        library_search=True,
    )
    found = await _call(tools["search_module_library"], query="另一组")
    assert [item["name"] for item in found["items"]] == ["另一组"]
    # 搜索不自动装入；本轮装配保持一个绑定。
    assert len(AssemblyStore(workspace.root).read().bindings) == 1


@pytest.mark.anyio
async def test_library_search_cannot_be_granted_without_an_author_request(
    tmp_path: Path,
) -> None:
    _, tools, _, _ = _setup(tmp_path)
    invalid = None
    try:
        await _call(
            tools["set_task_scope"],
            mode="read_only",
            summary="写第一章",
            authorization="写第一章",
            library_search=True,
        )
    except WorkspaceError as exc:
        invalid = exc
    assert invalid is not None
    assert invalid.code == "TASK_SCOPE_EVIDENCE_INVALID"


@pytest.mark.anyio
async def test_read_record_rejects_versions_outside_the_assembly(tmp_path: Path) -> None:
    _, tools, _, facts = _setup(tmp_path)

    denied = None
    try:
        await _call(
            tools["read_record"],
            module_id=facts["module_id"],
            revision_id="rev_0000000000000000",
            entry_id=facts["entry_id"],
        )
    except WorkspaceError as exc:
        denied = exc
    assert denied is not None
    assert denied.code == "MODULE_ACCESS_DENIED"

    body = await _call(
        tools["read_record"],
        module_id=facts["module_id"],
        revision_id=facts["revision_id"],
        entry_id=facts["entry_id"],
    )
    assert body["text"]
    assert body["module_ref"][2] == facts["revision_id"]
