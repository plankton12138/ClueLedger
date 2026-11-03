"""v1.1 Q1 M1：起步阶段注入——按作品事实把起步对话 / 结构起草接进构思回合。

判定只看两样事实（``book_intent`` 是否确认、``STRUCTURE.md`` 是否存在），
不读对话历史；起步阶段不叠加旧构思工作台说明，旧格式作品不参与起步。
"""

from __future__ import annotations

from pathlib import Path

from hastings.library.modules import ModuleLibrary
from hastings.prompts.authoring import STORY_DESIGN_INSTRUCTIONS
from hastings.workspace.agent import RunContext, prepare_request_inputs
from hastings.workspace.book_startup import confirm_startup_book_intent
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
    store = StoryStore(tmp_path / "state", workspace, format_version=4)
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


def test_intent_phase_injects_guided_prompt(tmp_path: Path) -> None:
    """意图未确认 + 作者点名引导：注入引导起步提示，不叠加旧构思说明。"""
    workspace, run, _ = _design_run(tmp_path, author_text="我还没想法，带我想想写什么")

    inputs = prepare_request_inputs(workspace=workspace, run=run)

    assert "陪作者为一本新书起步" in inputs.system
    assert "一次只问一个问题" in inputs.system
    assert "构思任务" not in inputs.system


def test_intent_phase_defaults_to_freeform(tmp_path: Path) -> None:
    """作者直接说想法：自由发挥模式，不追问。"""
    workspace, run, _ = _design_run(tmp_path, author_text="我想写暴雪旅馆的密室案")

    inputs = prepare_request_inputs(workspace=workspace, run=run)

    assert "作者会直接说想法" in inputs.system
    assert "一次只问一个问题" not in inputs.system


def test_draft_phase_injects_structure_prompt_after_confirm(tmp_path: Path) -> None:
    """意图已确认、结构未起草：注入结构起草提示，设定卡文本在场。"""
    workspace, run, store = _design_run(tmp_path, author_text="就这样开始")
    confirm_startup_book_intent(store, text=CARD, request_key="startup:1")

    inputs = prepare_request_inputs(workspace=workspace, run=run)

    assert "起草第一版全书结构" in inputs.system
    assert "湿手套" in inputs.system
    assert "write_structure" in inputs.system
    assert "构思任务" not in inputs.system


def test_done_phase_falls_back_to_design_instructions(tmp_path: Path) -> None:
    """结构与意图都在：不再处于起步阶段，走既有构思任务说明。"""
    workspace, run, store = _design_run(tmp_path, author_text="看看结构")
    confirm_startup_book_intent(store, text=CARD, request_key="startup:1")
    write_structure_document(
        workspace, text=STRUCTURE, expected_digest=None, creating=True
    )

    inputs = prepare_request_inputs(workspace=workspace, run=run)

    assert "陪作者为一本新书起步" not in inputs.system
    assert STORY_DESIGN_INSTRUCTIONS.strip()[:20] in inputs.system


def test_draft_phase_opens_structure_and_module_tools(tmp_path: Path) -> None:
    """起草回合：作者原话不含「结构」等关键词也开放结构与模块写入工具。"""
    workspace, run, store = _design_run(tmp_path, author_text="就这样开始")
    confirm_startup_book_intent(store, text=CARD, request_key="startup:1")
    run.library = ModuleLibrary(tmp_path / "模块库")

    inputs = prepare_request_inputs(workspace=workspace, run=run)

    names = [tool.name for tool in inputs.tools]
    assert "write_structure" in names
    assert "prepare_module_changes" in names
    assert "apply_module_changes" in names
    assert "set_task_scope" in names


def test_done_phase_keeps_intent_gating(tmp_path: Path) -> None:
    """起步完成后：讨论轮的模块工具剥离与结构写授权照旧，不留起步后门。"""
    workspace, run, store = _design_run(tmp_path, author_text="聊聊这条线怎么安排")
    confirm_startup_book_intent(store, text=CARD, request_key="startup:1")
    write_structure_document(
        workspace, text=STRUCTURE, expected_digest=None, creating=True
    )

    inputs = prepare_request_inputs(workspace=workspace, run=run)

    names = [tool.name for tool in inputs.tools]
    # 讨论轮不开放模块内容写入。
    assert "prepare_module_changes" not in names


def test_write_purpose_never_gets_startup_prompt(tmp_path: Path) -> None:
    """起步提示只进构思回合；正文用途即使意图为空也不注入。"""
    workspace = Workspace.create(tmp_path / "书")
    GitRepo(workspace.root).init()
    session = WriteSession(workspace, instruction="写第一章")
    run = RunContext(workspace, session, author_messages=((1, "写第一章"),))

    inputs = prepare_request_inputs(workspace=workspace, run=run)

    assert "陪作者为一本新书起步" not in inputs.system

