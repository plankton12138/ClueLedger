"""V03-1.2 入口预约与无模型想法记录（计划 M02、执行指导 §5.2）。

测试先行清单：
- 首次/连续保存三条想法：同一 entry 的三条想法落在同一作品/讨论；
- 失败重试不重复建作品（确定性作品身份 + 幂等回执）；
- 刷新/重启/切作品后归属正确（SQLite 重启 + 跨 entry 隔离）；
- 同键不同内容显式冲突（不静默覆盖）；存储失败按失败处理（不伪造已保存）；
- 保存想法零模型调用（模型运行记录器，对齐 V03-0.3 夹具手法）。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from hastings.server.v2 import V2Runtime, build_v2_app


class ModelCallRecorder:
    """零调用断言用：注入 agent runner 与 chat runner 工厂（V03-0.3 同款）。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(self, task_kind: str, context_package: dict) -> dict:
        self.calls.append(task_kind)
        return {"kind": "no_result", "reason": "recorded"}

    def model_binding(self) -> dict:
        return {"profile": "test", "model": "test-model", "api": "test"}

    def chat_runner(self) -> _RecordingChatRunner:
        return _RecordingChatRunner(self)


class _RecordingChatRunner:
    def __init__(self, recorder: ModelCallRecorder) -> None:
        self._recorder = recorder

    async def stream(self, run: object, sink) -> str:
        self._recorder.calls.append("chat")
        await sink("记录")
        return "记录"


def _client(path: str | None = None) -> tuple[TestClient, V2Runtime, ModelCallRecorder]:
    runtime = V2Runtime.sqlite(path) if path else V2Runtime.memory()
    recorder = ModelCallRecorder()
    runtime.configure_agent_runner(recorder)
    runtime.chat.runner_factory = recorder.chat_runner
    runtime.chat.bound_runner_factory = lambda _binding: recorder.chat_runner()
    return TestClient(build_v2_app(runtime)), runtime, recorder


def _save_idea(
    client: TestClient, entry_id: str, request_id: str, text: str, title: str = ""
) -> dict:
    body: dict = {"entry_id": entry_id, "request_id": request_id, "text": text}
    if title:
        body["title"] = title
    return client.post("/api/v2/entries", json=body)


def test_first_and_sequential_ideas_share_one_project(tmp_path: Path) -> None:
    client, runtime, recorder = _client()
    saved = [_save_idea(client, "entry-winter", f"req-{i}", f"想法 {i}") for i in range(3)]
    assert all(r.status_code == 200 for r in saved)
    targets = [r.json() for r in saved]
    project_ids = {t["project_id"] for t in targets}
    conversation_ids = {t["conversation_id"] for t in targets}
    assert len(project_ids) == 1 and len(conversation_ids) == 1
    message_ids = {t["message_id"] for t in targets}
    assert len(message_ids) == 3  # 三条想法三条消息，不重复
    # 消息状态 not_requested；列表可读
    conversation_id = targets[0]["conversation_id"]
    messages = client.get(
        f"/api/v2/projects/{targets[0]['project_id']}/conversations/{conversation_id}/messages"
    ).json()["messages"]
    author = [m for m in messages if m["role"] == "author"]
    assert len(author) == 3 and all(m["status"] == "not_requested" for m in author)
    # 零模型调用：保存想法从不启动模型
    assert recorder.calls == []
    _ = runtime


def test_retry_does_not_duplicate_project_or_message(tmp_path: Path) -> None:
    """同 request_id 重试：同一消息；同 entry 不同 request_id：不重复建作品。"""
    client, _runtime, recorder = _client()
    first = _save_idea(client, "entry-a", "req-1", "同一个想法")
    assert first.status_code == 200
    retry = _save_idea(client, "entry-a", "req-1", "同一个想法")
    assert retry.status_code == 200
    assert retry.json()["message_id"] == first.json()["message_id"]
    assert retry.json()["project_id"] == first.json()["project_id"]
    # 同键不同内容：显式冲突，不静默覆盖
    conflict = _save_idea(client, "entry-a", "req-1", "不同的想法")
    assert conflict.status_code == 400
    assert conflict.json()["error"]["code"] == "OPERATION_KEY_REUSED"
    # 另一条想法（新 request_id）：同一作品，新消息
    second = _save_idea(client, "entry-a", "req-2", "第二条")
    assert second.status_code == 200
    assert second.json()["project_id"] == first.json()["project_id"]
    assert second.json()["message_id"] != first.json()["message_id"]
    assert recorder.calls == []


