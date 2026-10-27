"""手动整理上下文的后台操作（0.9 C1）。

整理不是创作轮次：它不注入作者消息，不产生作品提交，也不占用「一轮」的位置。
所以它有自己的操作标识与状态，作者看到的是「正在整理」而不是一条假的助手回复。

一次操作只有四种终态：``completed``（提交了新记录）、``noop``（没有可整理的较早内容）、
``failed``（摘要不成立，旧投影保留）、``stopped``（作者取消）。``queued`` 与 ``running``
是过程状态。

同一个 ``request_key`` 只受理一次：重复点击或重试返回同一个操作，不会发出第二次摘要请求。
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, cast

from hastings.workspace.errors import WorkspaceError
from tau_agent.harness import SimpleCancellationToken
from tau_agent.provider import CancellationToken

if TYPE_CHECKING:
    from hastings.server.provider_runtime import ModelSnapshot
    from hastings.workspace.service import TurnService

__all__ = ["CompactionOperation", "CompactionRunner", "OperationState"]

OperationState = Literal["queued", "running", "completed", "noop", "failed", "stopped"]

TERMINAL_STATES = frozenset({"completed", "noop", "failed", "stopped"})


@dataclass(slots=True)
class CompactionOperation:
    operation_id: str
    conversation_id: str
    request_key: str
    state: OperationState = "queued"
    record: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    #: 操作失败不影响写作，但界面要能说清原因。
    note: str = field(default="")
    turn_id: str = ""
    snapshot: ModelSnapshot | None = field(default=None, repr=False)
    signal: SimpleCancellationToken = field(default_factory=SimpleCancellationToken, repr=False)

    @property
    def finished(self) -> bool:
        return self.state in TERMINAL_STATES

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "conversation_id": self.conversation_id,
            "state": self.state,
            "record": self.record,
            "error": self.error,
            "note": self.note,
            "turn_id": self.turn_id,
        }


class CompactionRunner:
    """操作状态追加到会话事件；重启只恢复状态，不重新发起付费调用。"""

    def __init__(self, service: TurnService) -> None:
        self.service = service
        self._operations: dict[str, CompactionOperation] = {}
        self._by_key: dict[tuple[str, str], str] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()
        service.compactions = self

    def _save(self, operation: CompactionOperation) -> None:
        self.service.store.append(
            operation.conversation_id,
            {
                "type": f"context_compaction_{operation.state}",
                "turn_id": "",
                "operation_id": operation.operation_id,
                "operation": {**operation.as_dict(), "request_key": operation.request_key},
            },
        )

    def recover(self) -> None:
        for conversation in self.service.store.conversations():
            pending: dict[str, dict[str, Any]] = {}
            for event in self.service.store.events(conversation.id):
                if event.get("stage") == "compaction":
                    if event.get("type") == "model_request_started":
                        pending[str(event["operation_id"])] = event
                    elif event.get("type") == "model_request_finished":
                        pending.pop(str(event["operation_id"]), None)
                raw = event.get("operation")
                if not str(event.get("type", "")).startswith(
                    "context_compaction_"
                ) or not isinstance(raw, dict):
                    continue
                if raw.get("state") not in {"queued", "running", *TERMINAL_STATES}:
                    continue
                operation = CompactionOperation(
                    operation_id=str(raw["operation_id"]),
                    conversation_id=conversation.id,
                    request_key=str(raw["request_key"]),
                    state=cast(OperationState, raw["state"]),
                    record=raw.get("record"),
                    error=raw.get("error"),
                    note=str(raw.get("note", "")),
                    turn_id=str(raw.get("turn_id", "")),
                )
                self._operations[operation.operation_id] = operation
                self._by_key[(conversation.id, operation.request_key)] = operation.operation_id
            for operation in self._operations.values():
                if operation.conversation_id == conversation.id and not operation.finished:
                    operation.state = "stopped"
                    operation.note = "服务重启，整理已中断；没有自动重新调用模型。"
                    self._save(operation)
            for op, event in pending.items():
                self.service.store.append(
                    conversation.id,
                    {
                        "type": "model_request_finished",
                        "turn_id": "",
                        "operation_id": op,
                        "stage": "compaction",
                        "model": event.get("model"),
                        "status": "interrupted",
                        "usage": None,
                        "duration_ms": None,
                    },
                )

    def latest(self, conversation_id: str) -> CompactionOperation | None:
        return next(
            (
                op
                for op in reversed(tuple(self._operations.values()))
                if op.conversation_id == conversation_id
            ),
            None,
        )

    async def submit(self, conversation_id: str, request_key: str) -> CompactionOperation:
        """受理一次整理；同一个键只受理一次。"""
        self.service.store.get(conversation_id)
        async with self._lock:
            existing = self._by_key.get((conversation_id, request_key))
            if existing is not None and existing in self._operations:
                return self._operations[existing]
            active = self.service.active_turn()
            turn_id = active.turn_id if active and active.conversation_id == conversation_id else ""
            snapshot = (
                self.service._model_snapshots.get(turn_id)
                if turn_id
                else self.service.provider_factory()
            )
            operation = CompactionOperation(
                operation_id=f"op_{uuid.uuid4().hex[:12]}",
                conversation_id=conversation_id,
                request_key=request_key,
                turn_id=turn_id,
                snapshot=snapshot,
            )
            self._operations[operation.operation_id] = operation
            self._by_key[(conversation_id, request_key)] = operation.operation_id
            self._save(operation)
            self._tasks[operation.operation_id] = asyncio.create_task(self._run(operation))
            return operation

    def get(self, conversation_id: str, operation_id: str) -> CompactionOperation:
        operation = self._operations.get(operation_id)
        if operation is None or operation.conversation_id != conversation_id:
            raise WorkspaceError(
                code="CONTEXT_COMPACTION_NOT_FOUND",
                where="conversation.compaction",
                why="找不到这次整理操作",
                fix="确认当前会话，或重新发起整理",
                details={"operation_id": operation_id},
            )
        return operation

    async def stop(self, conversation_id: str, operation_id: str) -> CompactionOperation:
        """取消排队或运行中的整理；已完成的操作保持原状。"""
        operation = self.get(conversation_id, operation_id)
        if operation.finished:
            return operation
        operation.signal.cancel()
        task = self._tasks.get(operation_id)
        if operation.state == "queued" and task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if operation.state == "queued":
            operation.state = "stopped"
            operation.note = "整理已取消；当前上下文没有变化。"
            self._save(operation)
            self._tasks.pop(operation_id, None)
            # 任务可能在第一次调度前被取消，此时 _run 的 finally 尚未进入。
            if not operation.turn_id and operation.snapshot is not None:
                snapshot, operation.snapshot = operation.snapshot, None
                await snapshot.aclose()
        return operation

    async def at_boundary(
        self,
        turn_id: str,
        snapshot: ModelSnapshot,
        events: tuple[dict[str, Any], ...],
        signal: CancellationToken | None,
    ) -> bool:
        handled = False
        for operation in tuple(self._operations.values()):
            if operation.turn_id == turn_id and operation.state == "queued":
                await self._execute(operation, snapshot, events, signal)
                handled = True
        return handled

    async def finish_turn(self, turn_id: str, *, stopped: bool) -> None:
        for operation in tuple(self._operations.values()):
            if operation.turn_id != turn_id or operation.state != "queued":
                continue
            if stopped:
                await self.stop(operation.conversation_id, operation.operation_id)
            else:
                # 原轮没有后续推理边界时，在关闭其冻结模型之前完成排队操作。
                async with self.service._lock:
                    await self._execute(operation, operation.snapshot, None, None)

    async def _run(self, operation: CompactionOperation) -> None:
        try:
            async with self.service._lock:
                if operation.state == "queued":
                    # 绑定运行轮次的操作由安全边界或收尾处理，不能抢在该轮之前执行。
                    if operation.turn_id:
                        return
                    await self._execute(operation, operation.snapshot, None, None)
        finally:
            self._tasks.pop(operation.operation_id, None)
            if not operation.turn_id and operation.snapshot is not None:
                snapshot, operation.snapshot = operation.snapshot, None
                await snapshot.aclose()

    async def _execute(
        self,
        operation: CompactionOperation,
        snapshot: ModelSnapshot | None,
        events: tuple[dict[str, Any], ...] | None,
        signal: CancellationToken | None,
    ) -> None:
        class CombinedSignal:
            def is_cancelled(self) -> bool:
                return operation.signal.is_cancelled() or bool(signal and signal.is_cancelled())

        operation.state = "running"
        self._save(operation)
        try:
            record = await self.service._compact_locked(
                operation.conversation_id,
                reason="manual",
                snapshot=snapshot,
                events=events,
                signal=CombinedSignal(),
            )
        except asyncio.CancelledError:
            operation.state = "stopped"
            operation.note = "整理已取消；当前上下文没有变化。"
            if not operation.signal.is_cancelled() or (signal and signal.is_cancelled()):
                raise
        except WorkspaceError as exc:
            operation.state = "failed"
            operation.error = {
                "code": exc.code,
                "where": exc.where,
                "why": exc.why,
                "fix": exc.fix,
            }
        except Exception as exc:  # 未预计的失败同样不能让作者以为整理成功了
            operation.state = "failed"
            operation.error = {
                "code": "COMPACTION_FAILED",
                "where": "conversation.compaction",
                "why": str(exc) or type(exc).__name__,
                "fix": "保留当前上下文继续写作；稍后再试一次",
            }
        else:
            if record is None:
                operation.state = "noop"
                operation.note = "当前没有需要整理的较早内容。"
            else:
                operation.state = "completed"
                operation.record = record.as_dict()
        finally:
            if operation.signal.is_cancelled() or (signal and signal.is_cancelled()):
                operation.state = "stopped"
                operation.note = "整理已取消；未完成的摘要没有启用。"
            self._save(operation)
