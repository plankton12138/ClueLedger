"""WebSocket 事件桥测试（P3 T3，P4 T5 slug 化）。

三层断言：
① 帧协议——响应帧/事件帧结构、id 配对、assistantMessageEvent 别名保真
  （FakeProvider 剧本经 TestClient 全链路干跑）；
② 命令语义——busy 拒绝翻译成 error 帧、steer/follow_up 到达会话层、
  get_messages 快照、abort（协议翻译用 StubSession 钉住确定性；
  FakeProvider 剧本瞬发，从测试线程"运行中插命令"必然竞态）；
③ 多连接——第二连接不经任何命令也收到完整事件流（listener fan-out）。

时序契约：harness.prompt() 在调用瞬间同步置位 is_running 并同步抛 busy
（test_server_session.py 已钉住），StubSession 复刻这一契约——WS 层的
busy 翻译依赖它，异步 generator 形式的桩会让异常延迟到 drain 才抛。
"""

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fake_scripts import assistant_stream, character_upsert, closing
from hastings.bible.store import StoryWorkspace
from hastings.prompts import build_system_prompt
from hastings.server.app import create_app
from hastings.server.hastings_session import HastingsSession
from hastings.server.registry import WorkspaceRegistry
from hastings.server.session import AgentBusyError, WritingSession
from hastings.tools.bible_tools import create_bible_tools
from tau_agent import AgentHarness, AgentHarnessConfig
from tau_agent.events import AgentEvent
from tau_ai import FakeProvider
from test_server_app import StubSession

SLUG = "orient-express"
WS_URL = f"/ws/{SLUG}"
STEER_TEXT = "记得他是比利时人"
FOLLOW_UP_TEXT = "再补一条细节"


@pytest.fixture
def workspace(tmp_path: Path) -> StoryWorkspace:
    return StoryWorkspace(root=tmp_path, slug=SLUG)


def _registry_with_session(root: Path, session: WritingSession) -> WorkspaceRegistry:
    """桩工厂注册表：任何 slug 都装配到指定会话实例。"""

    async def factory(ws: StoryWorkspace) -> WritingSession:
        del ws
        return session

    return WorkspaceRegistry(root=root, session_factory=factory)


def _app(workspace: StoryWorkspace, turns: list[list[Any]]) -> FastAPI:
    tools = create_bible_tools(workspace)
    harness = AgentHarness(
        AgentHarnessConfig(
            provider=FakeProvider(turns),
            model="fake",
            system=build_system_prompt(tools),
            tools=tools,
        )
    )
    session = HastingsSession(harness=harness, workspace=workspace)
    return create_app(_registry_with_session(workspace.root, session))


def _standard_app(workspace: StoryWorkspace) -> FastAPI:
    return _app(
        workspace,
        [
            assistant_stream(
                call=character_upsert("call-w1", "波洛", "比利时侦探"),
                text="先建档。",
                reason="toolUse",
            ),
            closing("建档完成。"),
        ],
    )


def _read_until(ws: Any, stop_type: str) -> list[Any]:
    """逐帧读取直到遇到指定类型的事件帧（含该帧）。"""
    frames: list[Any] = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame.get("type") == stop_type:
            return frames


def _prompt_and_wait_done(ws: Any, message: str = "建一个角色") -> list[Any]:
    """发 prompt 命令，跳过响应帧后读到 agent_end，返回期间的事件帧。"""
    ws.send_json({"id": 1, "type": "prompt", "message": message})
    first = ws.receive_json()
    assert first["type"] == "response" and first["success"] is True
    return _read_until(ws, "agent_end")


# --- ① 帧协议：FakeProvider 剧本全链路干跑 ---


