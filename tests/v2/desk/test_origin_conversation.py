"""对话来源与结果投影（V03-4.5）：归属固定、切讨论不改、投影可回读。

对应产品设计 §5.2「每次导入、采用或修改完成创建唯一的持久结果记录：绑定发起
会话或本作品当前会话，无会话时复用『创作记录』默认讨论」。
"""

from __future__ import annotations

import base64
from typing import Any

from fastapi.testclient import TestClient

from hastings.server.v2 import V2Runtime, build_v2_app

MD = """# 雪山旅馆

## 第一章 雪夜

侦探在雪夜抵达旅馆，前台没有人。

## 第二章 空房

二楼走廊尽头有一扇没有门牌的门。
"""


def _runtime() -> tuple[V2Runtime, TestClient, str]:
    runtime = V2Runtime.memory()
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "来源"}).json()["project_id"]
    return runtime, client, pid


def _conversation(client: TestClient, pid: str, title: str = "讨论一") -> str:
    return client.post(f"/api/v2/projects/{pid}/conversations", json={"title": title}).json()[
        "conversation_id"
    ]


def _import(client: TestClient, pid: str, *, conversation_id: str | None = None) -> dict[str, Any]:
    preview = client.post(
        f"/api/v2/projects/{pid}/imports/previews",
        json={
            "filename": "雪山旅馆.md",
            "content_base64": base64.b64encode(MD.encode("utf-8")).decode("ascii"),
        },
    ).json()
    body: dict[str, Any] = {"preview_hash": preview["preview_hash"]}
    if conversation_id is not None:
        body["conversation_id"] = conversation_id
    response = client.post(
        f"/api/v2/projects/{pid}/imports/previews/{preview['preview_id']}/confirm",
        json=body,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _messages(client: TestClient, pid: str, conversation_id: str) -> list[dict[str, Any]]:
    return client.get(f"/api/v2/projects/{pid}/conversations/{conversation_id}/messages").json()[
        "messages"
    ]


def _operation(client: TestClient, pid: str, operation_id: str) -> dict[str, Any]:
    return client.get(f"/api/v2/projects/{pid}/operations/{operation_id}").json()


def test_result_binds_to_origin_conversation() -> None:
    """给了发起讨论就落在那里，不是落在「创作记录」，也不是落在当前打开的讨论。"""
    runtime, client, pid = _runtime()
    conversation_id = _conversation(client, pid)
    result = _import(client, pid, conversation_id=conversation_id)
    runtime.dispatch_pending(pid)

    payload = _operation(client, pid, result["operation_id"])
    assert payload["conversation_id"] == conversation_id
    assert payload["projection"]["conversation_id"] == conversation_id
    assert payload["projection"]["state"] == "projected"
    texts = [message["text"] for message in _messages(client, pid, conversation_id)]
    assert any("操作已完成" in text for text in texts)


def test_without_origin_result_falls_back_to_creation_log() -> None:
    """没有发起讨论时归「创作记录」：不需要先有一条作者消息才能挂结果。"""
    runtime, client, pid = _runtime()
    result = _import(client, pid)
    runtime.dispatch_pending(pid)

    payload = _operation(client, pid, result["operation_id"])
    conversations = client.get(f"/api/v2/projects/{pid}/conversations").json()["conversations"]
    creation_log = next(row for row in conversations if row["title"] == "创作记录")
    assert payload["conversation_id"] == creation_log["conversation_id"]
    assert _messages(client, pid, creation_log["conversation_id"])


def test_foreign_conversation_is_rejected_at_the_entry() -> None:
    """来源讨论属于别的作品时直接拒绝：归属错了，作者两处都找不到结果。"""
    runtime, client, pid = _runtime()
    other = client.post("/api/v2/projects", json={"name": "别的作品"}).json()["project_id"]
    foreign = _conversation(client, other, "别人的讨论")

    preview = client.post(
        f"/api/v2/projects/{pid}/imports/previews",
        json={
            "filename": "雪山旅馆.md",
            "content_base64": base64.b64encode(MD.encode("utf-8")).decode("ascii"),
        },
    ).json()
    response = client.post(
        f"/api/v2/projects/{pid}/imports/previews/{preview['preview_id']}/confirm",
        json={"preview_hash": preview["preview_hash"], "conversation_id": foreign},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"
    # 拒绝发生在写之前：正文没变，别人的讨论里也没有消息。
    assert client.get(f"/api/v2/projects/{pid}/manuscript").json()["chapters"] == []
    assert _messages(client, other, foreign) == []


def test_projection_reports_pending_before_dispatch() -> None:
    """还没投影时如实说 pending，操作结果本身照样能查——投影不是结果的前提。"""
    _, client, pid = _runtime()
    conversation_id = _conversation(client, pid)
    result = _import(client, pid, conversation_id=conversation_id)

    payload = _operation(client, pid, result["operation_id"])
    assert payload["projection"]["state"] == "pending"
    assert payload["commit_id"]


def test_switching_conversation_does_not_move_existing_result() -> None:
    """归属在提交时固化：之后另开讨论、继续聊天都不改变已有结果的会话。"""
    runtime, client, pid = _runtime()
    first = _conversation(client, pid, "先开的讨论")
    result = _import(client, pid, conversation_id=first)
    later = _conversation(client, pid, "后开的讨论")
    runtime.dispatch_pending(pid)

    payload = _operation(client, pid, result["operation_id"])
    assert payload["conversation_id"] == first
    assert _messages(client, pid, later) == []
