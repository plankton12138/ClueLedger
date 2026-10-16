"""对话领域模型（Phase 19.3 T19.3a）：形状、序列化与状态不变量。"""

from hastings.domain.conversation import models as m


def test_conversation_round_trips_through_dict():
    conversation = m.Conversation(
        conversation_id="conv_1",
        project_id="prj_1",
        title="讨论凶手动机",
        created_at="2026-10-16T00:00:00+00:00",
        updated_at="2026-10-16T00:00:01+00:00",
    )
    data = conversation.to_dict()
    assert data == {
        "conversation_id": "conv_1",
        "project_id": "prj_1",
        "title": "讨论凶手动机",
        "created_at": "2026-10-16T00:00:00+00:00",
        "updated_at": "2026-10-16T00:00:01+00:00",
    }
    assert m.Conversation.from_dict(data) == conversation


def test_message_round_trips_and_keeps_refs_and_payload():
    message = m.ChatMessage(
        message_id="msg_1",
        conversation_id="conv_1",
        turn_id="turn_1",
        role=m.ROLE_AUTHOR,
        kind=m.KIND_TEXT,
        text="凶手是谁？",
        refs=[{"ref_id": "r1", "kind": "chapter", "target_id": "ch1", "title": "第一章",
               "version": "cmt_1", "saved": True}],
        model=None,
        status=m.MESSAGE_COMPLETED,
        error=None,
        created_at="2026-10-16T00:00:00+00:00",
    )
    restored = m.ChatMessage.from_dict(message.to_dict())
    assert restored == message
    assert restored.refs[0]["ref_id"] == "r1"
    assert restored.payload is None


def test_turn_round_trips_with_optional_retry_of():
    turn = m.ChatTurn(
        turn_id="turn_1",
        conversation_id="conv_1",
        status=m.TURN_RUNNING,
        model="deepseek-chat",
        end_reason=None,
        idempotency_key="key-1",
        retry_of="turn_0",
    )
    assert m.ChatTurn.from_dict(turn.to_dict()) == turn


def test_event_envelope_round_trips():
    event = m.ChatEvent(
        project_id="prj_1",
        conversation_id="conv_1",
        turn_id="turn_1",
        sequence=3,
        event={"type": "message.delta", "turn_id": "turn_1", "text": "你好"},
    )
    data = event.to_dict()
    assert data["sequence"] == 3
    assert data["event"]["type"] == "message.delta"
    assert m.ChatEvent.from_dict(data) == event


def test_terminal_turn_status_classifier():
    assert not m.is_terminal_turn(m.TURN_QUEUED)
    assert not m.is_terminal_turn(m.TURN_RUNNING)
    for status in (m.TURN_COMPLETED, m.TURN_STOPPED, m.TURN_FAILED, m.TURN_INTERRUPTED):
        assert m.is_terminal_turn(status)


def test_message_status_for_turn_maps_settlement():
    assert m.message_status_for_turn(m.TURN_COMPLETED) == m.MESSAGE_COMPLETED
    assert m.message_status_for_turn(m.TURN_STOPPED) == m.MESSAGE_STOPPED
    assert m.message_status_for_turn(m.TURN_FAILED) == m.MESSAGE_FAILED
    assert m.message_status_for_turn(m.TURN_INTERRUPTED) == m.MESSAGE_FAILED


def test_from_dict_rejects_unknown_role():
    import pytest

    with pytest.raises(m.ConversationViolation):
        m.ChatMessage.from_dict(
            {
                "message_id": "msg_1",
                "conversation_id": "conv_1",
                "turn_id": None,
                "role": "robot",
                "kind": "text",
                "text": "",
                "refs": [],
                "model": None,
                "status": "completed",
                "error": None,
                "created_at": "2026-10-16T00:00:00+00:00",
            }
        )
