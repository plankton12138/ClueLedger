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
from typing import TYPE_CHECKING, Any, Literal

from hastings.workspace.errors import WorkspaceError

if TYPE_CHECKING:
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
        }


class CompactionRunner:
    """按作品管理整理操作；状态只在本进程内，重启后由已提交记录决定投影。"""

    def __init__(self, service: TurnService) -> None:
        self.service = service
        self._operations: dict[str, CompactionOperation] = {}
        self._by_key: dict[str, str] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def submit(self, conversation_id: str, request_key: str) -> CompactionOperation:
        """受理一次整理；同一个键只受理一次。"""
        self.service.store.get(conversation_id)
        async with self._lock:
            existing = self._by_key.get(request_key)
            if existing is not None and existing in self._operations:
                return self._operations[existing]
            operation = CompactionOperation(
                operation_id=f"op_{uuid.uuid4().hex[:12]}",
                conversation_id=conversation_id,
                request_key=request_key,
            )
            self._operations[operation.operation_id] = operation
            self._by_key[request_key] = operation.operation_id
            self._tasks[operation.operation_id] = asyncio.create_task(self._run(operation))
            return operation

    def get(self, conversation_id: str, operation_id: str) -> CompactionOperation:
        operation = self._operations.get(operation_id)
        if operation is None or operation.conversation_id != conversation_id:
            raise WorkspaceError(
                code="CONTEXT_COMPACTION_NOT_FOUND",
                where="conversation.compaction",
                why="找不到这次整理操作",
                fix="重新点一次「整理上下文」；服务重启后旧的整理操作不再保留",
                details={"operation_id": operation_id},
            )
        return operation

    async def stop(self, conversation_id: str, operation_id: str) -> CompactionOperation:
        """取消排队或运行中的整理；已完成的操作保持原状。"""
        operation = self.get(conversation_id, operation_id)
        task = self._tasks.get(operation_id)
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        return operation

    async def _run(self, operation: CompactionOperation) -> None:
        operation.state = "running"
        try:
            record = await self.service.compact(operation.conversation_id, reason="manual")
        except asyncio.CancelledError:
            operation.state = "stopped"
            operation.note = "整理已取消；当前上下文没有变化。"
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
            self._tasks.pop(operation.operation_id, None)
