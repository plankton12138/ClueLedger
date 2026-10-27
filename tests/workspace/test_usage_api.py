"""0.9 U2：会话用量读取接口。

契约：
- 路径挂在作品下、按会话读取；不存在的会话是 404，不是空统计。
- 只读：一次读取不产生会话事件，也不产生模型调用。
- 累计以请求去重；缺失用量如实标记，不填零。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.workspace.state import ConversationStore, RecentWorkspaces, conversation_root


def _unconfigured_model() -> tuple[object, str]:
    raise AssertionError("用量读取不应该调用模型")


def _setup(tmp_path: Path) -> tuple[TestClient, ConversationStore, str, str]:
    state_root = tmp_path / "state"
    workspaces = RecentWorkspaces(state_root)
    client = TestClient(
        create_workspace_app(workspaces=workspaces, provider_factory=_unconfigured_model)  # type: ignore[arg-type]
    )
    root = tmp_path / "雪山旅馆"
    opened = client.post("/api/file-workspaces", json={"path": str(root), "create": True})
    assert opened.status_code == 200
    workspace_id = str(opened.json()["id"])
    store = ConversationStore(conversation_root(state_root, workspace_id))
    accepted = store.create_with_first_turn(
        text="写第一章", request_key="k1", turn_id="t1"
    )
    # 补一条终态：否则第一次读取时服务会把「没有结束事件的轮次」按中断收尾，
    # 那是恢复逻辑的正常行为，和用量读取无关。
    store.append_batch(
        accepted.conversation_id,
        [
            {
                "type": "run_finished",
                "turn_id": "t1",
                "status": "completed",
                "recording": "no_change",
                "changed_paths": [],
            }
        ],
    )
    return client, store, workspace_id, accepted.conversation_id


def _request(
    store: ConversationStore, conversation_id: str, *, operation: str, usage: object
) -> None:
    store.append_batch(
        conversation_id,
        [
            {
                "type": "model_request_started",
                "turn_id": "t1",
                "operation_id": operation,
                "stage": "write",
                "model": "deepseek-chat",
            },
            {
                "type": "model_request_finished",
                "turn_id": "t1",
                "operation_id": operation,
                "stage": "write",
                "status": "completed",
                "usage": usage,
                "duration_ms": 900,
            },
        ],
    )


def test_new_conversation_reports_no_usage_yet(tmp_path: Path) -> None:
    """还没发过请求：请求数为零，累计值不是零而是空。"""
    client, _store, workspace_id, conversation_id = _setup(tmp_path)

    body = client.get(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/usage"
    ).json()

    assert body["conversation_id"] == conversation_id
    assert body["usage"]["finished_requests"] == 0
    assert body["usage"]["requests_in_progress"] == 0
    assert body["usage"]["total_tokens"] is None
    assert body["usage"]["input_tokens"] is None
    assert body["diagnostics"] == []
    assert body["through_seq"] >= 1


def test_usage_adds_up_across_requests(tmp_path: Path) -> None:
    client, store, workspace_id, conversation_id = _setup(tmp_path)
    _request(
        store,
        conversation_id,
        operation="op1",
        usage={"input": 1000, "output": 200, "cache_read": 300, "total_tokens": 1500},
    )
    _request(
        store,
        conversation_id,
        operation="op2",
        usage={"input": 500, "output": 100, "total_tokens": 600},
    )

    usage = client.get(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/usage"
    ).json()["usage"]

    assert usage["finished_requests"] == 2
    assert usage["input_tokens"] == 1800  # 含缓存读写
    assert usage["output_tokens"] == 300
    assert usage["cache_read_tokens"] == 300
    assert usage["total_tokens"] == 2100
    assert usage["model_duration_ms"] == 1800


def test_partial_usage_keeps_known_total(tmp_path: Path) -> None:
    client, store, workspace_id, conversation_id = _setup(tmp_path)
    _request(
        store, conversation_id, operation="op1", usage={"input": 100, "output": 20}
    )
    _request(store, conversation_id, operation="op2", usage=None)

    usage = client.get(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/usage"
    ).json()["usage"]

    assert usage["finished_requests"] == 2
    assert usage["requests_without_usage"] == 1
    assert usage["total_tokens"] == 120


def test_reading_usage_does_not_append_events(tmp_path: Path) -> None:
    """打开浮层不能改变数据：读多少次事件数都一样。"""
    client, store, workspace_id, conversation_id = _setup(tmp_path)
    _request(store, conversation_id, operation="op1", usage={"output": 10})
    before = len(store.events(conversation_id))

    client.get(f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/usage")
    client.get(f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/usage")

    assert len(store.events(conversation_id)) == before


def test_unknown_conversation_is_not_found(tmp_path: Path) -> None:
    client, _store, workspace_id, _conversation_id = _setup(tmp_path)

    response = client.get(
        f"/api/file-workspaces/{workspace_id}/conversations/c-missing/usage"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"
