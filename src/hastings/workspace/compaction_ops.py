"""手动压缩上下文的后台操作（0.9 C1；1.0.1 P2 并入统一操作）。

压缩不是创作轮次：它不注入作者消息，不产生作品提交，也不占用「一轮」的位置。
所以它有自己的操作标识与状态，作者看到的是「正在压缩」而不是一条假的助手回复。

一次操作只有四种终态：``completed``（提交了新记录）、``noop``（没有可压缩的较早内容）、
``failed``（摘要不成立，旧投影保留）、``stopped``（作者取消）。``queued`` 与 ``running``
是过程状态。

1.0.1 起，状态与代次写进应用级 ``OperationStore``：同一张表里的操作都能用
``/api/operations/{id}`` 读取与暂停。这里只保留执行用的内存对象（冻结模型、取消令牌、
任务引用），不再另存一份状态日志。

同一个 ``request_key`` 只受理一次：重复点击或重试返回同一个操作，不会发出第二次摘要请求。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, cast

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.operations import (
    OPERATION_TERMINAL,
    Operation,
    OperationStatus,
    OperationStore,
    utc_now,
)
from tau_agent.harness import SimpleCancellationToken
from tau_agent.provider import CancellationToken

if TYPE_CHECKING:
    from hastings.server.provider_runtime import ModelSnapshot
    from hastings.workspace.service import TurnService

__all__ = ["CompactionOperation", "CompactionRunner", "OperationState"]

OperationState = Literal["queued", "running", "completed", "noop", "failed", "stopped"]

TERMINAL_STATES = frozenset({"completed", "noop", "failed", "stopped"})

#: 过程／终态 → 统一操作表的（状态，阶段）。``noop`` 也用完成的阶段承载，
#: 具体「没有可压缩内容」写在说明里，界面仍能给出确定结论。
_STORE_STATE: dict[OperationState, tuple[OperationStatus, str]] = {
    "queued": ("accepted", "queued"),
    "running": ("running", "running"),
    "completed": ("completed", "completed"),
    "noop": ("completed", "noop"),
    "failed": ("failed", "failed"),
    "stopped": ("paused", "stopped"),
}

_STATE_NOTE: dict[OperationState, str] = {
    "queued": "已受理，等待当前写作轮次到达安全边界。",
    "running": "正在整理较早的完整轮次。",
    "completed": "整理已完成，新的摘要已经启用。",
    "noop": "当前没有需要压缩的较早内容。",
    "failed": "整理没有成立，旧投影保留。",
    "stopped": "压缩已取消；当前上下文没有变化。",
}


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
    #: 作者对这次整理的补充要求；插话与继续都会带进来。
    instruction: str = ""
    reason: str = "manual"
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


def _state_of(operation: Operation) -> OperationState:
    """统一操作记录 → 压缩视图状态；未知状态按停止处理，不假装还在跑。"""
    if operation.phase == "noop":
        return "noop"
    if operation.status == "completed":
        return "completed"
    if operation.status == "failed":
        return "failed"
    if operation.status == "accepted":
        return "queued"
    if operation.status == "running":
        return "running"
    return "stopped"


def _record_of(operation: Operation) -> dict[str, Any] | None:
    for receipt in reversed(operation.committed_receipts):
        if receipt.get("kind") == "compaction" and isinstance(receipt.get("record"), dict):
            return cast(dict[str, Any], receipt["record"])
    return None


def _source_digest(service: TurnService, conversation_id: str) -> str:
    events = [
        event
        for event in service.store.events(conversation_id)
        if event.get("type")
        in (
            "turn_started",
            "assistant_message",
            "author_interjected",
            "tool_finished",
            "run_finished",
        )
        and not event.get("compaction_operation_id")
    ]
    return hashlib.sha256(
        json.dumps(events, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


class CompactionRunner:
    """在统一操作表里受理、推进与取消压缩。"""

    def __init__(self, service: TurnService) -> None:
        self.service = service
        #: 只存执行中的对象；状态事实在 ``service.operations``。
        self._operations: dict[str, CompactionOperation] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()
        service.compactions = self

    @property
    def _store(self) -> OperationStore:
        return self.service.operations

    # -- 记录 ---------------------------------------------------------------

    def _hydrate(self, stored: Operation) -> CompactionOperation:
        """把持久操作读成压缩视图；重复读取返回同一个内存对象。"""
        cached = self._operations.get(stored.id)
        if cached is not None:
            cached.state = _state_of(stored)
            cached.note = stored.note
            cached.error = stored.error
            cached.record = _record_of(stored)
            return cached
        operation = CompactionOperation(
            operation_id=stored.id,
            conversation_id=stored.conversation_id,
            request_key=stored.request_key,
            state=_state_of(stored),
            record=_record_of(stored),
            error=stored.error,
            note=stored.note,
            instruction="\n".join(str(item.get("text", "")) for item in stored.instruction_log),
        )
        self._operations[stored.id] = operation
        return operation

    def _sync(self, operation: CompactionOperation) -> None:
        """把内存状态写回统一操作表；操作文件损坏时保留内存事实。"""
        status, phase = _STORE_STATE[operation.state]
        note = operation.note or _STATE_NOTE[operation.state]
        last_seq = self.service._last_seq(operation.conversation_id)

        def mutate(stored: Operation) -> None:
            stored.status = status
            stored.phase = phase
            stored.note = note
            stored.error = operation.error
            if operation.record is not None:
                stored.committed_receipts = [
                    item
                    for item in stored.committed_receipts
                    if not (
                        item.get("kind") == "compaction"
                        and item.get("operation_id") == operation.operation_id
                    )
                ]
                stored.committed_receipts.append(
                    {
                        "kind": "compaction",
                        "operation_id": operation.operation_id,
                        "record": operation.record,
                        "at": utc_now(),
                    }
                )
            stored.last_seq = last_seq

        with suppress(WorkspaceError):
            self._store.update(operation.operation_id, mutate)

    # -- 查询 ---------------------------------------------------------------

    def latest(self, conversation_id: str) -> CompactionOperation | None:
        live = next(
            (
                op
                for op in reversed(tuple(self._operations.values()))
                if op.conversation_id == conversation_id
            ),
            None,
        )
        if live is not None:
            return live
        candidates = [
            item
            for item in self._store.list()
            if item.purpose == "compaction" and item.conversation_id == conversation_id
        ]
        if not candidates:
            return None
        newest = max(candidates, key=lambda item: item.updated_at)
        return self._hydrate(newest)

    def get(self, conversation_id: str, operation_id: str) -> CompactionOperation:
        operation = self._operations.get(operation_id)
        if operation is not None and operation.conversation_id == conversation_id:
            return operation
        stored = self._store.try_read(operation_id)
        if (
            stored is None
            or stored.purpose != "compaction"
            or stored.conversation_id != conversation_id
        ):
            raise WorkspaceError(
                code="CONTEXT_COMPACTION_NOT_FOUND",
                where="conversation.compaction",
                why="找不到这次压缩操作",
                fix="确认当前会话，或重新发起压缩",
                details={"operation_id": operation_id},
            )
        return self._hydrate(stored)

    # -- 受理 ---------------------------------------------------------------

    async def submit(
        self, conversation_id: str, request_key: str, *, reason: str = "manual"
    ) -> CompactionOperation:
        """受理一次压缩；同一个键只受理一次。"""
        self.service.store.get(conversation_id)
        async with self._lock:
            existing = self._store.find_by_request_key(
                purpose="compaction", conversation_id=conversation_id, request_key=request_key
            )
            if existing is not None:
                return self._hydrate(existing)
            active = self.service.active_turn()
            turn_id = active.turn_id if active and active.conversation_id == conversation_id else ""
            snapshot = (
                self.service._model_snapshots.get(turn_id)
                if turn_id
                else self.service.provider_factory()
            )
            facts = snapshot.describe() if snapshot is not None else {}
            stored = Operation(
                id=f"op_{uuid.uuid4().hex[:12]}",
                owner=f"workspace:{self.service.workspace_id or conversation_id}",
                workspace_id=self.service.workspace_id,
                conversation_id=conversation_id,
                turn_id="",
                purpose="compaction",
                request_key=request_key,
                target="压缩较早的对话",
                model=str(facts.get("model", "")),
                connection=str(facts.get("connection_name", "")),
            )
            self._store.create(stored)
            operation = CompactionOperation(
                operation_id=stored.id,
                conversation_id=conversation_id,
                request_key=request_key,
                turn_id=turn_id,
                snapshot=snapshot,
                reason=reason,
            )
            self._operations[stored.id] = operation
            self._sync(operation)
            self._tasks[stored.id] = asyncio.create_task(self._run(operation))
            return operation

    async def stop(self, conversation_id: str, operation_id: str) -> CompactionOperation:
        """旧停止路由的薄别名：走同一条暂停语义。"""
        self.get(conversation_id, operation_id)
        return await self.pause_operation(operation_id, f"legacy-stop:{operation_id}")

    # -- 控制 ---------------------------------------------------------------

    def _require_control(
        self,
        prior: dict[str, Any],
        kind: str,
        operation_id: str,
        *,
        expected_generation: int | None = None,
        text: str = "",
    ) -> None:
        if (
            prior.get("kind") == kind
            and prior.get("expected_generation") == expected_generation
            and prior.get("text", "") == text.strip()
        ):
            return
        raise WorkspaceError(
            code="OPERATION_REQUEST_CONFLICT",
            where="operations.control",
            why="这个请求标识已经用在另一种控制上",
            fix="换一个请求标识；重试同一动作请保持原来的键",
            details={"operation_id": operation_id, "kind": prior.get("kind")},
        )

    def _require_generation(self, stored: Operation, expected: int) -> None:
        if expected == stored.generation:
            return
        raise WorkspaceError(
            code="OPERATION_GENERATION_CONFLICT",
            where="operations.control",
            why=f"这次操作已经是第 {stored.generation} 代，请求里写的是第 {expected} 代",
            fix="刷新任务状态后按当前代次再操作；旧代次的按钮已经失效",
            details={"operation_id": stored.id, "generation": stored.generation},
        )

    async def _close_owned(self, operation: CompactionOperation) -> None:
        """关闭本操作自己创建的模型实例；绑定运行轮次的实例由该轮关闭。"""
        if operation.turn_id or operation.snapshot is None:
            return
        snapshot, operation.snapshot = operation.snapshot, None
        await snapshot.aclose()

    async def pause_operation(self, operation_id: str, request_key: str) -> CompactionOperation:
        """暂停：先撤销这一代，再取消远端请求；重复请求返回同一结果。"""
        async with self._lock:
            stored = self._store.read(operation_id)
            prior = stored.control(request_key)
            if prior is not None:
                self._require_control(prior, "pause", operation_id)
                return self._hydrate(stored)
            if stored.status in OPERATION_TERMINAL or stored.status == "paused":
                return self._hydrate(stored)

            def mutate(current: Operation) -> None:
                current.status = "paused"
                current.phase = "stopped"
                current.note = "压缩已暂停；当前上下文没有变化。"
                current.record_control(request_key, "pause", generation=current.generation)

            stored = self._store.update(operation_id, mutate)
            operation = self._operations.get(operation_id)
            if operation is not None:
                if operation.state not in TERMINAL_STATES:
                    operation.state = "stopped"
                    operation.note = stored.note
                operation.signal.cancel()
            task = self._tasks.get(operation_id)
            if task is not None and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            if operation is not None:
                await self._close_owned(operation)
            return self._hydrate(stored)

    async def interject_operation(
        self, operation_id: str, request_key: str, *, expected_generation: int, text: str
    ) -> CompactionOperation:
        """生成中插话：撤销这一代，再用带要求的下一代替换。"""
        if not text.strip():
            raise WorkspaceError(
                code="OPERATION_INPUT_INVALID",
                where="operations.interject",
                why="插话是空的",
                fix="写下要补充的要求，或直接暂停这次整理",
                details={"operation_id": operation_id},
            )
        async with self._lock:
            stored = self._store.read(operation_id)
            prior = stored.control(request_key)
            if prior is not None:
                self._require_control(
                    prior,
                    "interject",
                    operation_id,
                    expected_generation=expected_generation,
                    text=text,
                )
                return self._hydrate(stored)
            self._require_generation(stored, expected_generation)
            if not stored.resumable:
                raise WorkspaceError(
                    code="OPERATION_RECOVERED_CONFLICT",
                    where="operations.control",
                    why="这次整理在服务重启时中断了，没有可以承接的摘要请求",
                    fix="重新发起一次压缩；已有的摘要与上下文保持原样",
                    details={"operation_id": stored.id},
                )
            if stored.status not in ("accepted", "running"):
                raise WorkspaceError(
                    code="OPERATION_STATE_CONFLICT",
                    where="operations.control",
                    why=f"这次整理当前是「{stored.status}」，不在生成中",
                    fix="已经暂停的整理用「继续」补充要求",
                    details={"operation_id": stored.id, "status": stored.status},
                )
            operation = self._operations.get(operation_id) or self._hydrate(stored)
            if stored.recovery_request:
                from hastings.workspace.service import _same_model_snapshot

                snapshot = self.service.provider_factory()
                if not _same_model_snapshot(
                    snapshot, stored.recovery_request
                ) or stored.recovery_request.get("source_digest") != _source_digest(
                    self.service, stored.conversation_id
                ):
                    await snapshot.aclose()
                    raise WorkspaceError(
                        code="OPERATION_RECOVERED_CONFLICT",
                        where="operations.control",
                        why="整理依据的会话或模型配置已经变化",
                        fix="检查会话后重新发起压缩",
                    )
                await snapshot.aclose()
            await self._cancel_attempt(operation)
            stored = self._register_generation(
                operation, request_key, "interject", text, request_action="interject"
            )
            return self._hydrate(stored)

    async def continue_operation(
        self,
        operation_id: str,
        request_key: str,
        *,
        expected_generation: int,
        text: str = "",
    ) -> CompactionOperation:
        """继续（可带补充要求）：开新的一代，重新读取当前会话。"""
        async with self._lock:
            stored = self._store.read(operation_id)
            kind = "interject" if text.strip() else "continue"
            prior = stored.control(request_key)
            if prior is not None:
                self._require_control(
                    prior,
                    kind,
                    operation_id,
                    expected_generation=expected_generation,
                    text=text,
                )
                return self._hydrate(stored)
            self._require_generation(stored, expected_generation)
            if not stored.resumable:
                raise WorkspaceError(
                    code="OPERATION_RECOVERED_CONFLICT",
                    where="operations.control",
                    why="这次整理在服务重启时中断了，没有可以承接的摘要请求",
                    fix="重新发起一次压缩；已有的摘要与上下文保持原样",
                    details={"operation_id": stored.id},
                )
            if stored.status not in ("paused", "awaiting_answer", "needs_attention"):
                raise WorkspaceError(
                    code="OPERATION_STATE_CONFLICT",
                    where="operations.control",
                    why=f"这次整理当前是「{stored.status}」，不能继续",
                    fix="先暂停正在执行的整理，或等它结束后再操作",
                    details={"operation_id": stored.id, "status": stored.status},
                )
            operation = self._operations.get(operation_id) or self._hydrate(stored)
            if stored.recovery_request:
                from hastings.workspace.service import _same_model_snapshot

                snapshot = self.service.provider_factory()
                if not _same_model_snapshot(
                    snapshot, stored.recovery_request
                ) or stored.recovery_request.get("source_digest") != _source_digest(
                    self.service, stored.conversation_id
                ):
                    await snapshot.aclose()
                    raise WorkspaceError(
                        code="OPERATION_RECOVERED_CONFLICT",
                        where="operations.control",
                        why="整理依据的会话或模型配置已经变化",
                        fix="检查会话后重新发起压缩",
                    )
                await snapshot.aclose()
            await self._cancel_attempt(operation)
            stored = self._register_generation(
                operation,
                request_key,
                kind,
                text,
                request_action="continue",
                expected_generation=expected_generation,
            )
            return self._hydrate(stored)

    async def _cancel_attempt(self, operation: CompactionOperation) -> None:
        """取消上一代并等它真正停下；新代次不能与旧代次同时跑。"""
        operation.signal.cancel()
        task = self._tasks.get(operation.operation_id)
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await self._close_owned(operation)

    def _register_generation(
        self,
        operation: CompactionOperation,
        request_key: str,
        kind: str,
        text: str,
        *,
        request_action: str,
        expected_generation: int | None = None,
    ) -> Operation:
        """在锁内推进代次并入队新尝试；真正的任务随后启动。"""
        instruction = "\n".join(part for part in (operation.instruction, text.strip()) if part)

        def mutate(stored: Operation) -> None:
            stored.generation += 1
            stored.status = "accepted"
            stored.phase = "queued"
            stored.note = ""
            stored.error = None
            if text.strip():
                stored.instruction(text.strip(), kind, request_key=request_key)
            stored.record_control(
                request_key,
                kind,
                generation=stored.generation,
                request_action=request_action,
                expected_generation=(
                    expected_generation
                    if expected_generation is not None
                    else stored.generation - 1
                ),
                text=text,
            )

        stored = self._store.update(operation.operation_id, mutate)
        # 绑定运行轮次的操作仍由安全边界执行；原轮次已经结束时不再挂到它上面。
        active = self.service.active_turn()
        turn_id = operation.turn_id
        if turn_id and not (active and active.turn_id == turn_id):
            turn_id = ""
        fresh = CompactionOperation(
            operation_id=operation.operation_id,
            conversation_id=operation.conversation_id,
            request_key=operation.request_key,
            instruction=instruction,
            turn_id=turn_id,
        )
        self._operations[fresh.operation_id] = fresh
        self._tasks[fresh.operation_id] = asyncio.create_task(self._run(fresh))
        return stored

    # -- 执行 ---------------------------------------------------------------

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
        owned: ModelSnapshot | None = None
        try:
            async with self.service._lock:
                # 读取最新状态：受理后可能已被暂停或插话替换。
                stored = self._store.try_read(operation.operation_id)
                if stored is None or stored.status not in ("accepted", "running"):
                    return
                if operation.turn_id:
                    # 绑定运行轮次的操作由安全边界或收尾处理，不能抢在该轮之前执行。
                    return
                if operation.state != "queued":
                    return
                snapshot = operation.snapshot
                if snapshot is None:
                    snapshot = self.service.provider_factory()
                    owned = snapshot
                try:
                    await self._execute(operation, snapshot, None, None)
                finally:
                    if owned is not None:
                        await owned.aclose()
        except Exception as exc:  # 快照构造失败等；不能让界面一直显示「进行中」
            operation.state = "failed"
            operation.error = {
                "code": "COMPACTION_FAILED",
                "where": "conversation.compaction",
                "why": str(exc) or type(exc).__name__,
                "fix": "保留当前上下文继续写作；配置好模型后重新整理",
            }
            self._sync(operation)
        finally:
            self._tasks.pop(operation.operation_id, None)
            await self._close_owned(operation)

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
        if snapshot is not None:

            def freeze(stored: Operation) -> None:
                stored.recovery_request = {
                    "connection_id": snapshot.connection_id,
                    "model": snapshot.model,
                    "model_facts": snapshot.describe(),
                    "model_limits": snapshot.limits.as_dict() if snapshot.limits else None,
                    "source_digest": _source_digest(self.service, operation.conversation_id),
                }

            self._store.update(operation.operation_id, freeze)
        self._sync(operation)
        try:
            record = await self.service._compact_locked(
                operation.conversation_id,
                reason=operation.reason,
                snapshot=snapshot,
                events=events,
                signal=CombinedSignal(),
                instruction=operation.instruction,
                operation_id=operation.operation_id,
            )
        except asyncio.CancelledError:
            operation.state = "stopped"
            operation.note = "压缩已取消；当前上下文没有变化。"
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
        except Exception as exc:  # 未预计的失败同样不能让作者以为压缩成功了
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
                operation.note = _STATE_NOTE["noop"]
            else:
                operation.state = "completed"
                operation.note = _STATE_NOTE["completed"]
                operation.record = record.as_dict()
        finally:
            if operation.signal.is_cancelled() or (signal and signal.is_cancelled()):
                operation.state = "stopped"
                operation.note = "压缩已取消；未完成的摘要没有启用。"
            self._sync(operation)

    # -- 重启 ---------------------------------------------------------------

    def recover(self) -> None:
        """重启后把未完成的压缩转为暂停；补记中断的摘要请求，不重新调用模型。

        压缩没有可以承接的冻结请求：重启后不自动继续，状态改成作者可识别的暂停。
        """
        for stored in self._store.list():
            if stored.purpose != "compaction" or stored.workspace_id != self.service.workspace_id:
                continue
            if stored.status in OPERATION_TERMINAL:
                continue
            if stored.status == "paused" and stored.phase == "stopped":
                continue
            already_recovered = stored.phase == "interrupted"

            def mutate(current: Operation, *, already: bool = already_recovered) -> None:
                current.status = "paused"
                current.phase = "stopped"
                current.note = "服务重启，压缩已中断；没有自动重新调用模型。"
                # 重启后没有冻结的摘要请求可以承接：不再宣称为可继续。
                current.resumable = bool(current.recovery_request)
                # 统一恢复点已经作废旧代次时不再重复 +1，也不重复写同一个检查点。
                if not already:
                    current.generation += 1
                    current.checkpoint(
                        "interrupted", generation=current.generation, note="服务重启"
                    )

            self._store.update(stored.id, mutate)
            self._mark_interrupted_requests(stored.conversation_id)

    def _mark_interrupted_requests(self, conversation_id: str) -> None:
        """把没有结束的摘要请求补成中断；累计用量不应显示「还在进行」。"""
        pending: dict[str, dict[str, Any]] = {}
        for event in self.service.store.events(conversation_id):
            if event.get("stage") != "compaction":
                continue
            if event.get("type") == "model_request_started":
                pending[str(event.get("operation_id", ""))] = event
            elif event.get("type") == "model_request_finished":
                pending.pop(str(event.get("operation_id", "")), None)
        for operation_id, event in pending.items():
            self.service.store.append(
                conversation_id,
                {
                    "type": "model_request_finished",
                    "turn_id": "",
                    "operation_id": operation_id,
                    "stage": "compaction",
                    "model": event.get("model"),
                    "status": "interrupted",
                    "usage": None,
                    "duration_ms": None,
                },
            )
