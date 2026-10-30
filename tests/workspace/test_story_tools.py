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


def _seed_identity(tmp_path: Path, clue_id: str = "glove", title: str = "手套") -> None:
    from uuid import uuid4

    from hastings.workspace.clue_models import ClueIdentity
    from hastings.workspace.clue_store import ClueLedgerStore

    ledger = ClueLedgerStore(tmp_path / "state")

    def apply(store: Any) -> dict[str, str]:
        store.clues[clue_id] = ClueIdentity(
            clue_id=clue_id,
            title=title,
            clue_revision=f"rev_{uuid4().hex}",
            created_at="2026-10-30T00:00:00+08:00",
            created_by="author",
        )
        return {"clue_id": clue_id}

    ledger.mutate(
        expected_revision=ledger.read().revision,
        request_key=f"identity-{clue_id}",
        kind="test_identity",
        payload={},
        apply=apply,
    )


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


def _scope(
    tmp_path: Path,
    *,
    writable: bool = True,
    guard: Any = None,
    author_text: str = "把开篇加一个节点",
) -> StoryToolScope:
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
        author_text=author_text,
    )


@pytest.fixture
def store(tmp_path: Path) -> StoryStore:
    store = StoryStore(tmp_path / "state")
    _seed(store)
    return store


def test_removed_variant_tools_are_not_offered(store: StoryStore, tmp_path: Path) -> None:
    assert store.require_variant(MAIN_VARIANT_ID)
    tools = build_story_tools(_scope(tmp_path, author_text="创建备选并采用整份备选"))
    names = {tool.name for tool in tools}
    assert "read_story" in names
    assert "create_story_variant" not in names
    assert "adopt_story_variant" not in names


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
        frozen=StoryContextSnapshot(variant_id=MAIN_VARIANT_ID, revision="rev", purpose="design"),
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


def test_structure_tool_obeys_task_finalizing(store: StoryStore, tmp_path: Path) -> None:
    def finalizing(where: str) -> None:
        raise WorkspaceError(
            code="TASK_FINALIZING_CONFLICT",
            where=where,
            why="本轮的模块内容已经应用，任务正在收尾",
            fix="只读取结果并回复；新的修改由作者在下一轮提出",
        )

    scope = StoryToolScope(
        state_root=tmp_path / "state",
        frozen=StoryContextSnapshot(variant_id=MAIN_VARIANT_ID, revision="rev_1", purpose="design"),
        writable=True,
        write_guard=finalizing,
        operation_id="op_1",
        generation=1,
        author_text="加一个节点",
    )
    with pytest.raises(WorkspaceError) as caught:
        _call(
            _tool(build_story_tools(scope), "update_story"),
            {
                "operations": [
                    {
                        "op": "add_node",
                        "node": {"id": "n8", "title": "收尾后", "action": "不写"},
                    }
                ]
            },
        )
    assert caught.value.code == "TASK_FINALIZING_CONFLICT"
    assert store.read().variant(MAIN_VARIANT_ID).nodes.get("n8") is None


