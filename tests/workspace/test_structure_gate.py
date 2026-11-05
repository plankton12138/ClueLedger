"""v1.2 结构先行：空作品写正文前必须在本轮建立 ``STRUCTURE.md``。

结构先行是 Agent 的本轮能力状态，不是 HTTP 门槛：普通正文保存、导入和候选采用都
不检查结构文件；模型侧的正文工具在结构缺失时给出可恢复的 ``STRUCTURE_NOT_READY``。
"""

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
from hastings.workspace.structure_io import write_structure_document
from tau_agent.tools import AgentTool

PATH = "正文/0001 雨夜.md"
TEXT = "# 雨夜\n\n雨仍未停。"
NEW = "# 雨夜\n\n雨已经停了。"
STRUCTURE = "## 铁律\n第三人称限制视角。\n\n## 路线\n雨夜收束。\n"


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


def test_require_structure_for_prose_is_per_turn_state(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path / "book")
    run = RunContext(workspace, WriteSession(workspace, instruction="写第一章"))

    with pytest.raises(WorkspaceError) as caught:
        run.require_structure_for_prose()
    assert caught.value.code == "STRUCTURE_NOT_READY"
    assert caught.value.where == "tools.prose"

    run.structure_ready = True
    run.require_structure_for_prose()


def test_build_tools_reads_structure_state_from_disk(tmp_path: Path) -> None:
    workspace, run = _book_run(tmp_path)
    build_tools(workspace, run)
    assert run.structure_ready is False

    write_structure_document(workspace, text=STRUCTURE, expected_digest=None, creating=True)
    build_tools(workspace, run)
    assert run.structure_ready is True


@pytest.mark.anyio
async def test_candidate_tool_requires_structure_and_recovers_in_same_turn(
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
    # 结构缺失时仍要能同轮补结构，否则正文门槛无法恢复。
    assert "write_structure" in tools

    digest = workspace.read(PATH).digest
    with pytest.raises(WorkspaceError) as caught:
        await _call(tools["propose_story_scene"], path=PATH, base_digest=digest, text=NEW)
    assert caught.value.code == "STRUCTURE_NOT_READY"
    assert workspace.read(PATH).text == TEXT

    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="建立第一版全书结构并继续第一章",
        authorization="写第一章",
        grants=[
            {"action": "create", "path": STRUCTURE_PATH},
            {"action": "update", "path": PATH},
        ],
    )
    await _call(tools["read_file"], path=PATH)
    await _call(tools["write_structure"], text=STRUCTURE)
    assert run.structure_ready is True

    proposed = await _call(tools["propose_story_scene"], path=PATH, base_digest=digest, text=NEW)
    assert proposed["status"] == "draft"
    assert workspace.read(PATH).text == TEXT


def test_write_purpose_without_structure_gets_same_turn_prompt(tmp_path: Path) -> None:
    _, run = _book_run(tmp_path)

    inputs = prepare_request_inputs(workspace=run.workspace, run=run)

    assert "还没有 STRUCTURE.md" in inputs.system
    assert "write_structure" in inputs.system
    assert "同一轮" in inputs.system


def test_existing_structure_keeps_prose_tools_without_gate_prompt(tmp_path: Path) -> None:
    workspace, run = _book_run(tmp_path)
    write_structure_document(workspace, text=STRUCTURE, expected_digest=None, creating=True)

    inputs = prepare_request_inputs(workspace=workspace, run=run)

    assert "还没有 STRUCTURE.md" not in inputs.system
    assert run.structure_ready is True


class _GateProvider:
    """按顺序返回脚本化回合；用来复现「先提正文失败、同轮补结构」的真实链路。"""

    def __init__(self, digest: str) -> None:
        self.digest = digest
        self.calls = 0
        self.tool_lists: list[set[str]] = []

    def stream_response(self, **kwargs: Any) -> Any:
        index = self.calls
        self.calls += 1
        self.tool_lists.append({tool.name for tool in kwargs.get("tools", [])})
        calls: dict[int, tuple[str, dict[str, Any]]] = {
            0: (
                "set_task_scope",
                {
                    "mode": "edit",
                    "summary": "建立结构并写第一章",
                    "authorization": "写第一章",
                    "grants": [
                        {"action": "create", "path": STRUCTURE_PATH},
                        {"action": "update", "path": PATH},
                    ],
                },
            ),
            1: ("read_file", {"path": PATH}),
            2: (
                "propose_story_scene",
                {"path": PATH, "base_digest": self.digest, "text": NEW},
            ),
            3: ("write_structure", {"text": STRUCTURE}),
            4: (
                "propose_story_scene",
                {"path": PATH, "base_digest": self.digest, "text": NEW},
            ),
            6: (
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


def test_service_turn_establishes_structure_before_candidate(tmp_path: Path) -> None:
    import asyncio

    from hastings.server.provider_runtime import ModelSnapshot
    from hastings.workspace.materials import ContextRequest
    from hastings.workspace.service import TurnService
    from hastings.workspace.state import ConversationStore
    from hastings.workspace.story_context import StoryRequest
    from hastings.workspace.story_models import AddNode, StoryChangeSet, StoryNode

    workspace = Workspace.create(tmp_path / "book")
    workspace.write(PATH, TEXT, expected_digest=None)
    store = StoryStore(tmp_path / "state", workspace)
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        request_key="seed",
        change=StoryChangeSet(
            id="seed",
            base_revision="empty",
            operations=[AddNode(node=StoryNode(id="n1", title="雨夜", action="雨夜开始"))],
        ),
    )
    GitRepo(workspace.root).init()
    provider = _GateProvider(workspace.read(PATH).digest)
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
    assert [(event["tool"], event["summary"]) for event in failures] == [
        ("propose_story_scene", failures[0]["summary"])
    ]
    assert "STRUCTURE_NOT_READY" in failures[0]["summary"]
    assert "write_structure" in provider.tool_lists[2]
    assert (workspace.root / STRUCTURE_PATH).exists()
    assert workspace.read(PATH).text == NEW
    assert any(event["type"] == "story_candidate_committed" for event in events)
