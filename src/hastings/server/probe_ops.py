"""模型连接测试作为统一操作（1.0.1 P2）。

一次「测试连接」也是一次被受理的模型调用：真实发出请求、可能超时、可以被取消。
1.0.1 之前它同步返回汇总，作者既看不到它正在跑，也没法在发现地址填错时立刻停下。

这里把它登记为 ``purpose="probe"`` 的操作：

- **202 + operation_id**：提交即返回标识，结果用操作快照与结果接口读取。
- **可暂停**：取消正在等待的供应商请求；不伪造结果。
- **可继续**：同一进程内保留这次测试的输入，作者可以重新发一次同样的测试。
- **不持久化输入**：草稿里可能有刚填写的 API Key，不写进操作文件；重启后不提供继续。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from hastings.server.model_probe import ProbeResult
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.operations import OPERATION_TERMINAL, Operation, utc_now

if TYPE_CHECKING:
    from hastings.server.model_connections import ConnectionDraft, ModelConnectionService
    from hastings.workspace.operations import OperationStore

__all__ = ["ProbeOperation", "ProbeRunner"]


@dataclass(slots=True)
class ProbeOperation:
    """执行期只需要输入与任务引用；状态事实在操作表里。"""

    operation_id: str
    kind: str
    model: str
    draft: ConnectionDraft
    connection_id: str | None
    task: asyncio.Task[None] | None = field(default=None, repr=False)


class ProbeRunner:
    """受理、执行与取消连接测试。"""

    def __init__(self, operations: OperationStore, service: ModelConnectionService) -> None:
        self.operations = operations
        self.service = service
        self._operations: dict[str, ProbeOperation] = {}
        self._lock = asyncio.Lock()

    # -- 受理 ---------------------------------------------------------------

    async def submit(
        self,
        *,
        kind: str,
        model: str,
        draft: ConnectionDraft,
        connection_id: str | None,
        request_key: str,
    ) -> Operation:
        if kind not in ("response", "tool"):
            raise WorkspaceError(
                code="MODEL_PROBE_KIND_INVALID",
                where="model.probe",
                why=f"未知的测试类型：{kind!r}",
                fix="测试类型只能是 response（响应）或 tool（工具调用）",
            )
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "kind": kind,
                    "model": model,
                    "connection_id": connection_id,
                    "draft": asdict(draft),
                },
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()
        async with self._lock:
            existing = self.operations.find_by_request_key(
                purpose="probe", conversation_id="", request_key=request_key
            )
            if existing is not None:
                if existing.recovery_request.get("input_fingerprint") != fingerprint:
                    raise WorkspaceError(
                        code="OPERATION_REQUEST_CONFLICT",
                        where="model.probe",
                        why="同一请求键对应的测试输入已经变化",
                        fix="新测试使用新的请求标识；重试保持原输入",
                    )
                return existing
            label = "响应测试" if kind == "response" else "工具测试"
            operation = Operation(
                id=f"op_{uuid.uuid4().hex[:12]}",
                owner="models",
                workspace_id="",
                conversation_id="",
                turn_id="",
                purpose="probe",
                request_key=request_key,
                target=f"{model or '未选择模型'}·{label}",
                model=model,
                connection=connection_id or "draft",
                recovery_request={"input_fingerprint": fingerprint},
            )
            self.operations.create(operation)
            runtime = ProbeOperation(
                operation_id=operation.id,
                kind=kind,
                model=model,
                draft=draft,
                connection_id=connection_id,
            )
            self._operations[operation.id] = runtime
            runtime.task = asyncio.create_task(self._run(runtime))
            return operation

    # -- 执行 ---------------------------------------------------------------

    async def _run(self, runtime: ProbeOperation) -> None:
        self._set(runtime.operation_id, status="running", phase=runtime.kind)
        generation = self.operations.read(runtime.operation_id).generation

        def output(text: str) -> None:
            def mutate(operation: Operation) -> None:
                if not operation.accepts_output(generation):
                    return
                events = operation.recovery_request.setdefault("events", [])
                events.append(
                    {
                        "seq": len(events) + 1,
                        "type": "text_delta",
                        "generation": generation,
                        "operation_id": operation.id,
                        "text": text,
                    }
                )

            self.operations.update(runtime.operation_id, mutate)

        try:
            result = await self.service.probe(
                runtime.draft,
                kind=runtime.kind,
                model=runtime.model,
                connection_id=runtime.connection_id,
                on_text=output,
            )
        except asyncio.CancelledError:
            # 取消来自暂停：保留暂停状态，不把取消说成失败。
            self._pause(runtime.operation_id, "连接测试已暂停；这次结果不保留。")
        except WorkspaceError as exc:
            self._fail(
                runtime.operation_id,
                {"code": exc.code, "where": exc.where, "why": exc.why, "fix": exc.fix},
                exc.why,
                generation=generation,
            )
        except Exception as exc:  # 未预计的失败同样要给出确定结论
            self._fail(
                runtime.operation_id,
                {
                    "code": "MODEL_PROBE_FAILED",
                    "where": "model.probe",
                    "why": str(exc) or type(exc).__name__,
                    "fix": "检查连接地址与凭据后重新测试",
                },
                "连接测试没有完成。",
                generation=generation,
            )
        else:
            self._finish(runtime.operation_id, result, generation=generation)
        finally:
            stored = self.operations.try_read(runtime.operation_id)
            if stored is None or stored.status in OPERATION_TERMINAL:
                # 已结束就丢掉输入；暂停保留，作者可以继续同一测试。
                self._operations.pop(runtime.operation_id, None)

    def _set(self, operation_id: str, *, status: str, phase: str, note: str = "") -> None:
        def mutate(operation: Operation) -> None:
            operation.status = status  # type: ignore[assignment]
            operation.phase = phase
            operation.note = note
            operation.error = None

        with suppress(WorkspaceError):
            self.operations.update(operation_id, mutate)

    def _pause(self, operation_id: str, note: str) -> None:
        def mutate(operation: Operation) -> None:
            if operation.status in OPERATION_TERMINAL:
                return
            operation.status = "paused"
            operation.phase = "paused"
            operation.note = note

        with suppress(WorkspaceError):
            self.operations.update(operation_id, mutate)

    def _fail(
        self, operation_id: str, error: dict[str, Any], note: str, *, generation: int
    ) -> None:
        def mutate(operation: Operation) -> None:
            if not operation.accepts_output(generation):
                return
            operation.status = "failed"
            operation.phase = "failed"
            operation.note = note
            operation.error = error
            operation.committed_receipts.append(
                {"kind": "probe", "at": utc_now(), "outcome": _outcome(error, note)}
            )

        with suppress(WorkspaceError):
            self.operations.update(operation_id, mutate)

    def _finish(self, operation_id: str, result: ProbeResult, *, generation: int) -> None:
        def mutate(operation: Operation) -> None:
            if not operation.accepts_output(generation):
                return
            failed = not result.ok
            operation.status = "failed" if failed else "completed"
            operation.phase = "failed" if failed else "completed"
            operation.note = result.message
            operation.error = (
                {
                    "code": result.code,
                    "where": "model.probe",
                    "why": result.message,
                    "fix": "按提示检查连接、凭据或模型后再测试",
                }
                if failed
                else None
            )
            outcome = {
                "model": result.model,
                "response": result.response,
                "tool_call": result.tool_call,
                "code": result.code,
                "message": result.message,
            }
            operation.committed_receipts = [
                item for item in operation.committed_receipts if item.get("kind") != "probe"
            ]
            operation.committed_receipts.append(
                {"kind": "probe", "at": utc_now(), "outcome": outcome}
            )

        with suppress(WorkspaceError):
            self.operations.update(operation_id, mutate)

    # -- 控制 ---------------------------------------------------------------

    async def pause_operation(self, operation_id: str, request_key: str) -> Operation:
        async with self._lock:
            operation = self.operations.read(operation_id)
            prior = operation.control(request_key)
            if prior is not None:
                _require_control(prior, "pause", operation_id)
                return operation
            if operation.status in OPERATION_TERMINAL or operation.status == "paused":
                return operation

            def mutate(current: Operation) -> None:
                current.status = "paused"
                current.phase = "paused"
                current.note = "连接测试已暂停；没有采用这次结果。"
                current.record_control(request_key, "pause", generation=current.generation)

            operation = self.operations.update(operation_id, mutate)
            runtime = self._operations.get(operation_id)
            task = runtime.task if runtime is not None else None
            if task is not None and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            return operation

    async def continue_operation(
        self,
        operation_id: str,
        request_key: str,
        *,
        expected_generation: int,
        text: str = "",
    ) -> Operation:
        kind = "interject" if text.strip() else "continue"
        target = {
            "响应测试": "response",
            "测试响应": "response",
            "工具测试": "tool",
            "测试工具调用": "tool",
        }.get(text.strip())
        if text.strip() and target is None:
            raise WorkspaceError(
                code="OPERATION_INPUT_INVALID",
                where="operations.interject",
                why="连接测试的补充目标无法识别",
                fix="填写“响应测试”或“工具测试”；连接凭据须在设置页修改",
            )
        async with self._lock:
            operation = self.operations.read(operation_id)
            prior = operation.control(request_key)
            if prior is not None:
                _require_control(
                    prior, kind, operation_id, expected_generation=expected_generation, text=text
                )
                return operation
            if expected_generation != operation.generation:
                raise WorkspaceError(
                    code="OPERATION_GENERATION_CONFLICT",
                    where="operations.control",
                    why=(
                        f"这次操作已经是第 {operation.generation} 代，"
                        f"请求里写的是第 {expected_generation} 代"
                    ),
                    fix="刷新任务状态后按当前代次再操作；旧代次的按钮已经失效",
                    details={"operation_id": operation.id, "generation": operation.generation},
                )
            if not operation.resumable:
                raise WorkspaceError(
                    code="OPERATION_RECOVERED_CONFLICT",
                    where="operations.control",
                    why="服务重启后已经丢失这次测试的输入",
                    fix="在模型设置里重新测试",
                    details={"operation_id": operation.id},
                )
            if operation.status not in (
                ("paused", "accepted", "running") if target else ("paused",)
            ):
                raise WorkspaceError(
                    code="OPERATION_STATE_CONFLICT",
                    where="operations.control",
                    why=f"这次操作当前是「{operation.status}」，不能继续",
                    fix="先暂停正在执行的测试，或等它结束后再操作",
                    details={"operation_id": operation.id, "status": operation.status},
                )
            runtime = self._operations.get(operation_id)
            if runtime is None or runtime.task is None:
                raise WorkspaceError(
                    code="OPERATION_RECOVERED_CONFLICT",
                    where="operations.control",
                    why="这次测试的输入只存在于当前进程",
                    fix="在模型设置里重新测试",
                    details={"operation_id": operation.id},
                )

            if runtime.task is not None and not runtime.task.done():
                self.operations.update(
                    operation_id, lambda current: setattr(current, "status", "paused")
                )
                runtime.task.cancel()
                with suppress(asyncio.CancelledError):
                    await runtime.task
            if target is not None:
                runtime.kind = target

            def mutate(current: Operation) -> None:
                current.generation += 1
                current.status = "accepted"
                current.phase = runtime.kind
                current.note = ""
                current.error = None
                current.record_control(
                    request_key,
                    kind,
                    generation=current.generation,
                    expected_generation=expected_generation,
                    text=text.strip(),
                )

            operation = self.operations.update(operation_id, mutate)
            runtime.task = asyncio.create_task(self._run(runtime))
            return operation


def _require_control(
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


def _outcome(error: dict[str, Any], note: str) -> dict[str, Any]:
    return {
        "model": "",
        "response": "failed",
        "tool_call": "failed",
        "code": error.get("code"),
        "message": note,
    }