def test_candidate_text_extends_objects_but_not_truth_authority(
    store: StoryStore, tmp_path: Path
) -> None:
    variant = store.require_variant(MAIN_VARIANT_ID)
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        request_key="candidate-seed",
        change=StoryChangeSet(
            id="candidate-seed",
            base_revision=variant.revision,
            operations=[
                {"op": "add_node", "node": {"id": "n2", "title": "对质", "action": "开口"}},
                {
                    "op": "upsert_clue_plan",
                    "clue_id": "glove",
                    "plan": {"author_truth": "管家所留"},
                },
            ],
        ),
    )
    _seed_identity(tmp_path)
    node = store.require_variant(MAIN_VARIANT_ID).nodes["n2"].model_dump(mode="json")
    scope = StoryToolScope(
        state_root=tmp_path / "state",
        frozen=StoryContextSnapshot(
            variant_id=MAIN_VARIANT_ID, revision="rev_1", purpose="design", node_ids=("n1",)
        ),
        writable=True,
        author_text="用这个",
        candidate_text="建议把「对质」改成他先沉默，手套的真相改为客人所留。",
        operation_id="op_1",
        generation=1,
    )
    result = _call(
        _tool(build_story_tools(scope), "update_story"),
        {
            "operations": [
                {"op": "update_node", "node_id": "n2", "node": {**node, "action": "先沉默"}}
            ]
        },
    )
    assert result.details["ok"] is True
    assert store.require_variant(MAIN_VARIANT_ID).nodes["n2"].action == "先沉默"

    with pytest.raises(WorkspaceError, match="STORY_AUTHORIZATION_CONFLICT"):
        _call(
            _tool(build_story_tools(scope), "update_story"),
            {
                "operations": [
                    {
                        "op": "upsert_clue_plan",
                        "clue_id": "glove",
                        "plan": {"author_truth": "客人所留"},
                    }
                ]
            },
        )
    assert (
        store.require_variant(MAIN_VARIANT_ID).clue_plans["glove"].author_truth
        == "管家所留"
    )


def test_read_only_scope_has_no_update_tool(tmp_path: Path) -> None:
    tools = build_story_tools(_scope(tmp_path, writable=False))

    assert [tool.name for tool in tools] == ["read_story"]


@pytest.mark.parametrize(
    "operation",
    [
        {
            "op": "add_constraint",
            "constraint": {"id": "c1", "text": "新要求", "strength": "required", "revision": "r1"},
        },
        {
            "op": "record_assessment",
            "assessment": {"id": "a1", "structure_revision": "r1", "decision_source": "author"},
        },
    ],
)
def test_model_cannot_create_required_rules_or_forge_author_decision(
    store: StoryStore, tmp_path: Path, operation: dict[str, Any]
) -> None:
    before = store.file.read_bytes()
    with pytest.raises(WorkspaceError) as caught:
        _call(
            _tool(build_story_tools(_scope(tmp_path)), "update_story"), {"operations": [operation]}
        )
    assert caught.value.code == "STORY_AUTHORIZATION_CONFLICT"
    assert store.file.read_bytes() == before


def test_model_cannot_forge_constraint_provenance(store: StoryStore, tmp_path: Path) -> None:
    result = _call(
        _tool(build_story_tools(_scope(tmp_path)), "update_story"),
        {
            "operations": [
                {"op": "create_clue_identity", "temp_id": "c1", "title": "脚印"},
                {"op": "upsert_clue_plan", "clue_id": "c1", "plan": {}},
                {
                    "op": "add_constraint",
                    "constraint": {
                        "id": "p1",
                        "text": "保持克制",
                        "strength": "preference",
                        "revision": "model-version",
                        "origin": "author_request",
                        "source_message_id": "forged",
                    },
                },
            ],
        },
    )
    assert result.details["ok"] is True
    variant = store.require_variant(MAIN_VARIANT_ID)
    assert len(variant.clue_plans) == 1
    assert next(iter(variant.clue_plans.values())).author_truth is None
    constraint = variant.constraints["p1"]
    assert constraint.origin == "model_suggestion"
    assert constraint.source_message_id == ""
    assert constraint.revision != "model-version"


def test_model_cannot_remove_author_assessment(store: StoryStore, tmp_path: Path) -> None:
    revision = store.require_variant(MAIN_VARIANT_ID).revision
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        change=StoryChangeSet(
            id="author_assessment",
            base_revision=revision,
            operations=[
                {
                    "op": "record_assessment",
                    "assessment": {
                        "id": "a1",
                        "structure_revision": revision,
                        "decision_source": "author",
                    },
                }
            ],
        ),
        request_key="author-assessment",
    )
    before = store.file.read_bytes()
    with pytest.raises(WorkspaceError) as caught:
        _call(
            _tool(build_story_tools(_scope(tmp_path)), "update_story"),
            {
                "operations": [{"op": "remove_assessment", "assessment_id": "a1"}],
            },
        )
    assert caught.value.code == "STORY_AUTHORIZATION_CONFLICT"
    assert store.file.read_bytes() == before


