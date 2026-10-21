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
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Literal

from hastings.server.provider_runtime import ModelSnapshot
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

__all__ = [
    "EXTERNAL_CHANGE_MESSAGE",
    "ActiveTurn",
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

#: 取得「这一轮用哪个连接、哪个模型、哪个实例」；未配置时抛带 code 的错误。
#:
#: 工厂在受理一轮时调用一次并固定结果：受理之后切换或编辑连接只影响下一轮。
ProviderFactory = Callable[[], ModelSnapshot]

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
    #: 这一轮受理前的历史位置：恢复范围止于此，当前提示不会重复进入模型上下文。
    history_seq: int = 0
    title: str = ""


ActiveState = Literal["accepted", "running", "stop_requested"]


@dataclass
class ActiveTurn:
    """本作品当前被占用的那一轮；只放公开信息，不含实例与凭据。"""

    conversation_id: str
    turn_id: str
    state: ActiveState
    model: str
    connection: str
    started_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "state": self.state,
            "model": self.model,
            "connection": self.connection,
            "started_at": self.started_at,
        }


class TurnService:
    """执行一轮对话：串行写入、事件记录、停止与中断。

    一个作品同一时刻只允许一轮——**包括已受理但还没开始执行的轮次**。受理锁覆盖
    「查重 → 忙碌判断 → 写初始记录 → 占用」，不能等后台任务拿到执行锁才算忙碌：
    否则两次几乎同时的发送都会通过检查，第二个在队列里等待，作者却以为它没被接受。

    停止在受理阶段就有效：还没调用模型的轮次直接收尾为「已停止」，不必先有一个 harness。
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
        self._admission = asyncio.Lock()
        self._lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._harnesses: dict[str, AgentHarness] = {}
        self._started: set[str] = set()
        self._stopped: set[str] = set()
        self._active: ActiveTurn | None = None

    @property
    def busy(self) -> bool:
        return self._active is not None

    def active_turn(self) -> ActiveTurn | None:
        return self._active

    async def accept(
        self, text: str, request_key: str, *, conversation_id: str | None = None
    ) -> TurnInfo:
        """受理一轮并立即返回；``conversation_id`` 为空表示「首次发送即建会话」。"""
        async with self._admission:
            previous = self.store.find_accepted(request_key)
            if previous is not None:
                if conversation_id is not None and previous.conversation_id != conversation_id:
                    raise WorkspaceError(
                        code="REQUEST_KEY_CONFLICT",
                        where="turns.accept",
                        why="这个请求标识已经属于另一个会话",
                        fix="换一个请求标识，或回到原会话继续",
                        details={"conversation": previous.conversation_id},
                    )
                if self.store.prompt_of(
                    previous.conversation_id, previous.turn_id
                ) != text.strip():
                    raise WorkspaceError(
                        code="REQUEST_KEY_CONFLICT",
                        where="turns.accept",
                        why="这个请求标识已经用在另一条要求上",
                        fix="要发新的要求就换一个请求标识；要重试上一次，请保持文字不变",
                        details={"conversation": previous.conversation_id},
                    )
                return TurnInfo(
                    turn_id=previous.turn_id,
                    conversation_id=previous.conversation_id,
                    started_at=previous.started_at,
                    history_seq=previous.last_seq - 1,
                    title=previous.title,
                )
            if self._active is not None:
                raise WorkspaceError(
                    code="WORKSPACE_BUSY_CONFLICT",
                    where="turns.accept",
                    why="这个作品还有一轮正在写入",
                    fix="等当前这轮结束或停止它，再发送新的要求",
                    details={
                        "conversation": self._active.conversation_id,
                        "turn_id": self._active.turn_id,
                    },
                )
            if conversation_id is None:
                # 先做空提示校验，再决定是否生成会话 ID：拒绝时不留任何记录。
                clean = text.strip()
                if not clean:
                    raise WorkspaceError(
                        code="PROMPT_EMPTY",
                        where="turns.accept",
                        why="提示词是空的",
                        fix="写下要让模型做什么，再发送",
                    )
            history_seq = self._history_cursor(conversation_id)
            turn_id = f"t_{os.urandom(4).hex()}"
            # 受理时固定这一轮的模型事实：之后切换、编辑或删除连接都不影响它。
            snapshot: ModelSnapshot | None
            failure: Exception | None = None
            try:
                snapshot = self.provider_factory()
            except Exception as exc:  # 未配置模型等情况在受理时就已经知道
                snapshot, failure = None, exc
            model_facts = snapshot.describe() if snapshot is not None else {}
            if conversation_id is None:
                accepted = self.store.create_with_first_turn(
                    text=text,
                    request_key=request_key,
                    turn_id=turn_id,
                    facts=model_facts,
                )
            else:
                accepted = self.store.accept_turn(
                    conversation_id,
                    text=text,
                    request_key=request_key,
                    turn_id=turn_id,
                    facts=model_facts,
                )
            # 旧记录的占位标题在首次受理时补上；作者改过的标题不动。
            if accepted.title == "新的对话":
                with suppress(WorkspaceError):
                    self.store.rename(accepted.conversation_id, _summary(text) or "新的对话")
            info = TurnInfo(
                turn_id=turn_id,
                conversation_id=accepted.conversation_id,
                started_at=accepted.started_at,
                history_seq=history_seq,
                title=accepted.title,
            )
            self._active = ActiveTurn(
                conversation_id=accepted.conversation_id,
                turn_id=turn_id,
                state="accepted",
                model=str(model_facts.get("model", "")),
                connection=str(model_facts.get("connection_name", "")),
                started_at=accepted.started_at,
            )
            # 初始轮次事件里的模型字段由 store 与提示一起原子写入。
            self._tasks[turn_id] = asyncio.create_task(
                self._run(info, text, snapshot, failure)
            )
            return info

    async def wait(self, turn_id: str) -> None:
        """测试与需要同步结果的调用方使用。"""
        task = self._tasks.get(turn_id)
        if task is not None:
            with suppress(asyncio.CancelledError):
                await task

    async def stop(self, turn_id: str) -> bool:
        """请求停止：覆盖「已受理但还没调模型」与「正在跑」两种情况。"""
        async with self._admission:
            active = self._active
            if active is None or active.turn_id != turn_id:
                return False
            if turn_id in self._stopped:
                return True
            self._stopped.add(turn_id)
            active.state = "stop_requested"
            harness = self._harnesses.get(turn_id)
            if harness is not None:
                harness.cancel()
                return True
            if turn_id in self._started:
                # 已经进了 _run 但还没建好 harness：标志位足以让它跳过模型调用。
                return True
            # 还没开始执行：取消排队的任务，由这里直接写终态——别无第二处会写。
            task = self._tasks.pop(turn_id, None)
            if task is not None:
                task.cancel()
            self._record_terminal(
                active.conversation_id,
                turn_id,
                "stopped",
                note="在调用模型之前就停止了，没有产生请求",
            )
            self._active = None
            self._stopped.discard(turn_id)
            return True

    def mark_interrupted(self) -> list[str]:
        """进程重启后把没有正常结束的轮次标为中断；不自动继续。

        按轮次配对，而不是只看最后一条：崩在工具开始、工具结束或助理回复之后的轮次
        同样要被识别。已经有终态的轮次不会再补第二遍。
        """
        interrupted: list[str] = []
        for conversation in self.store.conversations():
            for turn_id in self.store.unfinished_turns(conversation.id):
                if turn_id in self._tasks:
                    continue
                self.store.append(
                    conversation.id,
                    {
                        "type": "run_finished",
                        "turn_id": turn_id,
                        "status": "interrupted",
                        "recording": "unrecorded",
                        "changed_paths": [],
                        "commit": None,
                        "note": (
                            "进程重启时这一轮没有正常结束；"
                            "文件以磁盘为准，可查看差异后单独补记"
                        ),
                    },
                )
                interrupted.append(conversation.id)
        return interrupted

    def _history_cursor(self, conversation_id: str | None) -> int:
        if conversation_id is None:
            return 0
        events = self.store.events(conversation_id)
        return int(events[-1]["seq"]) if events else 0

    def _record_terminal(
        self, conversation_id: str, turn_id: str, status: RunStatus, *, note: str = ""
    ) -> None:
        self.store.append(
            conversation_id,
            {
                "type": "run_finished",
                "turn_id": turn_id,
                "status": status,
                "recording": "no_change",
                "changed_paths": [],
                "commit": None,
                "note": note,
            },
        )

    async def _run(
        self,
        info: TurnInfo,
        text: str,
        snapshot: ModelSnapshot | None = None,
        failure: Exception | None = None,
    ) -> None:
        # 局部导入：agent 依赖本模块的 WriteSession，顶层导入会成环。
        from hastings.workspace.agent import RunContext, build_harness
        from hastings.workspace.conversation_context import (
            execution_facts,
            replay_public_messages,
        )

        self._started.add(info.turn_id)
        conversation_id = info.conversation_id
        status: RunStatus = "completed"
        session = WriteSession(self.workspace, instruction=text)
        try:
            if info.turn_id in self._stopped:
                # 受理后立刻被停止：不调用模型，也不产生任何请求。
                status = "stopped"
                return
            if snapshot is None:
                raise failure or WorkspaceError(
                    code="MODEL_NOT_CONFIGURED",
                    where="turn.model",
                    why="当前没有可用的模型来源或模型名，这一轮不会调用模型",
                    fix="在模型设置里选择连接与模型，或补齐 config.yaml 的连接配置",
                )
            async with self._lock:
                if self._active is not None and self._active.turn_id == info.turn_id:
                    self._active.state = (
                        "stop_requested" if info.turn_id in self._stopped else "running"
                    )
                repo = GitRepo(self.workspace.root)
                if repo.is_repository():
                    # 仓库已初始化时先确认可写：暂存区/冲突/分离头都在调用模型前挡住。
                    repo.require_ready()
                history_events = tuple(
                    event
                    for event in self.store.events(conversation_id)
                    if int(event["seq"]) <= info.history_seq
                )
                run = RunContext(workspace=self.workspace, session=session)
                harness = build_harness(
                    provider=snapshot.provider,
                    model=snapshot.model,
                    workspace=self.workspace,
                    run=run,
                    max_turns=self.max_turns,
                    history=replay_public_messages(history_events),
                    history_facts=execution_facts(history_events),
                )
                self._harnesses[info.turn_id] = harness
                if info.turn_id in self._stopped:
                    # 建 harness 期间作者点了停止：停在调用模型之前。
                    status = "stopped"
                    return
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
                # 配置类错误自带稳定 code 与可读文案，原样透传比包一层更有用。
                code = getattr(exc, "code", None) or "TURN_FAILED"
                message = getattr(exc, "message", None) or f"这一轮没有完成：{exc}"
                self.store.append(
                    conversation_id,
                    {
                        "type": "error",
                        "turn_id": info.turn_id,
                        "code": code,
                        "message": message,
                    },
                )
        finally:
            if snapshot is not None:
                # 完成、失败、停止都关闭本轮实例：下一轮重新构造，不复用可能已失效的连接。
                await snapshot.aclose()
            result = session.finish(status)
            self.store.append(
                conversation_id,
                {"type": "run_finished", "turn_id": info.turn_id, **result.to_dict()},
            )
            self._harnesses.pop(info.turn_id, None)
            self._started.discard(info.turn_id)
            self._stopped.discard(info.turn_id)
            self._tasks.pop(info.turn_id, None)
            # 只释放自己占用的那一轮：后面受理的新轮次不能被前一轮的收尾清掉。
            if self._active is not None and self._active.turn_id == info.turn_id:
                self._active = None

    async def _consume(self, harness: AgentHarness, info: TurnInfo, text: str) -> None:
        """把 harness 事件翻译成会话事件：工具过程逐条记，回复整条记。

        模型侧失败必须变成这一轮的失败：厂商报错时消息里没有正文、也没有工具调用，
        如果只是「记了个空回复」，作者看到的是「已完成、文件没改」，而真正的原因是
        端点或 key 不对。这里直接抛出，由 ``_run`` 落成 ``error`` 事件与失败状态。
        """
        reply: list[str] = []
        last_args: dict[str, dict[str, Any]] = {}
        async for event in harness.prompt(text):
            if isinstance(event, MessageEndEvent) and isinstance(event.message, AssistantMessage):
                message = event.message
                if message.stop_reason == "error" or message.error_message:
                    raise WorkspaceError(
                        code="MODEL_CALL_FAILED",
                        where="turn.model",
                        why=f"模型调用失败：{message.error_message or '厂商没有返回可用回复'}",
                        fix="检查 config.yaml 里该档案的 base_url、api_key 与模型名，"
                        "或换成可用的模型后重试这一轮",
                        details={"model": message.model},
                    )
                text_part = "".join(
                    block.text for block in message.content if isinstance(block, TextContent)
                )
                if text_part.strip():
                    reply.append(text_part)
            elif isinstance(event, ToolExecutionStartEvent):
                args = _short_args(event.args)
                last_args[event.tool_name] = args
                self.store.append(
                    info.conversation_id,
                    {
                        "type": "tool_started",
                        "turn_id": info.turn_id,
                        "tool": event.tool_name,
                        "args": args,
                    },
                )
            elif isinstance(event, ToolExecutionEndEvent):
                # 结束事件带同一份定位参数：界面据此显示「写入 正文/…」而不是解析 JSON。
                self.store.append(
                    info.conversation_id,
                    {
                        "type": "tool_finished",
                        "turn_id": info.turn_id,
                        "tool": event.tool_name,
                        "args": last_args.get(event.tool_name, {}),
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
