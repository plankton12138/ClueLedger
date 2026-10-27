"""0.9 C1：手动整理接口。

契约：
- 提交立即返回 202，整理在后台跑；状态用操作标识查询。
- 同一个 ``request_key`` 只受理一次。
- 没有可整理内容时是 ``noop``，不是失败。
- 摘要不成立时是 ``failed`` 且不留压缩记录。
- 操作不属于该会话时是 404，不是空状态。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.server.model_limits import ModelLimitOverride, resolve_model_limits
from hastings.server.provider_config import ProviderProfile
from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.state import ConversationStore, RecentWorkspaces, conversation_root
from tau_agent.messages import AssistantMessage, TextContent
from tau_agent.provider_events import AssistantDoneEvent, AssistantStartEvent

SUMMARY = json.dumps(
    {
        "summary": "作者在改雨夜这一章。",
        "items": [
            {
                "kind": "author_decision",
                "text": "保留第一人称",
                "quote": "别改成第三人称",
                "sources": ["t1"],
            }
        ],
    },
    ensure_ascii=False,
)


class _SummaryProvider:
    def __init__(self, text: str = SUMMARY) -> None:
        self.text = text
        self.calls = 0

    def stream_response(self, **kwargs: Any):
        del kwargs
        self.calls += 1
        text = self.text

        async def generator():
            yield AssistantStartEvent(partial=AssistantMessage())
            yield AssistantDoneEvent(
                reason="stop",
                message=AssistantMessage(content=[TextContent(text=text)], stop_reason="stop"),
            )

        return generator()

    async def aclose(self) -> None:
        return None


def _snapshot(provider: Any) -> ModelSnapshot:
    profile = ProviderProfile(
        name="test",
        base_url="https://api.deepseek.com",
        api="openai-completions",
        default_model="fake-model",
        model_limits={"fake-model": ModelLimitOverride(context_window_tokens=8_000)},
    )
    return ModelSnapshot(
        connection_id="test",
        display_name="测试连接",
        model="fake-model",
        provider=provider,
        limits=resolve_model_limits(profile, "fake-model"),
    )


def _seed(store: ConversationStore, turns: int = 4) -> str:
    accepted = store.create_with_first_turn(
        text="写第一章" + "雨夜" * 400, request_key="k1", turn_id="t1"
    )
    conversation_id = accepted.conversation_id
    store.append_batch(
        conversation_id,
        [
            {"type": "assistant_message", "turn_id": "t1", "text": "第一章写好了" + "雨" * 400},
            {
                "type": "run_finished",
                "turn_id": "t1",
                "status": "completed",
                "recording": "committed",
                "changed_paths": [],
            },
        ],
    )
    for index in range(2, turns + 1):
        store.append_batch(
            conversation_id,
            [
                {"type": "turn_started", "turn_id": f"t{index}", "text": "再写一段" * 200},
                {"type": "assistant_message", "turn_id": f"t{index}", "text": "写好了" * 200},
                {
                    "type": "run_finished",
                    "turn_id": f"t{index}",
                    "status": "completed",
                    "recording": "committed",
                    "changed_paths": [],
                },
            ],
        )
    return conversation_id


def _setup(tmp_path: Path, provider: Any) -> tuple[TestClient, ConversationStore, str, str]:
    state_root = tmp_path / "state"
    workspaces = RecentWorkspaces(state_root)
    client = TestClient(
        create_workspace_app(workspaces=workspaces, provider_factory=lambda: _snapshot(provider))
    )
    opened = client.post(
        "/api/file-workspaces", json={"path": str(tmp_path / "雪山旅馆"), "create": True}
    )
    assert opened.status_code == 200
    workspace_id = str(opened.json()["id"])
    store = ConversationStore(conversation_root(state_root, workspace_id))
    return client, store, workspace_id, _seed(store)


def _wait(client: TestClient, workspace_id: str, conversation_id: str, operation: str) -> dict:
    for _ in range(60):
        body = client.get(
            f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}"
            f"/compactions/{operation}"
        ).json()
        if body["state"] in {"completed", "noop", "failed", "stopped"}:
            return body
        time.sleep(0.05)
    raise AssertionError("整理操作没有在预期时间内结束")


def test_compact_operation_completes_and_reports_the_record(tmp_path: Path) -> None:
    provider = _SummaryProvider()
    client, _store, workspace_id, conversation_id = _setup(tmp_path, provider)

    accepted = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/compact",
        json={"request_key": "rk1"},
    )
    assert accepted.status_code == 202
    operation = accepted.json()["operation_id"]

    body = _wait(client, workspace_id, conversation_id, operation)

    assert body["state"] == "completed"
    assert body["record"]["items"][0]["quote"] == "别改成第三人称"
    assert provider.calls == 1
    # 摘要请求计入会话用量，累计里能看到一次请求。
    usage = client.get(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/usage"
    ).json()
    assert usage["usage"]["finished_requests"] == 1


def test_same_request_key_is_accepted_once(tmp_path: Path) -> None:
    provider = _SummaryProvider()
    client, _store, workspace_id, conversation_id = _setup(tmp_path, provider)
    url = f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/compact"

    first = client.post(url, json={"request_key": "rk1"}).json()["operation_id"]
    second = client.post(url, json={"request_key": "rk1"}).json()["operation_id"]

    assert first == second
    _wait(client, workspace_id, conversation_id, first)
    assert provider.calls == 1


def test_no_earlier_turns_is_a_noop(tmp_path: Path) -> None:
    provider = _SummaryProvider()
    client, store, workspace_id, _conversation_id = _setup(tmp_path, provider)
    single = store.create_with_first_turn(text="只写一句", request_key="k9", turn_id="t9")
    store.append_batch(
        single.conversation_id,
        [
            {
                "type": "run_finished",
                "turn_id": "t9",
                "status": "completed",
                "recording": "no_change",
                "changed_paths": [],
            }
        ],
    )

    accepted = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations/{single.conversation_id}/compact",
        json={"request_key": "rk2"},
    )
    body = _wait(client, workspace_id, single.conversation_id, accepted.json()["operation_id"])

    assert body["state"] == "noop"
    assert body["note"]
    assert provider.calls == 0


def test_bad_summary_fails_and_keeps_the_projection(tmp_path: Path) -> None:
    provider = _SummaryProvider("好的，我整理了一下。")
    client, store, workspace_id, conversation_id = _setup(tmp_path, provider)

    accepted = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/compact",
        json={"request_key": "rk3"},
    )
    body = _wait(client, workspace_id, conversation_id, accepted.json()["operation_id"])

    assert body["state"] == "failed"
    assert body["error"]["code"] == "COMPACTION_FAILED"
    assert not [
        event for event in store.events(conversation_id) if event["type"] == "context_compacted"
    ]


def test_unknown_operation_is_not_found(tmp_path: Path) -> None:
    provider = _SummaryProvider()
    client, _store, workspace_id, conversation_id = _setup(tmp_path, provider)

    response = client.get(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}"
        "/compactions/op_missing"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CONTEXT_COMPACTION_NOT_FOUND"
