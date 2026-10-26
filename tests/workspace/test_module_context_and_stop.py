"""模块作品不注入旧文件结构；停止不依赖供应商继续返回数据。"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest

from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.agent import RunContext, build_tools
from hastings.workspace.context import build_assembled_context, catalog_section, settings_section
from hastings.workspace.files import Workspace
from hastings.workspace.service import TurnService, WriteSession
from hastings.workspace.state import ConversationStore
from tau_agent.messages import AssistantMessage, TextContent
from tau_agent.provider_events import AssistantStartEvent, TextDeltaEvent


@pytest.mark.parametrize("profile", ["writer", "review"])
def test_module_only_workspace_has_no_phantom_settings_partition(
    tmp_path: Path,
    profile: Any,
) -> None:
    workspace = Workspace.create(tmp_path / "book")
    assembled = build_assembled_context(
        workspace,
        profile=profile,
        modules="人物设定｜mod_test @ rev_test｜1 个条目",
    )

    assert "modules" in {part.key for part in assembled.parts}
    assert "settings" not in {part.key for part in assembled.parts}
    assert "设定集/" not in catalog_section(workspace)
    assert "尚无角色" not in assembled.text
    assert "目录创建一人一个" not in assembled.text
    assert settings_section(workspace) == ""
    if profile == "writer":
        assert "设定集/" not in assembled.text
        assert "模块人物没有对应的文件路径" in assembled.text


def test_existing_legacy_material_is_optional_and_does_not_invent_other_files(
    tmp_path: Path,
) -> None:
    workspace = Workspace.create(tmp_path / "old-book")
    workspace.write("设定集/线索.md", "旧版钥匙线索", expected_digest=None)
    assembled = build_assembled_context(workspace)
    catalog = catalog_section(workspace)
    settings = settings_section(workspace)

    assert "旧版钥匙线索" in settings
    assert "设定集/线索.md" in catalog
    assert "设定集/真相.md" not in catalog + settings
    assert "尚未写入" not in settings
    assert "旧版文件（兼容读取）" in catalog
    assert "## 已存在的旧版文件" in assembled.text


@pytest.mark.anyio
async def test_read_state_without_legacy_identities_is_a_legal_empty_result(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path / "book")
    run = RunContext(workspace, WriteSession(workspace, instruction="写第一章"))
    tools = {tool.name: tool for tool in build_tools(workspace, run)}

    for arguments in ({}, {"entity": "设定集/角色小传/林静.md"}):
        result = await tools["read_state"].execute("s", arguments)
        view = json.loads(result.text)
        assert view["status"] == "not_recorded"
        assert view["source"] == "legacy_character_files"
        assert view["entities"] == []
        assert "不表示模块中的人物不存在" in view["note"]
    listing = json.loads((await tools["list_files"].execute("l", {})).text)
    assert listing["settings"] == []
    assert listing["characters"] == []
    assert listing["total_entries"] == 0


@pytest.mark.parametrize("partial", [False, True])
def test_stop_interrupts_silent_provider_without_waiting_for_another_event(
    tmp_path: Path,
    partial: bool,
) -> None:
    class SilentProvider:
        def __init__(self) -> None:
            self.waiting = asyncio.Event()
            self.stream_closed = False
            self.closed = False

        async def stream_response(self, **kwargs: Any) -> Any:
            try:
                if partial:
                    yield AssistantStartEvent(partial=AssistantMessage())
                    yield TextDeltaEvent(
                        content_index=0,
                        delta="已经读到的文字",
                        partial=AssistantMessage(content=[TextContent(text="已经读到的文字")]),
                    )
                self.waiting.set()
                # 模拟等响应头或流中途无数据；供应商不主动检查取消令牌。
                await asyncio.Event().wait()
            finally:
                self.stream_closed = True

        async def aclose(self) -> None:
            self.closed = True

    async def scenario() -> None:
        provider = SilentProvider()
        workspace = Workspace.create(tmp_path / "book")
        store = ConversationStore(tmp_path / "state")
        service = TurnService(
            workspace,
            store,
            lambda: ModelSnapshot(
                connection_id="c", display_name="测试", model="m", provider=provider
            ),
        )
        info = await service.accept("只讨论开篇", "stop-silent")
        await asyncio.wait_for(provider.waiting.wait(), timeout=1)
        started = time.monotonic()
        assert await service.stop(info.turn_id)
        assert await service.stop(info.turn_id)  # 重复停止不重复写事件。
        await asyncio.wait_for(service.wait(info.turn_id), timeout=1)
        assert time.monotonic() - started < 1
        events = store.events(info.conversation_id)
        assert events[-1]["status"] == "stopped"
        assert sum(event["type"] == "stop_requested" for event in events) == 1
        finished = [event for event in events if event["type"] == "model_request_finished"]
        assert len(finished) == 1
        assert finished[0]["status"] == "stopped"
        assert finished[0]["stop_reason"] == "aborted"
        assert not [event for event in events if event["type"] == "error"]
        assert provider.closed and provider.stream_closed
        assert service.active_turn() is None
        if partial:
            blocks = [event for event in events if event["type"] == "assistant_block_finished"]
            assert blocks[-1]["text"] == "已经读到的文字"
            assert blocks[-1]["completion"] == "stopped"

    asyncio.run(scenario())
