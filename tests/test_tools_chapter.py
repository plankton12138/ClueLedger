"""write_chapter 工具单测：落盘格式 + 参数约束 + 覆盖保护。"""

from pathlib import Path

import pytest

from hastings.bible.store import StoryWorkspace
from hastings.tools.chapter_tools import create_chapter_tools
from hastings.tools.errors import ToolInputError
from tau_agent import AgentTool


@pytest.fixture
def workspace(tmp_path: Path) -> StoryWorkspace:
    return StoryWorkspace(root=tmp_path / "stories", slug="demo")


@pytest.fixture
def write_chapter(workspace: StoryWorkspace) -> AgentTool:
    return create_chapter_tools(workspace)[0]


@pytest.mark.anyio
async def test_writes_chapter_with_title(
    write_chapter: AgentTool, workspace: StoryWorkspace
) -> None:
    result = await write_chapter.execute(
        "call-1", {"chapter": 1, "title": "列车上的尸体", "content": "雪落无声。\n\n灯灭了。"}
    )

    path = workspace.chapters_dir / "ch01.md"
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "# 第1章 列车上的尸体\n\n雪落无声。\n\n灯灭了。\n"
    assert "ch01.md" in result.text
    assert result.details == {"path": str(path), "chapter": 1, "force": False}


@pytest.mark.anyio
async def test_writes_chapter_without_title(
    write_chapter: AgentTool, workspace: StoryWorkspace
) -> None:
    await write_chapter.execute("call-1", {"chapter": 12, "content": "尾声。"})

    text = (workspace.chapters_dir / "ch12.md").read_text(encoding="utf-8")
    assert text == "# 第12章\n\n尾声。\n"


@pytest.mark.anyio
async def test_rejects_non_integer_chapter(write_chapter: AgentTool) -> None:
    with pytest.raises(ToolInputError, match="整数"):
        await write_chapter.execute("call-1", {"chapter": "1", "content": "x"})


@pytest.mark.anyio
async def test_rejects_bool_chapter(write_chapter: AgentTool) -> None:
    with pytest.raises(ToolInputError, match="整数"):
        await write_chapter.execute("call-1", {"chapter": True, "content": "x"})


@pytest.mark.anyio
async def test_rejects_chapter_below_one(write_chapter: AgentTool) -> None:
    with pytest.raises(ToolInputError, match="≥1"):
        await write_chapter.execute("call-1", {"chapter": 0, "content": "x"})


@pytest.mark.anyio
async def test_rejects_blank_content(write_chapter: AgentTool) -> None:
    with pytest.raises(ToolInputError, match="content"):
        await write_chapter.execute("call-1", {"chapter": 1, "content": "  \n "})


@pytest.mark.anyio
async def test_rejects_overwrite_without_force(
    write_chapter: AgentTool, workspace: StoryWorkspace
) -> None:
    await write_chapter.execute("call-1", {"chapter": 1, "content": "初稿"})
    with pytest.raises(ToolInputError, match="force=true"):
        await write_chapter.execute("call-1", {"chapter": 1, "content": "改稿"})

    assert "初稿" in (workspace.chapters_dir / "ch01.md").read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_overwrite_with_force(write_chapter: AgentTool, workspace: StoryWorkspace) -> None:
    await write_chapter.execute("call-1", {"chapter": 1, "content": "初稿"})
    result = await write_chapter.execute("call-1", {"chapter": 1, "content": "改稿", "force": True})

    assert "改稿" in (workspace.chapters_dir / "ch01.md").read_text(encoding="utf-8")
    assert isinstance(result.details, dict) and result.details["force"] is True


def test_render_call(write_chapter: AgentTool) -> None:
    render = write_chapter.render_call
    assert render is not None
    assert render({"chapter": 1, "content": "abc"}) == "写入第1章（3 字）"
    assert render({"chapter": 2}) == "写入第2章（0 字）"
