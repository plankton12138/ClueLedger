"""写作 Agent 的文件工具（0.4 V04-3，计划 M03）。

契约：工具只在文件层之上做参数整理与错误说明；写入必须带读到的指纹，局部替换要求
原文唯一；读操作不会准备 Git 基线，只有第一次真正写文件才触发。工具成功表示内容
已经落盘，返回的 digest 是落盘后的实际指纹。
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
from hastings.workspace.service import WriteSession
from tau_agent.tools import AgentTool


def _setup(tmp_path: Path) -> tuple[Workspace, dict[str, AgentTool], WriteSession]:
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    GitRepo(workspace.root).init()
    session = WriteSession(workspace, instruction="写第一章")
    run = RunContext(
        workspace,
        session,
        author_messages=((1, "写第一章，改写雨夜这一章。"),),
    )
    tools = {tool.name: tool for tool in build_tools(workspace, run)}
    return workspace, tools, session


async def _grant(tools: dict[str, AgentTool], *paths: str) -> None:
    """登记本轮范围：这些章节允许新建、修改、删除与改名。"""
    renamed = [f"{path[: -len('.md')]}改.md" for path in paths]
    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="改写雨夜这一章",
        authorization="写第一章",
        grants=[
            {"action": action, "path": path}
            for path in (*paths, *renamed)
            for action in ("create", "update", "delete")
        ]
        + [
            {"action": "move", "path": path, "target": target}
            for path, target in zip(paths, renamed, strict=True)
        ],
    )


def _long_chapter(tmp_path: Path) -> tuple[Workspace, dict[str, AgentTool], WriteSession]:
    workspace, tools, session = _setup(tmp_path)
    workspace.write(
        "正文/0001 雨夜.md",
        "# 雨夜\n\n侦探抵达旅馆。\n\n前台说只有一位客人。\n\n雪越下越大。\n",
        expected_digest=None,
    )
    return workspace, tools, session


async def _call(tool: AgentTool, **arguments: Any) -> dict[str, Any]:
    result = await tool.execute("call-1", arguments)
    return json.loads(result.text)


def _code(exc_info: pytest.ExceptionInfo[WorkspaceError]) -> str:
    return exc_info.value.code


@pytest.mark.anyio
async def test_list_files_reports_both_blocks(tmp_path: Path) -> None:
    workspace, tools, _ = _setup(tmp_path)
    workspace.create_chapter("雨夜", "# 雨夜\n\n侦探抵达旅馆。\n")

    body = await _call(tools["list_files"])

    assert [item["path"] for item in body["chapters"]] == ["正文/0001 雨夜.md"]
    assert [item["path"] for item in body["settings"]] == [
        "设定集/线索.md",
        "设定集/真相.md",
    ]
    assert body["characters"] == []


@pytest.mark.anyio
async def test_list_files_shares_display_title_and_roles(tmp_path: Path) -> None:
    workspace, tools, _ = _setup(tmp_path)
    workspace.write("正文/0001 暴雪来客.md", "# 雪山旅馆\n", expected_digest=None)
    workspace.write(
        "设定集/角色小传/许衡.md",
        "---\nroles: [suspect, culprit]\n---\n# 许衡\n",
        expected_digest=None,
    )

    body = await _call(tools["list_files"])

    # 一级标题是书名：模型看到的显示名与界面一样回退到文件名，并带命名提示。
    assert body["chapters"][0]["display_title"] == "第一章 暴雪来客"
    assert body["chapters"][0]["chapter_number"] == 1
    assert body["characters"][0]["display_title"] == "许衡"
    assert body["characters"][0]["roles"] == ["suspect", "culprit"]


@pytest.mark.anyio
async def test_read_file_pages_and_returns_digest(tmp_path: Path) -> None:
    _, tools, _ = _long_chapter(tmp_path)

    first = await _call(tools["read_file"], path="正文/0001 雨夜.md", limit=2)
    assert first["offset"] == 1
    assert first["truncated"] is True
    assert first["total_lines"] == 8
    assert len(str(first["digest"])) == 64

    second = await _call(tools["read_file"], path="正文/0001 雨夜.md", offset=7, limit=2)
    assert second["offset"] == 7
    assert second["truncated"] is False


@pytest.mark.anyio
async def test_write_file_requires_digest_for_existing_file(tmp_path: Path) -> None:
    workspace, tools, session = _setup(tmp_path)
    await _grant(tools, "正文/0001 雨夜.md")
    created = await _call(
        tools["write_file"], path="正文/0001 雨夜.md", text="# 雨夜\n\n侦探抵达旅馆。\n"
    )
    assert created["digest"]
    assert session.prepared is True

    with pytest.raises(WorkspaceError) as exists:
        await _call(tools["write_file"], path="正文/0001 雨夜.md", text="# 覆盖\n")
    assert _code(exists) == "FILE_EXISTS_CONFLICT"

    written = await _call(
        tools["write_file"],
        path="正文/0001 雨夜.md",
        text="# 雨夜\n\n改写。\n",
        digest=created["digest"],
    )
    assert written["digest"] != created["digest"]
    assert "改写" in workspace.read("正文/0001 雨夜.md").text


@pytest.mark.anyio
async def test_edit_file_replaces_unique_text(tmp_path: Path) -> None:
    workspace, tools, _ = _setup(tmp_path)
    await _grant(tools, "正文/0001 雨夜.md")
    created = await _call(
        tools["write_file"], path="正文/0001 雨夜.md", text="# 雨夜\n\n侦探抵达旅馆。\n"
    )

    edited = await _call(
        tools["edit_file"],
        path="正文/0001 雨夜.md",
        old_text="侦探抵达旅馆。",
        new_text="侦探在深夜抵达旅馆。",
        digest=created["digest"],
    )

    assert edited["digest"] != created["digest"]
    assert "深夜" in workspace.read("正文/0001 雨夜.md").text


@pytest.mark.anyio
async def test_edit_file_rejects_missing_or_ambiguous_text(tmp_path: Path) -> None:
    _, tools, _ = _setup(tmp_path)
    await _grant(tools, "正文/0001 雨夜.md")
    created = await _call(
        tools["write_file"], path="正文/0001 雨夜.md", text="# 雨夜\n\n他来了。\n\n他来了。\n"
    )

    with pytest.raises(WorkspaceError) as ambiguous:
        await _call(
            tools["edit_file"],
            path="正文/0001 雨夜.md",
            old_text="他来了。",
            new_text="她来了。",
            digest=created["digest"],
        )
    assert _code(ambiguous) == "EDIT_MATCH_AMBIGUOUS"
    assert ambiguous.value.details["occurrences"] == 2

    with pytest.raises(WorkspaceError) as missing:
        await _call(
            tools["edit_file"],
            path="正文/0001 雨夜.md",
            old_text="从未出现的句子。",
            new_text="改。",
            digest=created["digest"],
        )
    assert _code(missing) == "EDIT_MATCH_NOT_FOUND"


@pytest.mark.anyio
async def test_edit_file_rejects_stale_digest(tmp_path: Path) -> None:
    workspace, tools, _ = _setup(tmp_path)
    await _grant(tools, "正文/0001 雨夜.md")
    created = await _call(tools["write_file"], path="正文/0001 雨夜.md", text="# 雨夜\n\n原稿。\n")
    # 外部在文末加了一段：要替换的原文仍然唯一，但指纹已经过期。
    (workspace.root / "正文/0001 雨夜.md").write_text(
        "# 雨夜\n\n原稿。\n\n外部新增。\n", encoding="utf-8"
    )

    with pytest.raises(WorkspaceError) as stale:
        await _call(
            tools["edit_file"],
            path="正文/0001 雨夜.md",
            old_text="原稿。",
            new_text="改写。",
            digest=created["digest"],
        )
    assert _code(stale) == "CONTEXT_SOURCE_STALE_CONFLICT"
    assert "外部新增" in (workspace.root / "正文/0001 雨夜.md").read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_tools_reject_paths_outside_managed_set(tmp_path: Path) -> None:
    _, tools, _ = _setup(tmp_path)

    for path in ("../x.md", "/etc/passwd", "正文\\0001.md", "设定集/时间线.md"):
        with pytest.raises(WorkspaceError) as rejected:
            await _call(tools["read_file"], path=path)
        assert _code(rejected) == "PATH_NOT_MANAGED"


@pytest.mark.anyio
async def test_move_and_delete_require_digest(tmp_path: Path) -> None:
    workspace, tools, _ = _setup(tmp_path)
    await _grant(tools, "正文/0001 雨夜.md")
    created = await _call(tools["write_file"], path="正文/0001 雨夜.md", text="# 雨夜\n")

    moved = await _call(
        tools["move_file"],
        source="正文/0001 雨夜.md",
        target="正文/0001 雨夜改.md",
        digest=created["digest"],
    )
    assert moved["path"] == "正文/0001 雨夜改.md"

    deleted = await _call(tools["delete_file"], path="正文/0001 雨夜改.md", digest=moved["digest"])
    assert deleted["deleted"] is True
    assert not (workspace.root / "正文/0001 雨夜改.md").exists()


@pytest.mark.anyio
async def test_git_tools_are_read_only(tmp_path: Path) -> None:
    workspace, tools, session = _setup(tmp_path)
    workspace.create_chapter("雨夜", "# 雨夜\n")

    log = await _call(tools["git_log"])
    diff = await _call(tools["git_diff"])

    assert [item["subject"] for item in log["commits"]] == ["初始化作品目录"]
    assert diff["diff"] == ""
    assert session.prepared is False


@pytest.mark.anyio
async def test_tool_input_validation(tmp_path: Path) -> None:
    _, tools, _ = _setup(tmp_path)

    with pytest.raises(WorkspaceError) as bad_int:
        await _call(tools["read_file"], path="正文/0001 雨夜.md", offset="一")
    assert _code(bad_int) == "TOOL_INPUT_INVALID"

    with pytest.raises(WorkspaceError) as empty_query:
        await _call(tools["search_text"], query="")
    assert _code(empty_query) == "TOOL_INPUT_INVALID"


@pytest.mark.anyio
async def test_register_references_needs_author_quote_or_real_evidence(tmp_path: Path) -> None:
    workspace, tools, _ = _setup(tmp_path)
    workspace.write("正文/0002 参考章.md", "# 参考章\n\n书房里有一枚脚印。\n", expected_digest=None)

    registered = await _call(
        tools["register_references"],
        references=[
            {
                "path": "正文/0002 参考章.md",
                "kind": "author_text",
                "quote": "写第一章",
                "required": True,
            }
        ],
    )
    assert registered["required"] == ["正文/0002 参考章.md"]

    with pytest.raises(WorkspaceError) as invented:
        await _call(
            tools["register_references"],
            references=[
                {"path": "正文/0002 参考章.md", "kind": "author_text", "quote": "模型觉得可以"}
            ],
        )
    assert _code(invented) == "REFERENCE_EVIDENCE_INVALID"


@pytest.mark.anyio
async def test_discovered_reference_must_be_read_first(tmp_path: Path) -> None:
    workspace, tools, _ = _setup(tmp_path)
    workspace.write("正文/0002 参考章.md", "# 参考章\n\n书房里有一枚脚印。\n", expected_digest=None)

    with pytest.raises(WorkspaceError) as unread:
        await _call(
            tools["register_references"],
            references=[
                {"path": "正文/0002 参考章.md", "kind": "discovered", "quote": "书房里有一枚脚印。"}
            ],
        )
    assert _code(unread) == "REFERENCE_EVIDENCE_INVALID"

    await _call(tools["read_file"], path="正文/0002 参考章.md")
    accepted = await _call(
        tools["register_references"],
        references=[
            {"path": "正文/0002 参考章.md", "kind": "discovered", "quote": "书房里有一枚脚印。"}
        ],
    )
    assert accepted["references"][0]["kind"] == "discovered"

    with pytest.raises(WorkspaceError) as wrong_quote:
        await _call(
            tools["register_references"],
            references=[
                {"path": "正文/0002 参考章.md", "kind": "discovered", "quote": "文中没有这句"}
            ],
        )
    assert _code(wrong_quote) == "REFERENCE_EVIDENCE_INVALID"


@pytest.mark.anyio
async def test_discovered_reference_must_quote_a_read_range(tmp_path: Path) -> None:
    """读过文件开头不能拿后面的原文当关联证据：引文要落在实际提供过的区间。"""
    path = "正文/0002 参考章.md"
    workspace, tools, _ = _setup(tmp_path)
    workspace.write(
        path, "# 参考章\n\n书房里有一枚脚印。\n\n窗外的雪停了。\n", expected_digest=None
    )
    await _call(tools["read_file"], path=path, limit=1)

    with pytest.raises(WorkspaceError) as unprovided:
        await _call(
            tools["register_references"],
            references=[{"path": path, "kind": "discovered", "quote": "窗外的雪停了。"}],
        )
    assert _code(unprovided) == "REFERENCE_EVIDENCE_INVALID"
    assert unprovided.value.details["path"] == path

    await _call(tools["read_file"], path=path)
    accepted = await _call(
        tools["register_references"],
        references=[{"path": path, "kind": "discovered", "quote": "窗外的雪停了。"}],
    )
    assert accepted["references"][0]["kind"] == "discovered"


@pytest.mark.anyio
async def test_register_references_rejects_other_kinds(tmp_path: Path) -> None:
    workspace, tools, _ = _setup(tmp_path)
    workspace.write("正文/0002 参考章.md", "# 参考章\n", expected_digest=None)
    with pytest.raises(WorkspaceError) as invalid:
        await _call(
            tools["register_references"],
            references=[{"path": "正文/0002 参考章.md", "kind": "author_selected"}],
        )
    assert _code(invalid) == "REFERENCE_KIND_INVALID"
