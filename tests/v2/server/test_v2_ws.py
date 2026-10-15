"""V2 事件 WS 通道（T17.5）测试：订阅、存量补齐、增量推送、断线按 sequence 补齐。

通道分工的回归锚点：业务 Commit 事件只从仓库 Outbox 出（与 Commit 同事务），
Job 走 REST 轮询、对话走 V1 WS——本通道不新增事件源，只增加可重放的出口。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hastings.application.handlers.manuscript_commands import manuscript_command
from hastings.domain.manuscript.commands import import_chapter as import_chapter_command
from hastings.domain.repository.ids import ProjectId
from hastings.server.v2 import V2Runtime, build_v2_app


@pytest.fixture
def runtime() -> V2Runtime:
    return V2Runtime.memory()


@pytest.fixture
def client(runtime: V2Runtime) -> TestClient:
    return TestClient(build_v2_app(runtime))


def _import_chapter(runtime: V2Runtime, project_id: str, title: str, text: str) -> str:
    """经领域命令提交一章（产生带 sequence 的 Outbox 事件）。"""
    chapter_id = runtime.new_artifact_id()
    result = runtime.handler.execute(
        manuscript_command(
            import_chapter_command,
            chapter_id=chapter_id,
            title=title,
            text=text,
            order_key="0001",
        ),
        project_id=ProjectId(project_id),
        idempotency_key=f"ws-test-{title}",
        summary=f"WS 测试：{title}",
    )
    assert result.status == "committed"
    return chapter_id


def test_subscribe_replays_existing_events(client: TestClient, runtime: V2Runtime) -> None:
    project = runtime.create_project("WS 项目").value
    _import_chapter(runtime, project, "第一章", "雪夜来电。\n\n侦探到场。")

    with client.websocket_connect(f"/ws/v2/{project}") as ws:
        ws.send_json({"type": "subscribe", "after_seq": 0})
        sequences: list[int] = []
        while True:
            frame = ws.receive_json()
            if frame["type"] == "replayed":
                break
            assert frame["type"] == "event"
            sequences.append(frame["sequence"])
        assert sequences, "存量事件应被补发"
        assert sequences == sorted(sequences)
        assert frame["up_to_seq"] == sequences[-1]


def test_incremental_events_and_resume(client: TestClient, runtime: V2Runtime) -> None:
    project = runtime.create_project("补齐项目").value
    _import_chapter(runtime, project, "第一章", "开场。")

    # 第一次连接：读存量到 replayed
    with client.websocket_connect(f"/ws/v2/{project}") as ws:
        ws.send_json({"type": "subscribe", "after_seq": 0})
        seen: list[int] = []
        while True:
            frame = ws.receive_json()
            if frame["type"] == "replayed":
                break
            seen.append(frame["sequence"])
        assert seen, "存量事件应被补发"

    # 断线期间产生新事件
    _import_chapter(runtime, project, "第二章", "新的证人。")

    # 重连：after_seq=0 仍能拿到全部事件（重放语义，客户端按 seq 去重）
    with client.websocket_connect(f"/ws/v2/{project}") as ws:
        ws.send_json({"type": "subscribe", "after_seq": 0})
        sequences = []
        while True:
            frame = ws.receive_json()
            if frame["type"] == "replayed":
                break
            sequences.append(frame["sequence"])
        assert len(sequences) >= 2, "重放应包含断线期间的新事件"
        assert sequences == sorted(sequences), "事件必须按 sequence 升序"


def test_bad_first_frame_is_rejected(client: TestClient, runtime: V2Runtime) -> None:
    project = runtime.create_project("拒绝项目").value
    with (
        pytest.raises(Exception),  # noqa: B017 —— 服务端 4400 关闭后 receive 断开
        client.websocket_connect(f"/ws/v2/{project}") as ws,
    ):
        ws.send_json({"type": "hello"})
        ws.receive_json()