def test_invalid_operations_are_reported_without_writing(store: StoryStore, tmp_path: Path) -> None:
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


def test_model_cannot_change_existing_clue_truth_without_object_authorization(
    store: StoryStore,
    tmp_path: Path,
) -> None:
    variant = store.require_variant("main")
    seed = StoryChangeSet(
        id="truth_seed",
        base_revision=variant.revision,
        operations=[
            {
                "op": "upsert_clue_plan",
                "clue_id": "glove",
                "plan": {"author_truth": "管家所留"},
            },
        ],
    )
    store.commit_change(variant_id="main", change=seed, request_key="truth_seed")
    _seed_identity(tmp_path)
    tools = build_story_tools(_scope(tmp_path, author_text="把手套的真相改一下"))
    with pytest.raises(WorkspaceError, match="STORY_AUTHORIZATION_CONFLICT"):
        _call(
            _tool(tools, "update_story"),
            {
                "operations": [
                    {
                        "op": "upsert_clue_plan",
                        "clue_id": "glove",
                        "plan": {"author_truth": "客人所留"},
                    },
                ]
            },
        )
    assert store.require_variant("main").clue_plans["glove"].author_truth == "管家所留"


def test_model_cannot_modify_node_outside_author_scope(store: StoryStore, tmp_path: Path) -> None:
    variant = store.require_variant(MAIN_VARIANT_ID)
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        request_key="seed-2",
        change=StoryChangeSet(
            id="seed_2",
            base_revision=variant.revision,
            operations=[
                {"op": "add_node", "node": {"id": "n2", "title": "雪停", "action": "雪停了"}}
            ],
        ),
    )
    node = store.require_variant(MAIN_VARIANT_ID).nodes["n2"].model_dump(mode="json")
    node["action"] = "雪停了，门外有脚印"
    scope = StoryToolScope(
        state_root=tmp_path / "state",
        frozen=StoryContextSnapshot(
            variant_id=MAIN_VARIANT_ID, revision="rev_1", purpose="write", node_ids=("n1",)
        ),
        writable=True,
        author_text="继续写开场",
    )
    before = store.file.read_bytes()
    with pytest.raises(WorkspaceError) as caught:
        _call(
            _tool(build_story_tools(scope), "update_story"),
            {"operations": [{"op": "update_node", "node_id": "n2", "node": node}]},
        )
    assert caught.value.code == "STORY_AUTHORIZATION_CONFLICT"
    assert caught.value.details["object_id"] == "n2"
    assert store.file.read_bytes() == before


def test_model_can_modify_object_the_author_names(store: StoryStore, tmp_path: Path) -> None:
    node = store.require_variant(MAIN_VARIANT_ID).nodes["n1"].model_dump(mode="json")
    node["action"] = "侦探走进旅馆，前台没人"
    scope = StoryToolScope(
        state_root=tmp_path / "state",
        frozen=StoryContextSnapshot(
            variant_id=MAIN_VARIANT_ID, revision="rev_1", purpose="write", node_ids=()
        ),
        writable=True,
        author_text="把抵达那场改成前台没人",
    )
    result = _call(
        _tool(build_story_tools(scope), "update_story"),
        {"operations": [{"op": "update_node", "node_id": "n1", "node": node}]},
    )
    assert result.details["ok"] is True
    assert store.require_variant(MAIN_VARIANT_ID).nodes["n1"].action == "侦探走进旅馆，前台没人"


def test_model_cannot_replace_truth_by_removing_and_recreating_clue(
    store: StoryStore,
    tmp_path: Path,
) -> None:
    variant = store.require_variant("main")
    store.commit_change(
        variant_id="main",
        request_key="truth-seed",
        change=StoryChangeSet(
            id="truth_seed",
            base_revision=variant.revision,
            operations=[
                {
                    "op": "upsert_clue_plan",
                    "clue_id": "glove",
                    "plan": {"author_truth": "管家所留"},
                },
            ],
        ),
    )
    _seed_identity(tmp_path)
    tools = build_story_tools(_scope(tmp_path, author_text="把原来的手套线索换掉"))
    with pytest.raises(WorkspaceError, match="STORY_AUTHORIZATION_CONFLICT"):
        _call(
            _tool(tools, "update_story"),
            {
                "operations": [
                    {"op": "remove_clue_plan", "clue_id": "glove"},
                    {
                        "op": "upsert_clue_plan",
                        "clue_id": "glove",
                        "plan": {"author_truth": "客人所留"},
                    },
                ]
            },
        )
    assert store.require_variant("main").clue_plans["glove"].author_truth == "管家所留"


