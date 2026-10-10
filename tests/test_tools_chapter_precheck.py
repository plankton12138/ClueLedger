"""write_chapter 分级预检 + outline 自动推进单测（P8 T4，计划 D3/D4）。

error（阻断）：阶段门 / 目标章不在 outline / 工作区无角色档案；
warning（不阻断）：method 未 locked / plant_plans 引用悬空或仍 planned，
全部进 details["precheck_warnings"]。预检在草稿锁 / run_kind 守卫之后、
落盘之前执行；落盘后自动推进该章 outline 状态（planned→written，
force 覆盖 written→revised）——正文与章纲状态由工具层自动对账。
"""

from pathlib import Path

import pytest

from hastings.bible.models import Character, Foreshadow, Method, OutlineEntry
from hastings.bible.store import (
    StoryWorkspace,
    list_characters,
    load_ledger,
    load_story_doc,
    save_character,
    save_ledger,
    save_story_doc,
)
from hastings.bible.story_state import StoryDoc
from hastings.manuscript.write_guard import EditMarkRegistry
from hastings.tools.chapter_tools import create_chapter_tools
from hastings.tools.errors import ToolInputError
from tau_agent import AgentTool


def _seed_writing(
    workspace: StoryWorkspace,
    *,
    chapters: tuple[int, ...] = (1,),
    characters: bool = True,
) -> None:
    """把工作区预置为 writing 态：story.yaml + 章纲（指定章）+ 可选角色。"""
    save_story_doc(workspace, StoryDoc(slug=workspace.slug, stage="writing"))
    save_ledger(
        workspace.outline_path,
        [OutlineEntry(name=f"第{n}章", chapter=n, beats=[f"节拍{n}"]) for n in chapters],
    )
    if characters:
        save_character(workspace, Character(name="波洛", identity="比利时侦探"))


def _make_write(workspace: StoryWorkspace) -> AgentTool:
    return create_chapter_tools(workspace)[0]


@pytest.fixture
def workspace(tmp_path: Path) -> StoryWorkspace:
    return StoryWorkspace(root=tmp_path / "stories", slug="demo")


async def _write(tool: AgentTool, chapter: int = 1, **extra: object) -> object:
    arguments: dict[str, object] = {"chapter": chapter, "content": "雪落无声。"}
    arguments.update(extra)
    return await tool.execute("call-1", arguments)  # type: ignore[arg-type]


# ---------- error 级：阶段门 ----------


@pytest.mark.anyio
async def test_write_rejected_at_intake_with_three_elements(
    workspace: StoryWorkspace,
) -> None:
    """intake 态写章被拒：三要素指明当前阶段与推进方式。"""
    tool = _make_write(workspace)

    with pytest.raises(ToolInputError) as exc_info:
        await _write(tool)
    msg = str(exc_info.value)
    assert "intake" in msg and "第1章" in msg
    assert "为什么错" in msg
    assert "story_stage" in msg  # 怎样算对：经 story_stage 推进到 writing
    assert not (workspace.chapters_dir / "ch01.md").exists()


@pytest.mark.anyio
@pytest.mark.parametrize("stage", ["bible_drafting", "bible_confirmed"])
async def test_write_rejected_before_writing_stages(workspace: StoryWorkspace, stage: str) -> None:
    save_story_doc(workspace, StoryDoc(slug=workspace.slug, stage=stage))  # type: ignore[arg-type]
    tool = _make_write(workspace)

    with pytest.raises(ToolInputError, match=stage):
        await _write(tool)


@pytest.mark.anyio
async def test_write_allowed_at_revising(workspace: StoryWorkspace) -> None:
    """revising 也是合法写作阶段（维护修订）。"""
    _seed_writing(workspace)
    save_story_doc(workspace, StoryDoc(slug=workspace.slug, stage="revising"))
    tool = _make_write(workspace)

    await _write(tool)

    assert (workspace.chapters_dir / "ch01.md").exists()


# ---------- error 级：目标章不在 outline ----------


