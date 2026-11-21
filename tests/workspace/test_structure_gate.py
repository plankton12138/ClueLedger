"""全书结构可选，正文候选仍须通过授权与发布核对。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hastings.workspace.agent import RunContext, build_tools, prepare_request_inputs
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import STRUCTURE_PATH, Workspace
from hastings.workspace.git import GitRepo
from hastings.workspace.service import WriteSession
from hastings.workspace.story_candidate import StoryCandidate
from hastings.workspace.story_context import StoryContextSnapshot
from hastings.workspace.story_store import MAIN_VARIANT_ID, StoryStore
from tau_agent.tools import AgentTool

PATH = "正文/0001 雨夜.md"
TEXT = "# 雨夜\n\n雨仍未停。"
NEW = "# 雨夜\n\n雨已经停了。"


async def _call(tool: AgentTool, **arguments: Any) -> dict[str, Any]:
    result = await tool.execute("call-1", arguments)
    return json.loads(result.text)


def _book_run(tmp_path: Path) -> tuple[Workspace, RunContext]:
    workspace = Workspace.create(tmp_path / "book")
    GitRepo(workspace.root).init()
    workspace.write(PATH, TEXT, expected_digest=None)
    run = RunContext(
        workspace,
        WriteSession(workspace, instruction="写第一章"),
        author_messages=((1, "写第一章"),),
        current_author_text="写第一章",
        state_root=tmp_path / "state",
        story=StoryContextSnapshot(
            variant_id=MAIN_VARIANT_ID,
            revision="empty",
            purpose="write",
        ),
    )
    return workspace, run


@pytest.mark.anyio
async def test_candidate_tool_allows_prose_without_structure(
    tmp_path: Path,
) -> None:
    workspace, run = _book_run(tmp_path)
    store = StoryStore(tmp_path / "state", workspace)
    run.story_candidate = StoryCandidate(
        workspace=workspace,
        state_root=store.root,
        frozen=run.story,
        operation_id="op",
        generation=1,
        guard=lambda: None,
        record=lambda kind, payload: None,
        author_text="写第一章",
    )
    inputs = prepare_request_inputs(workspace=workspace, run=run)
    tools = {tool.name: tool for tool in inputs.tools}
    assert "propose_story_scene" in tools
    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="写第一章",
        authorization="写第一章",
        grants=[{"action": "update", "path": PATH}],
    )
    await _call(tools["read_file"], path=PATH)
    digest = workspace.read(PATH).digest
    proposed = await _call(tools["propose_story_scene"], path=PATH, base_digest=digest, text=NEW)
    assert proposed["status"] == "draft"
    assert workspace.read(PATH).text == TEXT
    assert not (workspace.root / STRUCTURE_PATH).exists()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("author_text", "allowed"),
    [
        ("请保存这本书的全书结构，然后写下一章", True),
        ("请写下一章，并整理全书结构的讨论要点，仅在回复里展示，不要修改全书结构", False),
        ("先不要建立全书结构，请直接写下一章", False),
        ("只在回复里展示全书结构，然后写下一章", False),
    ],
)
async def test_write_turn_structure_authorization(
    tmp_path: Path, author_text: str, allowed: bool
) -> None:
    workspace, run = _book_run(tmp_path)
    run.current_author_text = author_text
    run.author_messages = ((1, author_text),)
    assert run.story is not None
    run.story_candidate = StoryCandidate(
        workspace=workspace,
        state_root=tmp_path / "state",
        frozen=run.story,
        operation_id="op",
        generation=1,
        guard=lambda: None,
        record=lambda kind, payload: None,
        author_text=author_text,
    )
    inputs = prepare_request_inputs(workspace=workspace, run=run)
    tools = {tool.name: tool for tool in inputs.tools}
    assert "propose_story_scene" in tools
    assert ("write_structure" in tools) is allowed
    if allowed:
        await _call(
            tools["set_task_scope"],
            mode="edit",
            summary="保存全书结构并写下一章",
            authorization=author_text,
            grants=[{"action": "create", "path": STRUCTURE_PATH}],
        )
        saved = await _call(tools["write_structure"], text="## 铁律\n规则。")
        assert saved["path"] == STRUCTURE_PATH
    else:
        unfiltered = {tool.name: tool for tool in build_tools(workspace, run)}
        with pytest.raises(WorkspaceError) as exc:
            await _call(unfiltered["write_structure"], text="## 铁律\n规则。")
        assert exc.value.code == "TASK_SCOPE_UNAUTHORIZED_CONFLICT"
        assert not (workspace.root / STRUCTURE_PATH).exists()


def test_write_prompt_does_not_require_structure(tmp_path: Path) -> None:
    workspace, run = _book_run(tmp_path)
    inputs = prepare_request_inputs(workspace=workspace, run=run)
    assert "本轮必须先建立全书结构" not in inputs.system


class _ProseProvider:
    """脚本化模型事件；工具、审核及落盘使用真实应用链路。"""

    def __init__(self, digest: str) -> None:
        self.digest = digest
        self.calls = 0

    def stream_response(self, **kwargs: Any) -> Any:
        index = self.calls
        self.calls += 1
        calls: dict[int, tuple[str, dict[str, Any]]] = {
            0: (
                "set_task_scope",
                {
                    "mode": "edit",
                    "summary": "写第一章",
                    "authorization": "写第一章",
                    "grants": [{"action": "create", "path": PATH}],
                },
            ),
            1: (
                "propose_story_scene",
                {"path": PATH, "base_digest": self.digest, "text": NEW},
            ),
            3: (
                "submit_story_candidate_review",
                {
                    "checks": [
                        {
                            "id": f"scene:{PATH}",
                            "status": "pass",
                            "path": PATH,
                            "quote": "雨已经停了",
                            "reason": "候选符合作者要求。",
                        }
                    ]
                },
            ),
        }

        async def generate() -> Any:
            from tau_agent.messages import AssistantMessage, TextContent, ToolCall
            from tau_agent.provider_events import AssistantDoneEvent, AssistantStartEvent

            yield AssistantStartEvent(partial=AssistantMessage())
            if index in calls:
                name, arguments = calls[index]
                message = AssistantMessage(
                    content=[ToolCall(id=f"call{index}", name=name, arguments=arguments)],
                    stop_reason="toolUse",
                )
                yield AssistantDoneEvent(reason="toolUse", message=message)
            else:
                yield AssistantDoneEvent(
                    reason="stop",
                    message=AssistantMessage(
                        content=[TextContent(text="完成。")], stop_reason="stop"
                    ),
                )

        return generate()


def test_service_turn_saves_first_chapter_without_structure(tmp_path: Path) -> None:
    import asyncio

    from hastings.server.provider_runtime import ModelSnapshot
    from hastings.workspace.materials import ContextRequest
    from hastings.workspace.service import TurnService
    from hastings.workspace.state import ConversationStore
    from hastings.workspace.story_context import StoryRequest

    workspace = Workspace.create(tmp_path / "book")
    store = StoryStore(tmp_path / "state", workspace)
    GitRepo(workspace.root).init()
    provider = _ProseProvider("absent")
    conversations = ConversationStore(store.root)
    service = TurnService(
        workspace,
        conversations,
        lambda: ModelSnapshot(
            connection_id="test", display_name="test", model="script", provider=provider
        ),
    )

    async def run() -> Any:
        info = await service.accept(
            "写第一章",
            "request",
            context=ContextRequest(story=StoryRequest(variant_id=MAIN_VARIANT_ID, purpose="write")),
        )
        await service.wait(info.turn_id)
        return info

    info = asyncio.run(run())
    events = list(conversations.events(info.conversation_id))
    failures = [event for event in events if event["type"] == "tool_finished" and not event["ok"]]
    assert failures == []
    assert not (workspace.root / STRUCTURE_PATH).exists()
    assert workspace.read(PATH).text == NEW
    assert any(event["type"] == "story_candidate_committed" for event in events)