@pytest.mark.parametrize("prefix", ["", "> ", "如果", "不要"])
def test_explicit_author_truth_requires_direct_command(
    store: StoryStore, tmp_path: Path, prefix: str
) -> None:
    variant = store.require_variant("main")
    store.commit_change(
        variant_id="main",
        request_key="seed-truth",
        change=StoryChangeSet(
            id="truth",
            base_revision=variant.revision,
            operations=[
                {
                    "op": "upsert_clue_plan",
                    "clue_id": "glove",
                    "plan": {"author_truth": "管家所留"},
                }
            ],
        ),
    )
    _seed_identity(tmp_path)
    tools = build_story_tools(_scope(tmp_path, author_text=prefix + "把手套的真相改为客人所留"))
    args = {
        "operations": [
            {
                "op": "upsert_clue_plan",
                "clue_id": "glove",
                "plan": {"author_truth": "客人所留"},
            }
        ]
    }
    if prefix:
        with pytest.raises(WorkspaceError, match="STORY_AUTHORIZATION_CONFLICT"):
            _call(_tool(tools, "update_story"), args)
        assert store.require_variant("main").clue_plans["glove"].author_truth == "管家所留"
    else:
        _call(_tool(tools, "update_story"), args)
        assert store.require_variant("main").clue_plans["glove"].author_truth == "客人所留"


def test_author_can_add_required_constraint(store: StoryStore, tmp_path: Path) -> None:
    tools = build_story_tools(_scope(tmp_path, author_text="新增约定：结局保留开放"))
    _call(
        _tool(tools, "update_story"),
        {
            "operations": [
                {
                    "op": "add_constraint",
                    "constraint": {
                        "id": "open",
                        "text": "结局保留开放",
                        "strength": "required",
                        "revision": "fake",
                    },
                }
            ]
        },
    )
    constraint = store.require_variant("main").constraints["open"]
    assert constraint.origin == "author_request"
    assert constraint.revision != "fake"


def test_quoted_or_discussed_command_keeps_structure_read_only(
    store: StoryStore, tmp_path: Path
) -> None:
    assert store.require_variant(MAIN_VARIANT_ID)
    for text in (
        "请解释“把手套的真相改为客人所留”这句话的意思。",
        "解释一下把手套的真相改为客人所留是什么意思",
    ):
        tools = build_story_tools(_scope(tmp_path, author_text=text))
        assert [tool.name for tool in tools] == ["read_story"]


def test_direct_command_with_quoted_value_still_authorizes_truth(
    store: StoryStore, tmp_path: Path
) -> None:
    variant = store.require_variant(MAIN_VARIANT_ID)
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        request_key="seed-quoted-value",
        change=StoryChangeSet(
            id="seed-quoted-value",
            base_revision=variant.revision,
            operations=[
                {
                    "op": "upsert_clue_plan",
                    "clue_id": "glove",
                    "plan": {"author_truth": "管家所留"},
                }
            ],
        ),
    )
    _seed_identity(tmp_path)
    tools = build_story_tools(_scope(tmp_path, author_text="把手套的真相改为“客人所留”"))

    _call(
        _tool(tools, "update_story"),
        {
            "operations": [
                {
                    "op": "upsert_clue_plan",
                    "clue_id": "glove",
                    "plan": {"author_truth": "客人所留"},
                }
            ]
        },
    )

    assert store.require_variant(MAIN_VARIANT_ID).clue_plans["glove"].author_truth == "客人所留"


