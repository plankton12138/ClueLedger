"""write_chapter / read_chapter / list_chapters 工具单测：落盘格式 + 参数约束 + 覆盖保护 + 读回。"""

from pathlib import Path

import pytest

from hastings.bible.store import StoryWorkspace
from hastings.manuscript.write_guard import EditMarkRegistry, RunKindGuard
from hastings.tools.chapter_tools import create_chapter_tools
from hastings.tools.errors import ToolInputError
from tau_agent import AgentTool


@pytest.fixture
def workspace(tmp_path: Path) -> StoryWorkspace:
    return StoryWorkspace(root=tmp_path / "stories", slug="demo")


@pytest.fixture
def write_chapter(workspace: StoryWorkspace) -> AgentTool:
    return create_chapter_tools(workspace)[0]


@pytest.fixture
def read_chapter(workspace: StoryWorkspace) -> AgentTool:
    return create_chapter_tools(workspace)[1]


@pytest.fixture
def list_chapters(workspace: StoryWorkspace) -> AgentTool:
    return create_chapter_tools(workspace)[2]


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


# ---------- read_chapter（P4 T3：正文读回） ----------


@pytest.mark.anyio
async def test_read_returns_full_text(
    write_chapter: AgentTool, read_chapter: AgentTool, workspace: StoryWorkspace
) -> None:
    await write_chapter.execute(
        "call-1", {"chapter": 1, "title": "雪封之夜", "content": "雪落无声。"}
    )
    result = await read_chapter.execute("call-2", {"chapter": 1})

    assert "雪落无声。" in result.text
    assert result.details == {
        "path": str(workspace.chapters_dir / "ch01.md"),
        "chapter": 1,
        "chars": len("# 第1章 雪封之夜\n\n雪落无声。\n"),
    }


@pytest.mark.anyio
async def test_read_missing_chapter_lists_available(
    write_chapter: AgentTool, read_chapter: AgentTool
) -> None:
    await write_chapter.execute("call-1", {"chapter": 1, "content": "初章"})
    with pytest.raises(ToolInputError, match="第3章尚未写作.*可用章节：1"):
        await read_chapter.execute("call-2", {"chapter": 3})


@pytest.mark.anyio
async def test_read_missing_chapter_when_empty(read_chapter: AgentTool) -> None:
    with pytest.raises(ToolInputError, match="尚未写作.*还没有任何章节.*write_chapter"):
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
    write_chapter: AgentTool, list_chapters: AgentTool
) -> None:
    await write_chapter.execute("call-1", {"chapter": 2, "content": "第二章"})
    await write_chapter.execute("call-2", {"chapter": 1, "title": "雪封之夜", "content": "第一章"})
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


# ---------- write_guard（P5 T3，D4）：作者草稿锁挡 agent 写入 ----------


@pytest.mark.anyio
async def test_write_blocked_by_author_mark_even_with_force(
    workspace: StoryWorkspace,
) -> None:
    """作者锁 3 章 → write_chapter(force=true) 也被拒，错误含三要素且未触盘。"""
    marks = EditMarkRegistry()
    marks.acquire("ch03")
    (write,) = create_chapter_tools(workspace, edit_marks=marks)[:1]

    with pytest.raises(ToolInputError) as exc_info:
        await write.execute("call-1", {"chapter": 3, "content": "抢写。", "force": True})

    message = str(exc_info.value)
    assert "第3章正在被作者手动编辑" in message  # 哪里错
    assert "为什么错" in message  # 覆盖会丢弃作者未保存的草稿
    assert "怎样算对" in message  # 等待保存或放弃
    assert not (workspace.chapters_dir / "ch03.md").exists()


@pytest.mark.anyio
async def test_write_unblocked_after_mark_release(workspace: StoryWorkspace) -> None:
    """锁生命周期：作者保存/放弃释放后，agent 恢复可写。"""
    marks = EditMarkRegistry()
    marks.acquire("ch03")
    tools = create_chapter_tools(workspace, edit_marks=marks)
    marks.release("ch03")

    await tools[0].execute("call-1", {"chapter": 3, "content": "接手续写。", "force": True})

    assert (workspace.chapters_dir / "ch03.md").exists()


@pytest.mark.anyio
async def test_other_chapters_not_blocked_by_mark(
    workspace: StoryWorkspace,
) -> None:
    """锁按章隔离：ch03 被锁不影响 ch04 落笔。"""
    marks = EditMarkRegistry()
    marks.acquire("ch03")
    (write,) = create_chapter_tools(workspace, edit_marks=marks)[:1]

    await write.execute("call-1", {"chapter": 4, "content": "第四章不受影响。"})

    assert (workspace.chapters_dir / "ch04.md").exists()


# ---------- run_kind 守卫（P5 T4，D4 末条）：对账轮禁止写正文 ----------


@pytest.mark.anyio
async def test_write_blocked_during_reconcile_run(workspace: StoryWorkspace) -> None:
    """对账运行中 write_chapter 被拒——硬约束在工具层，force 也无例外。"""
    run_kind = RunKindGuard()
    run_kind.begin("reconcile")
    (write,) = create_chapter_tools(workspace, run_kind=run_kind)[:1]

    with pytest.raises(ToolInputError) as exc_info:
        await write.execute("call-1", {"chapter": 3, "content": "对账时偷改稿。", "force": True})

    message = str(exc_info.value)
    assert "对账运行进行中" in message  # 哪里错
    assert "为什么错" in message  # 正文只经由作者的手或显式写作流变更
    assert "怎样算对" in message  # 只落账，不写正文
    assert not (workspace.chapters_dir / "ch03.md").exists()


@pytest.mark.anyio
async def test_write_allowed_again_after_reconcile_ends(workspace: StoryWorkspace) -> None:
    """守卫生命周期：对账结束（end）后恢复可写；user 类别也不拦。"""
    run_kind = RunKindGuard()
    run_kind.begin("reconcile")
    tools = create_chapter_tools(workspace, run_kind=run_kind)
    run_kind.end()

    await tools[0].execute("call-1", {"chapter": 3, "content": "对账已结束。", "force": True})

    assert (workspace.chapters_dir / "ch03.md").exists()


@pytest.mark.anyio
async def test_user_run_kind_does_not_block_write(workspace: StoryWorkspace) -> None:
    """run_kind="user"（普通写作流）不触发对账守门。"""
    run_kind = RunKindGuard()
    run_kind.begin("user")
    (write,) = create_chapter_tools(workspace, run_kind=run_kind)[:1]

    await write.execute("call-1", {"chapter": 3, "content": "正常写作流。"})

    assert (workspace.chapters_dir / "ch03.md").exists()
