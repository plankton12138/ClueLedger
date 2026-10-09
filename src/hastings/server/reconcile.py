"""对账同步管线（P5 T4，计划 D5）：一处修订，两级上下文。

手动修改破坏的是两级上下文——结构化的故事账本与 agent 的会话记忆。
对账管线把两者一次补齐：保存成功后入队（同章 debounce 合并连续保存），
等 agent_end 且空闲时出队——用户对话优先；对账以一条紧凑 user 消息进入
**同一 harness 会话**（不是第二个 agent——会话记忆更新因此零成本，事件流、
持久化、互斥全部复用既有设施）。落账由模型经既有 bible_* 工具完成。

失败路径（D5）：模型出错/中止 → sync_end{status:"failed"}；最近一次请求
保留在 _last 里供手动重跑（T5 接 UI）。正文永不回滚——文本是 SSOT，
对账失败只影响账本新鲜度，不影响作品本身。

并发正确性（R3-③）：空闲判断到 prompt() 提交之间不得有 await 点——
asyncio 单线程下二者原子，作者插话只可能发生在检查之前（对账让位）或
检查之后（用户收到 busy 三要素指引）。
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from inspect import isawaitable
from typing import TYPE_CHECKING, Literal

from hastings.manuscript.diff import DiffHunk
from hastings.manuscript.write_guard import RunKindGuard
from hastings.prompts import build_reconcile_message
from hastings.server.session import AgentBusyError
from tau_agent.events import AgentEvent, ToolExecutionEndEvent, TurnEndEvent

if TYPE_CHECKING:
    from hastings.bible.store import StoryWorkspace
    from hastings.server.session import WritingSession

NoticeKind = Literal["sync_queued", "sync_start", "sync_end"]
SyncStatus = Literal["done", "failed"]

# 计 D4 末条：对账期间模型不得替作者改稿——write_chapter 经闭包读本标记。
RUN_KIND_RECONCILE: Literal["reconcile"] = "reconcile"


@dataclass(frozen=True, slots=True)
class ReconcileRequest:
    """一次待对账的章节修订：hunks 为相对上一版本的段落级差异。"""

    chapter_id: str
    hunks: tuple[DiffHunk, ...]


@dataclass(frozen=True, slots=True)
class ReconcileNotice:
    """对账进度通知（ws 层翻成 notice 帧，前端凭它驱动同步卡片）。"""

    kind: NoticeKind
    chapter: str
    hunks: int = 0
    updates: int = 0
    status: SyncStatus | None = None


NoticeListener = Callable[[ReconcileNotice], Awaitable[None] | None]


class ReconcileQueue:
    """per-session 对账队列：debounce 合并、等空闲出队、用户优先。"""

    def __init__(
        self,
        session: WritingSession,
        workspace: StoryWorkspace,
        run_kind: RunKindGuard,
        *,
        debounce_seconds: float = 3.0,
    ) -> None:
        self._session = session
        self._workspace = workspace
        self._run_kind = run_kind
        self._debounce_seconds = debounce_seconds
        self._pending: dict[str, ReconcileRequest] = {}
        self._timers: dict[str, asyncio.Task[None]] = {}
        self._ready: deque[ReconcileRequest] = deque()
        self._last: dict[str, ReconcileRequest] = {}
        self._pump_task: asyncio.Task[None] | None = None
        self._listeners: list[NoticeListener] = []
        # 运行期计数：bible_* 工具成功调用数（sync_end.updates 的来源）
        self._in_run = False
        self._failed_in_run = False
        self._updates_in_run = 0
        self._unsubscribe = session.subscribe(self._on_event)

    # --- 对外表面 ---

    def subscribe(self, listener: NoticeListener) -> Callable[[], None]:
        """订阅对账通知；返回退订函数（ws 层每连接一个 listener）。"""
        self._listeners.append(listener)

        def unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._listeners.remove(listener)

        return unsubscribe

    async def enqueue(self, chapter_id: str, hunks: Sequence[DiffHunk]) -> None:
        """保存成功后入队（rest.py PUT 调用）：同章 debounce 合并，入队即发 queued。"""
        if not hunks:
            return  # 空 diff 是合法 no-op：无可对账，不通知不打扰
        request = ReconcileRequest(chapter_id=chapter_id, hunks=tuple(hunks))
        self._last[chapter_id] = request
        self._pending[chapter_id] = request  # 同章合并：后存覆盖，只对账最新差异
        old_timer = self._timers.get(chapter_id)
        if old_timer is not None:
            old_timer.cancel()
        self._timers[chapter_id] = asyncio.create_task(self._debounce_fire(chapter_id))
        await self._emit(
            ReconcileNotice(kind="sync_queued", chapter=chapter_id, hunks=len(request.hunks))
        )

    async def resync(self, chapter_id: str) -> bool:
        """手动重跑（对账失败重试 / 「同步上下文」按钮）：复用最近一次保存的差异。"""
        request = self._last.get(chapter_id)
        if request is None:
            return False
        self._ready.append(request)
        self._kick()
        return True

    def close(self) -> None:
        """会话废弃时解绑事件订阅（registry 生命周期钩子，当前测试直接调）。"""
        self._unsubscribe()

    # --- 内部：事件面 ---

    async def _on_event(self, event: AgentEvent) -> None:
        if not self._in_run:
            if event.type == "agent_end":
                self._kick()  # 空闲信号：起泵（此刻 is_running 仍 True，泵内再查）
            return
        if isinstance(event, TurnEndEvent) and getattr(event.message, "stop_reason", None) in {
            "error",
            "aborted",
        }:
            self._failed_in_run = True
        if (
            isinstance(event, ToolExecutionEndEvent)
            and event.tool_name.startswith("bible_")
            and not event.is_error
        ):
            self._updates_in_run += 1

    # --- 内部：泵 ---

    def _kick(self) -> None:
        if self._pump_task is None or self._pump_task.done():
            self._pump_task = asyncio.create_task(self._pump())
            self._pump_task.add_done_callback(self._log_pump_crash)

    async def _pump(self) -> None:
        while self._ready:
            if self._session.is_running:
                return  # 用户对话优先：让位，等下一次 agent_end 再起泵
            await self._run_one(self._ready.popleft())

    async def _debounce_fire(self, chapter_id: str) -> None:
        await asyncio.sleep(self._debounce_seconds)
        self._timers.pop(chapter_id, None)
        request = self._pending.pop(chapter_id, None)
        if request is not None:
            self._ready.append(request)
            self._kick()

    async def _run_one(self, request: ReconcileRequest) -> None:
        message = build_reconcile_message(self._workspace, request.chapter_id, request.hunks)
        # 空闲检查到 prompt() 提交之间无 await——原子，作者插话只能发生在两侧
        try:
            stream = self._session.prompt(message)
        except AgentBusyError:
            self._ready.appendleft(request)  # 让位成功：插回队首，等下一轮空闲
            return
        self._run_kind.begin(RUN_KIND_RECONCILE)
        self._in_run = True
        self._failed_in_run = False
        self._updates_in_run = 0
        await self._emit(
            ReconcileNotice(kind="sync_start", chapter=request.chapter_id, hunks=len(request.hunks))
        )
        status: SyncStatus = "done"
        try:
            await self._drain(stream)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — 对账失败不致命：正文与账本无损
            print(f"[reconcile] 第{request.chapter_id}章对账运行异常：{exc}", file=sys.stderr)
            status = "failed"
        finally:
            self._in_run = False
            self._run_kind.end()
        if self._failed_in_run:
            status = "failed"
        await self._emit(
            ReconcileNotice(
                kind="sync_end",
                chapter=request.chapter_id,
                hunks=len(request.hunks),
                updates=self._updates_in_run,
                status=status,
            )
        )

    async def _drain(self, stream: AsyncIterator[AgentEvent]) -> None:
        async for _event in stream:
            pass  # 事件经 harness listener 广播（ws 前端/持久化各自消费）

    async def _emit(self, notice: ReconcileNotice) -> None:
        for listener in list(self._listeners):
            result = listener(notice)
            if isawaitable(result):
                await result

    @staticmethod
    def _log_pump_crash(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            # 泵是 fire-and-forget 任务：异常必须显式暴露，不静默吞
            print(f"[reconcile] 对账泵异常退出：{exc}", file=sys.stderr)


__all__ = [
    "RUN_KIND_RECONCILE",
    "NoticeKind",
    "NoticeListener",
    "ReconcileNotice",
    "ReconcileQueue",
    "ReconcileRequest",
    "SyncStatus",
]
