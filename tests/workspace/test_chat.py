"""对话、轮次与事件（0.4 V04-3，计划 M05）。

契约：一轮请求由后台任务执行，事件按 ``seq`` 追加并可回放；同一作品同时只允许一轮
在写；停止保留已写入文件并记录为「已停止」；进程重启把没有正常结束的轮次标为中断。
模型用脚本化假 provider 驱动——它替代的是「模型说什么」，不替代工具、文件与 Git。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.git import GitRepo
from hastings.workspace.service import ProviderFactory
from hastings.workspace.state import RecentWorkspaces
from tau_agent.messages import AssistantMessage, TextContent, ToolCall
from tau_agent.provider_events import AssistantDoneEvent, AssistantStartEvent


class ScriptedProvider:
    """按脚本吐事件；``hang_from`` 之后的调用会一直等取消，用来模拟长轮次。"""

    def __init__(self, turns: list[list[Any]], *, hang_from: int | None = None) -> None:
        self.turns = turns
        self.hang_from = hang_from
        self.calls = 0

    def stream_response(self, *, signal: Any = None, **_: Any) -> Any:
        index = self.calls
        self.calls += 1

        async def generator() -> Any:
            yield AssistantStartEvent(partial=AssistantMessage())
            if index < len(self.turns):
                for event in self.turns[index]:
                    yield event
            if self.hang_from is not None and index >= self.hang_from:
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


@pytest.fixture()
def client_factory(tmp_path: Path) -> Iterator[Callable[[ProviderFactory], TestClient]]:
    """保持 TestClient 的 portal 存活：否则每个请求一个新事件循环，后台轮次会被拆掉。"""
    opened: list[TestClient] = []

    def build(factory: ProviderFactory) -> TestClient:
        book = RecentWorkspaces(tmp_path / "state")
        client = TestClient(create_workspace_app(book=book, provider_factory=factory))
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


def _conversation(client: TestClient, workspace_id: str) -> str:
    response = client.post(f"/api/file-workspaces/{workspace_id}/conversations", json={})
    assert response.status_code == 200, response.text
    return str(response.json()["id"])


def _events(client: TestClient, workspace_id: str, conversation_id: str) -> list[dict[str, Any]]:
    response = client.get(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/messages"
    )
    assert response.status_code == 200, response.text
    return list(response.json()["events"])


def _wait_until(
    client: TestClient,
    workspace_id: str,
    conversation_id: str,
    predicate: Callable[[list[dict[str, Any]]], bool],
    *,
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events = _events(client, workspace_id, conversation_id)
        if predicate(events):
            return events
        time.sleep(0.05)
    raise AssertionError(f"等待超时，当前事件：{_events(client, workspace_id, conversation_id)}")


def _finished(events: list[dict[str, Any]]) -> bool:
    return any(event["type"] == "run_finished" for event in events)


# -- 直接写入 -------------------------------------------------------------


def test_turn_writes_chapter_and_records_git_commit(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    provider = ScriptedProvider(
        [
            _tool_turn(
                ToolCall(
                    id="c1",
                    name="write_file",
                    arguments={
                        "path": "正文/0001 雨夜.md",
                        "text": "# 雨夜\n\n侦探抵达旅馆。\n",
                    },
                )
            ),
            _text_turn("第一章写好了。"),
        ]
    )
    client = client_factory(lambda: (provider, "fake-model"))
    workspace_id = _workspace(client, tmp_path)
    conversation_id = _conversation(client, workspace_id)

    response = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/turns",
        json={"text": "写第一章，雨夜"},
    )

    assert response.status_code == 202, response.text
    events = _wait_until(client, workspace_id, conversation_id, _finished)
    assert [event["type"] for event in events] == [
        "turn_started",
        "tool_started",
        "tool_finished",
        "assistant_message",
        "run_finished",
    ]
    assert events[2]["ok"] is True
    assert events[3]["text"] == "第一章写好了。"
    assert events[4]["status"] == "completed"
    assert events[4]["recording"] == "committed"
    assert events[4]["changed_paths"] == ["正文/0001 雨夜.md"]

    root = tmp_path / "雪山旅馆"
    assert (root / "正文/0001 雨夜.md").is_file()
    subjects = [commit.subject for commit in GitRepo(root).log()]
    assert subjects[0] == "写作：写第一章，雨夜"
    # 对话标题取首条消息，便于在左栏辨认。
    listed = client.get(f"/api/file-workspaces/{workspace_id}/conversations").json()
    assert listed[0]["title"] == "写第一章，雨夜"


def test_discussion_turn_changes_no_files(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    provider = ScriptedProvider([_text_turn("第三人称更合适，因为……")])
    client = client_factory(lambda: (provider, "fake-model"))
    workspace_id = _workspace(client, tmp_path)
    conversation_id = _conversation(client, workspace_id)
    root = tmp_path / "雪山旅馆"
    head = GitRepo(root).head()

    client.post(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/turns",
        json={"text": "这个故事用什么视角好？"},
    )

    events = _wait_until(client, workspace_id, conversation_id, _finished)
    assert events[-1]["recording"] == "no_change"
    assert events[-1]["changed_paths"] == []
    assert GitRepo(root).head() == head


# -- 并发与停止 -----------------------------------------------------------


def test_second_turn_is_rejected_while_one_is_running(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    provider = ScriptedProvider(
        [
            _tool_turn(
                ToolCall(
                    id="c1",
                    name="write_file",
                    arguments={"path": "正文/0001 雨夜.md", "text": "# 雨夜\n"},
                )
            ),
            [],
        ],
        hang_from=1,
    )
    client = client_factory(lambda: (provider, "fake-model"))
    workspace_id = _workspace(client, tmp_path)
    conversation_id = _conversation(client, workspace_id)
    url = f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/turns"

    first = client.post(url, json={"text": "写第一章"})
    assert first.status_code == 202
    _wait_until(
        client,
        workspace_id,
        conversation_id,
        lambda events: any(event["type"] == "tool_finished" for event in events),
    )

    second = client.post(url, json={"text": "再写第二章"})

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "WORKSPACE_BUSY_CONFLICT"

    stopped = client.post(
        f"/api/file-workspaces/{workspace_id}/turns/{first.json()['turn_id']}/stop"
    )
    assert stopped.status_code == 200
    events = _wait_until(client, workspace_id, conversation_id, _finished)
    assert events[-1]["status"] == "stopped"
    # 停止不撤销已经写进去的内容。
    assert (tmp_path / "雪山旅馆/正文/0001 雨夜.md").is_file()


def test_stop_marks_partial_commit(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    provider = ScriptedProvider(
        [
            _tool_turn(
                ToolCall(
                    id="c1",
                    name="write_file",
                    arguments={"path": "正文/0001 雨夜.md", "text": "# 雨夜\n\n写到一半。\n"},
                )
            ),
            [],
        ],
        hang_from=1,
    )
    client = client_factory(lambda: (provider, "fake-model"))
    workspace_id = _workspace(client, tmp_path)
    conversation_id = _conversation(client, workspace_id)

    turn = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/turns",
        json={"text": "写第一章"},
    ).json()
    _wait_until(
        client,
        workspace_id,
        conversation_id,
        lambda events: any(event["type"] == "tool_finished" for event in events),
    )
    client.post(f"/api/file-workspaces/{workspace_id}/turns/{turn['turn_id']}/stop")

    events = _wait_until(client, workspace_id, conversation_id, _finished)
    assert events[-1]["status"] == "stopped"
    assert events[-1]["recording"] == "committed"
    subjects = [commit.subject for commit in GitRepo(tmp_path / "雪山旅馆").log()]
    assert subjects[0] == "写作（已停止）：写第一章"


# -- 失败与重启 -----------------------------------------------------------


def test_turn_without_model_reports_failure(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    def factory() -> Any:
        raise WorkspaceError(
            code="MODEL_NOT_CONFIGURED",
            where="turn.model",
            why="当前没有可用的模型来源",
            fix="配置模型后重试",
        )

    client = client_factory(factory)
    workspace_id = _workspace(client, tmp_path)
    conversation_id = _conversation(client, workspace_id)

    client.post(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/turns",
        json={"text": "写第一章"},
    )

    events = _wait_until(client, workspace_id, conversation_id, _finished)
    assert [event["type"] for event in events] == ["turn_started", "error", "run_finished"]
    assert events[1]["code"] == "MODEL_NOT_CONFIGURED"
    assert events[2]["status"] == "failed"
    assert events[2]["recording"] == "no_change"


def test_unfinished_turn_is_marked_interrupted_on_restart(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    book = RecentWorkspaces(tmp_path / "state")
    provider = ScriptedProvider([_text_turn("你好")])
    app = create_workspace_app(book=book, provider_factory=lambda: (provider, "fake-model"))
    client = TestClient(app)
    workspace_id = _workspace(client, tmp_path)
    conversation_id = _conversation(client, workspace_id)

    # 直接写入一条没有结尾的轮次，模拟进程在运行中退出。
    from hastings.workspace.state import ConversationStore, conversation_root

    store = ConversationStore(conversation_root(book.root, workspace_id))
    store.append(conversation_id, {"type": "turn_started", "turn_id": "t_old", "text": "写第一章"})

    # 新进程：第一次访问这个作品时补一条中断记录。
    restarted = TestClient(
        create_workspace_app(book=book, provider_factory=lambda: (provider, "fake-model"))
    )
    events = _events(restarted, workspace_id, conversation_id)

    assert events[-1]["type"] == "run_finished"
    assert events[-1]["status"] == "interrupted"
    assert events[-1]["recording"] == "unrecorded"


# -- 请求键与事件流 -------------------------------------------------------


def test_request_key_is_idempotent(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    provider = ScriptedProvider([_text_turn("好")])
    client = client_factory(lambda: (provider, "fake-model"))
    workspace_id = _workspace(client, tmp_path)
    conversation_id = _conversation(client, workspace_id)
    url = f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/turns"

    first = client.post(url, json={"text": "你好", "request_key": "k1"})
    _wait_until(client, workspace_id, conversation_id, _finished)
    second = client.post(url, json={"text": "你好", "request_key": "k1"})

    assert second.json()["turn_id"] == first.json()["turn_id"]
    assert provider.calls == 1
    assert (
        len(
            [
                e
                for e in _events(client, workspace_id, conversation_id)
                if e["type"] == "turn_started"
            ]
        )
        == 1
    )


def test_event_stream_replays_and_stops_at_run_finished(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    provider = ScriptedProvider([_text_turn("好")])
    client = client_factory(lambda: (provider, "fake-model"))
    workspace_id = _workspace(client, tmp_path)
    conversation_id = _conversation(client, workspace_id)
    client.post(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/turns",
        json={"text": "你好"},
    )
    _wait_until(client, workspace_id, conversation_id, _finished)

    response = client.get(
        f"/api/file-workspaces/{workspace_id}/events",
        params={"conversation_id": conversation_id, "after": 0, "follow": False},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "event: " not in body  # 只用 data 行与 id 行
    assert '"type": "turn_started"' in body
    assert '"type": "run_finished"' in body
    assert body.count("data: ") == 3
