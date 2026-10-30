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
from hastings.workspace.operations import OperationStore
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


class BlockingBeforeFirstTokenProvider:
    """已经受理、还没发出任何增量：用来验证首 token 前暂停不会写出半截回复。"""

    def __init__(self) -> None:
        self.started = asyncio.Event()

    def stream_response(self, **kwargs: Any) -> Any:
        signal = kwargs.get("signal")

        async def generator() -> Any:
            self.started.set()
            while signal is None or not signal.is_cancelled():
                await asyncio.sleep(0.01)
            yield AssistantDoneEvent(
                reason="stop",
                message=AssistantMessage(
                    content=[TextContent(text="取消之后才补上的完整回复")], stop_reason="stop"
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


def test_pause_before_first_token_writes_no_partial_reply(tmp_path: Path) -> None:
    provider = BlockingBeforeFirstTokenProvider()
    workspace, store, service = service_for(tmp_path, provider)

    async def scenario() -> list[dict[str, Any]]:
        info = await service.accept("只讨论开篇", "pause-before-token")
        await asyncio.wait_for(provider.started.wait(), timeout=2)
        paused = await service.pause_operation(info.operation_id, "pause-before-token-1")
        assert paused.status == "paused"
        await service.wait(info.turn_id)
        return list(store.events(info.conversation_id))

    events = asyncio.run(scenario())

    # 一个增量都没到：暂停后既没有半截回复，也没有把取消后的完整文本补进来。
    assert [event for event in events if event["type"] == "assistant_message"] == []
    runs = [event for event in events if event["type"] == "run_finished"]
    assert runs[-1]["status"] == "stopped"


def test_pause_is_idempotent_and_does_not_rewrite_finished_operation(tmp_path: Path) -> None:
    provider = ScriptedProvider(
        [
            [
                AssistantDoneEvent(
                    reason="stop",
                    message=AssistantMessage(content=[TextContent(text="好")], stop_reason="stop"),
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
        repeated = await service.interject_operation(
            info.operation_id, "interject-key", expected_generation=1, text="把语气改得更克制"
        )
        assert repeated.generation == 2
        with pytest.raises(WorkspaceError) as different_text:
            await service.interject_operation(
                info.operation_id, "interject-key", expected_generation=1, text="改掉结局"
            )
        assert different_text.value.code == "OPERATION_REQUEST_CONFLICT"
        with pytest.raises(WorkspaceError) as different_generation:
            await service.interject_operation(
                info.operation_id, "interject-key", expected_generation=2, text="把语气改得更克制"
            )
        assert different_generation.value.code == "OPERATION_REQUEST_CONFLICT"
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


def test_interjection_is_saved_with_its_new_generation(tmp_path: Path) -> None:
    provider = ScriptedProvider(
        [[TextDeltaEvent(content_index=0, delta="初稿", partial=_partial("初稿"))]],
        hang_at=0,
    )
    _, _, service = service_for(tmp_path, provider)
    saved: list[dict[str, Any]] = []
    original_save = service.operations.save

    def observe_save(operation: Any) -> Any:
        result = original_save(operation)
        saved.append(service.operations.read(operation.id).to_dict())
        return result

    service.operations.save = observe_save  # type: ignore[method-assign]

    async def scenario() -> None:
        info = await service.accept("写开篇", "atomic-interject")
        await wait_until(lambda: service.operation(info.operation_id).status == "running")
        await service.interject_operation(
            info.operation_id, "interrupt", expected_generation=1, text="保留原结局"
        )
        assert saved
        assert saved[0]["generation"] == 2
        assert saved[0]["instruction_log"][-1]["text"] == "保留原结局"
        assert saved[0]["controls"]["interrupt"]["expected_generation"] == 1
        await wait_until(lambda: provider.calls == 2)
        await service.wait(info.turn_id)

    asyncio.run(scenario())


def test_opening_another_workspace_does_not_interrupt_a_running_operation(tmp_path: Path) -> None:
    provider = ScriptedProvider(
        [[TextDeltaEvent(content_index=0, delta="初稿", partial=_partial("初稿"))]],
        hang_at=0,
    )
    operations = OperationStore(tmp_path / "global-state")
    first = TurnService(
        Workspace.create(tmp_path / "first"),
        ConversationStore(tmp_path / "first-state"),
        lambda: snapshot(provider),
        workspace_id="first",
        operations=operations,
    )
    second = TurnService(
        Workspace.create(tmp_path / "second"),
        ConversationStore(tmp_path / "second-state"),
        lambda: snapshot(provider),
        workspace_id="second",
        operations=operations,
    )

    async def scenario() -> None:
        info = await first.accept("写开篇", "workspace-one")
        await wait_until(lambda: first.operation(info.operation_id).status == "running")
        second.mark_interrupted()
        assert first.operation(info.operation_id).status == "running"
        assert first.operation(info.operation_id).generation == 1
        first._write_guard(info)()
        await first.pause_operation(info.operation_id, "finish")
        await first.wait(info.turn_id)

    asyncio.run(scenario())


def test_rapid_interjections_skip_superseded_attempt_and_keep_both_requests(tmp_path: Path) -> None:
    provider = ScriptedProvider(
        [
            [TextDeltaEvent(content_index=0, delta="初稿", partial=_partial("初稿"))],
            [
                AssistantDoneEvent(
                    reason="stop",
                    message=AssistantMessage(
                        content=[TextContent(text="完成")], stop_reason="stop"
                    ),
                )
            ],
        ],
        hang_at=0,
    )
    _, store, service = service_for(tmp_path, provider)

    async def scenario() -> Any:
        info = await service.accept("写开篇", "rapid")
        await wait_until(lambda: provider.calls == 1)
        first = await service.interject_operation(
            info.operation_id, "i1", expected_generation=1, text="保留结局"
        )
        assert first.generation == 2
        second = await service.interject_operation(
            info.operation_id, "i2", expected_generation=2, text="语气克制"
        )
        assert second.generation == 3
        await wait_until(lambda: service.operation(info.operation_id).status == "completed")
        return info

    info = asyncio.run(scenario())
    assert provider.calls == 2
    messages = [
        message.text
        for message in provider.requests[1]["messages"]
        if isinstance(message, UserMessage)
    ]
    assert messages.count("保留结局") == 1
    assert sum("语气克制" in message for message in messages) == 1
    assert [
        e["text"] for e in store.events(info.conversation_id) if e["type"] == "author_interjected"
    ] == ["保留结局", "语气克制"]


def test_pause_while_next_generation_waits_does_not_start_or_block_workspace(
    tmp_path: Path,
) -> None:
    provider = ScriptedProvider(
        [[TextDeltaEvent(content_index=0, delta="初稿", partial=_partial("初稿"))]],
        hang_at=0,
    )
    _, _, service = service_for(tmp_path, provider)

    async def scenario() -> None:
        info = await service.accept("写开篇", "pause-waiter")
        await wait_until(lambda: provider.calls == 1)
        await service.interject_operation(
            info.operation_id, "i1", expected_generation=1, text="保留结局"
        )
        await service.pause_operation(info.operation_id, "p2")
        await wait_until(lambda: not service.busy)
        assert service.operation(info.operation_id).status == "paused"
        assert service.operation(info.operation_id).generation == 2

    asyncio.run(scenario())
    assert provider.calls == 1


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
            await service.continue_operation(info.operation_id, "c-1", expected_generation=1)
        await service.pause_operation(info.operation_id, "pause-1")
        with pytest.raises(WorkspaceError) as generation:
            await service.continue_operation(info.operation_id, "c-2", expected_generation=7)
        await service.wait(info.turn_id)
        return running.value.code, generation.value.code

    running_code, generation_code = asyncio.run(scenario())

    assert running_code == "OPERATION_STATE_CONFLICT"
    assert generation_code == "OPERATION_GENERATION_CONFLICT"


def test_restarted_service_continues_only_after_explicit_request(tmp_path: Path) -> None:
    provider = ScriptedProvider(
        [
            [TextDeltaEvent(content_index=0, delta="写到一半", partial=_partial("写到一半"))],
            [
                AssistantDoneEvent(
                    reason="stop",
                    message=AssistantMessage(
                        content=[TextContent(text="继续完成")], stop_reason="stop"
                    ),
                )
            ],
        ],
        hang_at=0,
    )
    workspace, store, service = service_for(tmp_path, provider)

    async def scenario() -> None:
        info = await service.accept("写开篇", "restart-1")
        await wait_until(lambda: service.operation(info.operation_id).status == "running")
        await service.pause_operation(info.operation_id, "pause-1")
        await service.wait(info.turn_id)
        calls = provider.calls
        restarted = TurnService(workspace, store, lambda: snapshot(provider))
        restarted.mark_interrupted()
        recovered = restarted.operation(info.operation_id)
        assert recovered.status == "needs_attention"
        assert recovered.resumable
        assert provider.calls == calls
        restarted.mark_interrupted()
        assert restarted.operation(info.operation_id).generation == recovered.generation
        await restarted.continue_operation(
            info.operation_id, "c-1", expected_generation=recovered.generation
        )
        await wait_until(lambda: restarted.operation(info.operation_id).status == "completed")
        assert provider.calls == calls + 1
        assert any(event.get("text") == "继续完成" for event in store.events(info.conversation_id))

    asyncio.run(scenario())


def test_restart_refuses_changed_reference_without_starting_model(tmp_path: Path) -> None:
    from hastings.workspace.materials import ContextRequest

    provider = ScriptedProvider(
        [[TextDeltaEvent(content_index=0, delta="一半", partial=_partial("一半"))]], hang_at=0
    )
    workspace, store, service = service_for(tmp_path, provider)
    path = "正文/0001 雨夜.md"
    original = workspace.write(path, "# 雨夜\n\n原文。", expected_digest=None)

    async def scenario() -> None:
        info = await service.accept(
            "讨论原文", "restart-ref", context=ContextRequest(reference_paths=(path,))
        )
        await wait_until(lambda: service.operation(info.operation_id).status == "running")
        await service.pause_operation(info.operation_id, "pause")
        await service.wait(info.turn_id)
        workspace.write(path, "# 雨夜\n\n修改。", expected_digest=original.digest)
        restarted = TurnService(workspace, store, lambda: snapshot(provider))
        restarted.mark_interrupted()
        recovered = restarted.operation(info.operation_id)
        calls = provider.calls
        with pytest.raises(WorkspaceError):
            await restarted.continue_operation(
                info.operation_id, "continue", expected_generation=recovered.generation
            )
        assert provider.calls == calls
        assert restarted.operation(info.operation_id).generation == recovered.generation

    asyncio.run(scenario())


def test_restart_rejects_changed_story_revision(tmp_path: Path) -> None:
    from hastings.workspace.materials import ContextRequest
    from hastings.workspace.story_context import StoryRequest
    from hastings.workspace.story_models import AddNode, StoryChangeSet, StoryNode
    from hastings.workspace.story_store import StoryStore

    provider = ScriptedProvider(
        [[TextDeltaEvent(content_index=0, delta="一半", partial=_partial("一半"))]], hang_at=0
    )
    workspace, store, service = service_for(tmp_path, provider)
    stories = StoryStore(store.root, workspace)
    receipt = stories.commit_change(
        variant_id="main",
        request_key="seed",
        change=StoryChangeSet(
            id="seed",
            base_revision="empty",
            operations=[AddNode(node=StoryNode(id="n1", title="开篇", action="进门"))],
        ),
    )

    async def scenario() -> None:
        info = await service.accept(
            "讨论开篇",
            "restart-story",
            context=ContextRequest(
                story=StoryRequest(variant_id="main", purpose="discuss", node_ids=("n1",))
            ),
        )
        await wait_until(lambda: service.operation(info.operation_id).status == "running")
        await service.pause_operation(info.operation_id, "pause")
        await service.wait(info.turn_id)
        stories.commit_change(
            variant_id="main",
            request_key="new",
            change=StoryChangeSet(
                id="new",
                base_revision=receipt.revision,
                operations=[AddNode(node=StoryNode(id="n2", title="新节点", action="下雨"))],
            ),
        )
        restarted = TurnService(workspace, store, lambda: snapshot(provider))
        restarted.mark_interrupted()
        operation = restarted.operation(info.operation_id)
        calls = provider.calls
        with pytest.raises(WorkspaceError):
            await restarted.continue_operation(
                info.operation_id, "continue", expected_generation=operation.generation
            )
        assert provider.calls == calls
        assert restarted.operation(info.operation_id).generation == operation.generation

    asyncio.run(scenario())


def test_restart_refuses_changed_selected_text(tmp_path: Path) -> None:
    from hastings.workspace.materials import ContextRequest

    provider = ScriptedProvider(
        [[TextDeltaEvent(content_index=0, delta="一半", partial=_partial("一半"))]], hang_at=0
    )
    workspace, store, service = service_for(tmp_path, provider)
    path = "正文/0001 雨夜.md"
    original = workspace.write(path, "# 雨夜\n\n原文。", expected_digest=None)

    async def scenario() -> None:
        info = await service.accept(
            "讨论原文", "restart-selected", context=ContextRequest(selected_path=path)
        )
        await wait_until(lambda: service.operation(info.operation_id).status == "running")
        await service.pause_operation(info.operation_id, "pause")
        await service.wait(info.turn_id)
        workspace.write(path, "# 雨夜\n\n新文。", expected_digest=original.digest)
        restarted = TurnService(workspace, store, lambda: snapshot(provider))
        restarted.mark_interrupted()
        operation = restarted.operation(info.operation_id)
        with pytest.raises(WorkspaceError, match="OPERATION_RECOVERED_CONFLICT"):
            await restarted.continue_operation(
                info.operation_id, "continue", expected_generation=operation.generation
            )
        assert restarted.operation(info.operation_id).generation == operation.generation

    asyncio.run(scenario())


def test_restart_refuses_changed_model_without_consuming_continue(tmp_path: Path) -> None:
    provider = ScriptedProvider(
        [[TextDeltaEvent(content_index=0, delta="一半", partial=_partial("一半"))]], hang_at=0
    )
    workspace, store, service = service_for(tmp_path, provider)

    async def scenario() -> None:
        info = await service.accept("讨论", "restart-model")
        await wait_until(lambda: service.operation(info.operation_id).status == "running")
        await service.pause_operation(info.operation_id, "pause")
        await service.wait(info.turn_id)
        restarted = TurnService(
            workspace,
            store,
            lambda: ModelSnapshot(
                connection_id="different", display_name="other", model="fake", provider=provider
            ),
        )
        restarted.mark_interrupted()
        recovered = restarted.operation(info.operation_id)
        with pytest.raises(WorkspaceError, match="OPERATION_RECOVERED_CONFLICT"):
            await restarted.continue_operation(
                info.operation_id, "continue", expected_generation=recovered.generation
            )
        assert restarted.operation(info.operation_id).status == "needs_attention"
        assert restarted.operation(info.operation_id).generation == recovered.generation

    asyncio.run(scenario())


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
