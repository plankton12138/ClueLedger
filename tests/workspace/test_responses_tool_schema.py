"""Responses 必须保留作品工具的可选参数，不能要求模型用 0 或空串占位。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hastings.workspace.agent import RunContext, build_tools
from hastings.workspace.files import Workspace
from hastings.workspace.git import GitRepo
from hastings.workspace.service import WriteSession
from tau_ai.openai_codex import _tool_to_codex
from tau_ai.openai_compatible import _tool_to_responses


@pytest.mark.anyio
@pytest.mark.parametrize("serialize", [_tool_to_responses, _tool_to_codex])
async def test_first_chapter_omits_optional_scope_fields(tmp_path: Path, serialize) -> None:
    workspace = Workspace.create(tmp_path / "作品")
    GitRepo(workspace.root).init()
    run = RunContext(
        workspace,
        WriteSession(workspace, instruction="开始写第一章"),
        author_messages=((1, "开始写第一章"),),
    )
    tools = {tool.name: tool for tool in build_tools(workspace, run)}
    wire = serialize(tools["set_task_scope"])
    # strict 未指定或为 null 时，Responses 可能将可选参数转换为必填参数。
    assert wire["strict"] is False
    schema = wire["parameters"]
    assert "authorization_seq" not in schema["required"]
    assert "carried_from_seq" not in schema["required"]
    assert "target" not in schema["properties"]["grants"]["items"]["required"]

    path = "正文/0001 最后一班车.md"
    await tools["set_task_scope"].execute(
        "scope",
        {
            "mode": "edit",
            "summary": "撰写并保存第一章",
            "authorization": "开始写第一章",
            "grants": [{"action": "create", "path": path}],
        },
    )
    assert run.scope.current is not None
    assert run.scope.current.draft.carried_from_seq is None
    text = "# 最后一班车\n\n小鸟游抵达了山上的旅馆。\n"
    result = await tools["write_file"].execute("write", {"path": path, "text": text})
    assert json.loads(result.text)["path"] == path
    assert (workspace.root / path).read_text(encoding="utf-8") == text
