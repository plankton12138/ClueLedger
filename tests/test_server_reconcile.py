"""对账同步管线测试（P5 T4，计划 D5）：队列行为单测 + CP2 端到端。

单测用可控桩会话钉住队列契约：debounce 合并、用户优先（忙时让位、
agent_end 再起泵）、busy 让位重排队、失败状态、手动重跑、run_kind 生命周期。
CP2（FakeProvider 全链路）：手改正文 → PUT → 断言对账轮进主会话消息、
bible_upsert 落账生效、notice 序列 queued→start→end 完整且帧形状精确。
"""

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from inspect import isawaitable
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fake_scripts import assistant_stream, closing
from hastings.bible.models import TimelineEntry
from hastings.bible.store import StoryWorkspace, load_ledger, save_ledger
from hastings.manuscript.diff import DiffHunk, HunkKind
from hastings.manuscript.write_guard import EditMarkRegistry, RunKindGuard
from hastings.server.app import create_app
from hastings.server.hastings_session import HastingsSession
from hastings.server.reconcile import ReconcileNotice, ReconcileQueue
from hastings.server.registry import WorkspaceRegistry
from hastings.server.session import AgentBusyError, WritingSession
from hastings.tools.errors import ToolInputError
from tau_agent import AssistantMessage, ToolCall
from tau_agent.events import AgentEndEvent, AgentEvent, AgentStartEvent, TurnEndEvent
from tau_agent.harness import EventListener
from tau_ai import FakeProvider

SLUG = "orient-express"


def _hunk(kind: HunkKind, old: str, new: str, old_index: int | None, new_index: int | None):
    return DiffHunk(kind=kind, old_index=old_index, new_index=new_index, old_text=old, new_text=new)


