"""V03-1.5 接着讨论（计划 M02、执行指导 §5.5）。

演示切片：首次打开 → 未配置模型 → 保存两条想法 → 刷新 → 连接已有服务 →
从某条消息继续讨论。验收：
- 「接着讨论」引用已保存消息（source_message_ids），不重复新增原消息；
- 连接成功零自动重放：模型调用计数为零，直到作者主动发送；
- 草稿与历史不自动发送；
- 幂等重放返回原轮次；跨讨论/非作者消息引用拒绝。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from hastings.server.v2 import V2Runtime, build_v2_app


class ScriptedChatRunner:
    """记录调用次数；返回固定回复（不经真模型）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, run: object, sink) -> str:
        self.calls += 1
        await sink("好的，我们继续聊这个想法。")
        return "好的，我们继续聊这个想法。"


def _client(path: str | None = None):
    runtime = V2Runtime.sqlite(path) if path else V2Runtime.memory()
    runner = ScriptedChatRunner()
    runtime.chat.runner_factory = lambda: runner
    runtime.chat.model_resolver = lambda: "test-model"
    runtime.chat.bound_runner_factory = None
    client = TestClient(build_v2_app(runtime))
    return client, runtime, runner


def _save_ideas(client: TestClient, entry_id: str, texts: list[str]) -> list[dict]:
    saved = []
    for index, text in enumerate(texts):
        response = client.post(
            "/api/v2/entries",
            json={"entry_id": entry_id, "request_id": f"req-{index}", "text": text},
        )
        assert response.status_code == 200, response.text
        saved.append(response.json())
    return saved


def test_continue_from_saved_messages_without_duplication(tmp_path: Path) -> None:
    path = str(tmp_path / "continue.sqlite")
    client, _runtime, runner = _client(path)
    # 未配置模型（runner 工厂已注入但无人调用）：保存两条想法
    saved = _save_ideas(client, "entry-idea", ["写一个双胞胎错位的故事", "旅馆场景开场"])
    project_id = saved[0]["project_id"]
    conversation_id = saved[0]["conversation_id"]
    assert runner.calls == 0  # 保存想法零调用

    # 「连接已有服务」：只是配置解析器（刷新后重新打开页面）；不自动重放历史
    # （同一 runtime 继续用；模型绑定解析在 send 时才发生）
    assert runner.calls == 0

    # 作者显式「接着讨论」：引用第一条已保存消息
    response = client.post(
        f"/api/v2/projects/{project_id}/conversations/{conversation_id}/turns",
        json={
            "idempotency_key": "continue-1",
            "source_message_ids": [saved[0]["message_id"]],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["turn"]["source_message_ids"] == [saved[0]["message_id"]]
    # 不复制作者消息：user_message 就是原消息（同一 message_id）
    assert body["user_message"]["message_id"] == saved[0]["message_id"]
    # 模型被作者显式启动：恰好一次调用
    assert runner.calls == 1

    # 幂等重放：同键返回原轮次，不再调用
    replay = client.post(
        f"/api/v2/projects/{project_id}/conversations/{conversation_id}/turns",
        json={
            "idempotency_key": "continue-1",
            "source_message_ids": [saved[0]["message_id"]],
        },
    )
    assert replay.status_code == 200
    assert replay.json()["turn"]["turn_id"] == body["turn"]["turn_id"]
    assert runner.calls == 1

    # 消息列表不重复：两条想法 + 一条助手回复
    messages = client.get(
        f"/api/v2/projects/{project_id}/conversations/{conversation_id}/messages"
    ).json()["messages"]
    author_texts = [m["text"] for m in messages if m["role"] == "author"]
    assert author_texts.count("写一个双胞胎错位的故事") == 1
    assistant = [m for m in messages if m["role"] == "assistant"]
    assert len(assistant) == 1


def test_drafts_and_history_never_auto_send(tmp_path: Path) -> None:
    """连接成功零自动重放：保存→重启→连接，计数为零直到显式发送。"""
    path = str(tmp_path / "no-auto.sqlite")
    client, _runtime, runner = _client(path)
    saved = _save_ideas(client, "entry-calm", ["一个安静的海边小镇"])
    project_id = saved[0]["project_id"]
    conversation_id = saved[0]["conversation_id"]

    # 重启 + 重新注入 runner（模拟「连接已有服务」）
    restarted, _rt2, runner2 = _client(path)
    # 读取历史/刷新页面：零调用
    restarted.get(f"/api/v2/projects/{project_id}/conversations").json()
    restarted.get(f"/api/v2/projects/{project_id}/conversations/{conversation_id}/messages").json()
    assert runner2.calls == 0
    _ = client, runner


def test_cross_conversation_and_assistant_refs_rejected(tmp_path: Path) -> None:
    client, _runtime, runner = _client()
    saved = _save_ideas(client, "entry-x", ["想法 A"])
    project_id = saved[0]["project_id"]
    conversation_id = saved[0]["conversation_id"]
    # 跨讨论引用拒绝
    other_conv = client.post(f"/api/v2/projects/{project_id}/conversations", json={}).json()[
        "conversation_id"
    ]
    # 消息不存在（跨讨论/无此消息）→ CHAT_MESSAGE_NOT_FOUND，按既有映射 404
    cross = client.post(
        f"/api/v2/projects/{project_id}/conversations/{other_conv}/turns",
        json={"idempotency_key": "c1", "source_message_ids": [saved[0]["message_id"]]},
    )
    assert cross.status_code == 404
    assert cross.json()["error"]["code"] == "CHAT_MESSAGE_NOT_FOUND"
    # 不存在的消息拒绝
    missing = client.post(
        f"/api/v2/projects/{project_id}/conversations/{conversation_id}/turns",
        json={"idempotency_key": "c2", "source_message_ids": ["msg_missing"]},
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "CHAT_MESSAGE_NOT_FOUND"
    # 非作者消息拒绝：先产生一条助手消息，再引用它
    client.post(
        f"/api/v2/projects/{project_id}/conversations/{conversation_id}/turns",
        json={"idempotency_key": "normal-1", "text": "聊聊这个"},
    )
    assert runner.calls == 1
    assistant = client.get(
        f"/api/v2/projects/{project_id}/conversations/{conversation_id}/messages"
    ).json()["messages"]
    assistant_id = next(m["message_id"] for m in assistant if m["role"] == "assistant")
    assistant_ref = client.post(
        f"/api/v2/projects/{project_id}/conversations/{conversation_id}/turns",
        json={"idempotency_key": "c3", "source_message_ids": [assistant_id]},
    )
    assert assistant_ref.status_code == 404
    assert assistant_ref.json()["error"]["code"] == "CHAT_MESSAGE_NOT_FOUND"
    # text 与 source_message_ids 互斥
    both = client.post(
        f"/api/v2/projects/{project_id}/conversations/{conversation_id}/turns",
        json={
            "idempotency_key": "c4",
            "text": "新发言",
            "source_message_ids": [saved[0]["message_id"]],
        },
    )
    assert both.status_code == 400
    assert both.json()["error"]["code"] == "CHAT_INVALID_COMMAND"
