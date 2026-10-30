"""统一操作接口：快照、事件、依据、结果与控制（1.0.1 P2）。

接口只读控制面，执行记录仍在会话事件里。事件流按 ``attempt_started`` 把区间标回
代次：客户端拿到的是「哪一代产生的哪条事件」，不需要自己按时间猜。

读取操作不会启动模型，也不会改写任何状态；控制请求用 ``request_key`` 去重，
重复提交返回同一份状态。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.operations import OperationStore
from hastings.workspace.state import ConversationStore, RecentWorkspaces, conversation_root

if TYPE_CHECKING:
    from hastings.server.probe_ops import ProbeRunner
    from hastings.workspace.compaction_ops import CompactionRunner
    from hastings.workspace.service import TurnService

__all__ = [
    "OPERATIONS_PREFIX",
    "ContinueOperationRequest",
    "OperationView",
    "PauseOperationRequest",
    "create_operation_router",
]

OPERATIONS_PREFIX = "/api/operations"

#: 操作已经不在执行中，但事件还没写完时最多再等这么久。
_DRAIN_SECONDS = 30.0


class OperationView(BaseModel):
    id: str
    conversation_id: str
    turn_id: str
    purpose: str
    target: str
    status: str
    generation: int
    phase: str
    model: str
    connection: str
    note: str
    error: dict[str, Any] | None = None
    pending_units: list[str] = Field(default_factory=list)
    last_seq: int
    created_at: str
    updated_at: str
    capabilities: dict[str, bool]


class PauseOperationRequest(BaseModel):
    model_config = {"extra": "forbid"}

    request_key: str = Field(min_length=1, max_length=120)


class ContinueOperationRequest(BaseModel):
    model_config = {"extra": "forbid"}

    request_key: str = Field(min_length=1, max_length=120)
    expected_generation: int = Field(ge=1)
    text: str = Field(default="", max_length=8000)


class OperationEventsView(BaseModel):
    events: list[dict[str, Any]]
    last_seq: int
    operation: OperationView


class OperationContextView(BaseModel):
    operation_id: str
    model: str
    connection: str
    refs: list[dict[str, Any]]
    injections: list[dict[str, Any]]
    note: str = ""


class OperationResultView(BaseModel):
    operation_id: str
    status: str
    note: str
    receipts: list[dict[str, Any]]
    pending_units: list[str]
    changed_paths: list[str]
    question: dict[str, Any] | None = None


def create_operation_router(
    workspaces: RecentWorkspaces,
    operations: OperationStore,
    services: Mapping[str, TurnService],
    *,
    runner_for: Callable[[str], CompactionRunner] | None = None,
    probe_runner: ProbeRunner | None = None,
) -> APIRouter:
    """挂载应用级操作接口。

    ``services`` 是写作路由共享的服务表；``runner_for`` 与 ``probe_runner`` 让压缩与
    连接测试也走同一套快照与控制，而不是各自再暴露一套状态机。
    """
    router = APIRouter(prefix=OPERATIONS_PREFIX, tags=["operations"])

    def read(operation_id: str) -> Any:
        return operations.read(operation_id)

    def service_for(operation_id: str) -> TurnService:
        operation = read(operation_id)
        service = services.get(operation.workspace_id)
        if service is None:
            raise WorkspaceError(
                code="OPERATION_UNAVAILABLE_CONFLICT",
                where="operations.control",
                why="这次操作所在的进程已经结束，当前没有可控制的任务",
                fix="刷新后查看结果；需要接着写就重新发起一轮",
                details={"operation_id": operation_id},
            )
        return service

    def store_for(operation_id: str) -> ConversationStore | None:
        """有会话的操作才有事件；连接测试不写会话，返回空即可。"""
        operation = read(operation_id)
        if not operation.workspace_id or not operation.conversation_id:
            return None
        workspaces.get(operation.workspace_id)
        return ConversationStore(conversation_root(workspaces.root, operation.workspace_id))

    def view(operation: Any) -> OperationView:
        return OperationView(**operation.as_view())

    async def control(
        operation_id: str,
        action: str,
        request_key: str,
        *,
        expected_generation: int = 1,
        text: str = "",
    ) -> OperationView:
        """按用途分派控制；执行记录仍在各自的会话事件里。"""
        operation = read(operation_id)
        if operation.purpose == "compaction":
            if runner_for is None:
                raise _unavailable(operation_id, "压缩")
            runner = runner_for(operation.workspace_id)
            if action == "pause":
                await runner.pause_operation(operation_id, request_key)
            elif action == "continue":
                await runner.continue_operation(
                    operation_id,
                    request_key,
                    expected_generation=expected_generation,
                    text=text,
                )
            else:
                await runner.interject_operation(
                    operation_id,
                    request_key,
                    expected_generation=expected_generation,
                    text=text,
                )
        elif operation.purpose == "probe":
            if probe_runner is None:
                raise _unavailable(operation_id, "连接测试")
            if action == "pause":
                await probe_runner.pause_operation(operation_id, request_key)
            elif action == "continue":
                await probe_runner.continue_operation(
                    operation_id,
                    request_key,
                    expected_generation=expected_generation,
                    text=text,
                )
            else:
                await probe_runner.continue_operation(operation_id, request_key,
                    expected_generation=expected_generation, text=text)
        else:
            service = service_for(operation_id)
            if action == "pause":
                await service.pause_operation(operation_id, request_key)
            elif action == "continue":
                await service.continue_operation(
                    operation_id,
                    request_key,
                    expected_generation=expected_generation,
                    text=text,
                )
            else:
                await service.interject_operation(
                    operation_id,
                    request_key,
                    expected_generation=expected_generation,
                    text=text,
                )
        return view(read(operation_id))

    @router.get("", response_model=list[OperationView])
    def list_operations(
        conversation_id: str = Query(min_length=1),
        limit: int = Query(default=1, ge=1, le=20),
    ) -> list[OperationView]:
        """按会话取最近的操作：刷新之后据此恢复「继续」入口，不重放旧任务。"""
        items = [
            operation
            for operation in operations.list()
            if operation.conversation_id == conversation_id
        ]
        items.sort(key=lambda operation: operation.updated_at, reverse=True)
        return [view(item) for item in items[:limit]]

    @router.get("/{operation_id}", response_model=OperationView)
    def read_operation(operation_id: str) -> OperationView:
        """只读快照；不触发模型，也不追加事件。"""
        return view(read(operation_id))

    @router.post("/{operation_id}/pause", response_model=OperationView)
    async def pause_operation(
        operation_id: str, body: PauseOperationRequest
    ) -> OperationView:
        return await control(operation_id, "pause", body.request_key)

    @router.post("/{operation_id}/continue", response_model=OperationView)
    async def continue_operation(
        operation_id: str, body: ContinueOperationRequest
    ) -> OperationView:
        return await control(
            operation_id,
            "continue",
            body.request_key,
            expected_generation=body.expected_generation,
            text=body.text,
        )

    @router.post("/{operation_id}/interject", response_model=OperationView)
    async def interject_operation(
        operation_id: str, body: ContinueOperationRequest
    ) -> OperationView:
        return await control(
            operation_id,
            "interject",
            body.request_key,
            expected_generation=body.expected_generation,
            text=body.text,
        )

    @router.get("/{operation_id}/events", response_model=OperationEventsView)
    def read_events(
        operation_id: str, after: int = Query(default=0, ge=0)
    ) -> OperationEventsView:
        """按游标回放这一操作的事件；每一条带它所属的代次。"""
        operation = read(operation_id)
        store = store_for(operation_id)
        events = () if store is None else store.events(operation.conversation_id, after=after)
        records, last_seq = _annotate(operation, events)
        if operation.purpose == "probe":
            records = [event for event in operation.recovery_request.get("events", [])
                       if event["seq"] > after]
            last_seq = records[-1]["seq"] if records else after
        return OperationEventsView(
            events=records, last_seq=last_seq, operation=view(operation)
        )

    @router.get("/{operation_id}/stream")
    async def stream_events(
        operation_id: str,
        after: int = Query(default=0, ge=0),
        follow: bool = Query(default=True),
    ) -> StreamingResponse:
        """事件流：操作还在执行时持续跟随，结束或暂停后读完剩余事件再关闭。"""
        operation = read(operation_id)
        store = store_for(operation_id)

        async def stream() -> AsyncIterator[str]:
            if store is None:
                yielded = after
                while True:
                    current = read(operation_id)
                    for event in current.recovery_request.get("events", []):
                        if event["seq"] > yielded:
                            yielded = event["seq"]
                            body = json.dumps(event, ensure_ascii=False)
                            yield f"id: {yielded}\ndata: {body}\n\n"
                    if not follow or current.status not in ("accepted", "running"):
                        return
                    await asyncio.sleep(0.1)
            tail = store.tail(operation.conversation_id, after=after)
            # 第一次 attempt_started 之前的事件属于第 1 代：受理本身就是这一代的开始。
            generation = 1
            yielded = after
            idle = 0.0
            while True:
                batch = tail.read()
                for record in batch:
                    if not _belongs(operation, record):
                        continue
                    if isinstance(record.get("generation"), int):
                        generation = int(record["generation"])
                    payload = {
                        **record,
                        "operation_id": operation.id,
                        "generation": generation,
                    }
                    yielded = int(record["seq"])
                    body = json.dumps(payload, ensure_ascii=False)
                    yield f"id: {record['seq']}\ndata: {body}\n\n"
                if not follow:
                    return
                current = operations.try_read(operation_id)
                if current is None:
                    return
                if current.status not in ("accepted", "running") and yielded >= current.last_seq:
                    return
                if not batch and current.status not in ("accepted", "running"):
                    # 已不在执行，但收尾事件还没写完：等一小会儿；超过上限就按已知内容关闭。
                    idle += 0.1
                    if idle >= _DRAIN_SECONDS:
                        return
                await asyncio.sleep(0.1)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/{operation_id}/context", response_model=OperationContextView)
    def read_context(operation_id: str) -> OperationContextView:
        """这一操作实际用了哪些材料：受理时冻结的引用与各代次的注入摘要。"""
        operation = read(operation_id)
        store = store_for(operation_id)
        injections: list[dict[str, Any]] = []
        for event in () if store is None else store.events(operation.conversation_id):
            if not _belongs(operation, event):
                continue
            # 装配记录与结构注入都是「实际输入」：前者是分区摘要，后者是结构覆盖范围。
            if event.get("type") not in ("state_injected", "story_context"):
                continue
            injections.append(
                {
                    "seq": event.get("seq"),
                    "generation": event.get("generation", 0),
                    "summary": {
                        key: value
                        for key, value in event.items()
                        if key
                        not in ("seq", "at", "event_version", "event_id", "conversation_id")
                    },
                }
            )
        return OperationContextView(
            operation_id=operation.id,
            model=operation.model,
            connection=operation.connection,
            refs=operation.context_refs,
            injections=injections,
            note=(
                "连接测试不读取作品材料；这里只列出这次测试本身。"
                if operation.purpose == "probe"
                else "引用是受理时冻结的身份；正文由服务端按标识读取，不在这里复制整份内容。"
            ),
        )

    @router.get("/{operation_id}/result", response_model=OperationResultView)
    def read_result(operation_id: str) -> OperationResultView:
        """结果以实际记录为准：提交收据、改动路径与待回答问题。"""
        operation = read(operation_id)
        store = store_for(operation_id)
        changed: list[str] = []
        question: dict[str, Any] | None = None
        for event in () if store is None else store.events(operation.conversation_id):
            if not _belongs(operation, event):
                continue
            if event.get("type") == "version_finished":
                for path in event.get("changed_paths") or []:
                    if isinstance(path, str) and path not in changed:
                        changed.append(path)
            elif event.get("type") == "author_input_requested" and question is None:
                question = {
                    "question": event.get("question"),
                    "question_id": event.get("question_id"),
                }
        return OperationResultView(
            operation_id=operation.id,
            status=operation.status,
            note=operation.note,
            receipts=operation.committed_receipts,
            pending_units=operation.pending_units,
            changed_paths=changed,
            question=question,
        )

    return router


def _unavailable(operation_id: str, label: str) -> WorkspaceError:
    """入口在当前进程不可用时明确报错，不退回同步调用。"""
    return WorkspaceError(
        code="OPERATION_UNAVAILABLE_CONFLICT",
        where="operations.control",
        why=f"{label}的控制入口在当前进程不可用",
        fix="刷新后查看结果；需要重新开始就再发起一次",
        details={"operation_id": operation_id},
    )


def _belongs(operation: Any, event: Mapping[str, Any]) -> bool:
    """事件是否属于这一操作。

    写作轮次按 ``turn_id`` 归属；压缩没有轮次，事件写入 ``compaction_operation_id``。
    连接测试不写会话事件，调用方在 store 为空时已经直接跳过。
    """
    if operation.purpose == "compaction":
        return str(event.get("compaction_operation_id") or "") == str(operation.id)
    return str(event.get("turn_id") or "") == str(operation.turn_id)


def _annotate(
    operation: Any, events: tuple[dict[str, Any], ...]
) -> tuple[list[dict[str, Any]], int]:
    """把这一操作的事件标回代次；没有 ``attempt_started`` 的旧事件按第 1 代。"""
    generation = 1
    records: list[dict[str, Any]] = []
    for event in events:
        if not _belongs(operation, event):
            continue
        # 事件自己带代次时以它为准：插话与收尾会跨在两代之间，按区间猜会标错。
        if isinstance(event.get("generation"), int):
            generation = int(event["generation"])
        records.append({**event, "operation_id": operation.id, "generation": generation})
    return records, int(records[-1]["seq"]) if records else 0
