"""1.0.1 P2：操作接口的快照、事件、依据、结果与三项控制。

断言看的是接口实际返回的状态与事件，不看助手文字。模型仍由脚本化 provider 驱动：
这里验证接线与代次，不评估模型写得如何。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.workspace.test_compaction_api import SUMMARY, _seed
from tests.workspace.test_compaction_api import _snapshot as _compact_snapshot

from hastings.server.file_app import create_workspace_app
from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.service import ProviderFactory
from hastings.workspace.state import ConversationStore, RecentWorkspaces, conversation_root
from tau_agent.messages import AssistantMessage, TextContent, message_text
from tau_agent.provider_events import (
    AssistantDoneEvent,
    AssistantStartEvent,
    TextDeltaEvent,
)


def _partial(text: str = "") -> AssistantMessage:
    return AssistantMessage(content=[TextContent(text=text)] if text else [])


class ScriptedProvider:
    def __init__(self, turns: list[list[Any]], *, hang_at: int | None = None) -> None:
        self.turns = turns
        self.hang_at = hang_at
        self.calls = 0

    def stream_response(self, **kwargs: Any) -> Any:
        index = self.calls
        self.calls += 1
        signal = kwargs.get("signal")

        async def generator() -> Any:
            yield AssistantStartEvent(partial=_partial())
            if index < len(self.turns):
                for event in self.turns[index]:
                    yield event
            if self.hang_at is not None and index == self.hang_at:
                while signal is None or not signal.is_cancelled():
                    await asyncio.sleep(0.02)

        return generator()


def snapshot(provider: Any) -> ModelSnapshot:
    return ModelSnapshot(
        connection_id="test", display_name="测试连接", model="fake", provider=provider
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


def open_workspace(client: TestClient, tmp_path: Path) -> str:
    root = tmp_path / "book"
    response = client.post(
        "/api/file-workspaces", json={"path": str(root), "create": not root.exists()}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["id"])


def wait_status(client: TestClient, operation_id: str, statuses: set[str]) -> dict[str, Any]:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        body = client.get(f"/api/operations/{operation_id}").json()
        if body["status"] in statuses:
            return body
        time.sleep(0.05)
    raise AssertionError(f"操作没有进入 {statuses}：{body}")


def test_accept_exposes_operation_and_snapshot_is_pure(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    provider = ScriptedProvider(
        [
            [
                AssistantDoneEvent(
                    reason="stop",
                    message=AssistantMessage(content=[TextContent(text="好")], stop_reason="stop"),
                )
            ]
        ]
    )
    client = client_factory(lambda: snapshot(provider))
    workspace_id = open_workspace(client, tmp_path)

    accepted = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={"text": "讨论开篇", "request_key": "k1", "purpose": "chat"},
    ).json()
    assert accepted["operation_id"].startswith("op_")
    assert accepted["generation"] == 1

    operation = wait_status(client, accepted["operation_id"], {"completed", "needs_attention"})
    calls = provider.calls
    again = client.get(f"/api/operations/{accepted['operation_id']}").json()

    assert again == operation
    # 读取快照不调用模型。
    assert provider.calls == calls
    assert again["capabilities"]["pause"] is False
    assert again["target"] == "写作对话"


def test_pause_and_continue_through_the_api(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    provider = ScriptedProvider(
        [
            [TextDeltaEvent(content_index=0, delta="第一段", partial=_partial("第一段"))],
            [
                AssistantDoneEvent(
                    reason="stop",
                    message=AssistantMessage(
                        content=[TextContent(text="第二段")], stop_reason="stop"
                    ),
                )
            ],
        ],
        hang_at=0,
    )
    client = client_factory(lambda: snapshot(provider))
    workspace_id = open_workspace(client, tmp_path)

    accepted = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={"text": "写开篇", "request_key": "k1", "purpose": "chat"},
    ).json()
    operation_id = accepted["operation_id"]
    wait_status(client, operation_id, {"running"})
    # 等到真的有增量再暂停，避免把「还没开始」当成「暂停成功」。
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        events = client.get(f"/api/operations/{operation_id}/events").json()["events"]
        if any(event["type"] == "assistant_block_delta" for event in events):
            break
        time.sleep(0.05)

    paused = client.post(
        f"/api/operations/{operation_id}/pause", json={"request_key": "p1"}
    ).json()
    assert paused["status"] == "paused"
    assert paused["capabilities"]["continue"] is True
    again = client.post(
        f"/api/operations/{operation_id}/pause", json={"request_key": "p1"}
    ).json()
    assert (again["id"], again["status"], again["generation"]) == (
        paused["id"],
        "paused",
        1,
    )
    # 重复暂停不产生第二次停止记录。
    events = client.get(f"/api/operations/{operation_id}/events").json()["events"]
    assert sum(event["type"] == "stop_requested" for event in events) == 1

    resumed = client.post(
        f"/api/operations/{operation_id}/continue",
        json={"request_key": "c1", "expected_generation": 1},
    ).json()
    assert resumed["generation"] == 2

    finished = wait_status(client, operation_id, {"completed", "needs_attention", "failed"})
    assert finished["status"] == "completed"
    assert finished["generation"] == 2


def test_interject_through_the_api_keeps_author_words(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    provider = ScriptedProvider(
        [
            [TextDeltaEvent(content_index=0, delta="初稿", partial=_partial("初稿"))],
            [
                AssistantDoneEvent(
                    reason="stop",
                    message=AssistantMessage(
                        content=[TextContent(text="改好了")], stop_reason="stop"
                    ),
                )
            ],
        ],
        hang_at=0,
    )
    client = client_factory(lambda: snapshot(provider))
    workspace_id = open_workspace(client, tmp_path)
    accepted = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={"text": "写开篇", "request_key": "k1", "purpose": "chat"},
    ).json()
    operation_id = accepted["operation_id"]
    wait_status(client, operation_id, {"running"})

    changed = client.post(
        f"/api/operations/{operation_id}/interject",
        json={"request_key": "i1", "expected_generation": 1, "text": "语气更克制"},
    ).json()

    assert changed["generation"] == 2
    wait_status(client, operation_id, {"completed", "needs_attention", "failed"})
    events = client.get(f"/api/operations/{operation_id}/events").json()["events"]
    interjections = [event for event in events if event["type"] == "author_interjected"]
    assert [event["text"] for event in interjections] == ["语气更克制"]
    assert interjections[0]["generation"] == 2
    # 旧代次的事件留在原位，标注为第 1 代。
    attempts = [event for event in events if event["type"] == "attempt_started"]
    assert [event["generation"] for event in attempts] == [1, 2]


def test_events_are_annotated_and_stream_closes_after_completion(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    provider = ScriptedProvider(
        [
            [
                AssistantDoneEvent(
                    reason="stop",
                    message=AssistantMessage(content=[TextContent(text="好")], stop_reason="stop"),
                )
            ]
        ]
    )
    client = client_factory(lambda: snapshot(provider))
    workspace_id = open_workspace(client, tmp_path)
    accepted = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={"text": "讨论开篇", "request_key": "k1", "purpose": "chat"},
    ).json()
    wait_status(client, accepted["operation_id"], {"completed", "needs_attention"})

    replay = client.get(f"/api/operations/{accepted['operation_id']}/events").json()
    types = [event["type"] for event in replay["events"]]
    assert types[0] == "turn_started"
    assert types[-1] == "run_finished"
    assert all(event["operation_id"] == accepted["operation_id"] for event in replay["events"])
    assert all(event["generation"] == 1 for event in replay["events"])

    stream = client.get(
        f"/api/operations/{accepted['operation_id']}/stream?after=0&follow=false"
    )
    assert stream.status_code == 200
    assert "turn_started" in stream.text


def test_context_and_result_report_what_actually_happened(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    provider = ScriptedProvider(
        [
            [
                AssistantDoneEvent(
                    reason="stop",
                    message=AssistantMessage(content=[TextContent(text="好")], stop_reason="stop"),
                )
            ]
        ]
    )
    client = client_factory(lambda: snapshot(provider))
    workspace_id = open_workspace(client, tmp_path)
    accepted = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={"text": "讨论开篇", "request_key": "k1", "purpose": "chat"},
    ).json()
    operation_id = accepted["operation_id"]
    wait_status(client, operation_id, {"completed", "needs_attention"})

    context = client.get(f"/api/operations/{operation_id}/context").json()
    assert context["operation_id"] == operation_id
    assert context["model"] == "fake"
    assert context["refs"] == []
    assert context["injections"]

    result = client.get(f"/api/operations/{operation_id}/result").json()
    assert result["status"] in ("completed", "needs_attention")
    assert result["changed_paths"] == []
    assert result["question"] is None


def test_latest_operation_by_conversation(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    """打开会话时按会话取最近一次操作：刷新后据此恢复「继续」入口。"""
    provider = ScriptedProvider(
        [
            [
                AssistantDoneEvent(
                    reason="stop",
                    message=AssistantMessage(
                        content=[TextContent(text="好")], stop_reason="stop"
                    ),
                )
            ]
        ]
    )
    client = client_factory(lambda: snapshot(provider))
    workspace_id = open_workspace(client, tmp_path)
    accepted = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={"text": "讨论开篇", "request_key": "k1", "purpose": "chat"},
    ).json()
    wait_status(client, accepted["operation_id"], {"completed", "needs_attention"})

    items = client.get(
        "/api/operations", params={"conversation_id": accepted["conversation_id"]}
    ).json()

    assert [item["id"] for item in items] == [accepted["operation_id"]]
    assert client.get(
        "/api/operations", params={"conversation_id": "c_missing"}
    ).json() == []


def test_unknown_operation_is_404(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    client = client_factory(lambda: snapshot(ScriptedProvider([])))

    response = client.get("/api/operations/op_missing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "OPERATION_NOT_FOUND"


def test_pause_rejects_reused_key_for_another_control(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    provider = ScriptedProvider(
        [[TextDeltaEvent(content_index=0, delta="写", partial=_partial("写"))]], hang_at=0
    )
    client = client_factory(lambda: snapshot(provider))
    workspace_id = open_workspace(client, tmp_path)
    accepted = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={"text": "写开篇", "request_key": "k1", "purpose": "chat"},
    ).json()
    operation_id = accepted["operation_id"]
    wait_status(client, operation_id, {"running"})
    client.post(f"/api/operations/{operation_id}/pause", json={"request_key": "same"})

    response = client.post(
        f"/api/operations/{operation_id}/continue",
        json={"request_key": "same", "expected_generation": 1},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "OPERATION_REQUEST_CONFLICT"


# -- 压缩与连接测试共用同一张操作表 ------------------------------------------


class SummaryProvider:
    """第一次摘要挂起等待取消，第二次返回合法摘要；用来验证暂停与继续。"""

    def __init__(self) -> None:
        self.calls = 0
        self.requests: list[dict[str, Any]] = []

    def stream_response(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        index = self.calls
        self.calls += 1

        async def generator() -> Any:
            if index == 0:
                while True:
                    await asyncio.sleep(0.02)
            yield AssistantDoneEvent(
                reason="stop",
                message=AssistantMessage(
                    content=[TextContent(text=SUMMARY)], stop_reason="stop"
                ),
            )

        return generator()

    async def aclose(self) -> None:
        return None


def test_compaction_joins_the_unified_operation_api(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    provider = SummaryProvider()
    client = client_factory(lambda: _compact_snapshot(provider))
    workspace_id = open_workspace(client, tmp_path)
    store = ConversationStore(conversation_root(tmp_path / "state", workspace_id))
    conversation_id = _seed(store)

    accepted = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/compact",
        json={"request_key": "rk1"},
    )
    assert accepted.status_code == 202
    operation_id = accepted.json()["operation_id"]

    running = wait_status(client, operation_id, {"running"})
    assert running["purpose"] == "compaction"
    assert running["model"] == "fake-model"
    assert running["capabilities"] == {"pause": True, "continue": False, "interject": True}

    paused = client.post(
        f"/api/operations/{operation_id}/pause", json={"request_key": "p1"}
    ).json()
    assert paused["status"] == "paused"
    assert paused["capabilities"]["continue"] is True
    # 同一个键重复暂停返回同一结果，不产生第二次取消。
    again = client.post(
        f"/api/operations/{operation_id}/pause", json={"request_key": "p1"}
    ).json()
    assert (again["status"], again["generation"]) == ("paused", 1)

    # 事件按操作归属：压缩没有轮次，读取时不会混入会话里的写作轮次。
    events = client.get(f"/api/operations/{operation_id}/events").json()["events"]
    assert events
    assert all(event["operation_id"] == operation_id for event in events)
    assert all(event["turn_id"] == "" for event in events)

    resumed = client.post(
        f"/api/operations/{operation_id}/continue",
        json={"request_key": "c1", "expected_generation": 1, "text": "只保留写作决定"},
    ).json()
    assert resumed["generation"] == 2

    finished = wait_status(client, operation_id, {"completed", "failed", "paused"})
    assert finished["status"] == "completed"

    # 插话的要求确实进入摘要提示，而不是只记在操作日志里。
    prompt = "\n".join(message_text(item) for item in provider.requests[1]["messages"])
    assert "只保留写作决定" in prompt

    result = client.get(f"/api/operations/{operation_id}/result").json()
    receipt = next(item for item in result["receipts"] if item["kind"] == "compaction")
    assert receipt["record"]["items"][0]["quote"] == "别改成第三人称"
    assert receipt["record"]["request_operation_id"] == operation_id
