"""事件持久化测试（P6 T4，计划 D4）：events.jsonl 写/读/分页/分类/泄压阀。

events.jsonl 是可观测性账本（工程/作者视角），与 session.jsonl（会话记忆）
职责分离——本模块只验证前者：seq 单调续号、倒序分页游标、分类过滤、
坏盘告警不断流、坏行读路径宽容。
"""

import json
from pathlib import Path
from typing import Any

import pytest

from hastings.bible.store import StoryWorkspace
from hastings.events_store import (
    EventStore,
    categorize,
    events_file,
)
from tau_agent.events import ToolExecutionEndEvent
from tau_agent.tools import AgentToolResult


@pytest.mark.anyio
async def test_append_assigns_monotonic_seq(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.jsonl")
    await store.append("agent_start", {})
    await store.append("agent_end", {"ok": True})

    page = await store.read()
    assert [record.seq for record in page.records] == [2, 1]  # 倒序：新在前
    assert page.records[0].type == "agent_end"
    assert page.records[0].data == {"ok": True}


@pytest.mark.anyio
async def test_seq_continues_across_restart(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    first = EventStore(path)
    await first.append("agent_start", {})
    await first.append("agent_end", {})

    second = EventStore(path)  # 模拟服务重启：seq 从末行续起
    await second.append("notice", {"kind": "sync_end"})

    assert [record.seq for record in (await second.read()).records] == [3, 2, 1]


@pytest.mark.anyio
async def test_read_missing_file_is_empty(tmp_path: Path) -> None:
    page = await EventStore(tmp_path / "none.jsonl").read()
    assert page.records == ()
    assert page.has_more is False


@pytest.mark.anyio
async def test_pagination_with_before_seq(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.jsonl")
    for index in range(5):
        await store.append("agent_start", {"i": index})

    first = await store.read(limit=2)
    assert [record.seq for record in first.records] == [5, 4]
    assert first.has_more is True

    older = await store.read(limit=2, before_seq=4)
    assert [record.seq for record in older.records] == [3, 2]
    assert older.has_more is True

    last = await store.read(limit=2, before_seq=2)
    assert [record.seq for record in last.records] == [1]
    assert last.has_more is False


@pytest.mark.anyio
async def test_category_filter(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.jsonl")
    await store.append("message_start", {})
    await store.append("tool_execution_start", {"toolName": "x"})
    await store.append("tool_execution_end", {"isError": True})
    await store.append("agent_end", {})

    async def types_for(category: str) -> list[str]:
        return [record.type for record in (await store.read(category=category)).records]

    assert await types_for("message") == ["message_start"]
    assert await types_for("tool") == ["tool_execution_start"]  # 失败的 end 归错误，不算工具
    assert await types_for("error") == ["tool_execution_end"]


def test_categorize_rules() -> None:
    assert categorize("message_end", {}) == "message"
    assert categorize("tool_execution_start", {}) == "tool"
    assert categorize("tool_execution_end", {"isError": True}) == "error"
    assert categorize("tool_execution_end", {"isError": False}) == "tool"
    assert categorize("agent_end", {}) == "other"
    assert categorize("notice", {"kind": "sync_end"}) == "other"


@pytest.mark.anyio
async def test_append_event_uses_wire_alias(tmp_path: Path) -> None:
    """帧原文与 WS 同形（by_alias camcelCase）——前端一套类型消费两种来源。"""
    store = EventStore(tmp_path / "events.jsonl")
    event = ToolExecutionEndEvent(
        tool_call_id="c1",
        tool_name="bible_upsert",
        result=AgentToolResult(content=[]),
        is_error=False,
    )
    await store.append_event(event)

    record = (await store.read()).records[0]
    assert record.type == "tool_execution_end"
    assert record.data["toolCallId"] == "c1"
    assert record.data["isError"] is False


@pytest.mark.anyio
async def test_write_oserror_warns_and_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    """坏盘泄压阀：OSError → stderr 三要素告警，调用方不抛（事件流不中断）。"""
    store = EventStore(tmp_path / "events.jsonl")

    def boom(record: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(store, "_write_line", boom)

    await store.append("agent_start", {})  # 不抛异常

    err = capsys.readouterr().err
    assert "事件流落盘失败" in err
    assert "disk full" in err
    assert "怎样算对" in err


@pytest.mark.anyio
async def test_read_skips_corrupt_lines(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    store = EventStore(path)
    await store.append("agent_start", {})
    path.write_text(path.read_text(encoding="utf-8") + "not-json\n", encoding="utf-8")

    page = await store.read()
    assert [record.type for record in page.records] == ["agent_start"]


def test_events_file_path(tmp_path: Path) -> None:
    workspace = StoryWorkspace(root=tmp_path, slug="story-a")
    assert events_file(workspace) == tmp_path / "story-a" / "events.jsonl"


@pytest.mark.anyio
async def test_written_line_is_single_json_object(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    await EventStore(path).append("agent_start", {"x": 1})
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    raw = json.loads(lines[0])
    assert raw["seq"] == 1
    assert raw["type"] == "agent_start"
    assert raw["data"] == {"x": 1}
    assert isinstance(raw["ts"], str)
