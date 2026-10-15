"""read_chapter / list_chapters 工具单测：读回 + 参数约束 + 目录概览。

V1 弃用后 chapter 工具只读（write_chapter 已随写作流退役）；播种直接
落盘 chapters/*.md（store 的 save_* 与裸写文件均为夹具级手段）。
"""

from pathlib import Path

import pytest

from hastings.bible.store import StoryWorkspace
from hastings.tools.chapter_tools import create_chapter_tools
from hastings.tools.errors import ToolInputError
from tau_agent import AgentTool


@pytest.fixture
def workspace(tmp_path: Path) -> StoryWorkspace:
    ws = StoryWorkspace(root=tmp_path / "stories", slug="demo")
    ws.ensure_dirs()
    return ws


def _write_chapter_file(workspace: StoryWorkspace, chapter: int, text: str) -> None:
    workspace.ensure_dirs()
    (workspace.chapters_dir / f"ch{chapter:02d}.md").write_text(text, encoding="utf-8")


@pytest.fixture
def read_chapter(workspace: StoryWorkspace) -> AgentTool:
    return create_chapter_tools(workspace)[0]


@pytest.fixture
def list_chapters(workspace: StoryWorkspace) -> AgentTool:
    return create_chapter_tools(workspace)[1]


# ---------- read_chapter（P4 T3：正文读回） ----------


@pytest.mark.anyio
async def test_read_returns_full_text(read_chapter: AgentTool, workspace: StoryWorkspace) -> None:
    text = "# 第1章 雪封之夜\n\n雪落无声。\n"
    _write_chapter_file(workspace, 1, text)
    result = await read_chapter.execute("call-2", {"chapter": 1})

    assert "雪落无声。" in result.text
    assert result.details == {
        "path": str(workspace.chapters_dir / "ch01.md"),
        "chapter": 1,
        "chars": len(text),
    }


@pytest.mark.anyio
async def test_read_missing_chapter_lists_available(
    read_chapter: AgentTool, workspace: StoryWorkspace
) -> None:
    _write_chapter_file(workspace, 1, "# 第1章\n\n初章\n")
    with pytest.raises(ToolInputError, match="第3章尚未写作.*可用章节：1"):
        await read_chapter.execute("call-2", {"chapter": 3})


@pytest.mark.anyio
async def test_read_missing_chapter_when_empty(
    read_chapter: AgentTool, workspace: StoryWorkspace
) -> None:
    with pytest.raises(ToolInputError, match="尚未写作.*还没有任何章节"):
        await read_chapter.execute("call-1", {"chapter": 1})


@pytest.mark.anyio
async def test_read_rejects_invalid_chapter(read_chapter: AgentTool) -> None:
    with pytest.raises(ToolInputError, match="整数"):
        await read_chapter.execute("call-1", {"chapter": "1"})
    with pytest.raises(ToolInputError, match="整数"):
        await read_chapter.execute("call-1", {"chapter": True})
    with pytest.raises(ToolInputError, match="≥1"):
        await read_chapter.execute("call-1", {"chapter": 0})


def test_read_render_call(read_chapter: AgentTool) -> None:
    render = read_chapter.render_call
    assert render is not None
    assert render({"chapter": 3}) == "读取第3章"


# ---------- list_chapters（P4 T3：目录概览） ----------


@pytest.mark.anyio
async def test_list_empty_chapters(list_chapters: AgentTool) -> None:
    result = await list_chapters.execute("call-1", {})

    assert "还没有任何章节" in result.text
    assert result.details == {"chapters": []}


@pytest.mark.anyio
async def test_list_chapters_sorted_with_titles(
    list_chapters: AgentTool, workspace: StoryWorkspace
) -> None:
    _write_chapter_file(workspace, 2, "# 第2章\n\n第二章\n")
    _write_chapter_file(workspace, 1, "# 第1章 雪封之夜\n\n第一章\n")
    result = await list_chapters.execute("call-3", {})

    assert "共 2 章" in result.text
    chapters = result.details["chapters"]
    assert [c["chapter"] for c in chapters] == [1, 2]
    assert chapters[0]["title"] == "雪封之夜"
    assert chapters[1]["title"] is None
    assert all(c["chars"] > 0 for c in chapters)


@pytest.mark.anyio
async def test_list_ignores_foreign_files(
    list_chapters: AgentTool, workspace: StoryWorkspace
) -> None:
    workspace.ensure_dirs()
    (workspace.chapters_dir / "notes.md").write_text("随手记", encoding="utf-8")
    result = await list_chapters.execute("call-1", {})

    assert "还没有任何章节" in result.text


def test_list_render_call(list_chapters: AgentTool) -> None:
    render = list_chapters.render_call
    assert render is not None
    assert render({}) == "列出章节目录"
