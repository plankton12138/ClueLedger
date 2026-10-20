"""一轮请求的执行与记录（0.4 M05）。

作者发出一次修改请求，可能只讨论、可能写零个到多个文件，也可能中途停止或失败。
这一层负责把「这一轮实际发生了什么」翻译成三件事：

- **内容基线**：首次真正写文件之前才准备。受管文件已有未记录的外部改动时，先单独
  记一笔「外部修改」，让作者改动和模型改动不混在同一个提交里；只读讨论不准备基线，
  也不要求仓库可写。
- **结果记录**：结束时按磁盘上的实际变化提交一次；没有变化不提交。停止或失败时
  已写入的内容仍然记一笔并标明「部分完成」。Git 失败不掩盖文件已保存的事实，
  结果记为 ``unrecorded``，可以单独补记，不重跑模型。
- **会话事件**：一轮的启动、工具过程、回复与结果是可回读的事件，界面据此补齐；
  一个作品同时只允许一轮在写，阅读不受限。

执行状态（完成/部分/失败/停止/中断）与记录状态（已提交/无变化/未记录）是两条轴：
「文件写进去了但历史没记上」必须能同时表达，不能合并成一个成功/失败。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.git import GitRepo
from hastings.workspace.state import ConversationStore
from tau_agent.events import (
    MessageEndEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
)
from tau_agent.harness import AgentHarness
from tau_agent.messages import AssistantMessage, TextContent
from tau_agent.provider import ModelProvider

__all__ = [
    "EXTERNAL_CHANGE_MESSAGE",
    "ProviderFactory",
    "Recording",
    "RunResult",
    "RunStatus",
    "TurnInfo",
    "TurnService",
    "WriteSession",
]

RunStatus = Literal["completed", "partial", "failed", "stopped", "interrupted"]
Recording = Literal["committed", "no_change", "unrecorded"]

#: 取得「这一轮用哪个 provider 与模型」；未配置时抛 WorkspaceError。
ProviderFactory = Callable[[], tuple[ModelProvider, str]]

#: 外部编辑形成的独立提交：让作者的改动先有归属，再开始这一轮。
EXTERNAL_CHANGE_MESSAGE = "外部修改：应用之外保存的内容"

_STATUS_LABEL: dict[str, str] = {
    "partial": "部分完成",
    "failed": "失败",
    "stopped": "已停止",
    "interrupted": "中断",
}

_MAX_SUMMARY = 40


@dataclass(frozen=True, slots=True)
class RunResult:
    """一轮的结果：执行状态、记录状态、实际改动路径与提交。"""

    status: RunStatus
    recording: Recording
    changed_paths: tuple[str, ...]
    commit: str | None
    note: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "recording": self.recording,
            "changed_paths": list(self.changed_paths),
            "commit": self.commit,
            "note": self.note,
        }


class WriteSession:
    """一轮写入的 Git 记录。调用顺序：``prepare()`` → 写文件 → ``finish(status)``。"""

    def __init__(self, workspace: Workspace, *, instruction: str = "") -> None:
        self.workspace = workspace
        self.repo = GitRepo(workspace.root)
        self.instruction = instruction
        self.baseline_head: str | None = None
        self.external_commit: str | None = None
        self.prepared = False

    def prepare(self) -> str | None:
        """首次实际修改前调用，返回这一轮的内容基线（提交 ID）。

        仓库不可写、有冲突或作者预暂存内容时抛错：此时还没有写任何文件，
        调用方应当停止写入，但阅读与讨论仍然可用。
        """
        self.repo.require_ready()
        self.baseline_head = self.repo.head()
        pending = self.repo.changes()
        if pending:
            # 作者在应用之外改过：先单独记一笔，避免混进本轮模型改动。
            self.external_commit = self.repo.record(EXTERNAL_CHANGE_MESSAGE)
            if self.external_commit is not None:
                self.baseline_head = self.external_commit
        self.prepared = True
        return self.baseline_head

    def finish(self, status: RunStatus = "completed") -> RunResult:
        """结束一轮：按实际变化提交一次，并如实报告记录状态。"""
        if not self.prepared:
            # 只读讨论：没有准备基线，也就没有要记录的内容。
            return RunResult(status=status, recording="no_change", changed_paths=(), commit=None)

        changes = self.repo.changes()
        paths = tuple(sorted({change.path for change in changes}))
        if not paths:
            return RunResult(status=status, recording="no_change", changed_paths=(), commit=None)

        head = self.repo.head()
        if head != self.baseline_head:
            # 运行期间历史被别人推进：不把未知改动并进本轮提交。
            return RunResult(
                status=status,
                recording="unrecorded",
                changed_paths=paths,
                commit=None,
                note="运行期间历史被外部改动，未自动提交；文件已保存，可单独补记",
            )

        try:
            commit = self.repo.record(self._message(status), paths)
        except WorkspaceError as exc:
            return RunResult(
                status=status,
                recording="unrecorded",
                changed_paths=paths,
                commit=None,
                note=f"文件已保存，历史尚未记录：{exc.why}",
            )
        if commit is None:
            return RunResult(status=status, recording="no_change", changed_paths=(), commit=None)
        return RunResult(
            status=status,
            recording="committed",
            changed_paths=paths,
            commit=commit,
        )

    def record_pending(self, message: str = EXTERNAL_CHANGE_MESSAGE) -> str | None:
        """单独补记当前未记录的改动：只执行 Git，不调用模型。"""
        self.repo.require_ready()
        return self.repo.record(message)

    def _message(self, status: RunStatus) -> str:
        summary = _summary(self.instruction)
        if status == "completed":
            return f"写作：{summary}" if summary else "写作：记录本轮修改"
        label = _STATUS_LABEL.get(status, status)
        return f"写作（{label}）：{summary}" if summary else f"写作（{label}）：记录已写入的内容"


def _summary(instruction: str) -> str:
    collapsed = " ".join(instruction.split())
    return collapsed[:_MAX_SUMMARY]


# -- 一轮请求 ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TurnInfo:
    turn_id: str
    conversation_id: str
    started_at: str


class TurnService:
    """执行一轮对话：串行写入、事件记录、停止与中断。

    一个作品同一时刻只允许一轮在写；第二轮直接拒绝（``WORKSPACE_BUSY_CONFLICT``），
    阅读不受影响。停止只保留已写入的文件并如实记录，不恢复原文、不自动继续。
    """

    def __init__(
        self,
        workspace: Workspace,
        store: ConversationStore,
        provider_factory: ProviderFactory,
        *,
        max_turns: int = 24,
    ) -> None:
        self.workspace = workspace
        self.store = store
        self.provider_factory = provider_factory
        self.max_turns = max_turns
        self._lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._harnesses: dict[str, AgentHarness] = {}
        self._stopped: set[str] = set()
        self._request_keys: dict[str, TurnInfo] = {}

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    async def start(
        self, conversation_id: str, text: str, *, request_key: str | None = None
    ) -> TurnInfo:
        """登记一轮并立即返回；实际执行在后台任务里。"""
        self.store.get(conversation_id)
        if request_key and request_key in self._request_keys:
            # 同一请求键重复发送：返回原来那一轮，不重复执行。
            return self._request_keys[request_key]
        if self._lock.locked():
            raise WorkspaceError(
                code="WORKSPACE_BUSY_CONFLICT",
                where="turns.start",
                why="这个作品还有一轮正在写入",
                fix="等当前这轮结束或停止它，再发送新的要求",
                details={"conversation": conversation_id},
            )
        turn_id = f"t_{os.urandom(4).hex()}"
        event = self.store.append(
            conversation_id,
            {"type": "turn_started", "turn_id": turn_id, "text": text},
        )
        info = TurnInfo(
            turn_id=turn_id, conversation_id=conversation_id, started_at=str(event["at"])
        )
        if request_key:
            self._request_keys[request_key] = info
        if self.store.get(conversation_id).title == "新的对话":
            self.store.rename(conversation_id, _summary(text) or "新的对话")
        self._tasks[turn_id] = asyncio.create_task(self._run(info, text))
        return info

    async def wait(self, turn_id: str) -> None:
        """测试与需要同步结果的调用方使用。"""
        task = self._tasks.get(turn_id)
        if task is not None:
            await task

    def stop(self, turn_id: str) -> bool:
        """请求停止：取消模型流，保留已写入文件，记录为「已停止」。"""
        harness = self._harnesses.get(turn_id)
        if harness is None:
            return False
        self._stopped.add(turn_id)
        harness.cancel()
        return True

    def mark_interrupted(self) -> list[str]:
        """进程重启后把没有正常结束的轮次标为中断；不自动继续。"""
        interrupted: list[str] = []
        for conversation in self.store.conversations():
            last = self.store.last_event(conversation.id)
            if last is None or last.get("type") != "turn_started":
                continue
            self.store.append(
                conversation.id,
                {
                    "type": "run_finished",
                    "turn_id": last.get("turn_id"),
                    "status": "interrupted",
                    "recording": "unrecorded",
                    "changed_paths": [],
                    "commit": None,
                    "note": "进程重启时这一轮没有正常结束；文件以磁盘为准，可查看差异后单独补记",
                },
            )
            interrupted.append(conversation.id)
        return interrupted

    async def _run(self, info: TurnInfo, text: str) -> None:
        # 局部导入：agent 依赖本模块的 WriteSession，顶层导入会成环。
        from hastings.workspace.agent import RunContext, build_harness

        conversation_id = info.conversation_id
        status: RunStatus = "completed"
        session = WriteSession(self.workspace, instruction=text)
        try:
            async with self._lock:
                repo = GitRepo(self.workspace.root)
                if repo.is_repository():
                    # 仓库已初始化时先确认可写：暂存区/冲突/分离头都在调用模型前挡住。
                    repo.require_ready()
                provider, model = self.provider_factory()
                run = RunContext(workspace=self.workspace, session=session)
                harness = build_harness(
                    provider=provider,
                    model=model,
                    workspace=self.workspace,
                    run=run,
                    max_turns=self.max_turns,
                )
                self._harnesses[info.turn_id] = harness
                await self._consume(harness, info, text)
                status = "stopped" if info.turn_id in self._stopped else "completed"
        except asyncio.CancelledError:
            status = "stopped"
            raise
        except WorkspaceError as exc:
            # 停止导致的收尾异常不算失败：作者主动结束，内容照常记录。
            status = "stopped" if info.turn_id in self._stopped else "failed"
            if status == "failed":
                self.store.append(
                    conversation_id,
                    {
                        "type": "error",
                        "turn_id": info.turn_id,
                        "code": exc.code,
                        "message": exc.message,
                    },
                )
        except Exception as exc:  # 一轮的失败要落到事件里，不能吞掉进程
            status = "stopped" if info.turn_id in self._stopped else "failed"
            if status == "failed":
                self.store.append(
                    conversation_id,
                    {
                        "type": "error",
                        "turn_id": info.turn_id,
                        "code": "TURN_FAILED",
                        "message": f"这一轮没有完成：{exc}",
                    },
                )
        finally:
            result = session.finish(status)
            self.store.append(
                conversation_id,
                {"type": "run_finished", "turn_id": info.turn_id, **result.to_dict()},
            )
            self._harnesses.pop(info.turn_id, None)
            self._stopped.discard(info.turn_id)
            self._tasks.pop(info.turn_id, None)

    async def _consume(self, harness: AgentHarness, info: TurnInfo, text: str) -> None:
        """把 harness 事件翻译成会话事件：工具过程逐条记，回复整条记。"""
        reply: list[str] = []
        async for event in harness.prompt(text):
            if isinstance(event, MessageEndEvent) and isinstance(event.message, AssistantMessage):
                text_part = "".join(
                    block.text for block in event.message.content if isinstance(block, TextContent)
                )
                if text_part.strip():
                    reply.append(text_part)
            elif isinstance(event, ToolExecutionStartEvent):
                self.store.append(
                    info.conversation_id,
                    {
                        "type": "tool_started",
                        "turn_id": info.turn_id,
                        "tool": event.tool_name,
                        "args": _short_args(event.args),
                    },
                )
            elif isinstance(event, ToolExecutionEndEvent):
                self.store.append(
                    info.conversation_id,
                    {
                        "type": "tool_finished",
                        "turn_id": info.turn_id,
                        "tool": event.tool_name,
                        "ok": not event.is_error,
                        "summary": _excerpt(event.result.text),
                    },
                )
        if reply:
            self.store.append(
                info.conversation_id,
                {"type": "assistant_message", "turn_id": info.turn_id, "text": "\n\n".join(reply)},
            )


def _short_args(arguments: dict[str, Any]) -> dict[str, Any]:
    """工具参数只保留定位信息，不把整章正文写进事件日志。"""
    kept: dict[str, Any] = {}
    for key in ("path", "source", "target", "query", "offset", "limit"):
        if key in arguments:
            kept[key] = arguments[key]
    return kept


def _excerpt(text: str, limit: int = 240) -> str:
    collapsed = " ".join(text.split())
    return collapsed[:limit]