def test_ws_prompt_streams_event_frames_with_alias_fidelity(
    workspace: StoryWorkspace,
) -> None:
    with TestClient(_standard_app(workspace)).websocket_connect(WS_URL) as ws:
        ws.send_json({"id": 7, "type": "prompt", "message": "建一个角色"})
        first = ws.receive_json()
        assert first == {
            "type": "response",
            "command": "prompt",
            "success": True,
            "id": 7,
            "data": {"started": True},
        }

        frames = _read_until(ws, "agent_end")

    types = [f["type"] for f in frames]
    assert types[0] == "agent_start"
    assert types[-1] == "agent_end"
    assert types.count("tool_execution_start") == 1

    # 首个 message_start 是 user prompt（协议保真；str 形态 UserContent）
    message_start = next(f for f in frames if f["type"] == "message_start")
    assert message_start["message"]["role"] == "user"
    assert message_start["message"]["content"] == "建一个角色"

    # 流式更新帧带 wire 别名 assistantMessageEvent（前端流式渲染依赖它）
    deltas = [
        f
        for f in frames
        if f["type"] == "message_update" and f["assistantMessageEvent"]["type"] == "text_delta"
    ]
    assert deltas, "应存在 text_delta 流式更新帧"
    assert deltas[0]["assistantMessageEvent"]["delta"] == "先建档。"


# --- ② 命令语义：协议翻译用 StubSession 钉确定性 ---


def test_ws_busy_rejected_with_error_frame_and_connection_survives(
    workspace: StoryWorkspace,
) -> None:
    app = create_app(_registry_with_session(workspace.root, StubSession()))
    with TestClient(app).websocket_connect(WS_URL) as ws:
        ws.send_json({"id": 5, "type": "prompt", "message": "再来一个"})
        error = ws.receive_json()

        # AgentBusyError → error 帧：id 配对 + 三要素指引
        assert error["type"] == "response"
        assert error["command"] == "prompt"
        assert error["success"] is False
        assert error["id"] == 5
        assert "steer" in error["error"] and "follow_up" in error["error"]

        # 连接存活：busy 不是断连，后续命令照常服务
        ws.send_json({"id": 6, "type": "get_state"})
        state = ws.receive_json()
        assert state["success"] is True
        assert state["id"] == 6


class RecordingSession(StubSession):
    """记录 steer/follow_up 调用的桩：验证 WS 命令以正确参数到达会话层。"""

    def __init__(self) -> None:
        super().__init__()
        self.steered: list[str] = []
        self.followed_up: list[str] = []

    def steer(self, content: str) -> None:
        self.steered.append(content)

    def follow_up(self, content: str) -> None:
        self.followed_up.append(content)


def test_ws_steer_and_follow_up_commands_reach_session(
    workspace: StoryWorkspace,
) -> None:
    session = RecordingSession()
    app = create_app(_registry_with_session(workspace.root, session))
    with TestClient(app).websocket_connect(WS_URL) as ws:
        ws.send_json({"id": 2, "type": "steer", "message": STEER_TEXT})
        steer_response = ws.receive_json()
        assert steer_response == {
            "type": "response",
            "command": "steer",
            "success": True,
            "id": 2,
        }

        ws.send_json({"id": 3, "type": "follow_up", "message": FOLLOW_UP_TEXT})
        follow_response = ws.receive_json()
        assert follow_response["success"] is True
        assert follow_response["id"] == 3

    assert session.steered == [STEER_TEXT]
    assert session.followed_up == [FOLLOW_UP_TEXT]


# --- sync 命令（P5 T5）：手动重跑对账 ---


class UnresyncableSession(StubSession):
    """无可重跑修订的桩：resync_reconcile 恒 False（走失败指引分支）。"""

    async def resync_reconcile(self, chapter_id: str) -> bool:
        del chapter_id
        return False


def test_ws_sync_command_triggers_resync_with_chapter(workspace: StoryWorkspace) -> None:
    session = StubSession()
    app = create_app(_registry_with_session(workspace.root, session))
    with TestClient(app).websocket_connect(WS_URL) as ws:
        ws.send_json({"id": 4, "type": "sync", "chapter": "ch03"})
        response = ws.receive_json()

    assert response == {
        "type": "response",
        "command": "sync",
        "success": True,
        "id": 4,
        "data": {"chapter": "ch03"},
    }
    assert session.resync_calls == ["ch03"]


def test_ws_sync_command_reports_missing_chapter(workspace: StoryWorkspace) -> None:
    app = create_app(_registry_with_session(workspace.root, StubSession()))
    with TestClient(app).websocket_connect(WS_URL) as ws:
        ws.send_json({"id": 5, "type": "sync"})
        error = ws.receive_json()

    assert error["type"] == "response"
    assert error["command"] == "sync"
    assert error["success"] is False
    assert error["id"] == 5
    assert "chapter 字段缺失" in error["error"]  # 哪里错
    assert "为什么错" in error["error"]
    assert "怎样算对" in error["error"]


