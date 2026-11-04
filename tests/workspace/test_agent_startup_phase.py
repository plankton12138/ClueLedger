"""v1.2 M2：空作品只按 ``STRUCTURE.md`` 是否存在判定起步。"""

from __future__ import annotations

from pathlib import Path

from hastings.library.modules import ModuleLibrary
from hastings.prompts.authoring import STORY_DESIGN_INSTRUCTIONS
from hastings.workspace.agent import RunContext, prepare_request_inputs
from hastings.workspace.files import Workspace
from hastings.workspace.git import GitRepo
from hastings.workspace.service import WriteSession
from hastings.workspace.story_context import StoryContextSnapshot
from hastings.workspace.story_store import EMPTY_REVISION, MAIN_VARIANT_ID, StoryStore
from hastings.workspace.structure_io import write_structure_document

CARD = "暴雪封山的旅馆发生命案，湿手套先让林砚怀疑管家；真相未定，悬念在于谁换了手套。"
STRUCTURE = "## 铁律\n第三人称限制视角。\n凶手不在未出场人物中。\n\n## 路线\n暴雪之夜收束。\n"


def _design_run(
    tmp_path: Path,
    *,
    author_text: str,
) -> tuple[Workspace, RunContext, StoryStore]:
    workspace = Workspace.create(tmp_path / "书")
    GitRepo(workspace.root).init()
    store = StoryStore(tmp_path / "state", workspace, format_version=5)
    session = WriteSession(workspace, instruction=author_text)
    run = RunContext(
        workspace,
        session,
        author_messages=((1, author_text),),
        current_author_text=author_text,
        state_root=tmp_path / "state",
        story=StoryContextSnapshot(
            variant_id=MAIN_VARIANT_ID,
            revision=EMPTY_REVISION,
            purpose="design",
        ),
    )
    return workspace, run, store


def test_empty_structure_injects_guided_prompt(tmp_path: Path) -> None:
    workspace, run, _ = _design_run(tmp_path, author_text="我还没想法，带我想想写什么")

    inputs = prepare_request_inputs(workspace=workspace, run=run)

    assert "作者希望被引导" in inputs.system
    assert "一次只问一个问题" in inputs.system
    assert "STRUCTURE.md" in inputs.system
    assert "构思任务" not in inputs.system


def test_empty_structure_defaults_to_freeform(tmp_path: Path) -> None:
    """作者直接说想法：自由发挥模式，不追问。"""
    workspace, run, _ = _design_run(tmp_path, author_text="我想写暴雪旅馆的密室案")

    inputs = prepare_request_inputs(workspace=workspace, run=run)

    assert "作者会直接描述想法" in inputs.system
    assert "一次只问一个问题" not in inputs.system
    assert "我想写暴雪旅馆的密室案" in inputs.system


def test_clear_request_injects_structure_prompt_directly(tmp_path: Path) -> None:
    workspace, run, _ = _design_run(tmp_path, author_text=CARD)

    inputs = prepare_request_inputs(workspace=workspace, run=run)

    assert "第一版" in inputs.system
    assert "湿手套" in inputs.system
    assert "直接写入" in inputs.system
    assert "设定卡" not in inputs.system
    assert "构思任务" not in inputs.system


def test_existing_structure_falls_back_to_design_instructions(tmp_path: Path) -> None:
    workspace, run, _ = _design_run(tmp_path, author_text="看看结构")
    write_structure_document(
        workspace, text=STRUCTURE, expected_digest=None, creating=True
    )

    inputs = prepare_request_inputs(workspace=workspace, run=run)

    assert "作者希望被引导" not in inputs.system
    assert STORY_DESIGN_INSTRUCTIONS.strip()[:20] in inputs.system


def test_empty_structure_opens_structure_but_not_module_write_tools(tmp_path: Path) -> None:
    """模块需要另行建议和确认，不能随空作品起步自动创建。"""
    workspace, run, _ = _design_run(tmp_path, author_text="就这样开始")
    run.library = ModuleLibrary(tmp_path / "模块库")

    inputs = prepare_request_inputs(workspace=workspace, run=run)

    names = [tool.name for tool in inputs.tools]
    assert "write_structure" in names
    assert "prepare_module_changes" not in names
    assert "apply_module_changes" not in names
    assert "set_task_scope" in names


def test_existing_structure_keeps_task_gating(tmp_path: Path) -> None:
    workspace, run, _ = _design_run(tmp_path, author_text="聊聊这条线怎么安排")
    write_structure_document(
        workspace, text=STRUCTURE, expected_digest=None, creating=True
    )

    inputs = prepare_request_inputs(workspace=workspace, run=run)

    names = [tool.name for tool in inputs.tools]
    # 讨论轮不开放模块内容写入。
    assert "prepare_module_changes" not in names


def test_write_purpose_never_gets_startup_prompt(tmp_path: Path) -> None:
    """没有作品故事上下文时不注入结构起步提示。"""
    workspace = Workspace.create(tmp_path / "书")
    GitRepo(workspace.root).init()
    session = WriteSession(workspace, instruction="写第一章")
    run = RunContext(workspace, session, author_messages=((1, "写第一章"),))

    inputs = prepare_request_inputs(workspace=workspace, run=run)

    assert "作者希望被引导" not in inputs.system

