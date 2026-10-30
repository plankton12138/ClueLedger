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


def _current_limits():
    """当前选择的容量与预算；只读配置，不构造 provider。"""
    from hastings.server.model_limits import resolve_model_limits
    from hastings.server.provider_config import ProviderProfile

    profile = ProviderProfile(
        name="deepseek",
        base_url="https://api.deepseek.com",
        api="openai-completions",
        default_model="deepseek-flash",
        catalog_id="deepseek",
    )
    return resolve_model_limits(profile, "deepseek-flash")


def _setup(
    tmp_path: Path, *, limits_factory=None
) -> tuple[TestClient, ConversationStore, str, str]:
    state_root = tmp_path / "state"
    workspaces = RecentWorkspaces(state_root)
    client = TestClient(
        create_workspace_app(
            workspaces=workspaces,
            provider_factory=_unconfigured_model,  # type: ignore[arg-type]
            limits_factory=limits_factory,
        )
    )
    root = tmp_path / "雪山旅馆"
    opened = client.post("/api/file-workspaces", json={"path": str(root), "create": True})
    assert opened.status_code == 200
    workspace_id = str(opened.json()["id"])
    store = ConversationStore(conversation_root(state_root, workspace_id))
    accepted = store.create_with_first_turn(text="写第一章", request_key="k1", turn_id="t1")
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
    _request(store, conversation_id, operation="op1", usage={"input": 100, "output": 20})
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

    response = client.get(f"/api/file-workspaces/{workspace_id}/conversations/c-missing/usage")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"


def test_usage_reports_context_state(tmp_path: Path) -> None:
    """新会话的上下文是待计算；有请求后给出估算与容量。"""
    client, store, workspace_id, conversation_id = _setup(tmp_path)

    payload = client.get(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/usage"
    ).json()
    pending = payload["context"]
    assert pending["status"] == "pending"
    assert pending["estimated_active_tokens"] is None
    assert pending["budget"] == "unknown"
    assert pending["capacity_source"] == "unknown"
    # 没有可用模型时预算视图为空，页面提示先选择模型。
    assert pending["input_limit_tokens"] is None
    assert pending["project_input_limit_tokens"] is None
    assert pending["model_identity"] == ""
    # 自动整理策略随视图返回，界面据此说明「什么时候需要整理」。
    assert payload["policy"] == {"auto_compaction": True, "threshold": 0.9}

    store.append_batch(
        conversation_id,
        [
            {
                "type": "model_request_started",
                "turn_id": "t1",
                "operation_id": "op1",
                "stage": "write",
                "request_estimate": {
                    "model": "deepseek:deepseek-chat",
                    "total_tokens": 5000,
                    "system_tokens": 1000,
                    "tool_tokens": 500,
                    "message_tokens": 3400,
                    "protocol_tokens": 100,
                    "method": "cjk-aware-v1",
                    "fingerprint": "f1",
                    "budget": "ok",
                    "context_window_tokens": 64_000,
                    "input_limit_tokens": 56_000,
                    "output_reserve_tokens": 4096,
                    "safety_tokens": 2048,
                    "capacity_source": "catalog",
                    "limits_revision": "r1",
                },
            },
            {
                "type": "model_request_finished",
                "turn_id": "t1",
                "operation_id": "op1",
                "stage": "write",
                "status": "completed",
                "usage": {"input": 5200, "output": 300, "total_tokens": 5500},
                "duration_ms": 800,
            },
        ],
    )

    ready = client.get(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/usage"
    ).json()["context"]
    assert ready["status"] == "ready"
    assert ready["estimated_active_tokens"] == 5000
    assert ready["last_request_input_tokens"] == 5200
    assert ready["context_window_tokens"] == 64_000
    assert ready["safety_tokens"] == 2048
    assert ready["capacity_source"] == "catalog"
    assert ready["budget"] == "ok"
    assert ready["limits_revision"] == "r1"
    assert ready["instructions_tokens"] == 1000


def test_pending_context_shows_current_budget_without_an_estimate(tmp_path: Path) -> None:
    """首次进入就能看到分母：项目上限与模型窗口都来自当前选择，估算是空的。"""
    client, store, workspace_id, conversation_id = _setup(tmp_path, limits_factory=_current_limits)
    before = len(store.events(conversation_id))

    context = client.get(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/usage"
    ).json()["context"]

    assert context["status"] == "pending"
    # 缺估算不等于零占用：没有数值，也不给百分比。
    assert context["estimated_active_tokens"] is None
    assert context["budget"] == "unknown"
    assert context["input_limit_tokens"] == 128_000
    assert context["project_input_limit_tokens"] == 128_000
    assert context["model_input_limit_tokens"] == 1_048_576 - 4096 - 4096
    assert context["context_window_tokens"] == 1_048_576
    assert context["input_limit_source"] == "project"
    assert context["capacity_source"] == "catalog"
    assert context["model_identity"] == "deepseek:deepseek-flash"
    assert context["policy_revision"]
    # 读取仍然只读：不写事件，也不构造 provider。
    assert len(store.events(conversation_id)) == before


def test_idle_usage_resolves_capacity_without_another_model_request(tmp_path: Path) -> None:
    """已结束会话的未知容量不能阻止当前目录生效；读取不重写旧事件。"""
    client, store, workspace_id, conversation_id = _setup(tmp_path, limits_factory=_current_limits)
    store.append_batch(
        conversation_id,
        [
            {
                "type": "model_request_started",
                "turn_id": "t1",
                "operation_id": "op1",
                "stage": "write",
                "request_estimate": {
                    "model_identity": "deepseek:deepseek-flash",
                    "total_tokens": 20331,
                    "system_tokens": 3298,
                    "tool_tokens": 10580,
                    "message_tokens": 6453,
                    "protocol_tokens": 0,
                    "method": "cjk-aware-v1",
                    "fingerprint": "old",
                    "context_window_tokens": None,
                    "input_limit_tokens": None,
                    "output_reserve_tokens": 4096,
                    "safety_tokens": 0,
                    "capacity_source": "unknown",
                    "limits_revision": "old",
                },
            },
            {
                "type": "model_request_finished",
                "turn_id": "t1",
                "operation_id": "op1",
                "stage": "write",
                "status": "completed",
                "usage": {"input": 1616},
            },
        ],
    )
    before = store.events(conversation_id)

    payload = client.get(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/usage"
    ).json()
    context = payload["context"]

    assert context["status"] == "ready"
    assert context["estimated_active_tokens"] == 20331
    assert context["context_window_tokens"] == 1_048_576
    assert context["project_input_limit_tokens"] == context["input_limit_tokens"] == 128_000
    assert context["capacity_source"] == "catalog"
    assert context["safety_tokens"] == 4096
    assert context["budget"] == "ok"
    assert payload["usage"]["finished_requests"] == 1
    assert store.events(conversation_id) == before
