"""story_stage 工具单测：推进/回退全链路 + 确认门派生 + 留痕 + 三要素（P8 T2）。"""

from pathlib import Path

import pytest

from hastings.bible.store import StoryWorkspace, load_story_doc, save_story_doc
from hastings.bible.story_state import StoryDoc
from hastings.tools.errors import ToolInputError
from hastings.tools.story_tools import create_story_tools
from tau_agent import AgentTool
from tau_agent.types import JSONValue


@pytest.fixture
def workspace(tmp_path: Path) -> StoryWorkspace:
    return StoryWorkspace(root=tmp_path / "stories", slug="demo")


@pytest.fixture
def tool(workspace: StoryWorkspace) -> AgentTool:
    return create_story_tools(workspace)[0]


async def _call(tool: AgentTool, arguments: dict[str, JSONValue]) -> object:
    return await tool.execute("call-1", arguments)


def _set_stage(workspace: StoryWorkspace, stage: str) -> None:
    save_story_doc(workspace, StoryDoc(slug=workspace.slug, stage=stage))  # type: ignore[arg-type]


# ---------- 推进 ----------


@pytest.mark.anyio
async def test_advance_intake_to_bible_drafting_with_history(
    tool: AgentTool, workspace: StoryWorkspace
) -> None:
    result = await _call(tool, {"action": "advance", "reason": "作者交付完整构想"})

    assert "intake" in result.text and "bible_drafting" in result.text
    doc = load_story_doc(workspace)
    assert doc.stage == "bible_drafting"
    assert len(doc.stage_history) == 1
    entry = doc.stage_history[0]
    assert entry.from_stage == "intake"
    assert entry.to_stage == "bible_drafting"
    assert entry.reason == "作者交付完整构想"
    assert entry.at  # ISO 时间戳非空


@pytest.mark.anyio
async def test_advance_requires_reason(tool: AgentTool) -> None:
    with pytest.raises(ToolInputError, match="reason"):
        await _call(tool, {"action": "advance"})


@pytest.mark.anyio
async def test_advance_rejects_invalid_action(tool: AgentTool) -> None:
    with pytest.raises(ToolInputError, match="action"):
        await _call(tool, {"action": "restart", "reason": "x"})


@pytest.mark.anyio
async def test_advance_rejects_unknown_target(tool: AgentTool) -> None:
    with pytest.raises(ToolInputError, match="advance"):
        await _call(tool, {"action": "advance", "target": "writing", "reason": "跳级"})


@pytest.mark.anyio
async def test_advance_self_loop_rejected(tool: AgentTool) -> None:
    """自环不是合法边：工具层边表检查先于一切（无效操作直接拒）。"""
    with pytest.raises(ToolInputError, match="合法去向"):
        await _call(tool, {"action": "advance", "target": "intake", "reason": "重复"})


@pytest.mark.anyio
async def test_advance_message_keeps_three_elements(tool: AgentTool) -> None:
    """非法迁移的错误消息三要素原样喂回模型（哪里错/为什么错/怎样算对）。"""
    with pytest.raises(ToolInputError) as exc_info:
        await _call(tool, {"action": "advance", "target": "complete", "reason": "x"})
    msg = str(exc_info.value)
    assert "intake" in msg and "complete" in msg
    assert "为什么错" in msg and "怎样算对" in msg


# ---------- 确认门派生（账本计数 → StagePrereqs） ----------


@pytest.mark.anyio
async def test_gate_blocks_empty_workspace_with_itemized_gaps(
    tool: AgentTool, workspace: StoryWorkspace
) -> None:
    """确认门：空工作区推进 →bible_confirmed 被拒，逐项点明缺口。"""
    _set_stage(workspace, "bible_drafting")
    with pytest.raises(ToolInputError) as exc_info:
        await _call(tool, {"action": "advance", "reason": "确认"})
    msg = str(exc_info.value)
    assert "角色 0/2" in msg  # 计数来自工作区实况
    assert "手法" in msg
    assert "章纲" in msg  # 章纲账本 T3 落地，本阶段恒为 0


# ---------- 回退 ----------


@pytest.mark.anyio
async def test_rollback_confirmed_to_drafting_ignores_prereqs(
    tool: AgentTool, workspace: StoryWorkspace
) -> None:
    """回退是作者驱动：空账本也允许推翻设定重来。"""
    _set_stage(workspace, "bible_confirmed")

    await _call(
        tool, {"action": "rollback", "target": "bible_drafting", "reason": "作者要求推翻人物设定"}
    )

    doc = load_story_doc(workspace)
    assert doc.stage == "bible_drafting"
    assert doc.stage_history[-1].reason == "作者要求推翻人物设定"


@pytest.mark.anyio
async def test_rollback_rejected_without_rollback_edge(
    tool: AgentTool, workspace: StoryWorkspace
) -> None:
    _set_stage(workspace, "intake")
    with pytest.raises(ToolInputError):
        await _call(tool, {"action": "rollback", "reason": "x"})


@pytest.mark.anyio
async def test_rollback_confirmed_to_drafting_keeps_three_elements(
    tool: AgentTool, workspace: StoryWorkspace
) -> None:
    _set_stage(workspace, "bible_confirmed")
    with pytest.raises(ToolInputError) as exc_info:
        await _call(tool, {"action": "rollback", "target": "intake", "reason": "越级"})
    msg = str(exc_info.value)
    assert "bible_confirmed" in msg and "intake" in msg
    assert "为什么错" in msg and "怎样算对" in msg


# ---------- writing 态的多目标推进 ----------


@pytest.mark.anyio
async def test_advance_from_writing_defaults_to_revising(
    tool: AgentTool, workspace: StoryWorkspace
) -> None:
    _set_stage(workspace, "writing")

    await _call(tool, {"action": "advance", "reason": "进入修订轮"})

    assert load_story_doc(workspace).stage == "revising"


@pytest.mark.anyio
async def test_advance_from_writing_explicit_complete(
    tool: AgentTool, workspace: StoryWorkspace
) -> None:
    _set_stage(workspace, "writing")

    await _call(tool, {"action": "advance", "target": "complete", "reason": "终稿定版"})

    assert load_story_doc(workspace).stage == "complete"


@pytest.mark.anyio
async def test_advance_from_revising_to_complete(
    tool: AgentTool, workspace: StoryWorkspace
) -> None:
    _set_stage(workspace, "revising")

    await _call(tool, {"action": "advance", "reason": "修订完成"})

    assert load_story_doc(workspace).stage == "complete"


@pytest.mark.anyio
async def test_rollback_complete_to_revising(tool: AgentTool, workspace: StoryWorkspace) -> None:
    _set_stage(workspace, "complete")

    await _call(tool, {"action": "rollback", "target": "revising", "reason": "作者要求返工"})

    assert load_story_doc(workspace).stage == "revising"
