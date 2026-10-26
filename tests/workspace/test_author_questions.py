"""作者提问与跨轮继续（0.8 模型自主维护模块）。

契约：``ask_author`` 记录一个具体问题并结束本轮，状态为 ``needs_input``，运行锁随之
释放；作者带 ``reply_to_question`` 回复时，服务端核对问题归属、是否已回答与目标版本，
通过后开启新轮次承接原请求。重复回答、跨会话回答与装配变化都不能复用旧问题。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.agent import RunContext
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.module_assembly import AssemblyStore
from hastings.workspace.service import ProviderFactory, WriteSession
from hastings.workspace.state import RecentWorkspaces
from tau_agent.messages import AssistantMessage, TextContent, ToolCall
from tau_agent.provider_events import AssistantDoneEvent, AssistantStartEvent


class ScriptedProvider:
    """按顺序吐出预设回合，并记录模型实际收到的 system 与 messages。"""

    def __init__(self, turns: list[list[Any]]) -> None:
        self.turns = turns
        self.calls = 0
        self.requests: list[dict[str, Any]] = []

    def stream_response(
        self,
        *,
        model: str = "",
        system: str = "",
        messages: list[Any] | None = None,
        **_rest: Any,
    ) -> Any:
        index = self.calls
        self.calls += 1
        self.requests.append({"model": model, "system": system, "messages": list(messages or [])})

        async def generator() -> Any:
            yield AssistantStartEvent(partial=AssistantMessage())
            if index < len(self.turns):
                for event in self.turns[index]:
                    yield event

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


def _snapshot(provider: Any, model: str = "fake-model") -> ModelSnapshot:
    return ModelSnapshot(
        connection_id="test", display_name="测试连接", model=model, provider=provider
    )


@pytest.fixture()
def client_factory(tmp_path: Path) -> Iterator[Callable[[ProviderFactory], TestClient]]:
    opened: list[TestClient] = []

    def build(factory: ProviderFactory) -> TestClient:
        book = RecentWorkspaces(tmp_path / "state")
        client = TestClient(create_workspace_app(workspaces=book, provider_factory=factory))
        client.__enter__()
        opened.append(client)
        return client

    yield build
    for client in opened:
        client.__exit__(None, None, None)


def _workspace(client: TestClient, tmp_path: Path) -> str:
    root = tmp_path / "雪山旅馆"
    response = client.post("/api/file-workspaces", json={"path": str(root), "create": True})
    assert response.status_code == 200, response.text
    return str(response.json()["id"])


def _events(client: TestClient, workspace_id: str, conversation_id: str) -> list[dict[str, Any]]:
    response = client.get(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/messages"
    )
    assert response.status_code == 200, response.text
    return list(response.json()["events"])


def _wait_finished(
    client: TestClient, workspace_id: str, conversation_id: str
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        events = _events(client, workspace_id, conversation_id)
        if any(event["type"] == "run_finished" for event in events):
            return events
        time.sleep(0.05)
    raise AssertionError(f"等待超时：{_events(client, workspace_id, conversation_id)}")


def _reply(
    client: TestClient,
    workspace_id: str,
    conversation_id: str,
    text: str,
    question_id: str,
) -> Any:
    return client.post(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/turns",
        json={
            "text": text,
            "request_key": f"k_{time.monotonic_ns()}",
            "reply_to_question": question_id,
        },
    )


def test_ask_author_pauses_then_reply_continues(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    provider = ScriptedProvider(
        [
            _tool_turn(
                ToolCall(
                    id="q1",
                    name="ask_author",
                    arguments={
                        "question": "死者与医生的关系选哪一种？",
                        "options": ["旧识", "亲姐弟"],
                        "paths": ["正文/0001 雨夜.md"],
                    },
                )
            ),
            _text_turn("需要你先决定这一点。"),
            _text_turn("按你的选择改成亲姐弟。"),
        ]
    )
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    first = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={"text": "写第一章，先确定人物关系", "request_key": "k_ask"},
    )
    assert first.status_code == 202, first.text
    conversation_id = first.json()["conversation_id"]

    events = _wait_finished(client, workspace_id, conversation_id)
    asked = next(event for event in events if event["type"] == "author_input_requested")
    # 这一轮不是「完成」：它在等作者回答。
    assert events[-1]["status"] == "needs_input"
    assert asked["question"] == "死者与医生的关系选哪一种？"
    assert asked["options"] == ["旧识", "亲姐弟"]
    # 运行锁已释放：同一作品可以立刻受理下一条。
    response = _reply(client, workspace_id, conversation_id, "选亲姐弟", asked["question_id"])
    assert response.status_code == 202, response.text

    events = _wait_finished(client, workspace_id, conversation_id)
    replied = next(event for event in events if event["type"] == "author_replied")
    assert replied["question_id"] == asked["question_id"]
    assert replied["answer"] == "选亲姐弟"
    assert events[-1]["status"] == "completed"
    # 下一轮的模型上下文里能看到自己问过什么、作者答了什么。
    system = provider.requests[2]["system"]
    assert "该轮向作者提问：「死者与医生的关系选哪一种？」" in system
    assert "作者回答：「选亲姐弟」" in system


def test_answering_twice_is_rejected(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    provider = ScriptedProvider(
        [
            _tool_turn(
                ToolCall(
                    id="q1",
                    name="ask_author",
                    arguments={"question": "用第一人称还是第三人称？", "options": ["第一", "第三"]},
                )
            ),
            _text_turn("等你决定。"),
            _text_turn("收到。"),
        ]
    )
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    first = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={"text": "开始写", "request_key": "k_once"},
    )
    conversation_id = first.json()["conversation_id"]
    events = _wait_finished(client, workspace_id, conversation_id)
    question_id = next(
        event["question_id"] for event in events if event["type"] == "author_input_requested"
    )

    assert _reply(client, workspace_id, conversation_id, "第一人称", question_id).status_code == 202
    _wait_finished(client, workspace_id, conversation_id)
    repeat = _reply(client, workspace_id, conversation_id, "第三人称", question_id)

    assert repeat.status_code == 409
    assert repeat.json()["error"]["code"] == "AUTHOR_QUESTION_ANSWERED_CONFLICT"


def test_answering_from_another_conversation_is_rejected(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    provider = ScriptedProvider(
        [
            _tool_turn(
                ToolCall(
                    id="q1",
                    name="ask_author",
                    arguments={"question": "这个点子要不要展开？"},
                )
            ),
            _text_turn("等你决定。"),
            _text_turn("另一个会话的开场。"),
        ]
    )
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    first = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={"text": "看看这个点子", "request_key": "k_origin"},
    )
    conversation_id = first.json()["conversation_id"]
    events = _wait_finished(client, workspace_id, conversation_id)
    question_id = next(
        event["question_id"] for event in events if event["type"] == "author_input_requested"
    )
    other = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={"text": "另开一个话题", "request_key": "k_other"},
    )
    assert other.status_code == 202, other.text
    other_id = other.json()["conversation_id"]

    response = _reply(client, workspace_id, other_id, "要展开", question_id)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "AUTHOR_QUESTION_CONVERSATION_CONFLICT"


def test_assembly_change_invalidates_the_old_question(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    provider = ScriptedProvider(
        [
            _tool_turn(
                ToolCall(
                    id="q1",
                    name="ask_author",
                    arguments={"question": "还要继续用这版人物设定吗？"},
                )
            ),
            _text_turn("等你决定。"),
        ]
    )
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    first = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={"text": "按当前设定继续", "request_key": "k_stale"},
    )
    conversation_id = first.json()["conversation_id"]
    events = _wait_finished(client, workspace_id, conversation_id)
    question_id = next(
        event["question_id"] for event in events if event["type"] == "author_input_requested"
    )
    store = AssemblyStore(tmp_path / "雪山旅馆")
    store.replace(
        library_id=None,
        bindings=(),
        expected_digest=store.digest(),
        request_key="k_assembly_change",
    )

    response = _reply(client, workspace_id, conversation_id, "继续", question_id)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "AUTHOR_QUESTION_STALE_CONFLICT"


@pytest.mark.anyio
async def test_pending_question_blocks_further_writes(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    run = RunContext(
        workspace,
        WriteSession(workspace, instruction="先确认关系"),
        state_root=tmp_path / "state",
        turn_id="t_q",
        conversation_id="c_q",
        pending_question={"question_id": "q_x", "question": "选哪一种？"},
    )

    with pytest.raises(WorkspaceError) as blocked:
        run.require_writable("tools.write_file")
    assert blocked.value.code == "TASK_AWAITING_INPUT_CONFLICT"


def test_unanswered_question_survives_a_restart(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    """刷新或重启后，问题仍从事件流与状态文件恢复；不靠内存标记。"""
    provider = ScriptedProvider(
        [
            _tool_turn(
                ToolCall(
                    id="q1",
                    name="ask_author",
                    arguments={"question": "凶手要不要在第一章就出现？"},
                )
            ),
            _text_turn("等你决定。"),
        ]
    )
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    first = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={"text": "开始第一章", "request_key": "k_restart"},
    )
    conversation_id = first.json()["conversation_id"]
    events = _wait_finished(client, workspace_id, conversation_id)
    question_id = next(
        event["question_id"] for event in events if event["type"] == "author_input_requested"
    )

    # 重新读一遍会话：问题仍在事件流里，且处于未回答状态。
    replayed = _events(client, workspace_id, conversation_id)
    asked = next(event for event in replayed if event["type"] == "author_input_requested")
    assert asked["question_id"] == question_id
    assert asked["answered"] is False
    assert not any(
        event["type"] == "author_replied" and event["question_id"] == question_id
        for event in replayed
    )
    # 等待期间不占用运行锁。
    listed = client.get(f"/api/file-workspaces/{workspace_id}/conversations").json()
    assert listed["active_turn"] is None
