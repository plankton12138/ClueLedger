"""ConversationStore 契约测试（Phase 19.3 T19.3b）：内存与 SQLite 双后端共用。"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from hastings.application.ports.conversations import ConversationStore, OpenTurn
from hastings.domain.conversation import error_codes as ec
from hastings.domain.conversation import models as m
from hastings.domain.conversation.errors import ConversationViolation
from hastings.domain.repository.serialization import content_hash
from hastings.infrastructure.conversations.store import (
    MemoryConversationStore,
    SqliteConversationStore,
)

NOW = "2026-10-16T00:00:00+00:00"


@pytest.fixture(params=["memory", "sqlite"])
def make_store(request: pytest.FixtureRequest, tmp_path: Path):  # type: ignore[no-untyped-def]
    def factory() -> ConversationStore:
        if request.param == "memory":
            return MemoryConversationStore()
        return SqliteConversationStore(str(tmp_path / "chat.sqlite"))

    return factory


_counter = itertools.count(1)


def _message(conversation_id: str, turn_id: str | None, *, text: str = "hi") -> m.ChatMessage:
    return m.ChatMessage(
        message_id=f"msg_{next(_counter)}",
        conversation_id=conversation_id,
        turn_id=turn_id,
        role=m.ROLE_AUTHOR,
        kind=m.KIND_TEXT,
        text=text,
        created_at=NOW,
    )


def _turn(conversation_id: str, *, key: str = "k1", retry_of: str | None = None) -> m.ChatTurn:
    return m.ChatTurn(
        turn_id=f"turn_{next(_counter)}",
        conversation_id=conversation_id,
        status=m.TURN_QUEUED,
        model=None,
        end_reason=None,
        idempotency_key=key,
        retry_of=retry_of,
    )


def _fingerprint(text: str, refs: list[dict[str, object]] | None = None) -> str:
    return content_hash({"text": text, "refs": refs or []})


async def _open(store: ConversationStore, project_id: str, conversation_id: str, **kwargs):  # type: ignore[no-untyped-def]
    turn = _turn(conversation_id, key=kwargs.pop("key", "k1"))
    message = _message(conversation_id, turn.turn_id, text=kwargs.pop("text", "hi"))
    return await store.open_turn(
        project_id=project_id,
        conversation_id=conversation_id,
        idempotency_key=turn.idempotency_key,
        fingerprint=kwargs.pop("fingerprint", _fingerprint(message.text)),
        user_message=message,
        turn=turn,
    )


@pytest.mark.anyio
async def test_conversation_lifecycle(make_store) -> None:  # type: ignore[no-untyped-def]
    store = make_store()
    conversation = await store.create_conversation("prj_1", "讨论凶手动机")
    assert conversation.project_id == "prj_1"
    assert await store.get_conversation(conversation.conversation_id) == conversation
    assert await store.get_conversation("missing") is None
    assert await store.list_conversations("prj_1") == [conversation]
    assert await store.list_conversations("other") == []


@pytest.mark.anyio
async def test_open_turn_creates_message_and_emits_accepted(make_store) -> None:  # type: ignore[no-untyped-def]
    store = make_store()
    conversation = await store.create_conversation("prj_1", "t")
    opened = await _open(store, "prj_1", conversation.conversation_id)
    assert isinstance(opened, OpenTurn)
    assert opened.created is True
    assert opened.turn.status == m.TURN_QUEUED
    assert opened.user_message.turn_id == opened.turn.turn_id
    events = await store.events_after(conversation.conversation_id, 0)
    assert [e.sequence for e in events] == [1]
    assert events[0].event["type"] == "turn.accepted"
    assert events[0].event["turn"]["turn_id"] == opened.turn.turn_id
    assert events[0].project_id == "prj_1"


@pytest.mark.anyio
async def test_open_turn_idempotent_replay(make_store) -> None:  # type: ignore[no-untyped-def]
    store = make_store()
    conversation = await store.create_conversation("prj_1", "t")
    first = await _open(store, "prj_1", conversation.conversation_id, key="same")
    second = await _open(store, "prj_1", conversation.conversation_id, key="same")
    assert second.created is False
    assert second.turn.turn_id == first.turn.turn_id
    assert second.user_message.message_id == first.user_message.message_id
    assert await store.events_after(conversation.conversation_id, 0) != []


@pytest.mark.anyio
async def test_open_turn_same_key_different_input_is_conflict(make_store) -> None:  # type: ignore[no-untyped-def]
    store = make_store()
    conversation = await store.create_conversation("prj_1", "t")
    await _open(store, "prj_1", conversation.conversation_id, key="same", text="hi")
    with pytest.raises(ConversationViolation) as excinfo:
        await _open(
            store,
            "prj_1",
            conversation.conversation_id,
            key="same",
            text="changed",
            fingerprint=_fingerprint("changed"),
        )
    assert excinfo.value.code == ec.CHAT_IDEMPOTENCY_CONFLICT


@pytest.mark.anyio
async def test_open_turn_rejects_when_active_turn_exists(make_store) -> None:  # type: ignore[no-untyped-def]
    store = make_store()
    conversation = await store.create_conversation("prj_1", "t")
    await _open(store, "prj_1", conversation.conversation_id, key="a")
    with pytest.raises(ConversationViolation) as excinfo:
        await _open(store, "prj_1", conversation.conversation_id, key="b")
    assert excinfo.value.code == ec.CHAT_CONVERSATION_BUSY


@pytest.mark.anyio
async def test_start_turn_only_when_queued(make_store) -> None:  # type: ignore[no-untyped-def]
    store = make_store()
    conversation = await store.create_conversation("prj_1", "t")
    opened = await _open(store, "prj_1", conversation.conversation_id)
    assistant = m.ChatMessage(
        message_id="msg_a",
        conversation_id=conversation.conversation_id,
        turn_id=opened.turn.turn_id,
        role=m.ROLE_ASSISTANT,
        kind=m.KIND_TEXT,
        text="",
        model="deepseek-chat",
        status=m.MESSAGE_STREAMING,
        created_at=NOW,
    )
    started = await store.start_turn(
        opened.turn.turn_id, assistant_message=assistant, model="deepseek-chat"
    )
    assert started
    assert not await store.start_turn(
        opened.turn.turn_id, assistant_message=assistant, model="deepseek-chat"
    )
    events = await store.events_after(conversation.conversation_id, 1)
    assert events[0].event["type"] == "turn.started"
    assert events[0].event["message_id"] == "msg_a"
    assert events[0].event["model"] == "deepseek-chat"


@pytest.mark.anyio
async def test_deltas_append_and_drop_after_settle(make_store) -> None:  # type: ignore[no-untyped-def]
    store = make_store()
    conversation = await store.create_conversation("prj_1", "t")
    opened = await _open(store, "prj_1", conversation.conversation_id)
    assistant = m.ChatMessage(
        message_id="msg_a",
        conversation_id=conversation.conversation_id,
        turn_id=opened.turn.turn_id,
        role=m.ROLE_ASSISTANT,
        kind=m.KIND_TEXT,
        text="",
        model="m",
        status=m.MESSAGE_STREAMING,
        created_at=NOW,
    )
    await store.start_turn(opened.turn.turn_id, assistant_message=assistant, model="m")
    assert await store.append_delta(opened.turn.turn_id, "你好")
    assert await store.append_delta(opened.turn.turn_id, "，世界")
    assert await store.settle_turn(
        opened.turn.turn_id, status=m.TURN_COMPLETED, end_reason="stop", text="你好，世界"
    )
    assert not await store.settle_turn(
        opened.turn.turn_id, status=m.TURN_STOPPED, end_reason="stopped"
    )
    assert not await store.append_delta(opened.turn.turn_id, "迟到")
    stored = await store.get_message("msg_a")
    assert stored is not None and stored.text == "你好，世界"
    assert stored.status == m.MESSAGE_COMPLETED
    events = await store.events_after(conversation.conversation_id, 0)
    types = [e.event["type"] for e in events]
    expected = [
        "turn.accepted",
        "turn.started",
        "message.delta",
        "message.delta",
        "message.completed",
    ]
    assert types == expected


@pytest.mark.anyio
async def test_settle_stopped_marks_message_stopped(make_store) -> None:  # type: ignore[no-untyped-def]
    store = make_store()
    conversation = await store.create_conversation("prj_1", "t")
    opened = await _open(store, "prj_1", conversation.conversation_id)
    assistant = m.ChatMessage(
        message_id="msg_a",
        conversation_id=conversation.conversation_id,
        turn_id=opened.turn.turn_id,
        role=m.ROLE_ASSISTANT,
        kind=m.KIND_TEXT,
        text="",
        model="m",
        status=m.MESSAGE_STREAMING,
        created_at=NOW,
    )
    await store.start_turn(opened.turn.turn_id, assistant_message=assistant, model="m")
    await store.append_delta(opened.turn.turn_id, "半句")
    await store.settle_turn(opened.turn.turn_id, status=m.TURN_STOPPED, end_reason="cancelled")
    stored = await store.get_message("msg_a")
    assert stored is not None and stored.status == m.MESSAGE_STOPPED and stored.text == "半句"
    turn = await store.get_turn(opened.turn.turn_id)
    assert turn is not None and turn.status == m.TURN_STOPPED


@pytest.mark.anyio
async def test_events_after_filters_and_lists_messages_ordered(make_store) -> None:  # type: ignore[no-untyped-def]
    store = make_store()
    conversation = await store.create_conversation("prj_1", "t")
    await _open(store, "prj_1", conversation.conversation_id, key="a")
    first_events = await store.events_after(conversation.conversation_id, 0)
    assert [e.sequence for e in first_events] == [1]
    assert await store.events_after(conversation.conversation_id, 1) == []
    messages = await store.list_messages(conversation.conversation_id)
    assert len(messages) == 1 and messages[0].role == m.ROLE_AUTHOR


@pytest.mark.anyio
async def test_idempotency_scoped_per_conversation(make_store) -> None:  # type: ignore[no-untyped-def]
    store = make_store()
    c1 = await store.create_conversation("prj_1", "one")
    c2 = await store.create_conversation("prj_1", "two")
    a = await _open(store, "prj_1", c1.conversation_id, key="same")
    b = await _open(store, "prj_1", c2.conversation_id, key="same")
    assert a.turn.turn_id != b.turn.turn_id
    assert b.created is True


@pytest.mark.anyio
async def test_mark_interrupted_settles_leftover_turns(make_store) -> None:  # type: ignore[no-untyped-def]
    store = make_store()
    busy = await store.create_conversation("prj_1", "running")
    running = await _open(store, "prj_1", busy.conversation_id, key="r")
    assistant = m.ChatMessage(
        message_id="msg_run",
        conversation_id=busy.conversation_id,
        turn_id=running.turn.turn_id,
        role=m.ROLE_ASSISTANT,
        kind=m.KIND_TEXT,
        text="部分",
        model="m",
        status=m.MESSAGE_STREAMING,
        created_at=NOW,
    )
    await store.start_turn(running.turn.turn_id, assistant_message=assistant, model="m")
    queued_conv = await store.create_conversation("prj_1", "queued")
    await _open(store, "prj_1", queued_conv.conversation_id, key="q")
    count = await store.mark_interrupted()
    assert count == 2
    turn = await store.get_turn(running.turn.turn_id)
    assert turn is not None and turn.status == m.TURN_INTERRUPTED
    message = await store.get_message("msg_run")
    assert message is not None and message.status == m.MESSAGE_FAILED and message.text == "部分"
    assert await store.mark_interrupted() == 0