@pytest.mark.anyio
async def test_write_rejected_when_chapter_not_in_outline(
    workspace: StoryWorkspace,
) -> None:
    _seed_writing(workspace, chapters=(2,))  # 只建了 ch2 的章纲
    tool = _make_write(workspace)

    with pytest.raises(ToolInputError) as exc_info:
        await _write(tool, chapter=1)
    msg = str(exc_info.value)
    assert "第1章" in msg and "章纲" in msg
    assert "bible_upsert" in msg  # 指路：先建章纲
    assert not (workspace.chapters_dir / "ch01.md").exists()


@pytest.mark.anyio
async def test_write_passes_outline_gate_when_entry_exists(
    workspace: StoryWorkspace,
) -> None:
    _seed_writing(workspace)
    tool = _make_write(workspace)

    result = await _write(tool)

    assert "已写入" in result.text  # type: ignore[attr-defined]


# ---------- error 级：工作区无角色档案 ----------


@pytest.mark.anyio
async def test_write_rejected_without_any_character(
    workspace: StoryWorkspace,
) -> None:
    _seed_writing(workspace, characters=False)
    tool = _make_write(workspace)

    with pytest.raises(ToolInputError) as exc_info:
        await _write(tool)
    msg = str(exc_info.value)
    assert "角色" in msg and "bible_upsert" in msg
    assert not (workspace.chapters_dir / "ch01.md").exists()


# ---------- warning 级：不阻断，进 details["precheck_warnings"] ----------


@pytest.mark.anyio
async def test_warning_when_method_not_all_locked(workspace: StoryWorkspace) -> None:
    _seed_writing(workspace)
    save_ledger(workspace.method_path, [Method(name="怀表诡计")])  # drafted
    tool = _make_write(workspace)

    result = await _write(tool)

    assert isinstance(result.details, dict)
    warnings = result.details["precheck_warnings"]
    assert any("手法" in w and "未全部锁定" in w for w in warnings)
    assert (workspace.chapters_dir / "ch01.md").exists()  # 不阻断


@pytest.mark.anyio
async def test_no_warning_key_when_method_locked(workspace: StoryWorkspace) -> None:
    _seed_writing(workspace)
    save_ledger(workspace.method_path, [Method(name="怀表诡计", status="locked")])
    tool = _make_write(workspace)

    result = await _write(tool)

    assert isinstance(result.details, dict)
    assert "precheck_warnings" not in result.details


@pytest.mark.anyio
async def test_warning_when_plant_plan_foreshadow_missing(
    workspace: StoryWorkspace,
) -> None:
    _seed_writing(workspace)
    save_ledger(
        workspace.outline_path,
        [OutlineEntry(name="第1章", chapter=1, plant_plans=["保温瓶的位置"])],
    )
    tool = _make_write(workspace)

    result = await _write(tool)

    warnings = result.details["precheck_warnings"]  # type: ignore[index]
    assert any("保温瓶的位置" in w and "未建档" in w for w in warnings)


@pytest.mark.anyio
async def test_warning_when_plant_plan_foreshadow_still_planned(
    workspace: StoryWorkspace,
) -> None:
    """引用已建档但仍 planned 的伏笔：提醒写后对账推进，不阻断。"""
    _seed_writing(workspace)
    save_ledger(
        workspace.outline_path,
        [OutlineEntry(name="第1章", chapter=1, plant_plans=["保温瓶的位置"])],
    )
    save_ledger(
        workspace.foreshadows_path,
        [Foreshadow(name="保温瓶的位置", content="瓶身有夹层")],
    )
    tool = _make_write(workspace)

    result = await _write(tool)

    warnings = result.details["precheck_warnings"]  # type: ignore[index]
    assert any("保温瓶的位置" in w and "planned" in w for w in warnings)


@pytest.mark.anyio
async def test_no_plant_plan_warning_when_foreshadow_planted(
    workspace: StoryWorkspace,
) -> None:
    _seed_writing(workspace)
    save_ledger(
        workspace.outline_path,
        [OutlineEntry(name="第1章", chapter=1, plant_plans=["保温瓶的位置"])],
    )
    save_ledger(
        workspace.foreshadows_path,
        [Foreshadow(name="保温瓶的位置", content="瓶身有夹层", status="planted")],
    )
    tool = _make_write(workspace)

    result = await _write(tool)

    assert isinstance(result.details, dict)
    assert "precheck_warnings" not in result.details