def test_survives_restart_and_isolates_entries(tmp_path: Path) -> None:
    """刷新/重启/切入口后归属正确：同一 entry 找回同一作品；不同 entry 不同作品。"""
    path = str(tmp_path / "entries.sqlite")
    client, _runtime, _recorder = _client(path)
    first = _save_idea(client, "entry-winter", "req-1", "雪夜的想法", title="雪夜手记")
    assert first.status_code == 200
    other = _save_idea(client, "entry-summer", "req-1", "另一本的想法")
    assert other.status_code == 200
    assert other.json()["project_id"] != first.json()["project_id"]

    # 重启：全新 runtime + 同一 SQLite
    restarted_client, restarted, _recorder = _client(path)
    again = _save_idea(restarted_client, "entry-winter", "req-2", "重启后继续")
    assert again.status_code == 200
    assert again.json()["project_id"] == first.json()["project_id"]
    conversation_id = again.json()["conversation_id"]
    messages = restarted_client.get(
        f"/api/v2/projects/{first.json()['project_id']}/conversations/{conversation_id}/messages"
    ).json()["messages"]
    author_texts = [m["text"] for m in messages if m["role"] == "author"]
    assert "雪夜的想法" in author_texts and "重启后继续" in author_texts
    # 另一入口的消息不会混入
    other_project = other.json()["project_id"]
    other_conv = other.json()["conversation_id"]
    other_messages = restarted_client.get(
        f"/api/v2/projects/{other_project}/conversations/{other_conv}/messages"
    ).json()["messages"]
    assert [m["text"] for m in other_messages if m["role"] == "author"] == ["另一本的想法"]
    _ = restarted


def test_author_message_endpoint_for_existing_project() -> None:
    """后续想法端点：复用消息库，无模型端口；跨项目讨论拒绝。"""
    client, runtime, recorder = _client()
    saved = _save_idea(client, "entry-b", "req-1", "第一条")
    project_id = saved.json()["project_id"]
    conversation_id = saved.json()["conversation_id"]
    saved2 = client.post(
        f"/api/v2/projects/{project_id}/conversations/{conversation_id}/author-messages",
        json={"request_id": "req-2", "text": "后续想法"},
    )
    assert saved2.status_code == 200
    assert saved2.json()["status"] == "not_requested"
    # 同键不同内容冲突
    conflict = client.post(
        f"/api/v2/projects/{project_id}/conversations/{conversation_id}/author-messages",
        json={"request_id": "req-2", "text": "另一条"},
    )
    assert conflict.status_code == 400
    assert conflict.json()["error"]["code"] == "OPERATION_KEY_REUSED"
    # 跨项目讨论：404 信封
    client.post("/api/v2/projects", json={"name": "其他作品"})
    other = client.get(f"/api/v2/projects/{project_id}/conversations").json()["conversations"]
    assert other  # 当前项目的讨论存在
    cross = client.post(
        "/api/v2/projects/prj_other/conversations/" + conversation_id + "/author-messages",
        json={"request_id": "x", "text": "跨项目"},
    )
    assert cross.status_code == 404
    assert recorder.calls == []
    _ = runtime


def test_send_mode_not_open_yet() -> None:
    """发送模式须作者显式授权（V03-1.5）：当前入口明确拒绝。"""
    client, _runtime, _recorder = _client()
    response = client.post(
        "/api/v2/entries",
        json={"entry_id": "e", "request_id": "r", "text": "x", "mode": "send"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "ENTRY_MODE_UNSUPPORTED"
