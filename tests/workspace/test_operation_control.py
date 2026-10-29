"""1.0.1 P2：统一操作的暂停、继续、插话与迟到输出，不调用真实模型。

这里用脚本化 provider 控制「模型什么时候说什么」。要验证的是控制面的边界：
暂停之后旧代次还能不能写、继续是不是真的开了新的一代、插话有没有作为作者的话
进入下一代的请求，以及控制请求重复提交会不会产生第二次副作用。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.service import TurnService, WriteSession
from hastings.workspace.state import ConversationStore
from tau_agent.messages import AssistantMessage, TextContent, UserMessage
from tau_agent.provider_events import (
    AssistantDoneEvent,
    AssistantStartEvent,
    TextDeltaEvent,
)


def _partial(text: str = "") -> AssistantMessage:
    return AssistantMessage(content=[TextContent(text=text)] if text else [])


class ScriptedProvider:
    """按脚本吐事件；``hang_at`` 指定的那次调用在脚本之后一直等取消。"""

    def __init__(self, turns: list[list[Any]], *, hang_at: int | None = None) -> None:
        self.turns = turns
        self.hang_at = hang_at
        self.calls = 0
        self.requests: list[dict[str, Any]] = []

    def stream_response(self, **kwargs: Any) -> Any:
        index = self.calls
        self.calls += 1
        self.requests.append({"messages": list(kwargs.get("messages") or [])})
        signal = kwargs.get("signal")

        async def generator() -> Any:
            yield AssistantStartEvent(partial=_partial())
            if index < len(self.turns):
                for event in self.turns[index]:
                    yield event
            if self.hang_at is not None and index == self.hang_at:
                while signal is None or not signal.is_cancelled():
                    await asyncio.sleep(0.02)

        return generator()


class LateTextProvider:
    """取消之后仍想补一段完整文本：用来证明晚到内容不会落盘。"""

    def __init__(self) -> None:
        self.streaming = asyncio.Event()

    def stream_response(self, **kwargs: Any) -> Any:
        signal = kwargs.get("signal")

        async def generator() -> Any:
            yield AssistantStartEvent(partial=_partial())
            yield TextDeltaEvent(
                content_index=0, delta="早到的内容", partial=_partial("早到的内容")
            )
            self.streaming.set()
            while signal is None or not signal.is_cancelled():
                await asyncio.sleep(0.01)
            yield AssistantDoneEvent(
                reason="stop",
                message=AssistantMessage(
                    content=[TextContent(text="早到的内容晚到的内容")], stop_reason="stop"
                ),
            )

        return generator()


def snapshot(provider: Any) -> ModelSnapshot:
    return ModelSnapshot(
        connection_id="test", display_name="测试连接", model="fake", provider=provider
    )


def service_for(tmp_path: Path, provider: Any) -> tuple[Workspace, ConversationStore, TurnService]:
    workspace = Workspace.create(tmp_path / "book")
    store = ConversationStore(tmp_path / "state")
    service = TurnService(workspace, store, lambda: snapshot(provider))
    return workspace, store, service


async def wait_until(predicate: Any, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("等待条件超时")


def test_pause_records_paused_operation_and_stops_late_text(tmp_path: Path) -> None:
    provider = LateTextProvider()
    workspace, store, service = service_for(tmp_path, provider)

    async def scenario() -> list[dict[str, Any]]:
        info = await service.accept("只讨论开篇", "pause-late")
        await asyncio.wait_for(provider.streaming.wait(), timeout=2)
        paused = await service.pause_operation(info.operation_id, "pause-1")
        assert paused.status == "paused"
        await service.wait(info.turn_id)
        return list(store.events(info.conversation_id))

    events = asyncio.run(scenario())

    texts = [event["text"] for event in events if event["type"] == "assistant_message"]
    # 暂停前到达的增量保留；取消之后才补上来的完整文本不能覆盖回来。
    assert texts and "早到的内容" in texts[-1]
    assert "晚到的内容" not in texts[-1]
    runs = [event for event in events if event["type"] == "run_finished"]
    assert runs[-1]["status"] == "stopped"


def test_pause_is_idempotent_and_does_not_rewrite_finished_operation(tmp_path: Path) -> None:
    provider = ScriptedProvider(
        [
            [
                AssistantDoneEvent(
                    reason="stop",
                    message=AssistantMessage(
                        content=[TextContent(text="好")], stop_reason="stop"
                    ),
                )
            ]
        ]
    )
    workspace, store, service = service_for(tmp_path, provider)

    async def scenario() -> tuple[Any, Any, int]:
        info = await service.accept("讨论开篇", "pause-idempotent")
        await service.wait(info.turn_id)
        finished = service.operation(info.operation_id)
        first = await service.pause_operation(info.operation_id, "pause-1")
        second = await service.pause_operation(info.operation_id, "pause-1")
        events = list(store.events(info.conversation_id))
        return finished, (first, second), sum(e["type"] == "stop_requested" for e in events)

    finished, (first, second), stops = asyncio.run(scenario())

    assert finished.status == "completed"
    # 已经完成的操作不会被改成暂停；重复暂停只返回同一份状态。
    assert first.status == "completed"
    assert second.status == "completed"
    assert stops == 0


def test_continue_starts_new_generation_with_previous_output_as_history(tmp_path: Path) -> None:
    provider = ScriptedProvider(
        [
            [
                TextDeltaEvent(content_index=0, delta="第一段", partial=_partial("第一段")),
            ],
            [
                AssistantDoneEvent(
                    reason="stop",
                    message=AssistantMessage(
                        content=[TextContent(text="第二段")], stop_reason="stop"
                    ),
                )
            ],
        ],
        hang_at=0,
    )
    workspace, store, service = service_for(tmp_path, provider)

    async def scenario() -> Any:
        info = await service.accept("写开篇", "continue-1")
        await wait_until(
            lambda: any(
                event["type"] == "assistant_block_delta"
                for event in store.events(info.conversation_id)
            )
        )
        await service.pause_operation(info.operation_id, "pause-1")
        await wait_until(lambda: service.operation(info.operation_id).status == "paused")
        resumed = await service.continue_operation(
            info.operation_id, "continue-1-key", expected_generation=1
        )
        assert resumed.generation == 2
        await wait_until(lambda: service.operation(info.operation_id).status == "completed")
        return info

    info = asyncio.run(scenario())

    assert provider.calls == 2
    second = provider.requests[1]["messages"]
    users = [message.text for message in second if isinstance(message, UserMessage)]
    replies = [
        "".join(block.text for block in message.content)
        for message in second
        if isinstance(message, AssistantMessage)
    ]
    # 上一代已经写出的内容作为历史进入新代次；当前提示是应用转达的继续说明。
    assert "第一段" in replies
    assert users[0] == "写开篇"
    assert users[-1].startswith("（应用转达）继续完成上一条要求")
    operation = service.operation(info.operation_id)
    assert operation.generation == 2
    assert operation.status == "completed"
    assert [item["kind"] for item in operation.instruction_log] == ["author", "continue"]


def test_interject_adds_author_text_and_registers_next_generation(tmp_path: Path) -> None:
    provider = ScriptedProvider(
        [
            [TextDeltaEvent(content_index=0, delta="初稿", partial=_partial("初稿"))],
            [
                AssistantDoneEvent(
                    reason="stop",
                    message=AssistantMessage(
                        content=[TextContent(text="改好了")], stop_reason="stop"
                    ),
                )
            ],
        ],
        hang_at=0,
    )
    workspace, store, service = service_for(tmp_path, provider)

    async def scenario() -> Any:
        info = await service.accept("写开篇", "interject-1")
        await wait_until(
            lambda: any(
                event["type"] == "assistant_block_delta"
                for event in store.events(info.conversation_id)
            )
        )
        changed = await service.interject_operation(
            info.operation_id,
            "interject-key",
            expected_generation=1,
            text="把语气改得更克制",
        )
        assert changed.generation == 2
        await wait_until(lambda: service.operation(info.operation_id).status == "completed")
        return info, list(store.events(info.conversation_id))

    info, events = asyncio.run(scenario())

    interjections = [event for event in events if event["type"] == "author_interjected"]
    assert [event["text"] for event in interjections] == ["把语气改得更克制"]
    # 插话作为作者的话进入下一代请求。
    second = provider.requests[1]["messages"]
    users = [message.text for message in second if isinstance(message, UserMessage)]
    assert any("把语气改得更克制" in text for text in users)
    assert not any(text == "写开篇" for text in users[1:])
    assert service.operation(info.operation_id).status == "completed"


def test_continue_requires_paused_state_and_matching_generation(tmp_path: Path) -> None:
    provider = ScriptedProvider(
        [[TextDeltaEvent(content_index=0, delta="还在写", partial=_partial("还在写"))]],
        hang_at=0,
    )
    workspace, store, service = service_for(tmp_path, provider)

    async def scenario() -> tuple[str, str]:
        info = await service.accept("写开篇", "continue-guard")
        await wait_until(lambda: service.operation(info.operation_id).status == "running")
        with pytest.raises(WorkspaceError) as running:
            await service.continue_operation(
                info.operation_id, "c-1", expected_generation=1
            )
        await service.pause_operation(info.operation_id, "pause-1")
        with pytest.raises(WorkspaceError) as generation:
            await service.continue_operation(
                info.operation_id, "c-2", expected_generation=7
            )
        await service.wait(info.turn_id)
        return running.value.code, generation.value.code

    running_code, generation_code = asyncio.run(scenario())

    assert running_code == "OPERATION_STATE_CONFLICT"
    assert generation_code == "OPERATION_GENERATION_CONFLICT"


def test_restarted_service_marks_operation_for_attention_and_refuses_continue(
    tmp_path: Path,
) -> None:
    provider = ScriptedProvider(
        [[TextDeltaEvent(content_index=0, delta="写到一半", partial=_partial("写到一半"))]],
        hang_at=0,
    )
    workspace, store, service = service_for(tmp_path, provider)

    async def scenario() -> tuple[str, str]:
        info = await service.accept("写开篇", "restart-1")
        await wait_until(lambda: service.operation(info.operation_id).status == "running")
        await service.pause_operation(info.operation_id, "pause-1")
        await service.wait(info.turn_id)
        # 新进程：同一状态目录，新的服务实例。
        restarted = TurnService(workspace, store, lambda: snapshot(provider))
        restarted.mark_interrupted()
        recovered = restarted.operation(info.operation_id)
        with pytest.raises(WorkspaceError) as excinfo:
            await restarted.continue_operation(
                info.operation_id, "c-1", expected_generation=recovered.generation
            )
        return recovered.status, excinfo.value.code

    status, code = asyncio.run(scenario())

    assert status == "needs_attention"
    assert code == "OPERATION_RECOVERED_CONFLICT"


def test_write_session_refuses_to_prepare_once_the_generation_is_revoked(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path / "book")

    def revoked() -> None:
        raise WorkspaceError(
            code="OPERATION_PAUSED_CONFLICT",
            where="turn.write",
            why="这次操作已经暂停",
            fix="继续后再写",
        )

    session = WriteSession(workspace, instruction="写一章", guard=revoked)

    with pytest.raises(WorkspaceError) as excinfo:
        session.prepare()

    assert excinfo.value.code == "OPERATION_PAUSED_CONFLICT"
    assert session.prepared is False
    assert session.finish("stopped").recording == "no_change"