# ---------- outline 自动推进（D3：正文与章纲状态工具层对账） ----------


@pytest.mark.anyio
async def test_outline_status_advances_to_written(workspace: StoryWorkspace) -> None:
    _seed_writing(workspace)
    tool = _make_write(workspace)

    result = await _write(tool)

    assert isinstance(result.details, dict)
    assert result.details["outline_status"] == "written"
    (entry,) = load_ledger(workspace.outline_path, OutlineEntry)
    assert entry.status == "written"


@pytest.mark.anyio
async def test_force_rewrite_written_advances_to_revised(
    workspace: StoryWorkspace,
) -> None:
    """force 覆盖已有 written 的章 = 修订轮：outline 推进到 revised。"""
    _seed_writing(workspace)
    save_ledger(
        workspace.outline_path,
        [OutlineEntry(name="第1章", chapter=1, status="written")],
    )
    tool = _make_write(workspace)

    result = await _write(tool, force=True)

    assert isinstance(result.details, dict)
    assert result.details["outline_status"] == "revised"
    (entry,) = load_ledger(workspace.outline_path, OutlineEntry)
    assert entry.status == "revised"


@pytest.mark.anyio
async def test_revised_outline_stays_revised(workspace: StoryWorkspace) -> None:
    _seed_writing(workspace)
    save_ledger(
        workspace.outline_path,
        [OutlineEntry(name="第1章", chapter=1, status="revised")],
    )
    tool = _make_write(workspace)

    result = await _write(tool, force=True)

    assert isinstance(result.details, dict)
    assert result.details["outline_status"] == "revised"


# ---------- 预检顺序：草稿锁 / run_kind 先于预检（D4 明文） ----------


@pytest.mark.anyio
async def test_author_mark_beats_stage_gate(workspace: StoryWorkspace) -> None:
    """intake 态 + 作者锁 ch03：报作者锁（草稿锁是更靠近写盘的守卫）。"""
    marks = EditMarkRegistry()
    marks.acquire("ch03")
    tool = create_chapter_tools(workspace, edit_marks=marks)[0]

    with pytest.raises(ToolInputError, match="作者手动编辑"):
        await _write(tool, chapter=3)


@pytest.mark.anyio
async def test_stage_gate_beats_outline_gate(workspace: StoryWorkspace) -> None:
    """空工作区（intake 且无章纲）：报阶段门而不是章纲缺失——阶段是外层门。"""
    tool = _make_write(workspace)

    with pytest.raises(ToolInputError) as exc_info:
        await _write(tool)
    assert "intake" in str(exc_info.value)
    assert "章纲" not in str(exc_info.value).split("为什么错")[0]


@pytest.mark.anyio
async def test_outline_gate_beats_character_gate(workspace: StoryWorkspace) -> None:
    """writing 态但无章纲无角色：报章纲缺失（outline 门先于角色门）。"""
    save_story_doc(workspace, StoryDoc(slug=workspace.slug, stage="writing"))
    tool = _make_write(workspace)

    with pytest.raises(ToolInputError, match="章纲"):
        await _write(tool)


@pytest.mark.anyio
async def test_precheck_leaves_ledgers_untouched_on_rejection(
    workspace: StoryWorkspace,
) -> None:
    """error 拒绝不触盘：outline 状态、角色账本、正文都保持原样。"""
    _seed_writing(workspace, characters=False)  # 无角色 → 角色门拒
    tool = _make_write(workspace)

    with pytest.raises(ToolInputError):
        await _write(tool)

    (entry,) = load_ledger(workspace.outline_path, OutlineEntry)
    assert entry.status == "planned"
    assert not list_characters(workspace)
    assert not (workspace.chapters_dir / "ch01.md").exists()
    assert load_story_doc(workspace).stage == "writing"
