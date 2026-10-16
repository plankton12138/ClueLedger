"""ConversationService 行为契约测试（Phase 19.3 T19.3d）。

先于实现写失败测试（计划 §19.3 清单）：聊天不产生创作 Job/Commit、项目隔离、
发送幂等、取消竞态（迟到分片无效）、部分回复保留、重启中断标记、Provider
失效不跨厂商回退（由错误分类保证）。执行器用可控 FakeRunner，不访问网络。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from hastings.application.errors import EnvironmentFailure, HastingsError
from hastings.application.services.conversation_service import ConversationService
from hastings.domain.conversation import error_codes as ec
from hastings.domain.conversation import models as m
from hastings.infrastructure.conversations.store import MemoryConversationStore


class FakeRunner:
    """按脚本执行的 ChatRunner：``script(turn_id)`` 返回分片序列或异常。"""

    def __init__(self, pace: float = 0.0) -> None:
        self.calls: list[str] = []
        self.gate: asyncio.Event | None = None
        self.script: dict[str, Any] = {}
        self.default: Any = ["回复", "完成"]
        self.pace = pace

    async def stream(self, run: Any, sink: Any) -> str:
        model = run.model
        self.calls.append(model)
        if self.gate is not None:
            await self.gate.wait()
            self.gate = None
        behaviour = self.script.get(model, self.default)
        if isinstance(behaviour, Exception):
            raise behaviour
        text = ""
        for delta in behaviour:
            await sink(delta)
            text += delta
            if self.pace:
                await asyncio.sleep(self.pace)
            else:
                await asyncio.sleep(0)
        return text


def _service(
    runner: FakeRunner | None = None, **kwargs: Any
) -> tuple[ConversationService, FakeRunner]:
    runner = runner or FakeRunner()
    store = MemoryConversationStore()
    service = ConversationService(
        store,
        runner_factory=lambda: runner,
        model_resolver=lambda: "test-model",
        **kwargs,
    )
    return service, runner


async def _settled(service: ConversationService, turn_id: str) -> m.ChatTurn | None:
    for _ in range(200):
        turn = await service.store.get_turn(turn_id)
        if turn is not None and m.is_terminal_turn(turn.status):
            return turn
        await asyncio.sleep(0.01)
    return await service.store.get_turn(turn_id)


async def _new_conversation(service: ConversationService, project_id: str = "p1") -> str:
    conversation = await service.create_conversation(project_id)
    return conversation.conversation_id


# -- 基本轮次生命周期 ---------------------------------------------------------


@pytest.mark.anyio
async def test_send_turn_runs_to_completed_and_persists_text() -> None:
    service, runner = _service()
    runner.default = ["你好", "，作者"]
    conversation_id = await _new_conversation(service)

    result = await service.send_turn(
        "p1", conversation_id, text="打个招呼", refs=(), idempotency_key="k1"
    )
    assert result.created
    assert result.turn.status == m.TURN_QUEUED

    turn = await _settled(service, result.turn.turn_id)
    assert turn is not None and turn.status == m.TURN_COMPLETED
    assert turn.model == "test-model"

    messages = await service.store.list_messages(conversation_id)
    assistant = [x for x in messages if x.role == m.ROLE_ASSISTANT]
    assert len(assistant) == 1
    assert assistant[0].text == "你好，作者"
    assert assistant[0].status == m.MESSAGE_COMPLETED
    # 实际模型定格在轮次与消息上（可观测性是事件契约的一部分）。
    assert assistant[0].model == "test-model"


@pytest.mark.anyio
async def test_chat_never_touches_job_or_commit_surfaces() -> None:
    """普通聊天没有写端口：服务对象上不存在任何提交/任务入口（结构断言）。"""
    service, _ = _service()
    conversation_id = await _new_conversation(service)
    result = await service.send_turn("p1", conversation_id, "聊聊", (), "k1")
    await _settled(service, result.turn.turn_id)
    for forbidden in ("commit", "submit", "approve", "accept", "open_job", "create_job"):
        assert not any(forbidden in name for name in dir(service)), forbidden


@pytest.mark.anyio
async def test_events_are_persisted_with_increasing_sequence() -> None:
    service, runner = _service()
    runner.default = ["片段"]
    conversation_id = await _new_conversation(service)
    result = await service.send_turn("p1", conversation_id, "问", (), "k1")
    await _settled(service, result.turn.turn_id)
    events = await service.store.events_after(conversation_id, 0)
    sequences = [e.sequence for e in events]
    assert sequences == sorted(sequences)
    types = [e.event["type"] for e in events]
    assert types[0] == "turn.accepted"
    assert "turn.started" in types
    assert "message.delta" in types
    assert types[-1] == "message.completed"


# -- 幂等与并发 ---------------------------------------------------------------


@pytest.mark.anyio
async def test_idempotent_replay_returns_same_turn_without_second_execution() -> None:
    service, runner = _service()
    conversation_id = await _new_conversation(service)
    first = await service.send_turn("p1", conversation_id, "问", (), "k1")
    replay = await service.send_turn("p1", conversation_id, "问", (), "k1")
    assert not replay.created
    assert replay.turn.turn_id == first.turn.turn_id
    await _settled(service, first.turn.turn_id)
    assert len(runner.calls) == 1


@pytest.mark.anyio
async def test_same_key_different_input_is_a_conflict() -> None:
    service, _ = _service()
    conversation_id = await _new_conversation(service)
    await service.send_turn("p1", conversation_id, "问 A", (), "k1")
    with pytest.raises(HastingsError) as excinfo:
        await service.send_turn("p1", conversation_id, "问 B", (), "k1")
    assert excinfo.value.code == ec.CHAT_IDEMPOTENCY_CONFLICT


@pytest.mark.anyio
async def test_second_turn_while_running_is_busy() -> None:
    service, runner = _service()
    runner.gate = asyncio.Event()
    conversation_id = await _new_conversation(service)
    first = await service.send_turn("p1", conversation_id, "慢问", (), "k1")
    with pytest.raises(HastingsError) as excinfo:
        await service.send_turn("p1", conversation_id, "第二问", (), "k2")
    assert excinfo.value.code == ec.CHAT_CONVERSATION_BUSY
    runner.gate.set()
    await _settled(service, first.turn.turn_id)


@pytest.mark.anyio
async def test_global_concurrency_is_bounded() -> None:
    service, runner = _service(max_concurrent=1)
    runner.gate = asyncio.Event()
    c1, c2 = await _new_conversation(service), await _new_conversation(service)
    first = await service.send_turn("p1", c1, "一", (), "k1")
    second = await service.send_turn("p1", c2, "二", (), "k2")
    await asyncio.sleep(0.05)
    # 全局槽位为 1：第二个会话排在后面，模型尚未被调用。
    assert len(runner.calls) == 1
    runner.gate.set()
    await asyncio.wait_for(_settled(service, first.turn.turn_id), 2)
    await asyncio.wait_for(_settled(service, second.turn.turn_id), 2)
    assert len(runner.calls) == 2


# -- 取消与失败 ---------------------------------------------------------------


@pytest.mark.anyio
async def test_cancel_preserves_partial_text_and_drops_late_deltas() -> None:
    service, runner = _service()
    runner.default = ["第一段", "第二段", "第三段"]
    runner.pace = 0.05
    conversation_id = await _new_conversation(service)
    result = await service.send_turn("p1", conversation_id, "慢慢说", (), "k1")
    await asyncio.sleep(0.08)
    await service.cancel_turn("p1", conversation_id, result.turn.turn_id)
    turn = await _settled(service, result.turn.turn_id)

    assert turn is not None
    assert turn.status == m.TURN_STOPPED
    messages = await service.store.list_messages(conversation_id)
    assistant = next(x for x in messages if x.role == m.ROLE_ASSISTANT)
    assert 0 < len(assistant.text) < len("第一段第二段第三段")
    # 迟到分片不复活已停止轮次：消息文本停在取消时刻。
    stale = assistant.text
    await asyncio.sleep(0.1)
    messages = await service.store.list_messages(conversation_id)
    assistant = next(x for x in messages if x.role == m.ROLE_ASSISTANT)
    assert assistant.text == stale


@pytest.mark.anyio
async def test_provider_failure_is_settled_with_error_body() -> None:
    runner = FakeRunner()
    runner.default = EnvironmentFailure(
        code=ec.CHAT_PROVIDER_UNAUTHORIZED,
        where="chat.runner",
        why="凭证被拒绝",
        fix="在设置页更新凭证",
    )
    service, _ = _service(runner)
    conversation_id = await _new_conversation(service)
    result = await service.send_turn("p1", conversation_id, "问", (), "k1")
    turn = await _settled(service, result.turn.turn_id)
    assert turn is not None and turn.status == m.TURN_FAILED
    messages = await service.store.list_messages(conversation_id)
    assistant = next(x for x in messages if x.role == m.ROLE_ASSISTANT)
    assert assistant.status == m.MESSAGE_FAILED
    assert assistant.error is not None
    assert assistant.error["code"] == ec.CHAT_PROVIDER_UNAUTHORIZED
    assert assistant.error["why"] and assistant.error["fix"]


@pytest.mark.anyio
async def test_unconfigured_model_fails_the_turn_without_calling_runner() -> None:
    store = MemoryConversationStore()
    runner = FakeRunner()

    def resolver() -> str:
        raise EnvironmentFailure(
            code=ec.CHAT_MODEL_NOT_CONFIGURED,
            where="chat.model",
            why="当前没有可用模型",
            fix="在设置页选择模型",
        )

    service = ConversationService(
        store, runner_factory=lambda: runner, model_resolver=resolver
    )
    conversation_id = await _new_conversation(service)
    result = await service.send_turn("p1", conversation_id, "问", (), "k1")
    turn = await _settled(service, result.turn.turn_id)
    assert turn is not None and turn.status == m.TURN_FAILED
    assert runner.calls == []
    # 失败详情随 turn.failed 事件持久化（三要素），轮次可追溯。
    events = await service.store.events_after(conversation_id, 0)
    failure_events = [e for e in events if e.event.get("type") == "turn.failed"]
    assert failure_events, events
    error = failure_events[-1].event.get("error")
    assert error is not None
    assert error["code"] == ec.CHAT_MODEL_NOT_CONFIGURED
    assert error["why"] and error["fix"]


# -- 重启恢复 ------------------------------------------------------------------


@pytest.mark.anyio
async def test_mark_interrupted_flags_stale_running_turns() -> None:
    service, runner = _service()
    runner.gate = asyncio.Event()
    conversation_id = await _new_conversation(service)
    result = await service.send_turn("p1", conversation_id, "问", (), "k1")
    await asyncio.sleep(0.02)
    count = await service.mark_interrupted()
    assert count >= 1
    turn = await service.store.get_turn(result.turn.turn_id)
    assert turn is not None and turn.status == m.TURN_INTERRUPTED
    messages = await service.store.list_messages(conversation_id)
    assistant = next(x for x in messages if x.role == m.ROLE_ASSISTANT)
    assert assistant.status == m.MESSAGE_FAILED
    # 中断不自动重发：执行次数不再增长。
    calls_before = len(runner.calls)
    await asyncio.sleep(0.05)
    assert len(runner.calls) == calls_before
    runner.gate.set()


@pytest.mark.anyio
async def test_mark_interrupted_is_idempotent() -> None:
    service, runner = _service()
    runner.gate = asyncio.Event()
    conversation_id = await _new_conversation(service)
    result = await service.send_turn("p1", conversation_id, "问", (), "k1")
    await asyncio.sleep(0.02)
    first = await service.mark_interrupted()
    second = await service.mark_interrupted()
    assert first >= 1 and second == 0
    turn = await service.store.get_turn(result.turn.turn_id)
    assert turn is not None and turn.status == m.TURN_INTERRUPTED
    runner.gate.set()


# -- 项目隔离与重试 -------------------------------------------------------------


@pytest.mark.anyio
async def test_cross_project_access_is_rejected() -> None:
    service, _ = _service()
    conversation_id = await _new_conversation(service, project_id="p1")
    with pytest.raises(HastingsError) as excinfo:
        await service.send_turn("p2", conversation_id, "越界", (), "k1")
    assert excinfo.value.code == ec.CHAT_CONVERSATION_NOT_FOUND
    with pytest.raises(HastingsError):
        await service.messages("p2", conversation_id)
    with pytest.raises(HastingsError):
        await service.events("p2", conversation_id, after=0)


@pytest.mark.anyio
async def test_retry_creates_linked_new_turn_and_reruns() -> None:
    runner = FakeRunner()
    runner.default = EnvironmentFailure(
        code=ec.CHAT_PROVIDER_UNAVAILABLE, where="chat.runner", why="断网", fix="重连后重试"
    )
    service, runner = _service(runner)
    conversation_id = await _new_conversation(service)
    first = await service.send_turn("p1", conversation_id, "问", (), "k1")
    await _settled(service, first.turn.turn_id)

    runner.script.clear()
    runner.default = ["好了"]
    retried = await service.retry_turn("p1", conversation_id, first.turn.turn_id)
    assert retried.retry_of == first.turn.turn_id
    turn = await _settled(service, retried.turn_id)
    assert turn is not None and turn.status == m.TURN_COMPLETED
    messages = await service.store.list_messages(conversation_id)
    assistants = [x for x in messages if x.role == m.ROLE_ASSISTANT]
    # 旧回复不覆盖：两次尝试都有消息。
    assert len(assistants) == 2
    assert assistants[-1].text == "好了"


@pytest.mark.anyio
async def test_retry_of_running_turn_is_rejected() -> None:
    service, runner = _service()
    runner.gate = asyncio.Event()
    conversation_id = await _new_conversation(service)
    result = await service.send_turn("p1", conversation_id, "问", (), "k1")
    with pytest.raises(HastingsError) as excinfo:
        await service.retry_turn("p1", conversation_id, result.turn.turn_id)
    assert excinfo.value.code == ec.CHAT_TURN_NOT_RETRYABLE
    runner.gate.set()
    await _settled(service, result.turn.turn_id)


@pytest.mark.anyio
async def test_unknown_conversation_send_is_not_found() -> None:
    service, _ = _service()
    with pytest.raises(HastingsError) as excinfo:
        await service.send_turn("p1", "conv_missing", "问", (), "k1")
    assert excinfo.value.code == ec.CHAT_CONVERSATION_NOT_FOUND
