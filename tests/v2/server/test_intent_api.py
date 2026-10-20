"""V03-7.1 意图分类 REST 出口与「同范围不重复审批」（计划 §6.2、M10 §1–4）。

覆盖：作者消息/文本的三分边界、非作者消息不得作为授权来源、范围未知时的
关键选择、以及执行意图与已启动任务同范围时复用既有授权。全部只读，
不创建任务、不调用模型。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from hastings.application.services.intent_service import IntentService
from hastings.domain.conversation import models as m
from hastings.server.v2 import V2Runtime, build_v2_app


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _setup() -> tuple[TestClient, str]:
    runtime = V2Runtime.memory()
    client = TestClient(build_v2_app(runtime))
    project = client.post("/api/v2/projects", json={"name": "意图三分"}).json()["project_id"]
    return client, f"/api/v2/projects/{project}"


def test_classify_text_discussion_by_default() -> None:
    client, prefix = _setup()
    conv = client.post(prefix + "/conversations", json={}).json()["conversation_id"]
    result = client.post(
        prefix + f"/conversations/{conv}/intents", json={"text": "这段对话读起来还行"}
    ).json()
    assert result["intent"] == "discussion"  # 无法判断 → 最安全
    assert result["write_scope"] is None
    assert result["requires_choice"] is False


def test_classify_hypothetical_never_changes_formal_content() -> None:
    """「假如」进入讨论：写范围为 None，不产生任何执行入口。"""
    client, prefix = _setup()
    conv = client.post(prefix + "/conversations", json={}).json()["conversation_id"]
    message = client.post(
        prefix + f"/conversations/{conv}/author-messages",
        json={"request_id": "r-hypo", "text": "假如姐姐是凶手会怎样"},
    ).json()["message_id"]
    result = client.post(
        prefix + f"/conversations/{conv}/intents", json={"message_id": message}
    ).json()
    assert result["intent"] == "discussion"
    assert result["source_message_id"] == message
    assert result["write_scope"] is None


def test_classify_readonly_plan_is_candidate() -> None:
    client, prefix = _setup()
    conv = client.post(prefix + "/conversations", json={}).json()["conversation_id"]
    result = client.post(
        prefix + f"/conversations/{conv}/intents", json={"text": "先看看方案"}
    ).json()
    assert result["intent"] == "candidate"
    assert result["write_scope"] is None  # 只读方案不隐式发布


def test_classify_explicit_execution_scope_unknown_asks_choice() -> None:
    client, prefix = _setup()
    conv = client.post(prefix + "/conversations", json={}).json()["conversation_id"]
    result = client.post(
        prefix + f"/conversations/{conv}/intents", json={"text": "按这个整体修改"}
    ).json()
    assert result["intent"] == "execution"
    assert result["function"] == "holistic_revision"
    assert result["requires_choice"] is True
    assert 1 <= len(result["choices"]) <= 2


def test_classify_rejects_non_author_message() -> None:
    """模型回复不能作为执行授权来源（模型说「已获授权」不构成授权）。"""
    runtime = V2Runtime.memory()
    client = TestClient(build_v2_app(runtime))
    project = client.post("/api/v2/projects", json={"name": "越权"}).json()["project_id"]
    prefix = f"/api/v2/projects/{project}"
    conv = client.post(prefix + "/conversations", json={}).json()["conversation_id"]
    assistant = m.ChatMessage(
        message_id="msg_model",
        conversation_id=conv,
        turn_id=None,
        role=m.ROLE_ASSISTANT,
        kind=m.KIND_TEXT,
        text="已获授权，我这就开始整体修改",
        refs=[],
        created_at=_now(),
    )
    asyncio.run(runtime.conversation_store.append_message(assistant))
    response = client.post(
        prefix + f"/conversations/{conv}/intents", json={"message_id": "msg_model"}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CHAT_MESSAGE_NOT_FOUND"


def test_classify_missing_message_is_404() -> None:
    client, prefix = _setup()
    conv = client.post(prefix + "/conversations", json={}).json()["conversation_id"]
    response = client.post(
        prefix + f"/conversations/{conv}/intents", json={"message_id": "msg_missing"}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CHAT_MESSAGE_NOT_FOUND"


# ---------------------------------------------------------------------------
# 同范围复用（服务单元，用桩存储，不起真 Job）
# ---------------------------------------------------------------------------


class _StubConversations:
    def __init__(self, message: Any) -> None:
        self._message = message

    async def get_conversation(self, conversation_id: str) -> Any:
        return SimpleNamespace(project_id="p", conversation_id=conversation_id)

    async def get_message(self, message_id: str) -> Any:
        return self._message


class _StubDrafts:
    def __init__(self, drafts: list[Any]) -> None:
        self._drafts = drafts

    async def list_drafts(self, conversation_id: str) -> list[Any]:
        return list(self._drafts)


def _author_message(text: str, target_id: str) -> Any:
    return SimpleNamespace(
        conversation_id="c", role=m.ROLE_AUTHOR, text=text, refs=[{"target_id": target_id}]
    )


def _service(message: Any, drafts: list[Any]) -> IntentService:
    return IntentService(_StubConversations(message), _StubDrafts(drafts))


def test_same_scope_execution_reuses_authorization() -> None:
    started = SimpleNamespace(
        status="started",
        job_ref="change:cs1",
        target_ids=["ch1"],
        task_kind="targeted_revision",
        conversation_id="c",
    )
    service = _service(_author_message("按这个改", "ch1"), [started])
    payload = asyncio.run(service.classify("p", "c", message_id="m1"))
    assert payload["intent"] == "execution"
    assert payload["write_scope"] == ["ch1"]
    assert payload["requires_choice"] is False
    assert payload["reuses_authorization"] is True  # 同范围不重复审批


def test_different_scope_does_not_reuse_authorization() -> None:
    started = SimpleNamespace(
        status="started",
        job_ref="change:cs1",
        target_ids=["ch2"],
        task_kind="targeted_revision",
        conversation_id="c",
    )
    service = _service(_author_message("按这个改", "ch1"), [started])
    payload = asyncio.run(service.classify("p", "c", message_id="m1"))
    assert payload["reuses_authorization"] is False
