"""S4 关系工具：没有登记的关系授权、未读区间和未装模块都不能写账本。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hastings.workspace.agent import RunContext, build_tools
from hastings.workspace.clue_designs import create_clue_identity
from hastings.workspace.clue_models import InferenceStatement
from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.module_assembly import AssemblyStore
from hastings.workspace.service import WriteSession
from tau_agent.tools import AgentTool

FIRST = "正文/0001 遗书.md"
PLAN = "设定集/线索.md"
QUOTE = "原计划：第三章让照片出现。"


def _setup(
    tmp_path: Path, *, author_text: str
) -> tuple[Workspace, dict[str, AgentTool], ClueLedgerStore, str, list[tuple[str, dict]]]:
    workspace = Workspace.create(tmp_path / "遗书")
    workspace.write(FIRST, "# 遗书\n\n信封背面有字。\n", expected_digest=None)
    workspace.write(PLAN, f"# 线索\n\n{QUOTE}\n", expected_digest=None)
    store = ClueLedgerStore(tmp_path / "state")
    result = create_clue_identity(
        store,
        title="遗书背面的字",
        request_key="fixture-create",
        expected_revision="empty",
    )
    events: list[tuple[str, dict]] = []
    run = RunContext(
        workspace,
        WriteSession(workspace, instruction=author_text),
        author_messages=((1, author_text),),
        assembly=AssemblyStore(workspace.root).read(),
        state_root=tmp_path / "state",
        turn_id="turn-clue-design",
        emit=lambda kind, payload: events.append((kind, payload)),
    )
    tools = {tool.name: tool for tool in build_tools(workspace, run)}
    return workspace, tools, store, str(result["clue_id"]), events


async def _call(tool: AgentTool, **arguments: Any) -> dict[str, Any]:
    result = await tool.execute("call-1", arguments)
    return json.loads(result.text)


def _plan_args(workspace: Workspace) -> dict[str, Any]:
    view = workspace.read(PLAN)
    start = view.text.index(QUOTE)
    return {
        "source": {"kind": "file", "path": PLAN, "digest": view.digest},
        "start": start,
        "end": start + len(QUOTE),
        "quote": QUOTE,
    }


@pytest.mark.anyio
async def test_link_requires_an_explicit_relation_grant(tmp_path: Path) -> None:
    author = "把设定里的计划关联到线索；先改写第一章。"
    workspace, tools, store, clue_id, _ = _setup(tmp_path, author_text=author)
    scope = await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="改写第一章",
        authorization="改写第一章",
        grants=[{"action": "update", "path": FIRST}],
    )
    assert scope["clue_design_grants"] == []
    await _call(tools["read_file"], path=PLAN)
    with pytest.raises(WorkspaceError, match="TASK_SCOPE_DENIED_CONFLICT"):
        await _call(
            tools["link_clue_design"],
            clue_id=clue_id,
            **_plan_args(workspace),
        )
    assert store.read().ledger is not None
    assert store.read().ledger.design_links == {}


@pytest.mark.anyio
async def test_relation_grant_needs_the_author_to_ask_for_it(tmp_path: Path) -> None:
    author = "帮我看看这段计划。"
    _, tools, _, clue_id, _ = _setup(tmp_path, author_text=author)
    with pytest.raises(WorkspaceError, match="TASK_SCOPE_EVIDENCE_INVALID"):
        await _call(
            tools["set_task_scope"],
            mode="edit",
            summary="关联计划",
            authorization="帮我看看这段计划",
            clue_design_grants=[{"action": "link", "clue_id": clue_id}],
        )


@pytest.mark.anyio
async def test_link_then_save_inference_records_real_relations(tmp_path: Path) -> None:
    author = "把设定里的计划关联到线索，并保存推理目标。"
    workspace, tools, store, clue_id, events = _setup(tmp_path, author_text=author)
    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="关联计划并保存推理目标",
        authorization="把设定里的计划关联到线索",
        clue_design_grants=[
            {"action": "link", "clue_id": clue_id},
            {"action": "inference", "clue_ids": [clue_id]},
        ],
    )
    await _call(tools["read_file"], path=PLAN)
    linked = await _call(
        tools["link_clue_design"],
        clue_id=clue_id,
        relation="第三章让照片出现",
        **_plan_args(workspace),
    )
    assert linked["link_id"].startswith("link_")
    saved = await _call(
        tools["save_clue_inference"],
        clue_ids=[clue_id],
        goal="林岚提前接触过原件",
        intent="deduce",
        premises=[
            {
                "text": "林岚能说出背面内容",
                "clue_ids": [clue_id],
                "design_link_ids": [linked["link_id"]],
            }
        ],
        alternatives=[{"text": "警方公开过照片"}],
        design_link_ids=[linked["link_id"]],
    )
    ledger = store.read().ledger
    assert ledger is not None
    assert ledger.design_links[linked["link_id"]].quote == QUOTE
    inference = ledger.inferences[saved["inference_id"]]
    assert inference.goal == "林岚提前接触过原件"
    assert inference.intent == "deduce"
    assert inference.design_link_ids == [linked["link_id"]]
    assert inference.premises[0].design_link_ids == [linked["link_id"]]
    assert store.read().ledger.contributions == {}
    assert [kind for kind, _ in events] == [
        "task_scope_set",
        "material_read",
        "clue_design_linked",
        "clue_inference_saved",
    ]


@pytest.mark.anyio
async def test_unread_plan_range_is_rejected_before_commit(tmp_path: Path) -> None:
    author = "把设定里的计划关联到线索。"
    workspace, tools, store, clue_id, _ = _setup(tmp_path, author_text=author)
    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="关联计划",
        authorization="把设定里的计划关联到线索",
        clue_design_grants=[{"action": "link", "clue_id": clue_id}],
    )
    with pytest.raises(WorkspaceError, match="REFERENCE_NOT_FULLY_READ_CONFLICT"):
        await _call(
            tools["link_clue_design"],
            clue_id=clue_id,
            **_plan_args(workspace),
        )
    assert store.read().ledger is not None
    assert store.read().ledger.design_links == {}


@pytest.mark.anyio
async def test_inference_without_its_grant_is_rejected(tmp_path: Path) -> None:
    author = "把设定里的计划关联到线索，并保存推理目标。"
    _, tools, store, clue_id, _ = _setup(tmp_path, author_text=author)
    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="只关联计划",
        authorization="把设定里的计划关联到线索",
        clue_design_grants=[{"action": "link", "clue_id": clue_id}],
    )
    with pytest.raises(WorkspaceError, match="TASK_SCOPE_DENIED_CONFLICT"):
        await _call(
            tools["save_clue_inference"],
            clue_ids=[clue_id],
            goal="林岚提前接触过原件",
            intent="deduce",
        )
    assert store.read().ledger is not None
    assert store.read().ledger.inferences == {}


def test_inference_statement_rejects_empty_text() -> None:
    with pytest.raises(ValueError):
        InferenceStatement(text="   ")