class FakeLoopSession:
    """可控的 WritingSession 替身：prompt 记录、运行态手动翻转、事件手动发射。

    复刻 harness 时序契约：prompt() 同步置忙（返回的生成器耗尽时才置闲），
    agent_end 在置闲前投递——队列「agent_end 时仍忙、起泵后再查」的假设
    依赖这条契约（test_server_session.py 已对真 harness 钉过）。
    """

    slug = "demo"
    model = "fake"

    def __init__(self, run_kind: RunKindGuard | None = None) -> None:
        self._running = False
        self._listeners: list[EventListener] = []
        self.prompts: list[str] = []
        self.edit_marks = EditMarkRegistry()
        self.run_kind = run_kind
        self.kinds_at_prompt: list[str | None] = []

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def tool_names(self) -> tuple[str, ...]:
        return ()

    @property
    def messages(self) -> tuple[Any, ...]:
        return ()

    def prompt(self, content: str) -> AsyncIterator[AgentEvent]:
        if self._running:
            raise AgentBusyError(
                "prompt 被拒绝：busy。为什么错：单流。怎样算对：steer / follow_up。"
            )
        self._running = True
        self.prompts.append(content)
        return self._stream()

    def _events(self) -> list[AgentEvent]:
        return [AgentStartEvent(), AgentEndEvent(messages=[])]

    async def _stream(self) -> AsyncIterator[AgentEvent]:
        # 对齐真 harness：事件在生成器内逐个广播给 listener（drain 期可见），
        # agent_end 投递时仍置忙；生成器耗尽才置闲（对齐 harness finally 时序）。
        if self.run_kind is not None:
            self.kinds_at_prompt.append(self.run_kind.kind)
        for event in self._events():
            await self._broadcast(event)
            yield event
        self._running = False

    async def _broadcast(self, event: AgentEvent) -> None:
        for listener in list(self._listeners):
            result = listener(event)
            if isawaitable(result):
                await result

    async def emit(self, event: AgentEvent) -> None:
        for listener in list(self._listeners):
            result = listener(event)
            if isawaitable(result):
                await result

    def subscribe(self, listener: EventListener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def steer(self, content: str) -> None:
        del content

    def follow_up(self, content: str) -> None:
        del content

    def cancel(self) -> None:
        self._running = False

    def subscribe_notice(self, listener: Any) -> Callable[[], None]:
        del listener
        return lambda: None

    async def request_reconcile(self, chapter_id: str, hunks: Sequence[DiffHunk]) -> None:
        del chapter_id, hunks


def _make_queue(
    tmp_path: Path,
    session: FakeLoopSession | None = None,
    *,
    debounce: float = 0.01,
) -> tuple[ReconcileQueue, FakeLoopSession, RunKindGuard]:
    run_kind = RunKindGuard()
    session = session if session is not None else FakeLoopSession(run_kind)
    workspace = StoryWorkspace(root=tmp_path, slug="demo")
    queue = ReconcileQueue(
        session=session,  # type: ignore[arg-type]
        workspace=workspace,
        run_kind=run_kind,
        debounce_seconds=debounce,
    )
    return queue, session, run_kind


class NoticeRecorder:
    def __init__(self) -> None:
        self.notices: list[ReconcileNotice] = []

    def __call__(self, notice: ReconcileNotice) -> None:
        self.notices.append(notice)


# --- 队列单测：debounce / 用户优先 / 让位 / 失败 / 重跑 ---


@pytest.mark.anyio
async def test_enqueue_debounce_merges_same_chapter(tmp_path: Path) -> None:
    """同章 3s 内连续保存合并为一次对账，只跑最新差异。"""
    queue, session, _ = _make_queue(tmp_path)
    recorder = NoticeRecorder()
    queue.subscribe(recorder)

    await queue.enqueue("ch03", [_hunk("modified", "旧1", "新1", 1, 1)])
    await queue.enqueue("ch03", [_hunk("modified", "旧2", "新2", 2, 2)])
    await asyncio.sleep(0.06)

    assert len(session.prompts) == 1
    assert "[修改] 段2：新2" in session.prompts[0]  # 对账的是最新差异
    assert [n.kind for n in recorder.notices] == [
        "sync_queued",
        "sync_queued",
        "sync_start",
        "sync_end",
    ]
    assert recorder.notices[-1].status == "done"


@pytest.mark.anyio
async def test_busy_session_defers_until_agent_end(tmp_path: Path) -> None:
    """用户对话优先：忙时不出队；agent_end 后再起泵运行。"""
    queue, session, _ = _make_queue(tmp_path)
    session._running = True  # 模拟用户写作流进行中
    await queue.enqueue("ch03", [_hunk("modified", "旧", "新", 1, 1)])
    await asyncio.sleep(0.06)

    assert session.prompts == []  # 忙时让位

    session._running = False
    await session.emit(AgentEndEvent(messages=[]))  # 空闲信号
    await asyncio.sleep(0.06)

    assert len(session.prompts) == 1
    assert "[修改] 段1：新" in session.prompts[0]


@pytest.mark.anyio
async def test_prompt_busy_error_requeues_front(tmp_path: Path) -> None:
    """空闲检查到 prompt 之间被抢跑：AgentBusyError → 插回队首等下一轮。"""

    class ClaimedSession(FakeLoopSession):
        def __init__(self, run_kind: RunKindGuard | None = None) -> None:
            super().__init__(run_kind)
            self.claimed = True

        def prompt(self, content: str) -> AsyncIterator[AgentEvent]:
            if self.claimed:
                self.claimed = False
                self._running = True  # 抢跑者置忙（如真实 harness）
                raise AgentBusyError("prompt 被拒绝：busy。为什么错：单流。怎样算对：排队。")
            return super().prompt(content)

    queue, session, _ = _make_queue(tmp_path, session=ClaimedSession())
    await queue.enqueue("ch03", [_hunk("modified", "旧", "新", 1, 1)])
    await asyncio.sleep(0.06)

    assert session.prompts == []  # 首次被抢跑，未运行

    session._running = False
    await session.emit(AgentEndEvent(messages=[]))
    await asyncio.sleep(0.06)

    assert len(session.prompts) == 1  # 插回队首后成功运行


@pytest.mark.anyio
async def test_aborted_run_reports_failed_status(tmp_path: Path) -> None:
    """模型中止 → sync_end{status:"failed"}；run_kind 复位、不 crash。"""

    class AbortedSession(FakeLoopSession):
        def _events(self) -> list[AgentEvent]:
            aborted = AssistantMessage(model="fake", stop_reason="aborted")
            return [
                AgentStartEvent(),
                TurnEndEvent(message=aborted, tool_results=[]),
                AgentEndEvent(messages=[]),
            ]

    session = AbortedSession()
    queue, _, run_kind = _make_queue(tmp_path, session=session)
    recorder = NoticeRecorder()
    queue.subscribe(recorder)

    await queue.enqueue("ch03", [_hunk("modified", "旧", "新", 1, 1)])
    await asyncio.sleep(0.06)

    end = recorder.notices[-1]
    assert end.kind == "sync_end"
    assert end.status == "failed"
    assert run_kind.kind is None  # 守卫复位，写作不受污染


@pytest.mark.anyio
async def test_run_kind_is_reconcile_during_run(tmp_path: Path) -> None:
    """run_kind 守卫生命周期：对账轮内置位（工具层读到 reconcile），结束复位。"""
    queue, session, run_kind = _make_queue(tmp_path)

    await queue.enqueue("ch03", [_hunk("modified", "旧", "新", 1, 1)])
    await asyncio.sleep(0.06)

    assert session.kinds_at_prompt == ["reconcile"]  # drain 期间工具读到对账类别
    assert run_kind.kind is None  # 结束复位，写作不受污染


@pytest.mark.anyio
async def test_resync_reruns_last_request(tmp_path: Path) -> None:
    """手动重跑：复用最近一次保存的差异；无历史时返回 False。"""
    queue, session, _ = _make_queue(tmp_path)

    assert await queue.resync("ch03") is False  # 无历史

    await queue.enqueue("ch03", [_hunk("modified", "旧", "新", 1, 1)])
    await asyncio.sleep(0.06)
    assert len(session.prompts) == 1

    assert await queue.resync("ch03") is True
    await asyncio.sleep(0.06)
    assert len(session.prompts) == 2
    assert session.prompts[1] == session.prompts[0]


@pytest.mark.anyio
async def test_updates_count_counts_bible_tool_calls(tmp_path: Path) -> None:
    """sync_end.updates = 对账轮内成功的 bible_* 工具调用数。"""
    from tau_agent.events import ToolExecutionEndEvent
    from tau_agent.messages import TextContent
    from tau_agent.tools import AgentToolResult

    class ToolRunSession(FakeLoopSession):
        def _events(self) -> list[AgentEvent]:
            events: list[AgentEvent] = [AgentStartEvent()]
            for call_id in ("c1", "c2", "c3"):
                name = "bible_upsert" if call_id != "c3" else "read_chapter"
                events.append(
                    ToolExecutionEndEvent(
                        tool_call_id=call_id,
                        tool_name=name,
                        result=AgentToolResult(content=[TextContent(text="ok")]),
                        is_error=call_id == "c2",
                    )
                )
            events.append(AgentEndEvent(messages=[]))
            return events

    queue, _, _ = _make_queue(tmp_path, session=ToolRunSession(run_kind=None))
    recorder = NoticeRecorder()
    queue.subscribe(recorder)

    await queue.enqueue("ch03", [_hunk("modified", "旧", "新", 1, 1)])
    await asyncio.sleep(0.06)

    assert recorder.notices[-1].updates == 1  # c1 成功计入；c2 失败不计，c3 非 bible_* 不计


# --- CP2：FakeProvider 端到端（手改 → PUT → 对账轮进主会话 + 落账 + notice） ---


def _timeline_upsert(call_id: str) -> ToolCall:
    return ToolCall(
        id=call_id,
        name="bible_upsert",
        arguments={
            "entity_type": "timeline",
            "data": {"name": "列车到站", "day": 1, "t_start": "10:07"},
        },
    )


def _seed_story(workspace: StoryWorkspace) -> None:
    workspace.ensure_dirs()
    (workspace.chapters_dir / "ch03.md").write_text(
        "# 第3章\n\n列车到站是九点四十七分。\n", encoding="utf-8"
    )
    save_ledger(
        workspace.timeline_path,
        [TimelineEntry(name="列车到站", day=1, t_start="09:47")],
    )


def _reconcile_app(root: Path, session: HastingsSession) -> FastAPI:
    async def factory(workspace: StoryWorkspace) -> WritingSession:
        del workspace
        return session

    return create_app(WorkspaceRegistry(root=root, session_factory=factory))


def _read_until_sync_end(ws: Any) -> list[Any]:
    frames: list[Any] = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame.get("type") == "notice" and frame.get("kind") == "sync_end":
            return frames


def test_cp2_manual_edit_then_put_reconciles_ledger(tmp_path: Path) -> None:
    """CP2 全链路：作者保存 → 自动对账 → 账本与主会话记忆同步更新。

    必须 `with client:`：进入后 TestClient 持有单一长驻 portal，HTTP 与
    WS 复用同一事件循环（对齐 uvicorn 生产拓扑）。否则每个 HTTP 请求
    独立起循环、请求结束即销毁，对账泵的 debounce 定时任务随循环被
    取消，sync_end 永不到达——这正是 CP2 首跑挂起的根因。
    """
    workspace = StoryWorkspace(root=tmp_path, slug=SLUG)
    _seed_story(workspace)
    session = HastingsSession.from_workspace(
        workspace,
        FakeProvider(
            [
                assistant_stream(
                    call=_timeline_upsert("call-r1"),
                    text="落账：列车到站改为十点零七分。",
                    reason="toolUse",
                ),
                closing("已更新时间线 1 处，无需作者注意。"),
            ]
        ),
        "fake",
        reconcile_debounce=0.01,
    )
    client = TestClient(_reconcile_app(tmp_path, session))

    with client, client.websocket_connect(f"/ws/{SLUG}") as ws:
        response = client.put(
            f"/api/{SLUG}/chapters/ch03",
            json={"content": "# 第3章\n\n列车到站改到十点零七分。\n", "baseRevision": 1},
        )
        assert response.status_code == 200

        frames = _read_until_sync_end(ws)

    # ① notice 序列完整且帧形状精确（queued→start→end）
    notices = [f for f in frames if f.get("type") == "notice"]
    assert notices == [
        {"type": "notice", "kind": "sync_queued", "chapter": "ch03", "hunks": 1, "updates": 0},
        {"type": "notice", "kind": "sync_start", "chapter": "ch03", "hunks": 1, "updates": 0},
        {
            "type": "notice",
            "kind": "sync_end",
            "chapter": "ch03",
            "hunks": 1,
            "updates": 1,
            "status": "done",
        },
    ]

    # ② 对账轮进主会话（同一 harness：会话记忆零成本更新）
    user_messages = [m for m in session.messages if m.role == "user"]
    assert len(user_messages) == 1
    assert str(user_messages[0].content).startswith("【手动修订同步】第3章已被作者直接修改")
    assert "[修改] 段1：列车到站改到十点零七分" in str(user_messages[0].content)
    assert "时间线：列车到站" in str(user_messages[0].content)  # 实体名匹配命中

    # ③ bible_upsert 落账生效（timeline.yaml 覆盖写）
    entries = load_ledger(workspace.timeline_path, TimelineEntry)
    assert len(entries) == 1
    assert entries[0].t_start == "10:07"

    # ④ 对账轮的事件帧经 listener 广播到 ws（前端同步卡片的数据源）
    tool_ends = [f for f in frames if f.get("type") == "tool_execution_end"]
    assert len(tool_ends) == 1
    assert tool_ends[0]["toolName"] == "bible_upsert"
    assert tool_ends[0]["isError"] is False


def test_from_workspace_tools_share_run_kind_guard(tmp_path: Path) -> None:
    """装配点验证：from_workspace 闭包的 write_chapter 读同一 run_kind 守卫。"""
    workspace = StoryWorkspace(root=tmp_path, slug="demo")
    session = HastingsSession.from_workspace(workspace, FakeProvider([]), "fake")

    session.run_kind.begin("reconcile")
    write = {tool.name: tool for tool in session.harness.config.tools}["write_chapter"]

    async def call() -> None:
        await write.execute("call-1", {"chapter": 3, "content": "偷改。", "force": True})

    async def runner() -> None:
        with pytest.raises(ToolInputError) as exc_info:
            await call()
        assert "对账运行进行中" in str(exc_info.value)

    asyncio.run(runner())
    session.run_kind.end()
