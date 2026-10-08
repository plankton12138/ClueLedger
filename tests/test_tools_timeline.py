"""timeline_check 工具包装单测：空账本自洽、冲突进 details、章节范围过滤（P2 T5）。"""

from collections.abc import Mapping
from pathlib import Path

import pytest

from hastings.bible.models import AlibiClaim, Method, TimelineEntry
from hastings.bible.store import StoryWorkspace, save_ledger
from hastings.tools.errors import ToolInputError
from hastings.tools.timeline_tools import create_timeline_tools
from tau_agent import AgentTool
from tau_agent.types import JSONValue


@pytest.fixture
def workspace(tmp_path: Path) -> StoryWorkspace:
    return StoryWorkspace(root=tmp_path / "stories", slug="demo")


@pytest.fixture
def tools(workspace: StoryWorkspace) -> dict[str, AgentTool]:
    return {tool.name: tool for tool in create_timeline_tools(workspace)}


async def _call(tool: AgentTool, arguments: Mapping[str, JSONValue]) -> object:
    return await tool.execute("call-1", arguments)


def _write_timeline(workspace: StoryWorkspace, entries: list[TimelineEntry]) -> None:
    save_ledger(workspace.timeline_path, entries)


# ---------- 空账本 / 干净账本（泄压阀：合法空结果，非 error） ----------


@pytest.mark.anyio
async def test_check_empty_ledgers_reports_consistent(
    tools: dict[str, AgentTool], workspace: StoryWorkspace
) -> None:
    del workspace
    result = await _call(tools["timeline_check"], {})

    assert "自洽" in result.text
    assert isinstance(result.details, dict)
    assert result.details["conflict_count"] == 0


@pytest.mark.anyio
async def test_check_clean_ledger_zero_conflict(
    tools: dict[str, AgentTool], workspace: StoryWorkspace
) -> None:
    _write_timeline(
        workspace,
        [
            TimelineEntry(name="晚宴", day=3, t_start="18:00", t_end="20:00", participants=["甲"]),
            TimelineEntry(
                name="晨间散步", day=4, t_start="07:00", t_end="08:00", participants=["甲"]
            ),
        ],
    )

    result = await _call(tools["timeline_check"], {})

    assert "自洽" in result.text
    assert isinstance(result.details, dict)
    assert result.details["checked_entries"] == 2


# ---------- 冲突上报（文本 + 结构化 details 给 P3 前端） ----------


@pytest.mark.anyio
async def test_check_reports_doppelganger_conflict(
    tools: dict[str, AgentTool], workspace: StoryWorkspace
) -> None:
    _write_timeline(
        workspace,
        [
            TimelineEntry(name="晚宴", day=3, t_start="18:00", t_end="20:00", participants=["甲"]),
            TimelineEntry(
                name="书房密会", day=3, t_start="19:30", t_end="21:00", participants=["甲"]
            ),
        ],
    )

    result = await _call(tools["timeline_check"], {})

    assert "人物分身" in result.text
    details = result.details
    assert isinstance(details, dict)
    assert details["conflict_count"] == 1
    conflict = details["conflicts"][0]
    assert conflict["rule"] == "R1"
    assert conflict["persons"] == ["甲"]
    assert conflict["message"]


@pytest.mark.anyio
async def test_check_reports_alibi_breach(
    tools: dict[str, AgentTool], workspace: StoryWorkspace
) -> None:
    _write_timeline(
        workspace,
        [
            TimelineEntry(name="晚宴", day=3, t_start="18:00", t_end="21:00", participants=["乙"]),
        ],
    )
    save_ledger(
        workspace.method_path,
        [
            Method(
                name="密室诡计",
                alibi_claims=[AlibiClaim(person="乙", day=3, t_start="20:00", t_end="22:00")],
            )
        ],
    )

    result = await _call(tools["timeline_check"], {})

    assert "不在场破绽" in result.text
    details = result.details
    assert isinstance(details, dict)
    assert details["conflicts"][0]["rule"] == "R2"
    assert details["checked_claims"] == 1


# ---------- 章节范围参数 ----------


@pytest.mark.anyio
async def test_chapter_range_filters_entries(
    tools: dict[str, AgentTool], workspace: StoryWorkspace
) -> None:
    _write_timeline(
        workspace,
        [
            TimelineEntry(name="序章事件", day=1, participants=["甲"], chapter="ch01"),
            TimelineEntry(name="正章事件", day=3, participants=["甲"], chapter="ch03"),
        ],
    )

    result = await _call(tools["timeline_check"], {"chapter_from": "ch02"})

    assert "自洽" in result.text
    assert isinstance(result.details, dict)
    assert result.details["checked_entries"] == 1


@pytest.mark.anyio
async def test_chapter_range_rejects_bad_param(tools: dict[str, AgentTool]) -> None:
    with pytest.raises(ToolInputError, match="chapter_from"):
        await _call(tools["timeline_check"], {"chapter_from": "第三章"})