def test_ws_sync_command_reports_no_revision_to_replay(workspace: StoryWorkspace) -> None:
    app = create_app(_registry_with_session(workspace.root, UnresyncableSession()))
    with TestClient(app).websocket_connect(WS_URL) as ws:
        ws.send_json({"id": 6, "type": "sync", "chapter": "ch03"})
        error = ws.receive_json()

    assert error["success"] is False
    assert "没有可重跑的对账修订" in error["error"]  # 哪里错
    assert "先" in error["error"] and "保存一次" in error["error"]  # 怎样算对


def test_ws_get_messages_restores_history_after_reconnect(
    workspace: StoryWorkspace,
) -> None:
    client = TestClient(_standard_app(workspace))
    with client.websocket_connect(WS_URL) as ws:
        _prompt_and_wait_done(ws)

    # 模拟浏览器刷新：新连接拉历史快照
    with client.websocket_connect(WS_URL) as fresh:
        fresh.send_json({"type": "get_messages"})
        snapshot = fresh.receive_json()
    assert snapshot["success"] is True
    messages = snapshot["data"]["messages"]
    assert [m["role"] for m in messages] == [
        "user",
        "assistant",
        "toolResult",
        "assistant",
    ]
    # user 消息的 content 经 wire 序列化是纯字符串（str 形态 UserContent）
    assert messages[0]["content"] == "建一个角色"
    # 收尾 assistant 的文本在 block 列表里
    assert messages[-1]["content"][0]["text"] == "建档完成。"


def test_ws_abort_command_and_cancel_signal(workspace: StoryWorkspace) -> None:
    tools = create_bible_tools(workspace)
    harness = AgentHarness(
        AgentHarnessConfig(
            provider=FakeProvider(
                [
                    assistant_stream(
                        call=character_upsert("call-w1", "波洛", "比利时侦探"),
                        text="先建档。",
                        reason="toolUse",
                    ),
                    closing("建档完成。"),
                ]
            ),
            model="fake",
            system=build_system_prompt(tools),
            tools=tools,
        )
    )
    session = HastingsSession(harness=harness, workspace=workspace)
    app = create_app(_registry_with_session(workspace.root, session))

    # 服务端 listener 在工具启动点确定性触发 cancel（FakeProvider 瞬发，
    # 从测试线程发 abort 存在竞态；abort 命令接线在 run 结束后单独验证）
    def cancel_on_tool_start(event: AgentEvent) -> None:
        if event.type == "tool_execution_start":
            session.cancel()

    unsubscribe = session.subscribe(cancel_on_tool_start)
    with TestClient(app).websocket_connect(WS_URL) as ws:
        frames = _prompt_and_wait_done(ws)
        ends = [f for f in frames if f["type"] == "tool_execution_end"]
        assert len(ends) == 1
        assert ends[0]["isError"] is True
        assert "Operation aborted" in ends[0]["result"]["content"][0]["text"]

        # abort 命令接线：run 结束后仍可调用，成功响应（幂等 no-op）
        ws.send_json({"id": 9, "type": "abort"})
        response = ws.receive_json()
        assert response == {
            "type": "response",
            "command": "abort",
            "success": True,
            "id": 9,
        }
    unsubscribe()


# --- ③ 多连接：listener fan-out ---


def test_ws_second_connection_receives_full_stream_via_listener(
    workspace: StoryWorkspace,
) -> None:
    with (
        TestClient(_standard_app(workspace)) as client,
        client.websocket_connect(WS_URL) as a,
        client.websocket_connect(WS_URL) as b,
    ):
        a.send_json({"id": 1, "type": "prompt", "message": "建一个角色"})
        assert a.receive_json()["success"] is True

        # b 不发任何命令，也收到完整事件流
        frames_b = _read_until(b, "agent_end")
        frames_a = _read_until(a, "agent_end")

    assert frames_b[0]["type"] == "agent_start"
    assert [f["type"] for f in frames_b] == [f["type"] for f in frames_a]


