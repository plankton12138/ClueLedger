"""CLI 装配测试（P8 T5 补齐）：build_harness 与 Web 端同一状态注入行为。

build_harness 是 CLI 冒烟入口的装配点——它必须与 HastingsSession.from_workspace
对齐：system prompt 都带「当前工作区状态」节，否则 CLI 走查观察到的模型行为
（协议遵循、阶段感知）与 Web 端不一致，冒烟结论不可信。
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from hastings.bible.store import StoryWorkspace, save_story_doc
from hastings.bible.story_state import StoryDoc
from hastings.cli import build_harness
from hastings.events_store import EventStore, events_file
from hastings.prompts import STATUS_HEADING
from tau_agent import AssistantMessage
from tau_agent.provider_events import (
    AssistantDoneEvent,
    AssistantStartEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
)
from tau_ai import FakeProvider


def test_build_harness_injects_workspace_status(tmp_path: Path) -> None:
    """CLI 装配的 system prompt 与 Web 端对齐：写作中工作区注入状态节。"""
    workspace = StoryWorkspace(root=tmp_path, slug="demo")
    save_story_doc(workspace, StoryDoc(slug="demo", stage="writing"))

    harness = build_harness(workspace, FakeProvider([]), "fake")

    assert STATUS_HEADING in harness.config.system
    assert "阶段：writing" in harness.config.system


# --- 事件持久化接线（F4 走查修复）：CLI/驱动链路也挂 events.jsonl ---


def _text_stream(text: str) -> Sequence[Any]:
    """纯文本 assistant 流（含一个 delta，用于验证 message_update 不入库）。"""
    return [
        AssistantStartEvent(partial=AssistantMessage(model="fake")),
        TextStartEvent(content_index=0, partial=AssistantMessage(model="fake")),
        TextDeltaEvent(
            content_index=0,
            delta=text,
            partial=AssistantMessage(content=text, model="fake"),
        ),
        TextEndEvent(content_index=0, content=text, partial=AssistantMessage(model="fake")),
        AssistantDoneEvent(reason="stop", message=AssistantMessage(content=text)),
    ]


@pytest.mark.anyio
async def test_build_harness_persists_events_jsonl(tmp_path: Path) -> None:
    """装配点挂 EventStore：CLI/驱动跑一轮后 events.jsonl 有全帧记录。

    走查发现（F4）：事件账本只挂在 Web 端 HastingsSession，CLI 冒烟与
    eval 驱动没有帧级账本，归因只能靠转录文本。装配点在 build_harness——
    run_repl 与 eval driver 都经它装配，一处接线两端生效。
    """
    workspace = StoryWorkspace(root=tmp_path, slug="demo")
    harness = build_harness(workspace, FakeProvider([_text_stream("收到。")]), "fake")

    async for _ in harness.prompt("你好"):
        pass

    page = await EventStore(events_file(workspace)).read()
    assert page.total > 0
    types = {record.type for record in page.records}
    assert "agent_start" in types
    assert "message_end" in types
    assert "agent_end" in types
    assert "message_update" not in types  # 流式中间态不入库（与 Web 端同一策略）
