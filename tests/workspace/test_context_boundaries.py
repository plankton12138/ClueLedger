"""0.9 修复：真实服务边界上的整理、取消、恢复与请求投影。"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from tests.workspace.test_compaction_service import SUMMARY, _seed, _service, _snapshot, _store

from hastings.workspace.compaction import active_compaction
from hastings.workspace.compaction_ops import CompactionRunner
from hastings.workspace.conversation_usage import context_snapshot, summarize_events
from hastings.workspace.errors import WorkspaceError
from tau_agent.messages import AssistantMessage, TextContent, ToolCall, message_text
from tau_agent.provider_events import AssistantDoneEvent


class BoundaryProvider:
    def __init__(self, *, pause: bool = False, tool: bool = False, hang_summary: bool = False):
        self.requests: list[dict[str, Any]] = []
        self.writing_started = asyncio.Event()
        self.summary_started = asyncio.Event()
        self.resume = asyncio.Event()
        if not pause:
            self.resume.set()
        self.tool, self.hang_summary = tool, hang_summary
        self.writes = 0
        self.fail_summary = False

    def stream_response(self, **request: Any):
        self.requests.append(request)

        async def stream():
            if not request["tools"]:
                self.summary_started.set()
                if self.fail_summary:
                    raise OSError("network failure")
                if self.hang_summary:
                    await asyncio.Event().wait()
                body = SUMMARY
            else:
                self.writes += 1
                self.writing_started.set()
                await self.resume.wait()
                if self.tool and self.writes == 1:
                    yield AssistantDoneEvent(
                        reason="toolUse",
                        message=AssistantMessage(
                            content=[
                                ToolCall(
                                    id="read1",
                                    name="read_file",
                                    arguments={"path": "设定集/线索.md"},
                                )
                            ],
                            stop_reason="toolUse",
                        ),
                    )
                    return
                body = "保留第一人称，不修改文件。"
            yield AssistantDoneEvent(
                reason="stop",
                message=AssistantMessage(
                    content=[TextContent(text=body)],
                    stop_reason="stop",
                ),
            )

        return stream()


def setup(tmp_path: Path, provider: BoundaryProvider, *, turns: int = 18):
    store = _store(tmp_path)
    cid = _seed(store, turns=turns)
    service = _service(tmp_path, provider, store)
    snapshot = _snapshot(provider)
    assert snapshot.limits is not None
    snapshot = replace(snapshot, limits=replace(snapshot.limits, context_window_tokens=64_000))
    service.provider_factory = lambda: snapshot
    runner = CompactionRunner(service)
    return service, store, cid, runner


def test_auto_compaction_projects_current_prompt_once_and_counts_summary(tmp_path: Path):
    async def scenario():
        provider = BoundaryProvider(tool=True)
        service, store, cid, _ = setup(tmp_path, provider)
        service.compaction_threshold = 0.05
        info = await service.accept("继续讨论，不改文件", "new", conversation_id=cid)
        await service.wait(info.turn_id)
        events = list(store.events(cid))
        assert active_compaction(events) is not None
        assert events[-1]["status"] == "completed"
        writing = [r for r in provider.requests if r["tools"]]
        assert len(writing) == 2
        for request in writing:
            texts = [message_text(message) for message in request["messages"]]
            assert texts.count("继续讨论，不改文件") == 1
            assert "应用整理" in texts[0]
            assert "别改成第三人称" in texts[0]
        assert sum(not r["tools"] for r in provider.requests) == 1
        assert summarize_events(events).totals.finished_requests == 3
        assert not list(service.workspace.root.glob("**/*.tmp"))

    asyncio.run(scenario())


def test_cancel_queued_operation_does_not_stop_writing(tmp_path: Path):
    async def scenario():
        provider = BoundaryProvider(pause=True, tool=True)
        service, store, cid, runner = setup(tmp_path, provider)
        service.auto_compaction = False
        info = await service.accept("只讨论", "new", conversation_id=cid)
        await asyncio.wait_for(provider.writing_started.wait(), 3)
        operation = await runner.submit(cid, "manual")
        assert operation.state == "queued"
        await runner.stop(cid, operation.operation_id)
        provider.resume.set()
        await service.wait(info.turn_id)
        assert operation.state == "stopped"
        assert list(store.events(cid))[-1]["status"] == "completed"
        assert not provider.summary_started.is_set()

    asyncio.run(scenario())


def test_cancel_before_background_task_starts_closes_owned_model(tmp_path: Path):
    async def scenario():
        class ClosableProvider(BoundaryProvider):
            closed = 0

            async def aclose(self):
                self.closed += 1

        provider = ClosableProvider()
        _, _, cid, runner = setup(tmp_path, provider)
        operation = await runner.submit(cid, "cancel-immediately")
        await runner.stop(cid, operation.operation_id)
        assert operation.state == "stopped"
        assert provider.closed == 1
        assert not provider.requests
        assert operation.snapshot is None

    asyncio.run(scenario())


def test_manual_request_runs_at_tool_boundary_with_frozen_model(tmp_path: Path):
    async def scenario():
        provider = BoundaryProvider(pause=True, tool=True)
        service, store, cid, runner = setup(tmp_path, provider)
        service.auto_compaction = False
        info = await service.accept("只讨论", "new", conversation_id=cid)
        await asyncio.wait_for(provider.writing_started.wait(), 3)
        operation = await runner.submit(cid, "manual")
        service.provider_factory = lambda: (_ for _ in ()).throw(AssertionError("不能换模型"))
        provider.resume.set()
        await service.wait(info.turn_id)
        assert operation.state == "completed"
        assert [bool(r["tools"]) for r in provider.requests] == [True, False, True]
        assert list(store.events(cid))[-1]["status"] == "completed"

    asyncio.run(scenario())


def test_stop_running_manual_summary_preserves_writing(tmp_path: Path):
    async def scenario():
        provider = BoundaryProvider(pause=True, tool=True, hang_summary=True)
        service, store, cid, runner = setup(tmp_path, provider)
        service.auto_compaction = False
        info = await service.accept("只讨论", "new", conversation_id=cid)
        await asyncio.wait_for(provider.writing_started.wait(), 3)
        operation = await runner.submit(cid, "manual")
        provider.resume.set()
        await asyncio.wait_for(provider.summary_started.wait(), 3)
        await runner.stop(cid, operation.operation_id)
        await asyncio.wait_for(service.wait(info.turn_id), 3)
        assert operation.state == "stopped"
        assert active_compaction(list(store.events(cid))) is None
        assert list(store.events(cid))[-1]["status"] == "completed"

    asyncio.run(scenario())


def test_recovery_keeps_idempotency_and_never_restarts_paid_work(tmp_path: Path):
    async def scenario():
        provider = BoundaryProvider(hang_summary=True)
        service, store, cid, runner = setup(tmp_path, provider)
        operation = await runner.submit(cid, "manual")
        await asyncio.wait_for(provider.summary_started.wait(), 3)
        task = runner._tasks[operation.operation_id]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        recovered = CompactionRunner(service)
        before = len(provider.requests)
        recovered.recover()
        same = await recovered.submit(cid, "manual")
        assert same.operation_id == operation.operation_id
        assert same.state == "stopped"
        assert recovered.latest(cid) is same
        assert len(provider.requests) == before
        assert summarize_events(list(store.events(cid))).totals.requests_in_progress == 0

    asyncio.run(scenario())


def test_compaction_input_is_not_current_context_and_old_tool_text_drops(tmp_path: Path):
    async def scenario():
        provider = BoundaryProvider()
        service, store, cid, _ = setup(tmp_path, provider)
        service.auto_compaction = False
        info = await service.accept("只讨论", "new", conversation_id=cid)
        await service.wait(info.turn_id)
        before = context_snapshot(list(store.events(cid)))
        record = await service.compact(cid)
        assert record is not None
        events = list(store.events(cid))
        after = context_snapshot(events)
        last = next(e for e in reversed(events) if e["type"] == "model_request_started")
        assert last["stage"] == "compaction"
        assert after.tools_tokens == before.tools_tokens and after.tools_tokens
        assert after.instructions_tokens == before.instructions_tokens
        assert after.estimated_active_tokens != last["request_estimate"]["total_tokens"]
        assert after.estimated_active_tokens < before.estimated_active_tokens
        assert after.fingerprint != before.fingerprint

    asyncio.run(scenario())


def test_oversized_required_input_fails_without_provider_call(tmp_path: Path):
    async def scenario():
        provider = BoundaryProvider()
        service, store, cid, _ = setup(tmp_path, provider, turns=2)
        info = await service.accept("必要原文" * 40_000, "new", conversation_id=cid)
        await service.wait(info.turn_id)
        errors = [e for e in store.events(cid) if e["type"] == "error"]
        assert errors[-1]["code"] == "CONTEXT_REQUIRED_INPUT_TOO_LARGE"
        assert not any(r["tools"] for r in provider.requests)

    asyncio.run(scenario())


def test_summary_failure_does_not_repeat_or_abort_a_request_that_still_fits(tmp_path: Path):
    async def scenario():
        provider = BoundaryProvider(tool=True)
        provider.fail_summary = True
        service, store, cid, _ = setup(tmp_path, provider)
        service.compaction_threshold = 0.05
        info = await service.accept("只讨论", "new", conversation_id=cid)
        await service.wait(info.turn_id)
        assert list(store.events(cid))[-1]["status"] == "completed"
        assert sum(not r["tools"] for r in provider.requests) == 1
        assert active_compaction(list(store.events(cid))) is None
        assert summarize_events(list(store.events(cid))).totals.requests_in_progress == 0

    asyncio.run(scenario())


def test_stop_writing_also_cancels_its_running_summary(tmp_path: Path):
    async def scenario():
        provider = BoundaryProvider(hang_summary=True)
        service, store, cid, _ = setup(tmp_path, provider)
        service.compaction_threshold = 0.05
        info = await service.accept("只讨论", "new", conversation_id=cid)
        await asyncio.wait_for(provider.summary_started.wait(), 3)
        await service.stop(info.turn_id)
        await asyncio.wait_for(service.wait(info.turn_id), 3)
        assert list(store.events(cid))[-1]["status"] == "stopped"
        assert active_compaction(list(store.events(cid))) is None
        assert summarize_events(list(store.events(cid))).totals.requests_in_progress == 0

    asyncio.run(scenario())


def test_long_history_is_segmented_but_stops_at_four_summary_requests(tmp_path: Path):
    async def scenario():
        provider = BoundaryProvider()
        store = _store(tmp_path)
        cid = _seed(store, turns=14)
        service = _service(tmp_path, provider, store)
        with pytest.raises(WorkspaceError) as failure:
            await service.compact(cid)
        assert failure.value.code == "COMPACTION_BUDGET_EXCEEDED"
        assert len(provider.requests) == 4
        assert all(not r["tools"] for r in provider.requests)
        assert active_compaction(list(store.events(cid))) is None

    asyncio.run(scenario())


def test_complete_segmented_summary_is_committed_once(tmp_path: Path):
    async def scenario():
        provider = BoundaryProvider()
        store = _store(tmp_path)
        cid = _seed(store, turns=6)
        service = _service(tmp_path, provider, store)
        record = await service.compact(cid)
        assert record is not None
        assert 1 < len(provider.requests) <= 4
        assert len([e for e in store.events(cid) if e["type"] == "context_compacted"]) == 1

    asyncio.run(scenario())


def test_restart_marks_unfinished_operation_terminal_without_model_call(tmp_path: Path):
    provider = BoundaryProvider()
    service, store, cid, runner = setup(tmp_path, provider)
    store.append(
        cid,
        {
            "type": "context_compaction_queued",
            "turn_id": "",
            "operation": {
                "operation_id": "crashed",
                "request_key": "same",
                "state": "queued",
            },
        },
    )
    runner.recover()
    assert runner.get(cid, "crashed").state == "stopped"
    assert not provider.requests
    before = len(tuple(store.events(cid)))
    runner.recover()
    assert len(tuple(store.events(cid))) == before
