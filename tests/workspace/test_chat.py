"""对话、轮次与事件（0.4 V04-3 / V04-10）。

契约：一轮请求由后台任务执行，事件按 ``seq`` 追加并可回放；**首次发送同时建会话并受理
首轮**，空提示不落任何记录；同一作品同时只允许一轮，包括已受理但还没开始执行的；
停止覆盖「已受理」与「正在跑」两个阶段；进程重启按轮次配对补中断，不自动继续。
同一会话能接上前文，不同会话的聊天互相独立。

模型用脚本化假 provider 驱动——它替代的是「模型说什么」，不替代工具、文件与 Git。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.conversation_context import replay_public_messages
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.git import GitRepo
from hastings.workspace.service import ProviderFactory, TurnService
from hastings.workspace.state import ConversationStore, RecentWorkspaces, conversation_root
from tau_agent.messages import AssistantMessage, TextContent, ToolCall, UserMessage
from tau_agent.provider_events import AssistantDoneEvent, AssistantErrorEvent, AssistantStartEvent


class ScriptedProvider:
    """按脚本吐事件；``hang_from`` 之后的调用会一直等取消，用来模拟长轮次。

    每次调用都记下实际收到的 ``system`` 与 ``messages``：判断上下文是否恢复，
    要看模型真正收到什么，而不是界面显示了几条消息。
    """

    def __init__(self, turns: list[list[Any]], *, hang_from: int | None = None) -> None:
        self.turns = turns
        self.hang_from = hang_from
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
        self.requests.append(
            {
                "model": model,
                "system": system,
                "messages": list(messages or []),
                # 工具列表是能力边界的事实依据：断言要看真正交给模型的工具。
                "tools": list(_rest.get("tools") or []),
            }
        )
        signal = _rest.get("signal")

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


def _snapshot(provider: Any, model: str = "fake-model") -> ModelSnapshot:
    """测试用快照：假 provider 没有 aclose，关闭被视为无操作。"""
    return ModelSnapshot(
        connection_id="test", display_name="测试连接", model=model, provider=provider
    )


@pytest.fixture()
def client_factory(tmp_path: Path) -> Iterator[Callable[[ProviderFactory], TestClient]]:
    """保持 TestClient 的 portal 存活：否则每个请求一个新事件循环，后台轮次会被拆掉。"""
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
    if root.exists():
        GitRepo(root).init()
    response = client.post(
        "/api/file-workspaces", json={"path": str(root), "create": not root.exists()}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["id"])


def _accept(
    client: TestClient,
    workspace_id: str,
    text: str = "写第一章",
    *,
    key: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """首次发送：一次请求建会话并受理首轮。"""
    body: dict[str, Any] = {"text": text, "request_key": key or f"k_{time.monotonic_ns()}"}
    if context is not None:
        body["context"] = context
    response = client.post(f"/api/file-workspaces/{workspace_id}/conversations", json=body)
    assert response.status_code == 202, response.text
    return dict(response.json())


def _send(
    client: TestClient,
    workspace_id: str,
    conversation_id: str,
    text: str,
    *,
    key: str | None = None,
) -> Any:
    return client.post(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/turns",
        json={"text": text, "request_key": key or f"k_{time.monotonic_ns()}"},
    )


def _events(client: TestClient, workspace_id: str, conversation_id: str) -> list[dict[str, Any]]:
    response = client.get(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/messages"
    )
    assert response.status_code == 200, response.text
    return list(response.json()["events"])


def _listed(client: TestClient, workspace_id: str) -> dict[str, Any]:
    response = client.get(f"/api/file-workspaces/{workspace_id}/conversations")
    assert response.status_code == 200, response.text
    return dict(response.json())


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
                    id="s0",
                    name="set_task_scope",
                    arguments={
                        "mode": "edit",
                        "summary": "写第一章",
                        "authorization": "写第一章",
                        "grants": [{"action": "create", "path": "正文/0001 雨夜.md"}],
                    },
                ),
                ToolCall(
                    id="c1",
                    name="write_file",
                    arguments={
                        "path": "正文/0001 雨夜.md",
                        "text": "# 雨夜\n\n侦探抵达旅馆。\n",
                    },
                ),
            ),
            _text_turn("第一章写好了。"),
        ]
    )
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    accepted = _accept(client, workspace_id, "写第一章，雨夜")
    conversation_id = accepted["conversation_id"]

    events = _wait_until(client, workspace_id, conversation_id, _finished)
    kinds = [event["type"] for event in events]
    # 过程事件（块、增量、阶段）不逐条断言；这里关心的是可读结果与顺序：
    # 工具先执行，公开回复随后，整轮结果最后。
    assert kinds[0] == "turn_started"
    assert kinds[-1] == "run_finished"
    assert kinds.index("tool_started") < kinds.index("tool_finished")
    assert kinds.index("tool_finished") < kinds.index("assistant_message")
    tool_finished = next(event for event in events if event["type"] == "tool_finished")
    assert tool_finished["ok"] is True
    assert [event["text"] for event in events if event["type"] == "assistant_message"] == [
        "第一章写好了。"
    ]
    assert events[-1]["status"] == "completed"
    assert events[-1]["recording"] == "committed"
    assert events[-1]["changed_paths"] == ["正文/0001 雨夜.md"]

    root = tmp_path / "雪山旅馆"
    assert (root / "正文/0001 雨夜.md").is_file()
    subjects = [commit.subject for commit in GitRepo(root).log()]
    assert subjects[0] == "写作：写第一章，雨夜"
    # 标题取首条提示，便于在列表里辨认。
    listed = _listed(client, workspace_id)
    assert [item["title"] for item in listed["items"]] == ["写第一章，雨夜"]
    assert accepted["title"] == "写第一章，雨夜"


def test_discussion_turn_changes_no_files(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    provider = ScriptedProvider([_text_turn("第三人称更合适，因为……")])
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    conversation_id = _accept(client, workspace_id, "这个故事用什么视角好？")["conversation_id"]
    root = tmp_path / "雪山旅馆"
    head = GitRepo(root).head()

    events = _wait_until(client, workspace_id, conversation_id, _finished)
    assert events[-1]["recording"] == "no_change"
    assert events[-1]["changed_paths"] == []
    assert GitRepo(root).head() == head


def test_injection_event_matches_the_system_prompt(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    """装配结果与事件同源：事件里记的分区，就是系统提示里实际出现的那几块。"""
    provider = ScriptedProvider([_text_turn("第三人称更合适。")])
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    conversation_id = _accept(client, workspace_id, "这个故事用什么视角好？")["conversation_id"]

    events = _wait_until(client, workspace_id, conversation_id, _finished)
    injected = next(event for event in events if event["type"] == "state_injected")
    assert injected["profile"] == "writer"
    assert [part["key"] for part in injected["parts"]][:2] == ["instructions", "catalog"]
    assert all(part["chars"] > 0 for part in injected["parts"])
    assert injected["skipped"] == []
    assert injected["budget"]["used"] > 0

    # 事件说的分区确实出现在发给模型的系统提示里。
    system = provider.requests[0]["system"]
    for part in injected["parts"]:
        assert f"chars={part['chars']}" not in system  # 事件只记规模，不复制正文
    assert "## 设定集内容" not in system
    assert "settings" not in {part["key"] for part in injected["parts"]}
    assert "## 当前作品" in system


def test_model_error_fails_the_turn_instead_of_reporting_done(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    """厂商报错时不能记成「完成、无改动」：轮次失败，错误原样落到事件里。"""
    provider = ScriptedProvider(
        [
            [
                AssistantErrorEvent(
                    reason="error",
                    error=AssistantMessage(stop_reason="error", error_message="502 Bad Gateway"),
                )
            ]
        ]
    )
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    conversation_id = _accept(client, workspace_id, "写第一章")["conversation_id"]
    root = tmp_path / "雪山旅馆"
    head = GitRepo(root).head()

    events = _wait_until(client, workspace_id, conversation_id, _finished)
    kinds = [event["type"] for event in events]
    assert kinds[-1] == "run_finished"
    # 失败与整轮结果之间不再夹着一条「成功」的公开回复。
    assert kinds.count("assistant_message") == 0
    error = next(event for event in events if event["type"] == "error")
    assert error["code"] == "MODEL_CALL_FAILED"
    assert "502 Bad Gateway" in error["message"]
    assert events[-1]["status"] == "failed"
    assert events[-1]["changed_paths"] == []
    assert GitRepo(root).head() == head


# -- 新建与首次发送 -------------------------------------------------------


def test_empty_create_is_rejected_and_leaves_no_conversation(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    """旧的「先建空会话」不再存在：没有提示就没有会话。"""
    provider = ScriptedProvider([])
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    url = f"/api/file-workspaces/{workspace_id}/conversations"

    assert client.post(url, json={}).status_code == 422
    blank = client.post(url, json={"text": "   ", "request_key": "k1"})
    assert blank.status_code == 400
    assert blank.json()["error"]["code"] == "PROMPT_EMPTY"

    listed = _listed(client, workspace_id)
    assert listed["items"] == []
    assert provider.calls == 0
    state_root = conversation_root(tmp_path / "state", workspace_id)
    assert not list(state_root.glob("*.jsonl"))


def test_first_send_creates_one_conversation_and_one_turn(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    provider = ScriptedProvider([_text_turn("好")])
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)

    accepted = _accept(client, workspace_id, "先讨论人物动机", key="k1")
    _wait_until(client, workspace_id, accepted["conversation_id"], _finished)
    retried = _accept(client, workspace_id, "先讨论人物动机", key="k1")

    assert retried["turn_id"] == accepted["turn_id"]
    assert retried["conversation_id"] == accepted["conversation_id"]
    assert provider.calls == 1
    listed = _listed(client, workspace_id)
    assert len(listed["items"]) == 1
    assert (
        len(
            [
                e
                for e in _events(client, workspace_id, accepted["conversation_id"])
                if e["type"] == "turn_started"
            ]
        )
        == 1
    )


def test_same_request_key_with_different_text_is_a_conflict(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    provider = ScriptedProvider([_text_turn("好")])
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    _accept(client, workspace_id, "第一件事", key="same")

    response = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={"text": "第二件事", "request_key": "same"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REQUEST_KEY_CONFLICT"


def test_index_is_rebuilt_from_the_log(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    """索引写坏或丢失不影响已受理的会话：日志才是受理成立的依据。"""
    provider = ScriptedProvider([_text_turn("好")])
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    accepted = _accept(client, workspace_id, "写第一章")
    _wait_until(client, workspace_id, accepted["conversation_id"], _finished)

    index = conversation_root(tmp_path / "state", workspace_id) / "conversations.json"
    index.unlink()

    listed = _listed(client, workspace_id)
    assert [item["id"] for item in listed["items"]] == [accepted["conversation_id"]]
    assert listed["items"][0]["title"] == "写第一章"


def test_failed_log_write_leaves_no_visible_conversation(
    tmp_path: Path,
    client_factory: Callable[[ProviderFactory], TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """初始日志都写不下去时不能留下「半条会话」：作者看到的是失败，不是空对话。"""
    provider = ScriptedProvider([])
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)

    def broken(self: ConversationStore, conversation_id: str, records: Any) -> None:
        raise WorkspaceError(
            code="STATE_WRITE_FAILED",
            where="conversations.create",
            why="磁盘不可写",
            fix="检查应用状态目录",
        )

    monkeypatch.setattr(ConversationStore, "_publish", broken)

    response = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={"text": "写第一章", "request_key": "k1"},
    )

    assert response.status_code == 500
    assert _listed(client, workspace_id)["items"] == []


# -- 连续讨论与隔离 -------------------------------------------------------


@pytest.mark.parametrize("text", ["继续写下一段。", "不要搜索灵感，也不要记下来。"])
def test_idea_permission_does_not_carry_over_in_the_same_conversation(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient], text: str
) -> None:
    provider = ScriptedProvider(
        [
            _text_turn("这轮已结束。"),
            _tool_turn(
                ToolCall(id="idea-search", name="search_ideas", arguments={"query": "房卡"})
            ),
            _tool_turn(
                ToolCall(id="idea-save", name="save_idea", arguments={"body": "未授权笔记。"})
            ),
            _text_turn("没有访问或保存灵感。"),
        ]
    )
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    first = _accept(client, workspace_id, "帮我找灵感，并把想法记下来。")
    conversation_id = first["conversation_id"]
    _wait_until(client, workspace_id, conversation_id, _finished)

    accepted = _send(client, workspace_id, conversation_id, text)
    assert accepted.status_code == 202, accepted.text
    turn_id = accepted.json()["turn_id"]
    events = _wait_until(
        client,
        workspace_id,
        conversation_id,
        lambda rows: any(
            row["type"] == "run_finished" and row["turn_id"] == turn_id for row in rows
        ),
    )
    failed = [
        event
        for event in events
        if event["type"] == "tool_finished" and event["turn_id"] == turn_id
    ]
    assert len(failed) == 2
    assert all(event["ok"] is False for event in failed)
    assert "IDEA_SEARCH_DENIED" in failed[0]["summary"]
    assert "IDEA_SAVE_DENIED" in failed[1]["summary"]
    ideas = client.get(f"/api/file-workspaces/{workspace_id}/ideas").json()
    assert ideas["total"] == 0


def test_second_turn_replays_public_history(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    """同一会话能接着聊：第二次请求真的带上了前一轮的提示与公开回复。"""
    provider = ScriptedProvider(
        [_text_turn("记住了：侦探不能是凶手。"), _text_turn("好，按这个约束写。")]
    )
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    accepted = _accept(client, workspace_id, "记住：侦探不能是凶手")
    conversation_id = accepted["conversation_id"]
    _wait_until(client, workspace_id, conversation_id, _finished)

    response = _send(client, workspace_id, conversation_id, "现在写第一章", key="k2")
    assert response.status_code == 202, response.text
    _wait_until(
        client,
        workspace_id,
        conversation_id,
        lambda events: len([e for e in events if e["type"] == "run_finished"]) == 2,
    )

    second = provider.requests[1]
    users = [message.text for message in second["messages"] if isinstance(message, UserMessage)]
    replies = [
        "".join(block.text for block in message.content)
        for message in second["messages"]
        if isinstance(message, AssistantMessage)
    ]
    # 当前提示只出现一次；前一轮的提示与公开回复都在。
    assert users == ["记住：侦探不能是凶手", "现在写第一章"]
    assert replies == ["记住了：侦探不能是凶手。"]
    # 执行事实通过应用说明进入系统提示，不冒充作者要求。
    assert "本会话此前的轮次" in second["system"]
    assert "完成" in second["system"]
    # 文件现状符合要求 ≠ 任务执行过：这条区分决定模型会不会把「现状达标」说成「已完成」。
    assert "不等于这项任务已经执行过" in second["system"]
    assert "不要用文件现状反推" in second["system"]


def test_new_conversation_has_no_history_from_another(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    provider = ScriptedProvider([_text_turn("A 记下了。"), _text_turn("B 不知道 A。")])
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    first = _accept(client, workspace_id, "A 的约束：没有超自然元素")
    _wait_until(client, workspace_id, first["conversation_id"], _finished)

    second = _accept(client, workspace_id, "B 的新问题")
    _wait_until(client, workspace_id, second["conversation_id"], _finished)

    request = provider.requests[1]
    users = [message.text for message in request["messages"] if isinstance(message, UserMessage)]
    assert users == ["B 的新问题"]
    assert "本会话此前的轮次" not in request["system"]


def test_two_conversations_share_workspace_files(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    """作品文件是共享的：A 写下的章节，B 下一轮能读到当前内容。"""
    provider = ScriptedProvider(
        [
            _tool_turn(
                ToolCall(
                    id="s0",
                    name="set_task_scope",
                    arguments={
                        "mode": "edit",
                        "summary": "写第一章",
                        "authorization": "写第一章",
                        "grants": [{"action": "create", "path": "正文/0001 雨夜.md"}],
                    },
                ),
                ToolCall(
                    id="c1",
                    name="write_file",
                    arguments={"path": "正文/0001 雨夜.md", "text": "# 雨夜\n\nA 写的。\n"},
                ),
            ),
            _text_turn("写好了。"),
            _text_turn("我读到了新的第一章。"),
        ]
    )
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    first = _accept(client, workspace_id, "写第一章")
    _wait_until(client, workspace_id, first["conversation_id"], _finished)

    second = _accept(client, workspace_id, "第一章现在是什么内容？")
    _wait_until(client, workspace_id, second["conversation_id"], _finished)

    assert (tmp_path / "雪山旅馆/正文/0001 雨夜.md").is_file()
    # B 的系统提示按当前作品装配，能看到 A 写出来的章节。
    assert "正文/0001 雨夜.md" in provider.requests[-1]["system"]


# -- 并发与停止 -----------------------------------------------------------


def test_second_turn_is_rejected_while_one_is_running(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    provider = ScriptedProvider(
        [
            _tool_turn(
                ToolCall(
                    id="s0",
                    name="set_task_scope",
                    arguments={
                        "mode": "edit",
                        "summary": "写第一章",
                        "authorization": "写第一章",
                        "grants": [{"action": "create", "path": "正文/0001 雨夜.md"}],
                    },
                ),
                ToolCall(
                    id="c1",
                    name="write_file",
                    arguments={"path": "正文/0001 雨夜.md", "text": "# 雨夜\n"},
                ),
            ),
            [],
        ],
        hang_from=1,
    )
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    first = _accept(client, workspace_id, "写第一章")
    conversation_id = first["conversation_id"]
    _wait_until(
        client,
        workspace_id,
        conversation_id,
        lambda events: any(event["type"] == "tool_finished" for event in events),
    )

    second = _send(client, workspace_id, conversation_id, "再写第二章")

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "WORKSPACE_BUSY_CONFLICT"
    assert second.json()["error"]["details"]["conversation"] == conversation_id

    stopped = client.post(f"/api/file-workspaces/{workspace_id}/turns/{first['turn_id']}/stop")
    assert stopped.status_code == 200
    events = _wait_until(client, workspace_id, conversation_id, _finished)
    assert events[-1]["status"] == "stopped"
    # 停止不撤销已经写进去的内容。
    assert (tmp_path / "雪山旅馆/正文/0001 雨夜.md").is_file()


def test_busy_workspace_rejects_a_new_conversation_without_creating_it(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    """新会话遇忙：拒绝且不留空会话。"""
    provider = ScriptedProvider([_text_turn("好")], hang_from=0)
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    first = _accept(client, workspace_id, "先写第一章")

    response = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={"text": "另开一个话题", "request_key": "k_new"},
    )

    assert response.status_code == 409
    listed = _listed(client, workspace_id)
    assert [item["id"] for item in listed["items"]] == [first["conversation_id"]]
    assert listed["active_turn"]["conversation_id"] == first["conversation_id"]
    client.post(f"/api/file-workspaces/{workspace_id}/turns/{first['turn_id']}/stop")


def test_stop_marks_partial_commit(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    provider = ScriptedProvider(
        [
            _tool_turn(
                ToolCall(
                    id="s0",
                    name="set_task_scope",
                    arguments={
                        "mode": "edit",
                        "summary": "写第一章",
                        "authorization": "写第一章",
                        "grants": [{"action": "create", "path": "正文/0001 雨夜.md"}],
                    },
                ),
                ToolCall(
                    id="c1",
                    name="write_file",
                    arguments={"path": "正文/0001 雨夜.md", "text": "# 雨夜\n\n写到一半。\n"},
                ),
            ),
            [],
        ],
        hang_from=1,
    )
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    turn = _accept(client, workspace_id, "写第一章")
    conversation_id = turn["conversation_id"]
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


def test_stop_works_before_the_model_is_called(tmp_path: Path) -> None:
    """受理阶段就能停止：不调用模型，也不留下「轮次不存在」。"""
    workspace = _create_workspace(tmp_path)
    store = ConversationStore(conversation_root(tmp_path / "state", "w1"))
    provider = ScriptedProvider([_text_turn("不该被调用")])
    service = TurnService(workspace, store, lambda: _snapshot(provider))

    async def scenario() -> tuple[bool, list[dict[str, Any]]]:
        info = await service.accept("写第一章", "k1")
        stopped = await service.stop(info.turn_id)
        await service.wait(info.turn_id)
        return stopped, list(store.events(info.conversation_id))

    stopped, events = asyncio.run(scenario())

    assert stopped is True
    assert provider.calls == 0
    assert events[-1]["type"] == "run_finished"
    assert events[-1]["status"] == "stopped"
    assert service.active_turn() is None
    # 中断修复不会把已经停止的轮次再补一遍。
    assert service.mark_interrupted() == []


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
    conversation_id = _accept(client, workspace_id, "写第一章")["conversation_id"]

    events = _wait_until(client, workspace_id, conversation_id, _finished)
    kinds = [event["type"] for event in events]
    assert kinds[0] == "turn_started"
    assert kinds[-1] == "run_finished"
    error = next(event for event in events if event["type"] == "error")
    assert error["code"] == "MODEL_NOT_CONFIGURED"
    assert events[-1]["status"] == "failed"
    assert events[-1]["recording"] == "no_change"


def test_unfinished_turn_is_marked_interrupted_on_restart(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    book = RecentWorkspaces(tmp_path / "state")
    provider = ScriptedProvider([_text_turn("你好")])
    client = TestClient(
        create_workspace_app(workspaces=book, provider_factory=lambda: _snapshot(provider))
    )
    workspace_id = _workspace(client, tmp_path)
    store = ConversationStore(conversation_root(book.root, workspace_id))
    accepted = store.create_with_first_turn(text="写第一章", request_key="k1", turn_id="t_old")
    # 崩在工具阶段：最后一条不是 turn_started 也要识别出来。
    store.append(
        accepted.conversation_id, {"type": "tool_started", "turn_id": "t_old", "tool": "read_file"}
    )

    restarted = TestClient(
        create_workspace_app(workspaces=book, provider_factory=lambda: _snapshot(provider))
    )
    events = _events(restarted, workspace_id, accepted.conversation_id)

    assert events[-1]["type"] == "run_finished"
    assert events[-1]["status"] == "interrupted"
    assert events[-1]["recording"] == "unrecorded"

    # 再启动一次不会补第二条中断。
    again = TestClient(
        create_workspace_app(workspaces=book, provider_factory=lambda: _snapshot(provider))
    )
    events = _events(again, workspace_id, accepted.conversation_id)
    assert len([e for e in events if e["type"] == "run_finished"]) == 1


def test_restart_recovers_reply_after_truncated_log_tail(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    """日志尾部留着半条记录时，补出来的正式回复仍要落地并进入下一轮上下文。"""
    book = RecentWorkspaces(tmp_path / "state")
    provider = ScriptedProvider([_text_turn("你好")])
    client = TestClient(
        create_workspace_app(workspaces=book, provider_factory=lambda: _snapshot(provider))
    )
    workspace_id = _workspace(client, tmp_path)
    store = ConversationStore(conversation_root(book.root, workspace_id))
    accepted = store.create_with_first_turn(text="写第一章", request_key="k1", turn_id="t_old")
    conversation_id = accepted.conversation_id
    # 崩之前：内容块已经收尾，只差那条正式回复与响应终态。
    store.append_batch(
        conversation_id,
        [
            {"type": "assistant_started", "turn_id": "t_old", "message_id": "m1"},
            {
                "type": "assistant_block_started",
                "turn_id": "t_old",
                "message_id": "m1",
                "block_id": "b1",
                "kind": "text",
                "order": 0,
            },
            {
                "type": "assistant_block_delta",
                "turn_id": "t_old",
                "message_id": "m1",
                "block_id": "b1",
                "text": "雪停了。",
                "part": 0,
            },
            {
                "type": "assistant_block_finished",
                "turn_id": "t_old",
                "message_id": "m1",
                "block_id": "b1",
                "kind": "text",
                "order": 0,
                "text": "雪停了。",
                "completion": "completed",
                "duration_ms": 12,
            },
        ],
    )
    # 写到一半断电：尾部留下一条没有换行、也无法解析的记录。
    with store.events_file(conversation_id).open("a", encoding="utf-8", newline="\n") as stream:
        stream.write('{"seq": 6, "at": "2026-10-21T10:00:00+08:00", "type": "assistant_mess')

    restarted = TestClient(
        create_workspace_app(workspaces=book, provider_factory=lambda: _snapshot(provider))
    )
    events = _events(restarted, workspace_id, conversation_id)

    replies = [event["text"] for event in events if event["type"] == "assistant_message"]
    assert replies == ["雪停了。"]
    assert events[-1]["type"] == "run_finished"
    assert events[-1]["status"] == "interrupted"
    # 下一轮模型上下文要带上这段回复，而不是只剩执行结果。
    replayed = [
        "".join(block.text for block in message.content)
        for message in replay_public_messages(events)
        if isinstance(message, AssistantMessage)
    ]
    assert replayed == ["雪停了。"]


# -- 请求键与事件流 -------------------------------------------------------


def test_request_key_is_idempotent(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    provider = ScriptedProvider([_text_turn("好")])
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    first = _accept(client, workspace_id, "你好", key="k1")
    _wait_until(client, workspace_id, first["conversation_id"], _finished)

    second = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations/{first['conversation_id']}/turns",
        json={"text": "你好", "request_key": "k1"},
    )

    assert second.json()["turn_id"] == first["turn_id"]
    assert provider.calls == 1


def test_event_stream_replays_and_stops_at_run_finished(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    provider = ScriptedProvider([_text_turn("好")])
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    conversation_id = _accept(client, workspace_id, "你好")["conversation_id"]
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
    # 连接在整轮结束时关闭：最后一条推送就是 run_finished，不会悬着等下一轮。
    last = [chunk for chunk in body.split("\n\n") if chunk.strip()][-1]
    assert '"type": "run_finished"' in last


def test_event_stream_isolates_conversations(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    """只推送指定会话：另一个会话的事件不会混进来。"""
    provider = ScriptedProvider([_text_turn("A"), _text_turn("B")])
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    first = _accept(client, workspace_id, "先聊 A")
    _wait_until(client, workspace_id, first["conversation_id"], _finished)
    second = _accept(client, workspace_id, "再聊 B")
    _wait_until(client, workspace_id, second["conversation_id"], _finished)

    response = client.get(
        f"/api/file-workspaces/{workspace_id}/events",
        params={"conversation_id": second["conversation_id"], "after": 0, "follow": False},
    )

    body = response.text
    assert "再聊 B" in body
    assert "先聊 A" not in body


# -- 改名与界面状态 -------------------------------------------------------


def test_rename_keeps_author_title(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    provider = ScriptedProvider([_text_turn("好"), _text_turn("好")])
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    accepted = _accept(client, workspace_id, "随便说点什么")
    conversation_id = accepted["conversation_id"]
    _wait_until(client, workspace_id, conversation_id, _finished)

    renamed = client.patch(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}",
        json={"title": "人物动机"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "人物动机"

    _send(client, workspace_id, conversation_id, "继续")
    _wait_until(
        client,
        workspace_id,
        conversation_id,
        lambda events: len([e for e in events if e["type"] == "run_finished"]) == 2,
    )
    # 自动标题只在首次受理时生成，后续轮次不覆盖作者改过的标题。
    assert _listed(client, workspace_id)["items"][0]["title"] == "人物动机"


def test_conversation_ui_keeps_drafts_and_recent(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    provider = ScriptedProvider([_text_turn("好")])
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    accepted = _accept(client, workspace_id, "写第一章")
    _wait_until(client, workspace_id, accepted["conversation_id"], _finished)
    url = f"/api/file-workspaces/{workspace_id}/conversation-ui"

    first = client.get(url).json()
    assert first["drafts"] == {}
    saved = client.patch(
        url,
        json={"set_draft": True, "draft_target": "new", "draft_text": "还没发出去的想法"},
        headers={"If-Match": first["revision"]},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["drafts"] == {"new": "还没发出去的想法"}

    # 过期版本被拒绝：不能让旧窗口的请求覆盖新草稿。
    stale = client.patch(
        url,
        json={"set_draft": True, "draft_target": "new", "draft_text": "旧窗口的内容"},
        headers={"If-Match": first["revision"]},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "CONVERSATION_UI_CONFLICT"

    chosen = client.patch(
        url,
        json={"set_recent": True, "recent_id": accepted["conversation_id"]},
        headers={"If-Match": saved.json()["revision"]},
    )
    assert chosen.json()["recent_id"] == accepted["conversation_id"]
    assert _listed(client, workspace_id)["recent_id"] == accepted["conversation_id"]

    cleared = client.patch(
        url,
        json={"set_draft": True, "draft_target": "new", "draft_text": ""},
        headers={"If-Match": chosen.json()["revision"]},
    )
    assert cleared.json()["drafts"] == {}


def _create_workspace(tmp_path: Path) -> Any:
    from hastings.workspace.files import Workspace

    return Workspace.create(tmp_path / "雪山旅馆")


# -- 本轮材料 ---------------------------------------------------------------


@pytest.mark.parametrize("length", [40, 3200])
def test_reference_preparation_matches_actual_request_on_first_and_followup(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient], length: int
) -> None:
    from tau_agent.messages import message_text

    workspace = _create_workspace(tmp_path)
    text = "参考唯一标记🌧️" + "正文" * length
    ref = workspace.write("正文/0001 参考章.md", text, expected_digest=None)
    provider = ScriptedProvider([_text_turn("已查看。"), _text_turn("继续查看。")])
    client = client_factory(lambda: _snapshot(provider))
    wid = _workspace(client, tmp_path)
    context = {"reference_paths": [ref.path], "observed_digests": {ref.path: ref.digest}}
    first = _accept(client, wid, "请查看参考。", context=context)
    _wait_until(client, wid, first["conversation_id"], _finished)
    second = client.post(
        f"/api/file-workspaces/{wid}/conversations/{first['conversation_id']}/turns",
        json={"text": "继续查看参考。", "request_key": "followup-ref", "context": context},
    )
    assert second.status_code == 202, second.text
    turn_id = second.json()["turn_id"]
    events = _wait_until(
        client,
        wid,
        first["conversation_id"],
        lambda rows: any(
            row["type"] == "run_finished" and row["turn_id"] == turn_id for row in rows
        ),
    )
    for request in provider.requests:
        blocks = [
            message_text(m) for m in request["messages"] if "应用提供的参考原文" in message_text(m)
        ]
        assert len(blocks) == 1
        supplied = json.loads(blocks[0].split("\n", 1)[1])[0]
        assert supplied["text"] == text[:2000]
        assert supplied["digest"] == ref.digest
        assert supplied["end"] == len(supplied["text"])
    assert len(provider.requests) == 2
    assert all(
        row["provided"][0]["end"] == min(2000, len(text))
        for row in events
        if row["type"] == "context_prepared"
    )


def test_reference_selection_limits_write_until_read(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    """作者指定参考后：先给开头一段，写作品前必须读完；未读完的写入被拒绝。"""
    workspace = _create_workspace(tmp_path)
    reference = workspace.write(
        "正文/0001 参考章.md",
        "# 参考章\n\n" + "书房里的脚印。" * 600 + "\n",
        expected_digest=None,
    )
    provider = ScriptedProvider(
        [
            _tool_turn(
                ToolCall(
                    id="s0",
                    name="set_task_scope",
                    arguments={
                        "mode": "edit",
                        "summary": "参考第一章写第三章",
                        "authorization": "参考第一章",
                        "grants": [{"action": "create", "path": "正文/0003 新章.md"}],
                    },
                ),
                ToolCall(
                    id="c1",
                    name="write_file",
                    arguments={"path": "正文/0003 新章.md", "text": "# 新章\n"},
                ),
            ),
            _text_turn("参考还没读完。"),
        ]
    )
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    accepted = _accept(
        client,
        workspace_id,
        "参考第一章写第三章",
        context={
            "reference_paths": [reference.path],
            "observed_digests": {reference.path: reference.digest},
        },
    )
    events = _wait_until(client, workspace_id, accepted["conversation_id"], _finished)

    prepared = next(event for event in events if event["type"] == "context_prepared")
    assert prepared["references"] == [{"path": reference.path, "digest": reference.digest}]
    provided = prepared["provided"][0]
    assert provided["start"] == 0
    assert provided["end"] < provided["length"]

    failed = next(event for event in events if event["type"] == "tool_finished" and not event["ok"])
    assert "REFERENCE_NOT_FULLY_READ_CONFLICT" in failed["summary"]
    assert not (tmp_path / "雪山旅馆/正文/0003 新章.md").exists()

    # 材料状态写进系统提示：模型知道只提供了部分原文，而不是读过全文。
    assert "已提供部分原文" in provider.requests[0]["system"]


def test_same_request_key_with_different_materials_conflicts(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    """同键重试必须带同一份材料：换了参考就是另一个请求，不能复用旧受理。"""
    provider = ScriptedProvider([_text_turn("好"), _text_turn("好")])
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    root = tmp_path / "雪山旅馆"
    (root / "正文/0001 雨夜.md").write_text("# 雨夜\n", encoding="utf-8")
    first = _accept(client, workspace_id, "写第一章", key="fixed")
    _wait_until(client, workspace_id, first["conversation_id"], _finished)

    changed = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={
            "text": "写第一章",
            "request_key": "fixed",
            "context": {"reference_paths": ["正文/0001 雨夜.md"]},
        },
    )
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "REQUEST_KEY_CONFLICT"

    same = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={"text": "写第一章", "request_key": "fixed"},
    )
    assert same.status_code == 202
    assert same.json()["turn_id"] == first["turn_id"]


def test_index_turn_publishes_and_hides_write_tools(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    """索引任务：只用只读工具与 save_chapter_index，发布后接口能看到有效索引。"""
    chapter = "正文/0003 书房.md"
    text = "# 书房\n\n侦探在书房发现一枚脚印。\n"
    quote = "侦探在书房发现一枚脚印。"
    root = tmp_path / "雪山旅馆"
    workspace = _create_workspace(tmp_path)
    created = workspace.write(chapter, text, expected_digest=None)
    start = text.index(quote)

    provider = ScriptedProvider(
        [
            _tool_turn(ToolCall(id="r1", name="read_file", arguments={"path": chapter})),
            _tool_turn(
                ToolCall(
                    id="i1",
                    name="save_chapter_index",
                    arguments={
                        "path": chapter,
                        "digest": created.digest,
                        "summary": "侦探在书房发现脚印。",
                        "evidence": [
                            {"index": 1, "start": start, "end": start + len(quote), "quote": quote}
                        ],
                        "events": [{"text": "发现脚印", "evidence": [1]}],
                        "clues": [{"text": "脚印", "evidence": [1]}],
                    },
                )
            ),
            _text_turn("索引更新好了。"),
        ]
    )
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = str(
        client.post("/api/file-workspaces", json={"path": str(root), "create": False}).json()["id"]
    )
    response = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={
            "text": "更新章节索引：书房",
            "request_key": "idx-1",
            "purpose": "update_chapter_index",
            "index_paths": [chapter],
        },
    )
    assert response.status_code == 202, response.text
    events = _wait_until(client, workspace_id, response.json()["conversation_id"], _finished)

    finished = [event for event in events if event["type"] == "tool_finished"]
    assert [event["tool"] for event in finished] == ["read_file", "save_chapter_index"]
    assert all(event["ok"] for event in finished), [e["summary"] for e in finished]
    updated = next(event for event in events if event["type"] == "chapter_index_updated")
    assert updated["path"] == chapter
    assert updated["status"] == "valid"

    index = client.get(f"/api/file-workspaces/{workspace_id}/chapter-index").json()
    assert index["items"][0]["status"] == "valid"
    assert index["items"][0]["summary"] == "侦探在书房发现脚印。"
    # 索引任务不产生作品提交：正文没有被改动。
    assert workspace.read(chapter).digest == created.digest


# -- 修改范围 ---------------------------------------------------------------


def test_reference_does_not_authorize_writing(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    """作者把文件当参考，不等于允许修改：没有登记范围时写入被拒绝，文件不变。"""
    chapter = "正文/0001 雨夜.md"
    text = "# 雨夜\n\n侦探抵达旅馆。\n"
    workspace = _create_workspace(tmp_path)
    created = workspace.write(chapter, text, expected_digest=None)
    provider = ScriptedProvider(
        [
            _tool_turn(
                ToolCall(
                    id="w1",
                    name="write_file",
                    arguments={
                        "path": chapter,
                        "text": "# 雨夜\n\n改写。\n",
                        "digest": created.digest,
                    },
                )
            ),
            _text_turn("这一章的结构是先抑后扬。"),
        ]
    )
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    accepted = _accept(
        client,
        workspace_id,
        "参考第一章，说说结构",
        context={"reference_paths": [chapter]},
    )
    events = _wait_until(client, workspace_id, accepted["conversation_id"], _finished)

    kinds = [event["type"] for event in events]
    assert "context_prepared" in kinds
    assert "task_scope_set" not in kinds
    failed = next(event for event in events if event["type"] == "tool_finished" and not event["ok"])
    assert "TASK_SCOPE_REQUIRED_CONFLICT" in failed["summary"]
    assert workspace.read(chapter).text == text


def test_scope_is_recorded_and_writes_follow_it(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    """登记范围后写入成功；范围事件带任务理解、授权原话与确切路径。"""
    provider = ScriptedProvider(
        [
            _tool_turn(
                ToolCall(
                    id="s0",
                    name="set_task_scope",
                    arguments={
                        "mode": "edit",
                        "summary": "写第三章的问询",
                        "authorization": "只改第三章",
                        "grants": [{"action": "create", "path": "正文/0003 书房.md"}],
                        "denied_paths": ["设定集/角色小传/林远.md"],
                    },
                ),
                ToolCall(
                    id="c1",
                    name="write_file",
                    arguments={"path": "正文/0003 书房.md", "text": "# 书房\n\n问询。\n"},
                ),
            ),
            _text_turn("第三章写好了。"),
        ]
    )
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    accepted = _accept(client, workspace_id, "只改第三章，写问询")
    events = _wait_until(client, workspace_id, accepted["conversation_id"], _finished)

    scope = next(event for event in events if event["type"] == "task_scope_set")
    assert scope["version"] == 1
    assert scope["summary"] == "写第三章的问询"
    assert scope["authorization"] == "只改第三章"
    assert scope["grants"] == [{"action": "create", "path": "正文/0003 书房.md"}]
    assert scope["denied_paths"] == ["设定集/角色小传/林远.md"]
    assert all(event["ok"] for event in events if event["type"] == "tool_finished")
    assert (tmp_path / "雪山旅馆/正文/0003 书房.md").is_file()


# -- 写后检查 ---------------------------------------------------------------


def test_review_turn_publishes_report(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    """显式检查任务：只读工具加保存报告，发布后接口能回看。"""
    chapter = "正文/0003 书房.md"
    text = "# 书房\n\n侦探在书房发现一枚脚印。\n\n林远说他一直在客厅。\n"
    quote = "侦探在书房发现一枚脚印。"
    workspace = _create_workspace(tmp_path)
    created = workspace.write(chapter, text, expected_digest=None)
    start = text.index(quote)
    provider = ScriptedProvider(
        [
            _tool_turn(ToolCall(id="r1", name="read_file", arguments={"path": chapter})),
            _tool_turn(
                ToolCall(
                    id="v1",
                    name="save_review_report",
                    arguments={
                        "target_paths": [chapter],
                        "requirement": "检查时间线",
                        "status": "findings",
                        "findings": [
                            {
                                "kind": "continuity",
                                "certainty": "supported",
                                "description": "不在场说法与书房脚印冲突。",
                                "suggestion": "补一句解释。",
                                "evidence": [
                                    {
                                        "path": chapter,
                                        "digest": created.digest,
                                        "start": start,
                                        "end": start + len(quote),
                                        "quote": quote,
                                    }
                                ],
                            }
                        ],
                    },
                )
            ),
            _text_turn("检查完成。"),
        ]
    )
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    response = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={
            "text": "检查第三章的时间线",
            "request_key": "rev-1",
            "purpose": "review",
            "review_paths": [chapter],
        },
    )
    assert response.status_code == 202, response.text
    events = _wait_until(client, workspace_id, response.json()["conversation_id"], _finished)

    assert any(event["type"] == "review_requested" for event in events)
    finished = next(event for event in events if event["type"] == "review_finished")
    assert finished["status"] == "findings"
    assert finished["findings"][0]["description"] == "不在场说法与书房脚印冲突。"
    assert [event["tool"] for event in events if event["type"] == "tool_finished"] == [
        "read_file",
        "save_review_report",
    ]
    assert events[-1]["status"] == "completed"

    reports = client.get(f"/api/file-workspaces/{workspace_id}/reviews").json()
    assert reports["items"][0]["freshness"] == "valid"
    # 检查不产生作品改动，也不产生提交。
    assert workspace.read(chapter).digest == created.digest


def test_write_then_check_enters_readonly_phase(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    """作者说「改完检查」：修改落盘后同一轮进入只读检查，工具换成检查工具集。"""
    chapter = "正文/0003 书房.md"
    text = "# 书房\n\n侦探在书房发现一枚脚印。\n"
    workspace = _create_workspace(tmp_path)
    created = workspace.write(chapter, text, expected_digest=None)
    provider = ScriptedProvider(
        [
            _tool_turn(
                ToolCall(id="r0", name="read_file", arguments={"path": chapter}),
                ToolCall(
                    id="s0",
                    name="set_task_scope",
                    arguments={
                        "mode": "edit",
                        "summary": "改写书房一段并检查时间线",
                        "authorization": "改完检查时间线",
                        "grants": [{"action": "update", "path": chapter}],
                    },
                ),
                ToolCall(
                    id="q1",
                    name="request_review",
                    arguments={
                        "requirement": "检查时间线是否自相矛盾",
                        "authorization": "改完检查时间线",
                    },
                ),
                ToolCall(
                    id="w1",
                    name="write_file",
                    arguments={
                        "path": chapter,
                        "text": "# 书房\n\n侦探在书房发现两枚脚印。\n",
                        "digest": created.digest,
                    },
                ),
            ),
            _text_turn("改好了。"),
            _tool_turn(
                ToolCall(id="r2", name="read_file", arguments={"path": chapter}),
                ToolCall(
                    id="v2",
                    name="save_review_report",
                    arguments={
                        "target_paths": [chapter],
                        "requirement": "检查时间线是否自相矛盾",
                        "status": "no_findings",
                    },
                ),
            ),
            _text_turn("检查完成，时间线一致。"),
        ]
    )
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    _seed_review_states(workspace, conversation_root(tmp_path / "state", workspace_id))
    accepted = _accept(client, workspace_id, "只改第三章，改完检查时间线")
    events = _wait_until(client, workspace_id, accepted["conversation_id"], _finished)

    tools = [event["tool"] for event in events if event["type"] == "tool_finished"]
    assert tools == [
        "read_file",
        "set_task_scope",
        "request_review",
        "write_file",
        "read_file",
        "save_review_report",
    ]
    assert all(event["ok"] for event in events if event["type"] == "tool_finished"), [
        (e["tool"], e["summary"]) for e in events if e["type"] == "tool_finished"
    ]
    review = next(event for event in events if event["type"] == "review_finished")
    assert review["status"] == "no_findings"
    assert events[-1]["status"] == "completed"
    # 修改保留：检查阶段不撤销已经写入的内容。
    assert "两枚脚印" in workspace.read(chapter).text
    review_system = provider.requests[2]["system"]
    assert "已死亡" in review_system and "第一章深夜" in review_system
    assert "尚未回收的线索" in review_system
    injections = [e for e in events if e["type"] == "state_injected"]
    assert [e["profile"] for e in injections] == ["writer", "review"]
    assert {"roster", "timeline", "clues"} <= {p["key"] for p in injections[-1]["parts"]}


def _seed_review_states(workspace: Any, root: Path) -> None:
    from hastings.workspace.character_states import (
        CharacterStateStore,
        StateSnapshot,
        publish_states,
    )
    from hastings.workspace.clues import ClueEntry, ClueStore, publish_clues
    from hastings.workspace.disclosure import ObservedEvidence
    from hastings.workspace.timeline import TimelineEntry, TimelineStore, publish_timeline

    person = "设定集/角色小传/林静.md"
    workspace.write(person, "# 林静\n", expected_digest=None)
    source = workspace.write(
        "正文/0001 雨夜.md", "林静死在书房，门边留有脚印。", expected_digest=None
    )
    evidence = ObservedEvidence(source.path, source.digest, 0, len(source.text), source.text)
    publish_states(
        workspace,
        CharacterStateStore(root),
        (StateSnapshot(person, workspace.read(person).digest, 1, "书房", False, "", evidence),),
    )
    publish_timeline(
        workspace, TimelineStore(root), (TimelineEntry(1, "第一章深夜", "", "书房", evidence),)
    )
    publish_clues(workspace, ClueStore(root), (ClueEntry("门边脚印", 1, 3, "open", "", evidence),))


@pytest.mark.parametrize(
    "context", [{"state_chapter": 1}, {"selected_path": "正文/0001 雨夜.md"}, {}]
)
def test_state_cutoff_reaches_model_and_request_identity(
    tmp_path: Path,
    client_factory: Callable[[ProviderFactory], TestClient],
    context: dict[str, Any],
) -> None:
    workspace = _create_workspace(tmp_path)
    workspace.write("正文/0001 雨夜.md", "第一章。", expected_digest=None)
    workspace.write("正文/0009 揭晓.md", "第九章。", expected_digest=None)
    workspace.write(
        "设定集/角色小传/林静.md", "---\nrevealed: [9:秘密身份]\n---\n# 林静", expected_digest=None
    )
    provider = ScriptedProvider([_text_turn("只讨论。")])
    client = client_factory(lambda: _snapshot(provider))
    workspace_id = _workspace(client, tmp_path)
    accepted = _accept(client, workspace_id, "只讨论第一章。", context=context, key="cutoff")
    events = _wait_until(client, workspace_id, accepted["conversation_id"], _finished)
    assert events[-1]["status"] == "completed"
    assert "秘密身份" not in provider.requests[0]["system"]
    changed = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={
            "text": "只讨论第一章。",
            "request_key": "cutoff",
            "context": {"state_chapter": 9},
        },
    )
    assert changed.status_code == 409


def test_check_phase_shares_the_turn_budget(tmp_path: Path) -> None:
    """写后检查不重新拿满回合：写作阶段用完了，就不再进入检查阶段。"""
    chapter = "正文/0003 书房.md"
    workspace = _create_workspace(tmp_path)
    GitRepo(workspace.root).init()
    created = workspace.write(chapter, "# 书房\n\n侦探在书房发现一枚脚印。\n", expected_digest=None)
    provider = ScriptedProvider(
        [
            _tool_turn(
                ToolCall(id="r0", name="read_file", arguments={"path": chapter}),
                ToolCall(
                    id="s0",
                    name="set_task_scope",
                    arguments={
                        "mode": "edit",
                        "summary": "改写书房一段并检查时间线",
                        "authorization": "改完检查时间线",
                        "grants": [{"action": "update", "path": chapter}],
                    },
                ),
                ToolCall(
                    id="q1",
                    name="request_review",
                    arguments={
                        "requirement": "检查时间线是否自相矛盾",
                        "authorization": "改完检查时间线",
                    },
                ),
                ToolCall(
                    id="w1",
                    name="write_file",
                    arguments={
                        "path": chapter,
                        "text": "# 书房\n\n侦探在书房发现两枚脚印。\n",
                        "digest": created.digest,
                    },
                ),
            ),
            _text_turn("改好了。"),
            _tool_turn(
                ToolCall(id="r2", name="read_file", arguments={"path": chapter}),
                ToolCall(
                    id="v2",
                    name="save_review_report",
                    arguments={
                        "target_paths": [chapter],
                        "requirement": "检查时间线是否自相矛盾",
                        "status": "no_findings",
                    },
                ),
            ),
            _text_turn("检查完成，时间线一致。"),
        ]
    )
    store = ConversationStore(conversation_root(tmp_path / "state", "w1"))
    service = TurnService(workspace, store, lambda: _snapshot(provider), max_turns=2)

    async def scenario() -> list[dict[str, Any]]:
        info = await service.accept("只改第三章，改完检查时间线", "k1")
        await service.wait(info.turn_id)
        return list(store.events(info.conversation_id))

    events = asyncio.run(scenario())

    # 两个回合都属于写作阶段；检查阶段没有剩余回合，不能再调用模型。
    assert provider.calls == 2
    review = next(event for event in events if event["type"] == "review_finished")
    assert review["status"] == "incomplete"
    assert "回合" in review["uncovered"][0]
    assert review["targets"] == [chapter]
    assert events[-1]["status"] == "partial"
    # 写作阶段的结果保留：检查没进入不能撤销已落盘的修改。
    assert "两枚脚印" in workspace.read(chapter).text


@pytest.mark.parametrize("failure", ["limit", "scope"])
def test_review_failure_has_actionable_reason(tmp_path: Path, failure: str) -> None:
    chapter = "正文/0003 书房.md"
    workspace = _create_workspace(tmp_path)
    workspace.write(chapter, "侦探来到书房。", expected_digest=None)
    if failure == "limit":
        turns = [_tool_turn(ToolCall(id="r1", name="read_file", arguments={"path": chapter}))]
        expected = "TURN_LIMIT_REACHED"
    else:
        turns = [
            _tool_turn(
                ToolCall(
                    id=f"s{i}",
                    name="save_review_report",
                    arguments={
                        "target_paths": ["正文/9999 不存在.md"],
                        "requirement": "检查时间线",
                        "status": "no_findings",
                    },
                )
            )
            for i in range(3)
        ]
        turns.append(_text_turn("结束。"))
        expected = "REVIEW_SCOPE_INVALID"
    provider = ScriptedProvider(turns)
    store = ConversationStore(conversation_root(tmp_path / "state", "w1"))
    service = TurnService(
        workspace, store, lambda: _snapshot(provider), max_turns=1 if failure == "limit" else 8
    )

    async def scenario() -> list[dict[str, Any]]:
        info = await service.accept(
            "检查第三章", "review-failure", purpose="review", review_paths=[chapter]
        )
        await service.wait(info.turn_id)
        return list(store.events(info.conversation_id))

    events = asyncio.run(scenario())
    error = next(event for event in events if event["type"] == "error")
    assert error["code"] == expected
    assert "api_key" not in error["message"]
    if failure == "scope":
        assert "3 次" in error["message"]
    review = next(event for event in events if event["type"] == "review_finished")
    assert review["status"] == "incomplete"
    assert review["targets"] == [chapter]
    assert expected in review["uncovered"][0]
    assert events[-1]["status"] == "failed"


@pytest.mark.parametrize("failure", ["limit", "scope"])
def test_failed_review_preserves_written_changes(tmp_path: Path, failure: str) -> None:
    chapter = "正文/0003 书房.md"
    workspace = _create_workspace(tmp_path)
    GitRepo(workspace.root).init()
    created = workspace.write(chapter, "侦探来到书房。", expected_digest=None)
    turns = [
        _tool_turn(
            ToolCall(id="read", name="read_file", arguments={"path": chapter}),
            ToolCall(
                id="scope",
                name="set_task_scope",
                arguments={
                    "mode": "edit",
                    "summary": "改写并检查",
                    "authorization": "改完检查时间线",
                    "grants": [{"action": "update", "path": chapter}],
                },
            ),
            ToolCall(
                id="request",
                name="request_review",
                arguments={
                    "requirement": "检查时间线",
                    "authorization": "改完检查时间线",
                },
            ),
            ToolCall(
                id="write",
                name="write_file",
                arguments={
                    "path": chapter,
                    "text": "侦探发现两枚脚印。",
                    "digest": created.digest,
                },
            ),
        ),
        _text_turn("改好了。"),
    ]
    if failure == "limit":
        turns.append(_tool_turn(ToolCall(id="r2", name="read_file", arguments={"path": chapter})))
        expected = "TURN_LIMIT_REACHED"
    else:
        turns.extend(
            [
                _tool_turn(
                    ToolCall(
                        id=f"s{i}",
                        name="save_review_report",
                        arguments={
                            "target_paths": ["正文/9999 不存在.md"],
                            "requirement": "检查时间线",
                            "status": "no_findings",
                        },
                    )
                )
                for i in range(3)
            ]
        )
        turns.append(_text_turn("结束。"))
        expected = "REVIEW_SCOPE_INVALID"
    store = ConversationStore(conversation_root(tmp_path / "state", "w1"))
    service = TurnService(
        workspace,
        store,
        lambda: _snapshot(ScriptedProvider(turns)),
        max_turns=3 if failure == "limit" else 8,
    )

    async def scenario() -> list[dict[str, Any]]:
        info = await service.accept("只改第三章，改完检查时间线", "write-review-failure")
        await service.wait(info.turn_id)
        return list(store.events(info.conversation_id))

    events = asyncio.run(scenario())
    review = next(event for event in events if event["type"] == "review_finished")
    assert review["status"] == "incomplete"
    assert review["targets"] == [chapter]
    assert expected in review["uncovered"][0]
    assert "api_key" not in review["uncovered"][0]
    assert events[-1]["status"] == "partial"
    assert events[-1]["recording"] == "committed"
    assert workspace.read(chapter).text == "侦探发现两枚脚印。"


def test_review_can_correct_target_after_scope_rejection(tmp_path: Path) -> None:
    chapter = "正文/0003 书房.md"
    workspace = _create_workspace(tmp_path)
    workspace.write(chapter, "侦探来到书房。", expected_digest=None)
    provider = ScriptedProvider(
        [
            _tool_turn(
                ToolCall(
                    id="bad",
                    name="save_review_report",
                    arguments={
                        "target_paths": ["正文/9999 不存在.md"],
                        "requirement": "检查时间线",
                        "status": "no_findings",
                    },
                )
            ),
            _tool_turn(ToolCall(id="read", name="read_file", arguments={"path": chapter})),
            _tool_turn(
                ToolCall(
                    id="good",
                    name="save_review_report",
                    arguments={
                        "target_paths": [chapter],
                        "requirement": "检查时间线",
                        "status": "no_findings",
                    },
                )
            ),
            _text_turn("检查完成。"),
        ]
    )
    store = ConversationStore(conversation_root(tmp_path / "state", "w1"))
    service = TurnService(workspace, store, lambda: _snapshot(provider))

    async def scenario() -> list[dict[str, Any]]:
        info = await service.accept(
            "检查第三章", "correct-review-scope", purpose="review", review_paths=[chapter]
        )
        await service.wait(info.turn_id)
        return list(store.events(info.conversation_id))

    events = asyncio.run(scenario())
    rejected = next(e for e in events if e["type"] == "tool_finished" and not e["ok"])
    assert chapter in rejected["summary"]
    assert events[-1]["status"] == "completed"
    review = next(e for e in events if e["type"] == "review_finished")
    assert review["status"] == "no_findings"
    tool = next(t for t in provider.requests[0]["tools"] if t.name == "save_review_report")
    assert tool.parameters["properties"]["target_paths"]["items"]["enum"] == [chapter]


# -- 展开灵感（0.8 I4） ------------------------------------------------------


def _expand_service(tmp_path: Path, provider: Any) -> tuple[Any, TurnService]:
    from hastings.workspace.files import Workspace

    workspace = Workspace.create(tmp_path / "雪山旅馆")
    store = ConversationStore(conversation_root(tmp_path / "state", "w1"))
    return workspace, TurnService(workspace, store, lambda: _snapshot(provider))


def _tool_names(provider: ScriptedProvider) -> set[str]:
    names: set[str] = set()
    for tool in provider.requests[0]["tools"]:
        name = getattr(tool, "name", None)
        if name is None and isinstance(tool, dict):
            name = tool.get("name")
        if isinstance(name, str):
            names.add(name)
    return names


def test_expand_idea_accepts_one_fixed_revision_and_stays_read_only(tmp_path: Path) -> None:
    """一次展开只带一条灵感的固定版本；交给模型的工具里没有任何写入能力。"""
    from hastings.workspace.ideas import IdeaStore
    from hastings.workspace.materials import ContextRequest

    provider = ScriptedProvider([_text_turn("三个方向：追查、放弃、反用。")])
    workspace, service = _expand_service(tmp_path, provider)
    idea = IdeaStore(workspace).create(
        body="灯塔在退潮时才会露出第二层台阶。", request_key="idea-expand-1"
    )

    async def scenario() -> tuple[Any, list[dict[str, Any]]]:
        info = await service.accept(
            "展开这条灵感",
            "k-expand-1",
            context=ContextRequest(idea_references=((idea["idea_id"], idea["revision_id"]),)),
            purpose="expand_idea",
        )
        await service.wait(info.turn_id)
        return info, list(service.store.events(info.conversation_id))

    info, events = asyncio.run(scenario())

    started = next(event for event in events if event.get("type") == "turn_started")
    assert started["purpose"] == "expand_idea"
    assert started["context"]["idea_references"] == [[idea["idea_id"], idea["revision_id"]]]
    assert started["context"]["reference_paths"] == []
    assert started["context"]["selected_path"] is None

    # 模型实际收到的工具：只读 + 灵感工具，没有写文件、装配、发布与索引。
    names = _tool_names(provider)
    assert {"read_idea", "read_file", "search_text"} <= names
    assert not names & {
        "write_file",
        "edit_file",
        "move_file",
        "delete_file",
        "save_review_report",
        "save_chapter_index",
        "attach_module",
        "detach_module",
        "publish_module",
    }
    # 上下文只装任务要求与那条灵感，不装写作约定与旧索引。
    system = str(provider.requests[0]["system"])
    assert "展开成可讨论的方案" in system
    assert "灯塔在退潮时" in system
    assert "写作约定" not in system


def test_expand_idea_rejects_missing_or_extra_materials(tmp_path: Path) -> None:
    from hastings.workspace.ideas import IdeaStore
    from hastings.workspace.materials import ContextRequest

    provider = ScriptedProvider([])
    workspace, service = _expand_service(tmp_path, provider)
    store = IdeaStore(workspace)
    first = store.create(body="第一条。", request_key="idea-expand-2")
    second = store.create(body="第二条。", request_key="idea-expand-3")
    workspace.write("正文/0001 雨夜.md", "# 雨夜\n\n侦探抵达。\n", expected_digest=None)

    async def scenario() -> None:
        with pytest.raises(WorkspaceError) as missing:
            await service.accept("展开", "k-expand-2", purpose="expand_idea")
        assert missing.value.code == "EXPAND_SCOPE_INVALID"

        with pytest.raises(WorkspaceError) as two:
            await service.accept(
                "展开",
                "k-expand-3",
                context=ContextRequest(
                    idea_references=(
                        (first["idea_id"], first["revision_id"]),
                        (second["idea_id"], second["revision_id"]),
                    )
                ),
                purpose="expand_idea",
            )
        assert two.value.code == "EXPAND_SCOPE_INVALID"

        with pytest.raises(WorkspaceError) as extra:
            await service.accept(
                "展开",
                "k-expand-4",
                context=ContextRequest(
                    selected_path="正文/0001 雨夜.md",
                    idea_references=((first["idea_id"], first["revision_id"]),),
                ),
                purpose="expand_idea",
            )
        assert extra.value.code == "EXPAND_SCOPE_INVALID"
        assert "当前阅读位置" in extra.value.why

        with pytest.raises(WorkspaceError) as targets:
            await service.accept(
                "展开",
                "k-expand-5",
                context=ContextRequest(idea_references=((first["idea_id"], first["revision_id"]),)),
                purpose="expand_idea",
                index_paths=["正文/0001 雨夜.md"],
            )
        assert targets.value.code == "EXPAND_SCOPE_INVALID"

    asyncio.run(scenario())
    # 全部被拒：没有建会话，也没有调用模型。
    assert provider.calls == 0
    assert service.store.conversations() == ()


def test_expand_idea_reuses_the_same_command_and_rejects_another_revision(
    tmp_path: Path,
) -> None:
    """双击与断线重试回到同一轮；同键换灵感版本是另一次请求，必须冲突。"""
    from hastings.workspace.ideas import IdeaStore
    from hastings.workspace.materials import ContextRequest

    provider = ScriptedProvider([_text_turn("展开结果。")])
    workspace, service = _expand_service(tmp_path, provider)
    store = IdeaStore(workspace)
    idea = store.create(body="最初的版本。", request_key="idea-expand-6")
    edited = store.update(
        str(idea["idea_id"]),
        expected_digest=str(idea["digest"]),
        expected_revision=str(idea["revision_id"]),
        body="改过的版本。",
        request_key="idea-expand-7",
    )

    async def scenario() -> tuple[Any, Any]:
        first = await service.accept(
            "展开这条灵感",
            "k-expand-6",
            context=ContextRequest(idea_references=((idea["idea_id"], idea["revision_id"]),)),
            purpose="expand_idea",
        )
        retry = await service.accept(
            "展开这条灵感",
            "k-expand-6",
            context=ContextRequest(idea_references=((idea["idea_id"], idea["revision_id"]),)),
            purpose="expand_idea",
        )
        await service.wait(first.turn_id)
        return first, retry

    first, retry = asyncio.run(scenario())
    assert (retry.turn_id, retry.conversation_id) == (first.turn_id, first.conversation_id)
    assert provider.calls == 1

    async def conflict() -> None:
        with pytest.raises(WorkspaceError) as excinfo:
            await service.accept(
                "展开这条灵感",
                "k-expand-6",
                context=ContextRequest(
                    idea_references=((edited["idea_id"], edited["revision_id"]),)
                ),
                purpose="expand_idea",
            )
        assert excinfo.value.code == "REQUEST_KEY_CONFLICT"

    asyncio.run(conflict())


def test_expand_idea_cannot_write_the_workspace(tmp_path: Path) -> None:
    """展开模式下模型即使调用写入工具也改不了作品：工具根本不在列表里。"""
    from hastings.workspace.ideas import IdeaStore
    from hastings.workspace.materials import ContextRequest

    provider = ScriptedProvider(
        [
            _tool_turn(
                ToolCall(
                    id="call-write",
                    name="write_file",
                    arguments={"path": "正文/0001 雨夜.md", "text": "被改写的内容"},
                )
            ),
            _text_turn("我没有写入权限，只给出方向。"),
        ]
    )
    workspace, service = _expand_service(tmp_path, provider)
    original = "# 雨夜\n\n侦探抵达旅馆。\n"
    workspace.write("正文/0001 雨夜.md", original, expected_digest=None)
    idea = IdeaStore(workspace).create(body="让雨夜提前结束。", request_key="idea-expand-8")

    async def scenario() -> list[dict[str, Any]]:
        info = await service.accept(
            "展开这条灵感",
            "k-expand-8",
            context=ContextRequest(idea_references=((idea["idea_id"], idea["revision_id"]),)),
            purpose="expand_idea",
        )
        await service.wait(info.turn_id)
        return list(service.store.events(info.conversation_id))

    events = asyncio.run(scenario())

    assert (workspace.root / "正文" / "0001 雨夜.md").read_text(encoding="utf-8") == original
    assert not [event for event in events if event.get("type") == "file_changed"]
    assert provider.calls == 2
