"""1.0.1 P4：结构工具与代次门禁，不调用模型。

工具是模型改结构的唯一入口，所以这里重点看三件事：读到的是精确 ID、提交带上应用
自己的元数据、暂停之后旧代次拿不到写权限。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.story_context import StoryContextSnapshot
from hastings.workspace.story_models import StoryChangeSet
from hastings.workspace.story_store import MAIN_VARIANT_ID, StoryStore
from hastings.workspace.story_tools import StoryToolScope, build_story_tools


def _tool(tools: list[Any], name: str) -> Any:
    return next(tool for tool in tools if tool.name == name)


def _call(tool: Any, arguments: dict[str, Any]) -> Any:
    return asyncio.run(tool.execute_fn("call_1", arguments))


def _seed(store: StoryStore) -> None:
    change = StoryChangeSet(
        id="seed_1",
        base_revision="empty",
        operations=[
            {
                "op": "add_node",
                "node": {"id": "n1", "title": "抵达", "action": "侦探走进旅馆"},
            }
        ],
    )
    store.commit_change(variant_id=MAIN_VARIANT_ID, change=change, request_key="seed")


def _scope(tmp_path: Path, *, writable: bool = True, guard: Any = None) -> StoryToolScope:
    frozen = StoryContextSnapshot(
        variant_id=MAIN_VARIANT_ID, revision="rev_1", purpose="design", node_ids=("n1",)
    )
    return StoryToolScope(
        state_root=tmp_path / "state",
        frozen=frozen,
        writable=writable,
        guard=guard,
        operation_id="op_1",
        generation=2,
        author_text="把开篇加一个节点",
    )


@pytest.fixture
def store(tmp_path: Path) -> StoryStore:
    store = StoryStore(tmp_path / "state")
    _seed(store)
    return store


def test_read_story_returns_exact_ids(store: StoryStore, tmp_path: Path) -> None:
    tools = build_story_tools(_scope(tmp_path))

    result = _call(_tool(tools, "read_story"), {})

    payload = result.details
    assert payload["variant_id"] == MAIN_VARIANT_ID
    assert payload["nodes"][0]["id"] == "n1"
    assert payload["revision"] == store.read().variant(MAIN_VARIANT_ID).revision
    # 提示里明确要求不要拿真相当台词。
    assert "author_truth" in payload["note"]


def test_update_story_commits_with_app_metadata(store: StoryStore, tmp_path: Path) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    scope = StoryToolScope(
        state_root=tmp_path / "state",
        frozen=StoryContextSnapshot(
            variant_id=MAIN_VARIANT_ID, revision="rev", purpose="design"
        ),
        writable=True,
        operation_id="op_42",
        generation=3,
        author_text="加一个转折节点",
        record=lambda kind, payload: events.append((kind, dict(payload))),
    )

    result = _call(
        _tool(build_story_tools(scope), "update_story"),
        {
            "operations": [
                {
                    "op": "add_node",
                    "node": {"id": "n2", "title": "转折", "action": "灯灭了"},
                }
            ]
        },
    )

    assert result.details["ok"] is True
    variant = store.read().variant(MAIN_VARIANT_ID)
    assert variant is not None and "n2" in variant.nodes
    # 提交带上的是应用的操作与代次，不是模型自己写的。
    document = store.read().document
    assert document is not None
    record = document.changes[result.details["change_id"]]
    assert record.change_id.startswith("agent_")
    assert result.details["changed"]
    assert events and events[0][0] == "story_changes_committed"


def test_paused_generation_cannot_commit(store: StoryStore, tmp_path: Path) -> None:
    def revoked() -> None:
        raise WorkspaceError(
            code="OPERATION_PAUSED_CONFLICT",
            where="turn.write",
            why="这次操作已经暂停",
            fix="继续后再改",
        )

    tools = build_story_tools(_scope(tmp_path, guard=revoked))

    with pytest.raises(WorkspaceError) as excinfo:
        _call(
            _tool(tools, "update_story"),
            {
                "operations": [
                    {
                        "op": "add_node",
                        "node": {"id": "n9", "title": "不该出现", "action": "不会写入"},
                    }
                ]
            },
        )

    assert excinfo.value.code == "OPERATION_PAUSED_CONFLICT"
    variant = store.read().variant(MAIN_VARIANT_ID)
    assert variant is not None and "n9" not in variant.nodes


def test_read_only_scope_has_no_update_tool(tmp_path: Path) -> None:
    tools = build_story_tools(_scope(tmp_path, writable=False))

    assert [tool.name for tool in tools] == ["read_story"]


def test_invalid_operations_are_reported_without_writing(
    store: StoryStore, tmp_path: Path
) -> None:
    tools = build_story_tools(_scope(tmp_path))

    with pytest.raises(WorkspaceError) as excinfo:
        _call(
            _tool(tools, "update_story"),
            {"operations": [{"op": "add_node", "node": {"id": "n3"}}]},
        )

    assert excinfo.value.code == "STORY_CHANGE_INVALID"
    variant = store.read().variant(MAIN_VARIANT_ID)
    assert variant is not None and "n3" not in variant.nodes


def test_commit_checks_eligibility_but_not_on_retry(store: StoryStore) -> None:
    variant = store.read().variant(MAIN_VARIANT_ID)
    assert variant is not None
    change = StoryChangeSet(
        id="agent_retry",
        base_revision=variant.revision,
        operations=[
            {
                "op": "add_node",
                "node": {"id": "n4", "title": "新节点", "action": "发生了一件事"},
            }
        ],
        operation_id="op_1",
        generation=2,
    )

    def revoked(operation_id: str, generation: int) -> None:
        raise WorkspaceError(
            code="OPERATION_PAUSED_CONFLICT",
            where="story.commit",
            why="暂停",
            fix="继续",
        )

    with pytest.raises(WorkspaceError):
        store.commit_change(
            variant_id=MAIN_VARIANT_ID,
            change=change,
            request_key="retry-key",
            require_eligible=revoked,
        )
    assert store.read().variant(MAIN_VARIANT_ID).nodes.get("n4") is None

    receipt = store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        change=change,
        request_key="retry-key",
        require_eligible=lambda operation_id, generation: None,
    )

    # 已提交过的同一请求再查一次资格也不该报错：收据比资格更早成立。
    again = store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        change=change,
        request_key="retry-key",
        require_eligible=revoked,
    )
    assert again.revision == receipt.revision


# -- 通过一轮真实工具调用改结构 ---------------------------------------------


class ScriptedProvider:
    """先调一次工具，再给一句回复。"""

    def __init__(self, call: dict[str, Any]) -> None:
        self.call = call
        self.calls = 0

    def stream_response(self, **kwargs: Any) -> Any:
        from tau_agent.messages import AssistantMessage, TextContent, ToolCall
        from tau_agent.provider_events import AssistantDoneEvent, AssistantStartEvent

        index = self.calls
        self.calls += 1

        async def generator() -> Any:
            yield AssistantStartEvent(partial=AssistantMessage())
            if index == 0:
                yield AssistantDoneEvent(
                    reason="toolUse",
                    message=AssistantMessage(
                        content=[
                            ToolCall(
                                id="call_story",
                                name="update_story",
                                arguments=self.call,
                            )
                        ],
                        stop_reason="toolUse",
                    ),
                )
            else:
                yield AssistantDoneEvent(
                    reason="stop",
                    message=AssistantMessage(
                        content=[TextContent(text="结构改好了")], stop_reason="stop"
                    ),
                )

        return generator()


def test_turn_updates_structure_through_the_tool(tmp_path: Path) -> None:
    import asyncio

    from hastings.server.provider_runtime import ModelSnapshot
    from hastings.workspace.files import Workspace
    from hastings.workspace.materials import ContextRequest
    from hastings.workspace.service import TurnService
    from hastings.workspace.state import ConversationStore

    store = StoryStore(tmp_path / "state")
    _seed(store)
    provider = ScriptedProvider(
        {
            "operations": [
                {
                    "op": "add_node",
                    "node": {"id": "n5", "title": "雪停", "action": "雪在清晨停了"},
                }
            ]
        }
    )
    workspace = Workspace.create(tmp_path / "book")
    conversations = ConversationStore(tmp_path / "state")
    service = TurnService(
        workspace,
        conversations,
        lambda: ModelSnapshot(
            connection_id="test", display_name="测试连接", model="fake", provider=provider
        ),
    )

    from hastings.workspace.story_context import StoryRequest

    async def run_turn() -> Any:
        info = await service.accept(
            "在构思里加一个节点：雪停",
            "story-tool-turn",
            context=ContextRequest(
                story=StoryRequest(variant_id=MAIN_VARIANT_ID, purpose="design")
            ),
        )
        await service.wait(info.turn_id)
        return info

    info = asyncio.run(run_turn())

    variant = store.read().variant(MAIN_VARIANT_ID)
    assert variant is not None and "n5" in variant.nodes
    events = list(conversations.events(info.conversation_id))
    finished = [event for event in events if event["type"] == "tool_finished"]
    assert any(event["tool"] == "update_story" and event["ok"] for event in finished)
    committed = [event for event in events if event["type"] == "story_changes_committed"]
    assert committed and committed[0]["operations"] == 1