# --- 错误帧：未知命令 / 缺 message / 坏 JSON ---


def test_ws_unknown_command_and_missing_message_and_bad_json(
    workspace: StoryWorkspace,
) -> None:
    with TestClient(_standard_app(workspace)).websocket_connect(WS_URL) as ws:
        ws.send_json({"type": "teleport"})
        error = ws.receive_json()
        assert error["success"] is False
        assert "未知命令" in error["error"]
        assert "get_messages" in error["error"]  # 怎样算对：列出可用命令

        ws.send_json({"id": 3, "type": "prompt"})
        error = ws.receive_json()
        assert "message 字段缺失" in error["error"]
        assert error["id"] == 3

        ws.send_text("这不是 JSON")
        error = ws.receive_json()
        assert "命令帧解析失败" in error["error"]


# --- ④ 多工作区：/ws/{slug} 按 slug 隔离（P4 T5） ---


def _multi_workspace_app(root: Path, closing_text_by_slug: dict[str, str]) -> FastAPI:
    """一个 app 服务多个 slug：每 slug 独立 FakeProvider 会话（互不共享状态）。"""
    sessions: dict[str, HastingsSession] = {}
    for slug, text in closing_text_by_slug.items():
        ws = StoryWorkspace(root=root, slug=slug)
        tools = create_bible_tools(ws)
        harness = AgentHarness(
            AgentHarnessConfig(
                provider=FakeProvider([closing(text)]),
                model="fake",
                system=build_system_prompt(tools),
                tools=tools,
            )
        )
        sessions[slug] = HastingsSession(harness=harness, workspace=ws)

    async def factory(workspace: StoryWorkspace) -> WritingSession:
        return sessions[workspace.slug]

    return create_app(WorkspaceRegistry(root=root, session_factory=factory))


def test_ws_events_do_not_leak_across_workspaces(tmp_path: Path) -> None:
    """双工作区事件隔离：A 的剧情事件不出现在 B 的连接里（计划 D4）。"""
    app = _multi_workspace_app(
        tmp_path,
        {"story-a": "甲故事独有收尾。", "story-b": "乙故事独有收尾。"},
    )
    with (
        TestClient(app) as client,
        client.websocket_connect("/ws/story-a") as a,
        client.websocket_connect("/ws/story-b") as b,
    ):
        a.send_json({"id": 1, "type": "prompt", "message": "写甲"})
        assert a.receive_json()["success"] is True
        frames_a = _read_until(a, "agent_end")

        b.send_json({"id": 1, "type": "prompt", "message": "写乙"})
        assert b.receive_json()["success"] is True
        frames_b = _read_until(b, "agent_end")

    texts_a = "".join(
        f["assistantMessageEvent"]["delta"]
        for f in frames_a
        if f["type"] == "message_update"
        and f.get("assistantMessageEvent", {}).get("type") == "text_delta"
    )
    texts_b = "".join(
        f["assistantMessageEvent"]["delta"]
        for f in frames_b
        if f["type"] == "message_update"
        and f.get("assistantMessageEvent", {}).get("type") == "text_delta"
    )
    assert "甲故事独有收尾" in texts_a
    assert "甲故事独有收尾" not in texts_b
    assert "乙故事独有收尾" in texts_b
    assert "乙故事独有收尾" not in texts_a


def test_ws_invalid_slug_rejects_connection_without_assembling(
    workspace: StoryWorkspace,
) -> None:
    """坏 slug（穿越/保留名）→ 连接被服务端关闭，不装配会话不留目录。"""
    from fastapi import WebSocketDisconnect

    client = TestClient(create_app(_registry_with_session(workspace.root, StubSession())))
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws/CON") as ws:
        ws.receive_json()


# --- 桩契约：busy 必须在 prompt() 调用时同步抛出 ---


def test_stub_session_raises_busy_synchronously() -> None:
    """WS 层 busy 翻译依赖"prompt() 调用即抛"语义；异步生成器会延迟到迭代。

    HastingsSession 靠 harness.prompt() 的同步置位满足此契约；本测试钉住
    StubSession 的复刻，防止未来有人把桩改成 async generator 形式。
    """
    with pytest.raises(AgentBusyError):
        StubSession().prompt("再来一个")
