"""构思创作路由：讨论只读、结构任务隐藏模块写入、确认候选、收据判定。"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hastings.library import ModuleLibrary
from hastings.server.file_app import create_workspace_app
from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.materials import ContextRequest
from hastings.workspace.service import TurnService
from hastings.workspace.state import ConversationStore, RecentWorkspaces, conversation_root
from hastings.workspace.story_context import StoryRequest
from hastings.workspace.story_models import StoryChangeSet
from hastings.workspace.story_store import EMPTY_REVISION, MAIN_VARIANT_ID, StoryStore
from tau_agent.messages import AssistantMessage, TextContent, ToolCall
from tau_agent.provider_events import AssistantDoneEvent, AssistantStartEvent


class RoutingProvider:
    """按脚本回复；记录模型实际收到的系统提示与工具清单。"""

    def __init__(self, turns: list[list[Any]], *, hang_at: set[int] | None = None) -> None:
        self.turns = turns
        self.hang_at = hang_at or set()
        self.calls = 0
        self.requests: list[dict[str, Any]] = []

    def stream_response(
        self, *, system: str = "", messages: list[Any] | None = None, **rest: Any
    ) -> Any:
        index = self.calls
        self.calls += 1
        self.requests.append(
            {
                "system": system,
                "messages": list(messages or []),
                "tools": [tool.name for tool in rest.get("tools") or []],
            }
        )
        signal = rest.get("signal")

        async def generator() -> Any:
            yield AssistantStartEvent(partial=AssistantMessage())
            if index < len(self.turns):
                for event in self.turns[index]:
                    yield event
            if index in self.hang_at:
                while signal is None or not signal.is_cancelled():
                    await asyncio.sleep(0.02)

        return generator()


def _tool_turn(*calls: ToolCall) -> list[Any]:
    return [
        AssistantDoneEvent(
            reason="toolUse",
            message=AssistantMessage(content=list(calls), stop_reason="toolUse"),
        )
    ]


def _text_turn(text: str) -> list[Any]:
    return [
        AssistantDoneEvent(
            reason="stop",
            message=AssistantMessage(content=[TextContent(text=text)], stop_reason="stop"),
        )
    ]


def _service(
    tmp_path: Path,
    provider: RoutingProvider,
    *,
    library: ModuleLibrary | None = None,
) -> tuple[TurnService, Workspace]:
    workspace = Workspace.create(tmp_path / "book")
    store = ConversationStore(conversation_root(tmp_path / "state", "w1"))
    service = TurnService(
        workspace,
        store,
        lambda: ModelSnapshot(
            connection_id="test", display_name="测试连接", model="fake", provider=provider
        ),
        library=library,
        workspace_id="w1",
    )
    return service, workspace


def _seed_node(state_root: Path, *, node_id: str = "n1", title: str = "抵达") -> StoryStore:
    store = StoryStore(state_root)
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        request_key=f"seed-{node_id}",
        change=StoryChangeSet(
            id=f"seed-{node_id}",
            base_revision=EMPTY_REVISION,
            operations=[
                {"op": "add_node", "node": {"id": node_id, "title": title, "action": "走进门厅"}}
            ],
        ),
    )
    return store


def _story_context() -> ContextRequest:
    return ContextRequest(story=StoryRequest(variant_id=MAIN_VARIANT_ID, purpose="design"))


def test_discussion_offers_read_only_and_records_intent(tmp_path: Path) -> None:
    provider = RoutingProvider([_text_turn("这条线索可以指向更早的回忆。")])
    service, _ = _service(tmp_path, provider)

    async def scenario() -> dict[str, Any]:
        info = await service.accept("解释一下这条线索是什么意思", "k1", context=_story_context())
        await service.wait(info.turn_id)
        events = list(service.store.events(info.conversation_id))
        return {"info": info, "events": events}

    result = asyncio.run(scenario())
    info = result["info"]
    events = result["events"]
    tools = provider.requests[0]["tools"]
    assert "read_story" in tools
    assert "update_story" not in tools
    assert "prepare_module_changes" not in tools
    intent_event = next(event for event in events if event["type"] == "task_intent")
    assert intent_event["kind"] == "discussion"
    assert events[-1]["type"] == "run_finished"
    assert events[-1]["status"] == "completed"
    operation = service.operations.read(info.operation_id)
    assert operation.intent["kind"] == "discussion"


def test_structure_task_hides_module_writes_and_sends_empty_plan(tmp_path: Path) -> None:
    provider = RoutingProvider([_text_turn("我先把线索和节点整理出来。")])
    service, _ = _service(tmp_path, provider)

    async def scenario() -> tuple[Any, list[dict[str, Any]]]:
        info = await service.accept("构建线索和调查推进", "k2", context=_story_context())
        await service.wait(info.turn_id)
        return info, list(service.store.events(info.conversation_id))

    info, events = asyncio.run(scenario())
    request = provider.requests[0]
    assert "update_story" in request["tools"]
    assert "prepare_module_changes" not in request["tools"]
    assert "apply_module_changes" not in request["tools"]
    assert "当前方案为空" in request["system"]
    assert "不要保存为模块" in request["system"]
    story_event = next(event for event in events if event["type"] == "story_context")
    assert story_event["revision"] == EMPTY_REVISION
    assert story_event["intent"]["kind"] == "structure"
    # 只有文字、没有结构提交：不能报整体完成。
    finished = next(event for event in events if event["type"] == "run_finished")
    assert finished["status"] == "partial"
    assert "构思结构没有提交" in finished["note"]
    operation = service.operations.read(info.operation_id)
    assert operation.status == "needs_attention"
    assert operation.intent["structure"] is True


def test_creation_action_commits_structure_and_never_opens_module_writes(tmp_path: Path) -> None:
    provider = RoutingProvider(
        [
            _tool_turn(ToolCall(id="read", name="read_story", arguments={})),
            _tool_turn(
                ToolCall(
                    id="create",
                    name="update_story",
                    arguments={
                        "operations": [
                            {"op": "create_clue_identity", "temp_id": "glove", "title": "湿手套"},
                            {"op": "upsert_clue_plan", "clue_id": "glove", "plan": {}},
                            {
                                "op": "add_node",
                                "node": {
                                    "id": "find",
                                    "title": "发现",
                                    "action": "侦探发现湿手套",
                                    "clue_uses": [
                                        {
                                            "clue_id": "glove",
                                            "presentation": "手套还在滴水",
                                            "mode": "narration",
                                        }
                                    ],
                                },
                            },
                            {
                                "op": "add_node",
                                "node": {
                                    "id": "question",
                                    "title": "对质",
                                    "action": "侦探询问管家",
                                },
                            },
                            {
                                "op": "connect",
                                "edge": {
                                    "id": "next",
                                    "from_id": "find",
                                    "to_id": "question",
                                    "kind": "next",
                                },
                            },
                        ]
                    },
                )
            ),
            _text_turn("节点与线索已经保存。"),
        ]
    )
    service, _ = _service(tmp_path, provider, library=ModuleLibrary(tmp_path / "library"))

    async def scenario():
        info = await service.accept(
            "创建构思：根据人物设定模块和线索档案，创建发现与对质两个节点和湿手套线索。",
            "creation-action",
            context=_story_context(),
            story_action="create",
        )
        await service.wait(info.turn_id)
        return info

    info = asyncio.run(scenario())
    assert all("prepare_module_changes" not in request["tools"] for request in provider.requests)
    assert "update_story" in provider.requests[0]["tools"]
    operation = service.operations.read(info.operation_id)
    assert operation.intent["kind"] == "structure"
    assert operation.intent["action"] == "create"
    assert any(receipt["kind"] == "story" for receipt in operation.committed_receipts)
    assert not any(receipt["kind"] == "module" for receipt in operation.committed_receipts)
    variant = StoryStore(service.store.root).require_variant(MAIN_VARIANT_ID)
    assert set(variant.nodes) == {"find", "question"}
    assert len(variant.clue_plans) == 1
    assert variant.nodes["find"].clue_uses[0].clue_id in variant.clue_plans
    assert "next" in variant.edges
    assert service.store.events(info.conversation_id)[-1]["status"] == "completed"


def test_creation_action_without_target_is_rejected_before_request(tmp_path: Path) -> None:
    provider = RoutingProvider([_text_turn("未调用")])
    service, _ = _service(tmp_path, provider)
    with pytest.raises(WorkspaceError) as error:
        asyncio.run(service.accept("创建构思", "missing-target", story_action="create"))
    assert error.value.code == "STORY_SCOPE_INVALID"
    assert not provider.requests


def test_confirmation_binds_last_candidate_and_keeps_module_writes_closed(
    tmp_path: Path,
) -> None:
    provider = RoutingProvider(
        [
            _text_turn("建议新增线索「没有响的木板」，再连接核对路线。"),
            _tool_turn(
                ToolCall(
                    id="call_write_structure",
                    name="update_story",
                    arguments={
                        "operations": [
                            {
                                "op": "create_clue_identity",
                                "temp_id": "board",
                                "title": "没有响的木板",
                            },
                            {
                                "op": "upsert_clue_plan",
                                "clue_id": "board",
                                "plan": {},
                            },
                            {
                                "op": "add_node",
                                "node": {
                                    "id": "observe",
                                    "title": "发现异样",
                                    "action": "木板没有响",
                                },
                            },
                            {
                                "op": "add_node",
                                "node": {
                                    "id": "check",
                                    "title": "核对路线",
                                    "action": "比较不同人的路线",
                                },
                            },
                            {
                                "op": "connect",
                                "edge": {
                                    "id": "edge-observe",
                                    "from_id": "observe",
                                    "to_id": "check",
                                    "kind": "next",
                                },
                            },
                        ]
                    },
                )
            ),
            _text_turn("我按这份候选写入构思。"),
        ]
    )
    service, _ = _service(tmp_path, provider)

    async def scenario() -> tuple[Any, list[dict[str, Any]]]:
        first = await service.accept("先讨论一下线索和调查", "k3", context=_story_context())
        await service.wait(first.turn_id)
        second = await service.accept(
            "写入构思并构建线索",
            "k4",
            conversation_id=first.conversation_id,
            context=_story_context(),
        )
        await service.wait(second.turn_id)
        return second, list(service.store.events(second.conversation_id))

    info, events = asyncio.run(scenario())
    intent_event = next(
        event
        for event in events
        if event["type"] == "task_intent" and event["turn_id"] == info.turn_id
    )
    candidate = intent_event["candidate"]
    assert candidate is not None
    assert candidate["seq"] >= 1
    assert "没有响的木板" in candidate["text"]
    request = provider.requests[1]
    assert "没有响的木板" in request["system"]
    assert "只以本轮提供的候选为来源" in request["system"]
    assert "update_story" in request["tools"]
    assert "prepare_module_changes" not in request["tools"]
    finished = next(
        event
        for event in events
        if event["type"] == "run_finished" and event["turn_id"] == info.turn_id
    )
    assert finished["status"] == "completed"
    variant = StoryStore(conversation_root(tmp_path / "state", "w1")).require_variant(
        MAIN_VARIANT_ID
    )
    assert len(variant.clue_plans) == 1
    assert "observe" in variant.nodes
    assert "check" in variant.nodes
    assert "edge-observe" in variant.edges


def test_confirmation_without_candidate_asks_and_does_not_write(tmp_path: Path) -> None:
    provider = RoutingProvider([_text_turn("你想写入哪一份？")])
    service, _ = _service(tmp_path, provider)

    async def scenario() -> tuple[Any, list[dict[str, Any]]]:
        info = await service.accept("就用这个", "k5", context=_story_context())
        await service.wait(info.turn_id)
        return info, list(service.store.events(info.conversation_id))

    info, events = asyncio.run(scenario())
    intent_event = next(event for event in events if event["type"] == "task_intent")
    assert intent_event["kind"] == "clarify"
    request = provider.requests[0]
    assert "update_story" not in request["tools"]
    assert "没有可定位的上一轮候选" in request["system"]
    finished = next(event for event in events if event["type"] == "run_finished")
    assert finished["status"] == "partial"
    assert "指定" in finished["note"]


def test_module_request_keeps_module_tools_and_hides_structure_write(
    tmp_path: Path,
) -> None:
    library = ModuleLibrary(tmp_path / "library", cache_file=tmp_path / "cache" / "catalog.json")
    provider = RoutingProvider([_text_turn("我把这段内容保存为资料。")])
    service, _ = _service(tmp_path, provider, library=library)

    async def scenario() -> tuple[Any, list[dict[str, Any]]]:
        info = await service.accept(
            "把这段讨论保存为资料模块", "k-module", context=_story_context()
        )
        await service.wait(info.turn_id)
        return info, list(service.store.events(info.conversation_id))

    info, events = asyncio.run(scenario())
    request = provider.requests[0]
    assert "prepare_module_changes" in request["tools"]
    assert "apply_module_changes" in request["tools"]
    assert "update_story" not in request["tools"]
    intent_event = next(
        event
        for event in events
        if event["type"] == "task_intent" and event["turn_id"] == info.turn_id
    )
    assert intent_event["kind"] == "module"
    finished = next(
        event
        for event in events
        if event["type"] == "run_finished" and event["turn_id"] == info.turn_id
    )
    # 只有文字、没有实际保存模块：仍然不能报全部完成。
    assert finished["status"] == "partial"
    assert "资料模块没有保存" in finished["note"]


def test_structure_commit_completes_and_records_receipt(tmp_path: Path) -> None:
    provider = RoutingProvider(
        [
            _tool_turn(
                ToolCall(
                    id="call_read",
                    name="read_story",
                    arguments={},
                ),
                ToolCall(
                    id="call_update",
                    name="update_story",
                    arguments={
                        "operations": [
                            {
                                "op": "create_clue_identity",
                                "temp_id": "board",
                                "title": "没有响的木板",
                            },
                            {
                                "op": "upsert_clue_plan",
                                "clue_id": "board",
                                "plan": {},
                            },
                            {
                                "op": "add_node",
                                "node": {
                                    "id": "observe",
                                    "title": "发现异样",
                                    "action": "木板没有响",
                                    "clue_uses": [
                                        {
                                            "clue_id": "board",
                                            "presentation": "有人经过时木板没有响",
                                            "mode": "action",
                                        }
                                    ],
                                },
                            },
                        ]
                    },
                ),
            ),
            _text_turn("线索与节点已经写入构思。"),
        ]
    )
    service, _ = _service(tmp_path, provider)

    async def scenario() -> tuple[Any, list[dict[str, Any]]]:
        info = await service.accept("构建线索和调查推进", "k6", context=_story_context())
        await service.wait(info.turn_id)
        return info, list(service.store.events(info.conversation_id))

    info, events = asyncio.run(scenario())
    finished = next(event for event in events if event["type"] == "run_finished")
    assert finished["status"] == "completed"
    committed = next(event for event in events if event["type"] == "story_changes_committed")
    assert any(key.startswith("clue_plan:") for key in committed["changed"])
    assert "node:observe" in committed["changed"]
    assert any("没有响的木板" in label for label in committed["labels"])
    operation = service.operations.read(info.operation_id)
    story_receipt = next(item for item in operation.committed_receipts if item["kind"] == "story")
    assert story_receipt["change_id"] == committed["change_id"]
    assert story_receipt["labels"] == committed["labels"]
    variant = StoryStore(conversation_root(tmp_path / "state", "w1")).require_variant(
        MAIN_VARIANT_ID
    )
    assert len(variant.clue_plans) == 1
    assert "observe" in variant.nodes


def test_continue_after_own_commit_uses_the_new_revision(tmp_path: Path) -> None:
    provider = RoutingProvider(
        [
            _tool_turn(
                ToolCall(
                    id="call_update",
                    name="update_story",
                    arguments={
                        "operations": [
                            {
                                "op": "add_node",
                                "node": {"id": "n1", "title": "抵达", "action": "走进门厅"},
                            }
                        ]
                    },
                )
            ),
            _text_turn("写完了结构。"),
            _text_turn("继续完成这一轮。"),
        ],
        hang_at={1},
    )
    service, _ = _service(tmp_path, provider)

    async def scenario() -> Any:
        info = await service.accept("构建一个抵达节点", "k9", context=_story_context())
        for _ in range(200):
            events = list(service.store.events(info.conversation_id))
            if any(event["type"] == "story_changes_committed" for event in events):
                break
            await asyncio.sleep(0.02)
        await service.pause_operation(info.operation_id, "pause-routing")
        await service.wait(info.turn_id)
        paused = service.operations.read(info.operation_id)
        assert paused.status == "paused"
        resumed = await service.continue_operation(
            info.operation_id,
            "continue-routing",
            expected_generation=paused.generation,
        )
        for _ in range(300):
            current = service.operations.read(info.operation_id)
            if current.status not in ("accepted", "running"):
                break
            await asyncio.sleep(0.02)
        return info, resumed

    info, resumed = asyncio.run(scenario())
    operation = service.operations.read(info.operation_id)
    assert operation.status == "completed"
    events = list(service.store.events(info.conversation_id))
    errors = [
        event
        for event in events
        if event["type"] == "error" and event.get("code") == "STORY_REVISION_CONFLICT"
    ]
    assert errors == []
    finished = [
        event
        for event in events
        if event["type"] == "run_finished" and event["turn_id"] == info.turn_id
    ]
    assert finished and finished[-1]["status"] == "completed"
    assert resumed.generation > 1
    variant = StoryStore(conversation_root(tmp_path / "state", "w1")).require_variant(
        MAIN_VARIANT_ID
    )
    assert "n1" in variant.nodes


def test_no_change_requires_a_real_read(tmp_path: Path) -> None:
    state_root = conversation_root(tmp_path / "state", "w1")
    store = _seed_node(state_root)
    provider = RoutingProvider(
        [
            _tool_turn(
                ToolCall(
                    id="call_no_change",
                    name="update_story",
                    arguments={
                        "no_change": {
                            "objects": [{"kind": "node", "id": "n1"}],
                            "reason": "作者要求增加细节，但现有安排已经包含该细节。",
                        }
                    },
                )
            ),
            _text_turn("我先读一下当前节点。"),
            _tool_turn(
                ToolCall(
                    id="call_read",
                    name="read_story",
                    arguments={"node_ids": ["n1"]},
                ),
                ToolCall(
                    id="call_no_change_2",
                    name="update_story",
                    arguments={
                        "no_change": {
                            "objects": [{"kind": "node", "id": "n1"}],
                            "reason": "作者要求增加细节，但现有安排已经包含该细节。",
                        }
                    },
                ),
            ),
            _text_turn("我核对过当前节点，无需改动。"),
        ]
    )
    service, _ = _service(tmp_path, provider)
    before = store.require_variant(MAIN_VARIANT_ID).revision

    async def scenario() -> tuple[Any, Any]:
        first = await service.accept("把「抵达」加一个细节", "k7", context=_story_context())
        await service.wait(first.turn_id)
        second = await service.accept(
            "把「抵达」的细节补上",
            "k8",
            conversation_id=first.conversation_id,
            context=_story_context(),
        )
        await service.wait(second.turn_id)
        return first, second

    first_info, second_info = asyncio.run(scenario())
    first_events = list(service.store.events(first_info.conversation_id))
    first_finished = next(
        event
        for event in first_events
        if event["type"] == "run_finished" and event["turn_id"] == first_info.turn_id
    )
    assert first_finished["status"] == "partial"
    first_tools = [
        event
        for event in first_events
        if event["type"] == "tool_finished" and event["turn_id"] == first_info.turn_id
    ]
    assert first_tools and first_tools[0]["ok"] is False
    # no_change 没有实际读取：不能作为完成依据，也没有写出 no-op 收据。
    assert not any(
        event["type"] == "story_change_noop" and event["turn_id"] == first_info.turn_id
        for event in first_events
    )

    second_events = list(service.store.events(second_info.conversation_id))
    second_finished = next(
        event
        for event in second_events
        if event["type"] == "run_finished" and event["turn_id"] == second_info.turn_id
    )
    assert second_finished["status"] == "completed"
    noop = next(
        event
        for event in second_events
        if event["type"] == "story_change_noop" and event["turn_id"] == second_info.turn_id
    )
    assert noop["objects"][0]["id"] == "n1"
    assert service.operations.read(second_info.operation_id).status == "completed"
    # 无需改动不产生新修订，也没有把模型自述当成结构提交。
    assert store.require_variant(MAIN_VARIANT_ID).revision == before


def test_result_endpoint_returns_intent_and_story_receipt(tmp_path: Path) -> None:
    provider = RoutingProvider(
        [
            _tool_turn(
                ToolCall(
                    id="call_api_update",
                    name="update_story",
                    arguments={
                        "operations": [
                            {
                                "op": "add_node",
                                "node": {"id": "n1", "title": "抵达", "action": "走进门厅"},
                            }
                        ]
                    },
                )
            ),
            _text_turn("结构已经写入。"),
        ]
    )
    book = RecentWorkspaces(tmp_path / "api-state")
    app = create_workspace_app(
        workspaces=book,
        provider_factory=lambda: ModelSnapshot(
            connection_id="test", display_name="测试连接", model="fake", provider=provider
        ),
    )
    with TestClient(app) as client:
        workspace_id = client.post(
            "/api/file-workspaces",
            json={"path": str(tmp_path / "api-book"), "create": True},
        ).json()["id"]
        accepted = client.post(
            f"/api/file-workspaces/{workspace_id}/conversations",
            json={
                "text": "构建一个抵达节点",
                "request_key": "api-routing-1",
                "context": {"story": {"variant_id": "main", "purpose": "design"}},
            },
        )
        assert accepted.status_code == 202, accepted.text
        turn = accepted.json()
        deadline = time.monotonic() + 10
        events: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            events = client.get(
                f"/api/file-workspaces/{workspace_id}/conversations/"
                f"{turn['conversation_id']}/messages"
            ).json()["events"]
            if any(event["type"] == "run_finished" for event in events):
                break
            time.sleep(0.02)
        finished = next(event for event in events if event["type"] == "run_finished")
        assert finished["status"] == "completed"

        result = client.get(f"/api/operations/{turn['operation_id']}/result").json()
        assert result["status"] == "completed"
        assert result["intent"]["kind"] == "structure"
        story = next(item for item in result["receipts"] if item["kind"] == "story")
        assert story["variant_id"] == MAIN_VARIANT_ID
        assert story["labels"] == ["节点「抵达」"]


def test_plain_chat_resolves_story_and_stops_with_recoverable_draft(tmp_path: Path) -> None:
    provider = RoutingProvider(
        [
            _tool_turn(
                ToolCall(
                    id="prepare",
                    name="update_story",
                    arguments={
                        "mode": "prepare",
                        "operations": [
                            {
                                "op": "add_node",
                                "node": {"id": "n1", "title": "发现", "action": "发现矛盾"},
                            }
                        ],
                    },
                )
            )
        ]
    )
    service, _ = _service(tmp_path, provider)
    service.max_turns = 4

    async def scenario() -> Any:
        info = await service.accept("根据现有模块生成构思", "plain-create")
        await service.wait(info.turn_id)
        return info

    info = asyncio.run(scenario())
    tools = provider.requests[0]["tools"]
    assert "update_story" in tools and "prepare_module_changes" not in tools
    assert "set_task_scope" not in tools
    operation = service.operations.read(info.operation_id)
    assert operation.status == "needs_attention"
    assert operation.as_view()["capabilities"]["continue"]
    assert service._restore_operation_info(operation).context.story.revision == "empty"
    assert StoryStore(service.store.root).read().document is None


def test_unknown_tool_feedback_has_recovery_and_separate_author_summary(tmp_path: Path) -> None:
    from tau_agent.messages import ToolResultMessage

    provider = RoutingProvider(
        [
            _tool_turn(
                ToolCall(id="bad", name="draft_revision", arguments={}),
                ToolCall(
                    id="bad-fields",
                    name="update_story",
                    arguments={
                        "mode": "prepare",
                        "operations": [{"op": "add_node", "id": "n2"}],
                    },
                ),
            ),
            _text_turn("读取草稿后继续。"),
        ]
    )
    service, _ = _service(tmp_path, provider)

    async def scenario() -> Any:
        info = await service.accept("根据现有模块生成构思", "unknown-tool")
        await service.wait(info.turn_id)
        return info

    info = asyncio.run(scenario())
    event = next(
        e for e in service.store.events(info.conversation_id) if e["type"] == "tool_finished"
    )
    assert not event["ok"] and "[TOOL_NOT_FOUND]" in event["summary"]
    assert "建议：" in event["summary"] and "update_story" in event["summary"]
    assert "execution_budget" not in event["summary"]
    message = next(m for m in provider.requests[1]["messages"] if isinstance(m, ToolResultMessage))
    payload = json.loads(message.content[0].text)
    assert message.is_error and payload["code"] == "TOOL_NOT_FOUND"
    assert payload["execution_budget"]["remaining_model_rounds"] == 23
    assert "draft_revision" not in payload["available_tools"]
    invalid = next(
        m
        for m in provider.requests[1]["messages"]
        if isinstance(m, ToolResultMessage) and m.tool_name == "update_story"
    )
    diagnostic = json.loads(invalid.content[0].text)
    assert invalid.is_error and diagnostic["code"] == "STORY_CHANGE_INVALID"
    assert diagnostic["issues"][0]["object_id"] == "n2"
    assert "execution_budget" in diagnostic
    field_event = next(
        e
        for e in service.store.events(info.conversation_id)
        if e["type"] == "tool_finished" and e["tool"] == "update_story"
    )
    assert "[STORY_CHANGE_INVALID]" in field_event["summary"]
    assert "execution_budget" not in field_event["summary"]
    assert '"issues"' not in field_event["summary"]
    assert StoryStore(service.store.root).read().document is None
