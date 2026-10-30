"""统一的模型操作：受理归属、代次资格、暂停、继续与插话（1.0.1 P2）。

1.0.1 之前，模型入口各有一套结束方式：轮次靠 ``stop``，压缩靠自己的 ``stop``，
连接测试一次性返回。作者看到的是「有的能停，有的不能；停了不知道还能不能接着来」。
这个模块把「一次被受理的模型任务」变成同一种持久对象：

- **操作有稳定身份**：``op_`` 前缀的 ID、所属作品与会话、用途与目标。刷新、断线、
  换页面都按 ID 找回同一个任务，不靠界面里的「最后一条消息」猜。
- **代次决定谁能写**：每次真正请求模型是一个代次。暂停先持久撤销代次资格，再取消
  远端请求；被撤销的代次即使晚到，文本、工具结果与提交都不再被接受。
- **控制请求幂等**：暂停、继续、插话都带 ``request_key``。同一个键重复提交返回同一
  结果，不产生第二次取消，也不会重复追加要求。
- **重启只恢复事实**：进程重启后未终结的操作转为 ``needs_attention``。继续需要原本
  冻结的请求身份，重启后已在内存里丢失，因此标为不可继续——不假装能接上原来的任务。

这里只管控制面。模型输出仍然写在会话事件里，操作不复制第二份执行记录；接口读取时
按 ``attempt_started`` 把事件区间标回代次。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from hastings.workspace.atomic_io import atomic_write_json, locked
from hastings.workspace.errors import WorkspaceError

__all__ = [
    "OPERATION_TERMINAL",
    "Operation",
    "OperationStatus",
    "OperationStore",
    "operation_status_for_run",
    "utc_now",
]

OperationStatus = Literal[
    "accepted",
    "running",
    "paused",
    "awaiting_answer",
    "needs_attention",
    "completed",
    "failed",
]

#: 不会再自行变化的状态；暂停可以继续，所以不算终态。
OPERATION_TERMINAL = frozenset({"completed", "failed"})

#: 可以由作者继续的状态：暂停、等作者回答、以及「有问题需要处理」。
RESUMABLE_STATUS = frozenset({"paused", "awaiting_answer", "needs_attention"})

#: 有「补充要求」通道的用途。连接测试没有可解释的自然语言指令，不宣称能插话。
INTERJECTABLE_PURPOSES = frozenset({"chat", "review", "compaction", "probe"})

_RUN_STATUS: dict[str, OperationStatus] = {
    "completed": "completed",
    "needs_input": "awaiting_answer",
    "partial": "needs_attention",
    "stopped": "paused",
    "failed": "failed",
    "interrupted": "needs_attention",
}


def utc_now() -> str:
    """本地时区的毫秒时间戳；界面直接显示，不再换算。"""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def operation_status_for_run(status: str) -> OperationStatus:
    """一轮执行状态 → 操作状态；未知状态按需要处理，不伪装成完成。"""
    return _RUN_STATUS.get(status, "needs_attention")


@dataclass
class Operation:
    """一次被受理的模型任务。字段与[数据契约](../../dev-notes/v1.0.1/data-and-runtime.md)一致。"""

    id: str
    owner: str
    workspace_id: str
    conversation_id: str
    turn_id: str
    purpose: str
    #: 受理时作者给的幂等键。压缩与连接测试没有会话受理记录可查，只能靠它认重复。
    request_key: str = ""
    target: str = ""
    status: OperationStatus = "accepted"
    generation: int = 1
    phase: str = "accepted"
    model: str = ""
    connection: str = ""
    #: 作者原话、继续与插话按发生顺序记录；插话的文本确实来自作者。
    instruction_log: list[dict[str, Any]] = field(default_factory=list)
    #: 受理时冻结的材料身份；只有标识与指纹，不含正文副本。
    context_refs: list[dict[str, Any]] = field(default_factory=list)
    #: 每次尝试的结束状态，供回看「暂停发生在哪一代」。
    checkpoints: list[dict[str, Any]] = field(default_factory=list)
    #: 已提交的收据：Git 记录与后续的结构变更单元。
    committed_receipts: list[dict[str, Any]] = field(default_factory=list)
    #: 没有完成的单元；空列表表示没有未完成项。
    pending_units: list[str] = field(default_factory=list)
    #: 受理时冻结的任务目标：区分讨论、结构修改、模块保存与待澄清。
    #: 它只用于判断完成，不授予写权限。
    intent: dict[str, Any] = field(default_factory=dict)
    last_seq: int = 0
    created_at: str = ""
    updated_at: str = ""
    note: str = ""
    error: dict[str, Any] | None = None
    #: 控制请求收据：同键重复提交返回同一结果。
    controls: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: 重启后为假：没有冻结的请求身份，继续只能重开一轮。
    resumable: bool = True
    recovery_request: dict[str, Any] = field(default_factory=dict)
    version: int = 1

    # -- 判定 ---------------------------------------------------------------

    def accepts_output(self, generation: int) -> bool:
        """该代次现在还能写入吗：只有当前代次且操作仍在运行才行。"""
        return self.generation == generation and self.status in ("accepted", "running")

    def control(self, request_key: str) -> dict[str, Any] | None:
        return self.controls.get(request_key) if request_key else None

    def record_control(
        self, request_key: str, kind: str, *, generation: int, note: str = "",
        request_action: str = "", expected_generation: int | None = None, text: str = "",
    ) -> dict[str, Any]:
        receipt = {
            "kind": kind,
            "generation": generation,
            "status": self.status,
            "at": utc_now(),
            "note": note,
            "request_action": request_action or kind,
            "expected_generation": expected_generation,
            "text": text.strip(),
        }
        if request_key:
            self.controls[request_key] = receipt
        return receipt

    def instruction(self, text: str, kind: str, *, request_key: str = "") -> None:
        self.instruction_log.append(
            {
                "kind": kind,
                "text": text,
                "generation": self.generation,
                "request_key": request_key,
                "at": utc_now(),
            }
        )

    def checkpoint(self, status: str, *, generation: int, note: str = "") -> None:
        self.checkpoints.append(
            {"generation": generation, "status": status, "note": note, "at": utc_now()}
        )

    # -- 表示 ---------------------------------------------------------------

    def as_view(self) -> dict[str, Any]:
        """给界面的快照：不含请求键、事件游标等内部字段。"""
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "purpose": self.purpose,
            "target": self.target,
            "status": self.status,
            "generation": self.generation,
            "phase": self.phase,
            "model": self.model,
            "connection": self.connection,
            "note": self.note,
            "error": self.error,
            "pending_units": list(self.pending_units),
            "last_seq": self.last_seq,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "capabilities": {
                "pause": self.status in ("accepted", "running"),
                # 重启恢复后仅冻结请求完整的操作可以尝试继续。
                "continue": self.status in RESUMABLE_STATUS and self.resumable,
                "interject": (
                    self.status in ("accepted", "running")
                    and self.resumable
                    and self.purpose in INTERJECTABLE_PURPOSES
                ),
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "id": self.id,
            "owner": self.owner,
            "workspace_id": self.workspace_id,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "purpose": self.purpose,
            "request_key": self.request_key,
            "target": self.target,
            "status": self.status,
            "generation": self.generation,
            "phase": self.phase,
            "model": self.model,
            "connection": self.connection,
            "instruction_log": self.instruction_log,
            "context_refs": self.context_refs,
            "checkpoints": self.checkpoints,
            "committed_receipts": self.committed_receipts,
            "pending_units": self.pending_units,
            "intent": self.intent,
            "last_seq": self.last_seq,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "note": self.note,
            "error": self.error,
            "controls": self.controls,
            "resumable": self.resumable,
            "recovery_request": self.recovery_request,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Operation:
        status = str(raw.get("status") or "accepted")
        if status not in (
            "accepted",
            "running",
            "paused",
            "awaiting_answer",
            "needs_attention",
            "completed",
            "failed",
        ):
            status = "needs_attention"
        return cls(
            id=str(raw["id"]),
            owner=str(raw.get("owner") or ""),
            workspace_id=str(raw.get("workspace_id") or ""),
            conversation_id=str(raw.get("conversation_id") or ""),
            turn_id=str(raw.get("turn_id") or ""),
            purpose=str(raw.get("purpose") or "chat"),
            request_key=str(raw.get("request_key") or ""),
            target=str(raw.get("target") or ""),
            status=status,  # type: ignore[arg-type]
            generation=max(1, int(raw.get("generation") or 1)),
            phase=str(raw.get("phase") or "accepted"),
            model=str(raw.get("model") or ""),
            connection=str(raw.get("connection") or ""),
            instruction_log=list(raw.get("instruction_log") or []),
            context_refs=list(raw.get("context_refs") or []),
            checkpoints=list(raw.get("checkpoints") or []),
            committed_receipts=list(raw.get("committed_receipts") or []),
            pending_units=[str(item) for item in raw.get("pending_units") or []],
            intent=dict(raw.get("intent") or {}),
            last_seq=int(raw.get("last_seq") or 0),
            created_at=str(raw.get("created_at") or ""),
            updated_at=str(raw.get("updated_at") or ""),
            note=str(raw.get("note") or ""),
            error=raw.get("error") if isinstance(raw.get("error"), dict) else None,
            controls={
                str(key): dict(value)
                for key, value in (raw.get("controls") or {}).items()
                if isinstance(value, dict)
            },
            resumable=bool(raw.get("resumable", True)),
            recovery_request=dict(raw.get("recovery_request") or {}),
            version=int(raw.get("version") or 1),
        )


class OperationStore:
    """按操作分文件的持久存储：一个操作一个 JSON，写入走原子替换。

    分文件的理由是读多写少且互不相关：暂停一个操作不该重写其他操作的文件，
    损坏一个操作也不该让整张表读不出来。
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.directory = self.root / "operations"

    # -- 路径 ---------------------------------------------------------------

    def path(self, operation_id: str) -> Path:
        if not operation_id or "/" in operation_id or "\\" in operation_id:
            raise WorkspaceError(
                code="OPERATION_NOT_FOUND",
                where="operations.read",
                why=f"操作标识不合法：{operation_id}",
                fix="从界面重新读取当前任务",
                details={"operation_id": operation_id},
            )
        return self.directory / f"{operation_id}.json"

    def _lock_path(self, operation_id: str) -> Path:
        return self.directory / f"{operation_id}.lock"

    @contextmanager
    def eligible_generation(self, operation_id: str, generation: int) -> Iterator[None]:
        """提交决定与暂停共用操作锁，决定前核对该代仍有资格。"""
        with locked(self._lock_path(operation_id)):
            operation = self.read(operation_id)
            if not operation.accepts_output(generation):
                raise WorkspaceError(
                    code="OPERATION_PAUSED_CONFLICT",
                    where="operations.commit",
                    why="这次操作的代次已不具备提交资格",
                    fix="读取当前任务状态；需要继续时开启新的代次",
                    details={"operation_id": operation_id, "generation": generation},
                )
            yield

    # -- 读 -----------------------------------------------------------------

    def read(self, operation_id: str) -> Operation:
        path = self.path(operation_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise WorkspaceError(
                code="OPERATION_NOT_FOUND",
                where="operations.read",
                why=f"没有找到这次操作：{operation_id}",
                fix="刷新后按当前显示的任务查看；已过期的事件不再重放",
                details={"operation_id": operation_id},
            ) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkspaceError(
                code="OPERATION_STATE_FAILED",
                where="operations.read",
                why=f"操作记录无法读取：{exc}",
                fix="保留状态目录现状并联系维护者；不要手工改写这个文件",
                details={"operation_id": operation_id},
            ) from exc
        if not isinstance(raw, dict):
            raise WorkspaceError(
                code="OPERATION_STATE_FAILED",
                where="operations.read",
                why="操作记录不是一个对象",
                fix="保留状态目录现状并联系维护者；不要手工改写这个文件",
                details={"operation_id": operation_id},
            )
        return Operation.from_dict(raw)

    def try_read(self, operation_id: str) -> Operation | None:
        if not operation_id:
            return None
        try:
            return self.read(operation_id)
        except WorkspaceError:
            return None

    def list(self) -> tuple[Operation, ...]:
        if not self.directory.is_dir():
            return ()
        items: list[Operation] = []
        for path in sorted(self.directory.glob("*.json")):
            operation = self.try_read(path.stem)
            if operation is not None:
                items.append(operation)
        return tuple(items)

    def find_by_request_key(
        self, *, purpose: str, conversation_id: str, request_key: str
    ) -> Operation | None:
        """按幂等键找已受理的操作：压缩与连接测试没有会话受理记录可查。"""
        if not request_key:
            return None
        for operation in self.list():
            if (
                operation.purpose == purpose
                and operation.conversation_id == conversation_id
                and operation.request_key == request_key
            ):
                return operation
        return None

    # -- 写 -----------------------------------------------------------------

    def create(self, operation: Operation) -> Operation:
        path = self.path(operation.id)
        with locked(self._lock_path(operation.id)):
            if path.exists():
                raise WorkspaceError(
                    code="OPERATION_REQUEST_CONFLICT",
                    where="operations.create",
                    why=f"操作标识已存在：{operation.id}",
                    fix="重新读取当前任务；不要复用旧标识",
                    details={"operation_id": operation.id},
                )
            now = utc_now()
            operation.created_at = operation.created_at or now
            operation.updated_at = now
            atomic_write_json(path, operation.to_dict())
        return operation

    def save(self, operation: Operation) -> Operation:
        path = self.path(operation.id)
        with locked(self._lock_path(operation.id)):
            operation.updated_at = utc_now()
            atomic_write_json(path, operation.to_dict())
        return operation

    def update(self, operation_id: str, mutate: Callable[[Operation], None]) -> Operation:
        """读—改—写在同一个文件锁里完成。

        暂停、继续与收尾都要基于「刚读到的状态」判断，分两次加锁会让两边都以为
        自己拿到的是最新状态。这里不返回值判断，调用方要用的就是锁内改完的那一份。
        """
        path = self.path(operation_id)
        with locked(self._lock_path(operation_id)):
            operation = self.read(operation_id)
            mutate(operation)
            operation.updated_at = utc_now()
            atomic_write_json(path, operation.to_dict())
            return operation

    def recover(self, *, workspace_id: str | None = None) -> tuple[Operation, ...]:
        """进程重启后撤销旧代次；有冻结身份的操作可在显式请求后尝试继续。"""
        changed: list[Operation] = []
        for operation in self.list():
            if workspace_id is not None and operation.workspace_id != workspace_id:
                continue
            if operation.status in OPERATION_TERMINAL:
                continue
            if operation.phase == "interrupted" or (
                operation.status in ("paused", "awaiting_answer", "needs_attention")
                and not operation.resumable
            ):
                continue
            operation.status = "needs_attention"
            operation.phase = "interrupted"
            operation.resumable = bool(operation.recovery_request) and operation.purpose != "probe"
            operation.note = (
                "服务重启时这次操作没有正常结束；可检查结果后继续"
                if operation.resumable else
                "服务重启时这次操作没有正常结束；已写出的内容保留，可查看结果后重新发起"
            )
            operation.checkpoint(
                "interrupted", generation=operation.generation, note="服务重启"
            )
            # 重启后旧代次一律作废：即使有进程外的旧任务，也不能再写入。
            operation.generation += 1
            self.save(operation)
            changed.append(operation)
        return tuple(changed)


def new_operation_id() -> str:
    return f"op_{os.urandom(6).hex()}"


def context_refs_of(context: Any) -> list[dict[str, Any]]:
    """冻结材料身份 → 操作的来源清单；只放标识与指纹。"""
    if context is None:
        return []
    refs: list[dict[str, Any]] = []
    selected = getattr(context, "selected_path", None)
    if isinstance(selected, str) and selected:
        refs.append(
            {
                "kind": "selected",
                "path": selected,
                "digest": getattr(context, "observed_digests", {}).get(selected, ""),
            }
        )
    for reference in getattr(context, "references", ()) or ():
        refs.append(
            {
                "kind": "reference",
                "path": reference.path,
                "digest": reference.digest,
            }
        )
    return refs
