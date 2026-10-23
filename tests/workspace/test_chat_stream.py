"""流式回复、块终态与中断修复（V05-2A）。

契约：模型的实际增量要落到会话记录里，界面上同一条回复持续更新——不是等整轮结束后
把一段文字一次性贴出来。因此这里检查的是**事件顺序与可读时机**：

- 分段内容在响应结束前就能经接口读到，空闲时也要提交缓冲；
- 思考与文字块按内容位置保序，空块与 redacted 内容不进入日志；
- 结束时的完整消息是校对依据，已发增量不能被再拼一遍；
- 停止、失败、输出上限各有终态，部分内容保留；
- 同名工具按调用 ID 配对，不互相覆盖；
- 重启修复按已保存增量重建未封口的内容块，且只补一次；
- 流式增量不进入下一轮模型上下文。

模型仍用脚本化假 provider 驱动，替换的是「模型说什么」，不替换存储与接口。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.state import ConversationStore, RecentWorkspaces, conversation_root
from tau_agent.messages import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolCall,
    UserMessage,
)
from tau_agent.provider_events import (
    AssistantDoneEvent,
    AssistantErrorEvent,
    AssistantStartEvent,
    TextDeltaEvent,
    TextEndEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
)

# -- 假 provider ------------------------------------------------------------

#: 夹具作品里的章节路径：多个用例都要定位同一份正文。
CHAPTER = "正文/0001 雨夜.md"


class FlowProvider:
    """按脚本吐增量事件；``pace`` 是每条事件之间的人为间隔。

    与 test_chat.py 的 ``ScriptedProvider`` 分开：这里要控制「是否发开始事件」和
    「事件之间是否停顿」，用来验证定时提交与缺开始事件的端点。
    """

    def __init__(
        self,
        turns: list[list[Any]],
        *,
        emit_start: bool = True,
        pace: float = 0.0,
        hang_from: int | None = None,
    ) -> None:
        self.turns = turns
        self.emit_start = emit_start
        self.pace = pace
        self.hang_from = hang_from
        self.calls = 0
        self.requests: list[dict[str, Any]] = []

    def stream_response(
        self,
        *,
        model: str = "",
        system: str = "",
        messages: list[Any] | None = None,
        **_rest: Any,
    ) -> Any:
        index = self.calls
        self.calls += 1
        self.requests.append({"model": model, "system": system, "messages": list(messages or [])})
        signal = _rest.get("signal")

        async def generator() -> Any:
            if self.emit_start:
                yield AssistantStartEvent(partial=AssistantMessage())
            if index < len(self.turns):
                for event in self.turns[index]:
                    if self.pace:
                        await asyncio.sleep(self.pace)
                    yield event
            if self.hang_from is not None and index >= self.hang_from:
                while signal is None or not signal.is_cancelled():
                    await asyncio.sleep(0.02)

        return generator()


def _partial() -> AssistantMessage:
    return AssistantMessage()


def _text_delta(text: str, index: int = 0) -> TextDeltaEvent:
    return TextDeltaEvent(content_index=index, delta=text, partial=_partial())


def _thinking_delta(text: str, index: int = 0) -> ThinkingDeltaEvent:
    return ThinkingDeltaEvent(content_index=index, delta=text, partial=_partial())


def _text_end(text: str, index: int = 0) -> TextEndEvent:
    return TextEndEvent(content_index=index, content=text, partial=_partial())


def _thinking_end(text: str, index: int = 0) -> ThinkingEndEvent:
    return ThinkingEndEvent(content_index=index, content=text, partial=_partial())


def _done(
    content: list[Any], *, reason: str = "stop", stop_reason: str = "stop"
) -> AssistantDoneEvent:
    return AssistantDoneEvent(
        reason=reason,  # type: ignore[arg-type]
        message=AssistantMessage(content=content, stop_reason=stop_reason),  # type: ignore[arg-type]
    )


def _text_turn(text: str) -> list[Any]:
    return [_done([TextContent(text=text)])]


def _snapshot(provider: Any, model: str = "fake-model") -> ModelSnapshot:
    return ModelSnapshot(
        connection_id="test", display_name="测试连接", model=model, provider=provider
    )


@pytest.fixture()
def client_factory(tmp_path: Path) -> Iterator[Callable[[Any], TestClient]]:
    opened: list[TestClient] = []

    def build(provider: Any) -> TestClient:
        book = RecentWorkspaces(tmp_path / "state")
        client = TestClient(
            create_workspace_app(workspaces=book, provider_factory=lambda: _snapshot(provider))
        )
        client.__enter__()
        opened.append(client)
        return client

    yield build
    for client in opened:
        client.__exit__(None, None, None)


def _workspace(client: TestClient, tmp_path: Path) -> str:
    root = tmp_path / "雪山旅馆"
    response = client.post("/api/file-workspaces", json={"path": str(root), "create": True})
    assert response.status_code == 200, response.text
    return str(response.json()["id"])


def _accept(client: TestClient, workspace_id: str, text: str = "写第一章") -> dict[str, Any]:
    response = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={"text": text, "request_key": f"k_{time.monotonic_ns()}"},
    )
    assert response.status_code == 202, response.text
    return dict(response.json())


def _events(client: TestClient, workspace_id: str, conversation_id: str) -> list[dict[str, Any]]:
    response = client.get(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/messages"
    )
    assert response.status_code == 200, response.text
    return list(response.json()["events"])


def _wait_until(
    client: TestClient,
    workspace_id: str,
    conversation_id: str,
    predicate: Callable[[list[dict[str, Any]]], bool],
    *,
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events = _events(client, workspace_id, conversation_id)
        if predicate(events):
            return events
        time.sleep(0.02)
    raise AssertionError(f"等待超时，当前事件：{_events(client, workspace_id, conversation_id)}")


def _finished(events: list[dict[str, Any]]) -> bool:
    return any(event["type"] == "run_finished" for event in events)


def _of_type(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [event for event in events if event.get("type") == kind]


def _has_delta(events: list[dict[str, Any]]) -> bool:
    return bool(_of_type(events, "assistant_block_delta"))


def _phase(name: str) -> dict[str, Any]:
    return {"type": "run_phase", "turn_id": "t1", "phase": name}


# -- 分段可见 ---------------------------------------------------------------


def test_deltas_are_readable_before_the_response_ends(
    tmp_path: Path, client_factory: Callable[[Any], TestClient]
) -> None:
    """首段文字要在模型还没结束时就能读到：否则「流式」只是名称。"""
    provider = FlowProvider(
        [
            [
                _text_delta("旅馆的灯"),
                _text_end("旅馆的灯熄灭了。"),
                _done([TextContent(text="旅馆的灯熄灭了。")]),
            ]
        ],
        pace=0.4,
    )
    client = client_factory(provider)
    workspace_id = _workspace(client, tmp_path)
    conversation_id = _accept(client, workspace_id)["conversation_id"]

    seen = _wait_until(client, workspace_id, conversation_id, _has_delta)
    assert not _finished(seen)
    delta = _of_type(seen, "assistant_block_delta")[0]
    assert delta["text"] == "旅馆的灯"
    assert delta["part"] == 0
    assert delta["kind"] == "text"
    assert delta["message_id"].startswith("m_")

    events = _wait_until(client, workspace_id, conversation_id, _finished)
    request_start = _of_type(events, "model_request_started")[0]
    request_end = _of_type(events, "model_request_finished")[0]
    assert request_start["operation_id"] == request_end["operation_id"]
    assert request_start["seq"] < request_end["seq"]
    # 活动诊断事件不改变回复流的既有顺序与内容。
    events = [
        event
        for event in events
        if event["type"]
        not in ("model_request_started", "model_request_finished", "version_finished")
    ]
    assert [event["type"] for event in events] == [
        "turn_started",
        # 装配记录先落盘：配置与分区在模型开始输出之前就已经确定。
        "state_injected",
        "run_phase",
        "assistant_started",
        "assistant_block_started",
        "assistant_block_delta",
        "assistant_block_finished",
        "assistant_message",
        "assistant_finished",
        "run_phase",
        "run_finished",
    ]
    assert [event["phase"] for event in _of_type(events, "run_phase")] == ["model", "record"]
    finished = events[6]
    assert finished["text"] == "旅馆的灯熄灭了。"  # 完整结束值覆盖增量
    assert finished["last_part"] == 0
    assert finished["completion"] == "completed"
    assert finished["duration_ms"] >= 0
    assert events[7]["text"] == "旅馆的灯熄灭了。"
    assert events[7]["completion"] == "completed"
    assert events[8]["stop_reason"] == "stop"


def test_idle_timer_flushes_without_waiting_for_the_next_token(
    tmp_path: Path, client_factory: Callable[[Any], TestClient]
) -> None:
    """没有后续 token 时也要提交：定时器不能依赖下一条事件到达。"""
    provider = FlowProvider(
        [
            [
                _text_delta("第一句。"),
                _text_delta("第二句。"),
                _text_end("第一句。第二句。"),
                _done([TextContent(text="第一句。第二句。")]),
            ]
        ],
        pace=0.5,
    )
    client = client_factory(provider)
    workspace_id = _workspace(client, tmp_path)
    conversation_id = _accept(client, workspace_id)["conversation_id"]

    seen = _wait_until(
        client,
        workspace_id,
        conversation_id,
        lambda events: len(_of_type(events, "assistant_block_delta")) == 1,
    )
    # 只在收到一段之后就停顿了 0.5 秒；这一段已经落盘，不必等第二段。
    assert _of_type(seen, "assistant_block_delta")[0]["text"] == "第一句。"


def test_thinking_and_text_interleave_by_content_index(
    tmp_path: Path, client_factory: Callable[[Any], TestClient]
) -> None:
    """块顺序来自内容位置，不按类型重排；思考默认只保存文本。"""
    provider = FlowProvider(
        [
            [
                _thinking_delta("先确认时间线。", index=0),
                _thinking_end("先确认时间线。", index=0),
                _text_delta("时间线是雪夜。", index=1),
                _text_end("时间线是雪夜。", index=1),
                _done(
                    [
                        ThinkingContent(thinking="先确认时间线。"),
                        TextContent(text="时间线是雪夜。"),
                    ]
                ),
            ]
        ]
    )
    client = client_factory(provider)
    workspace_id = _workspace(client, tmp_path)
    conversation_id = _accept(client, workspace_id)["conversation_id"]

    events = _wait_until(client, workspace_id, conversation_id, _finished)
    started = _of_type(events, "assistant_block_started")
    assert [block["kind"] for block in started] == ["thinking", "text"]
    assert [block["order"] for block in started] == [0, 1]
    assert started[0]["block_id"].endswith(":thinking:0")
    assert started[1]["block_id"].endswith(":text:1")
    thinking = _of_type(events, "assistant_block_finished")[0]
    assert thinking["text"] == "先确认时间线。"
    assert thinking["duration_ms"] >= 0
    # 正式公开文本只含文字块，不夹带思考。
    assert _of_type(events, "assistant_message")[0]["text"] == "时间线是雪夜。"


def test_response_without_deltas_is_shown_once(
    tmp_path: Path, client_factory: Callable[[Any], TestClient]
) -> None:
    """不提供增量的端点：收到完整回复后正常展示，不伪造分段过程。"""
    provider = FlowProvider([_text_turn("整段回复。")], emit_start=False)
    client = client_factory(provider)
    workspace_id = _workspace(client, tmp_path)
    conversation_id = _accept(client, workspace_id)["conversation_id"]

    events = _wait_until(client, workspace_id, conversation_id, _finished)
    assert _of_type(events, "assistant_block_delta") == []
    blocks = _of_type(events, "assistant_block_started")
    assert len(blocks) == 1
    assert _of_type(events, "assistant_block_finished")[0]["last_part"] == -1
    assert _of_type(events, "assistant_message")[0]["text"] == "整段回复。"


def test_redacted_thinking_is_not_saved(
    tmp_path: Path, client_factory: Callable[[Any], TestClient]
) -> None:
    """只有签名或 redacted 的思考不进入日志，也不生成空的可展开面板。"""
    provider = FlowProvider(
        [
            [
                _done(
                    [
                        ThinkingContent(thinking="", thinking_signature="sig-1", redacted=True),
                        TextContent(text="直接回答。", text_signature="sig-2"),
                    ]
                )
            ]
        ]
    )
    client = client_factory(provider)
    workspace_id = _workspace(client, tmp_path)
    conversation_id = _accept(client, workspace_id)["conversation_id"]

    events = _wait_until(client, workspace_id, conversation_id, _finished)
    assert [block["kind"] for block in _of_type(events, "assistant_block_started")] == ["text"]
    raw = str(events)
    assert "sig-1" not in raw and "sig-2" not in raw


def test_final_message_overrides_accumulated_deltas(
    tmp_path: Path, client_factory: Callable[[Any], TestClient]
) -> None:
    """完整结束消息是校对依据：增量与最终值不一致时以最终值为准，且不重复拼接。"""
    provider = FlowProvider(
        [
            [
                _text_delta("草稿："),
                _text_delta("半句"),
                _text_end("定稿全文。"),
                _done([TextContent(text="定稿全文。")]),
            ]
        ]
    )
    client = client_factory(provider)
    workspace_id = _workspace(client, tmp_path)
    conversation_id = _accept(client, workspace_id)["conversation_id"]

    events = _wait_until(client, workspace_id, conversation_id, _finished)
    assert _of_type(events, "assistant_block_finished")[0]["text"] == "定稿全文。"
    assert _of_type(events, "assistant_message")[0]["text"] == "定稿全文。"


def test_multiple_responses_keep_one_public_text_each(
    tmp_path: Path, client_factory: Callable[[Any], TestClient]
) -> None:
    """一轮里的多次响应各有 message_id，公开文本不合并成第二个气泡。"""
    provider = FlowProvider(
        [
            [
                _text_delta("先读一下。"),
                _text_end("先读一下。"),
                _done(
                    [
                        TextContent(text="先读一下。"),
                        ToolCall(id="c1", name="read_file", arguments={"path": CHAPTER}),
                    ],
                    reason="toolUse",
                    stop_reason="toolUse",
                ),
            ],
            _text_turn("读完了，这样改。"),
        ]
    )
    client = client_factory(provider)
    workspace_id = _workspace(client, tmp_path)
    conversation_id = _accept(client, workspace_id)["conversation_id"]

    events = _wait_until(client, workspace_id, conversation_id, _finished)
    started = _of_type(events, "assistant_started")
    assert len(started) == 2
    assert started[0]["message_id"] != started[1]["message_id"]
    assert [event["text"] for event in _of_type(events, "assistant_message")] == [
        "先读一下。",
        "读完了，这样改。",
    ]
    assert len(_of_type(events, "assistant_finished")) == 2


# -- 工具配对 ---------------------------------------------------------------


def test_same_tool_called_twice_is_paired_by_call_id(
    tmp_path: Path, client_factory: Callable[[Any], TestClient]
) -> None:
    """同名工具的不同调用独立配对，参数不被后一次覆盖。"""
    provider = FlowProvider(
        [
            [
                _done(
                    [
                        ToolCall(id="c1", name="read_file", arguments={"path": CHAPTER}),
                        ToolCall(id="c2", name="read_file", arguments={"path": "设定集/线索.md"}),
                    ],
                    reason="toolUse",
                    stop_reason="toolUse",
                )
            ],
            _text_turn("都读过了。"),
        ]
    )
    client = client_factory(provider)
    workspace_id = _workspace(client, tmp_path)
    conversation_id = _accept(client, workspace_id)["conversation_id"]

    events = _wait_until(client, workspace_id, conversation_id, _finished)
    started = _of_type(events, "tool_started")
    finished = _of_type(events, "tool_finished")
    assert [event["tool_call_id"] for event in started] == ["c1", "c2"]
    assert [event["tool_call_id"] for event in finished] == ["c1", "c2"]
    assert [event["args"]["path"] for event in finished] == ["正文/0001 雨夜.md", "设定集/线索.md"]
    assert all(event["duration_ms"] >= 0 for event in finished)


def test_failed_tool_keeps_reason_and_phase(
    tmp_path: Path, client_factory: Callable[[Any], TestClient]
) -> None:
    """工具失败在折叠状态也可见：失败标记与原因都留在事件里。"""
    provider = FlowProvider(
        [
            [
                _done(
                    [ToolCall(id="c1", name="read_file", arguments={"path": "正文/不存在.md"})],
                    reason="toolUse",
                    stop_reason="toolUse",
                )
            ],
            _text_turn("没读到那一章。"),
        ]
    )
    client = client_factory(provider)
    workspace_id = _workspace(client, tmp_path)
    conversation_id = _accept(client, workspace_id)["conversation_id"]

    events = _wait_until(client, workspace_id, conversation_id, _finished)
    finished = _of_type(events, "tool_finished")[0]
    assert finished["ok"] is False
    assert finished["summary"].strip() != ""
    phases = [event["phase"] for event in _of_type(events, "run_phase")]
    assert "tool" in phases and "record" in phases


# -- 终态 -------------------------------------------------------------------


def test_stop_keeps_partial_text_and_closes_blocks(
    tmp_path: Path, client_factory: Callable[[Any], TestClient]
) -> None:
    """停止后已保存文字继续显示，并标明回复未完整生成。"""
    provider = FlowProvider(
        [
            [
                _text_delta("写到一半。"),
                _text_end("写到一半。"),
            ]
        ],
        hang_from=0,
    )
    client = client_factory(provider)
    workspace_id = _workspace(client, tmp_path)
    turn = _accept(client, workspace_id)
    conversation_id = turn["conversation_id"]
    _wait_until(client, workspace_id, conversation_id, _has_delta)
    client.post(f"/api/file-workspaces/{workspace_id}/turns/{turn['turn_id']}/stop")

    events = _wait_until(client, workspace_id, conversation_id, _finished)
    # 这一块确实收到了结束事件，所以块本身是完整的；未完整的是整次响应。
    assert _of_type(events, "assistant_block_finished")[0]["completion"] == "completed"
    assert _of_type(events, "assistant_message")[0]["completion"] == "stopped"
    assert _of_type(events, "assistant_message")[0]["text"] == "写到一半。"
    assert _of_type(events, "assistant_finished")[0]["completion"] == "stopped"
    assert events[-1]["status"] == "stopped"


def test_model_error_marks_content_failed(
    tmp_path: Path, client_factory: Callable[[Any], TestClient]
) -> None:
    """厂商报错时已收到的文字保留，但状态必须是失败，不能显示成完整成功。"""
    provider = FlowProvider(
        [
            [
                _text_delta("刚开头。"),
                _text_end("刚开头。"),
                AssistantErrorEvent(
                    reason="error",
                    error=AssistantMessage(stop_reason="error", error_message="502 Bad Gateway"),
                ),
            ]
        ]
    )
    client = client_factory(provider)
    workspace_id = _workspace(client, tmp_path)
    conversation_id = _accept(client, workspace_id)["conversation_id"]

    events = _wait_until(client, workspace_id, conversation_id, _finished)
    assert _of_type(events, "assistant_block_finished")[0]["completion"] == "completed"
    assert _of_type(events, "assistant_finished")[0]["completion"] == "failed"
    assert _of_type(events, "error")[0]["code"] == "MODEL_CALL_FAILED"
    assert events[-1]["status"] == "failed"


def test_provider_crash_keeps_received_text(
    tmp_path: Path, client_factory: Callable[[Any], TestClient]
) -> None:
    """供应商在流中途抛异常：已收到的文字保留，响应封口为失败。"""

    class CrashProvider(FlowProvider):
        def stream_response(self, **_rest: Any) -> Any:
            async def generator() -> Any:
                yield AssistantStartEvent(partial=AssistantMessage())
                yield _text_delta("写到一半。")
                raise RuntimeError("连接被重置")

            self.calls += 1
            return generator()

    client = client_factory(CrashProvider([]))
    workspace_id = _workspace(client, tmp_path)
    conversation_id = _accept(client, workspace_id)["conversation_id"]

    events = _wait_until(client, workspace_id, conversation_id, _finished)
    assert _of_type(events, "assistant_block_finished")[0]["completion"] == "failed"
    assert _of_type(events, "assistant_message")[0]["text"] == "写到一半。"
    assert "连接被重置" in _of_type(events, "error")[0]["message"]
    assert events[-1]["status"] == "failed"


def test_length_stop_marks_reply_truncated(
    tmp_path: Path, client_factory: Callable[[Any], TestClient]
) -> None:
    """达到输出上限：文字保留，块与响应标为截断，整轮记为部分完成。"""
    provider = FlowProvider(
        [[_done([TextContent(text="话说到一半")], stop_reason="length")]],
    )
    client = client_factory(provider)
    workspace_id = _workspace(client, tmp_path)
    conversation_id = _accept(client, workspace_id)["conversation_id"]

    events = _wait_until(client, workspace_id, conversation_id, _finished)
    assert _of_type(events, "assistant_block_finished")[0]["completion"] == "truncated"
    assert _of_type(events, "assistant_finished")[0]["completion"] == "truncated"
    assert events[-1]["status"] == "partial"


# -- 保存与追尾 -------------------------------------------------------------


def test_batch_append_allocates_consecutive_seq(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "state")
    accepted = store.create_with_first_turn(text="写第一章", request_key="k1", turn_id="t1")

    records = store.append_batch(
        accepted.conversation_id,
        [_phase("model"), _phase("tool")],
    )

    assert [record["seq"] for record in records] == [2, 3]
    assert all(isinstance(record["at"], str) and record["at"] for record in records)
    stored = store.events(accepted.conversation_id)
    assert [event["seq"] for event in stored] == [1, 2, 3]


def test_event_tail_reads_only_new_complete_lines(tmp_path: Path) -> None:
    """追尾读取只拿新增的完整行；文件被截断时重新定位。"""
    store = ConversationStore(tmp_path / "state")
    accepted = store.create_with_first_turn(text="写第一章", request_key="k1", turn_id="t1")
    store.append_batch(accepted.conversation_id, [_phase("model")])

    tail = store.tail(accepted.conversation_id, after=1)
    first = tail.read()
    assert [event["seq"] for event in first] == [2]
    assert tail.read() == ()

    store.append_batch(accepted.conversation_id, [_phase("tool")])
    assert [event["seq"] for event in tail.read()] == [3]

    # 文件被替换或截断：重新从头定位，不把旧偏移当成有效位置。
    store.events_file(accepted.conversation_id).write_text("", encoding="utf-8")
    store.append_batch(accepted.conversation_id, [_phase("record")])
    assert [event.get("phase") for event in tail.read()] == ["record"]


def test_event_stream_pushes_deltas_before_run_finished(
    tmp_path: Path, client_factory: Callable[[Any], TestClient]
) -> None:
    """SSE 按游标追尾推送：增量先到，整轮结束再关闭连接。"""
    provider = FlowProvider(
        [[_text_delta("好"), _text_end("好"), _done([TextContent(text="好")])]], pace=0.0
    )
    client = client_factory(provider)
    workspace_id = _workspace(client, tmp_path)
    conversation_id = _accept(client, workspace_id)["conversation_id"]
    _wait_until(client, workspace_id, conversation_id, _finished)

    with client.stream(
        "GET",
        f"/api/file-workspaces/{workspace_id}/events",
        params={"conversation_id": conversation_id, "after": 0, "follow": True},
    ) as response:
        body = "".join(response.iter_text())

    assert '"type": "assistant_block_delta"' in body
    assert body.rstrip().endswith("}")
    assert '"type": "run_finished"' in body.split("\n\n")[-2]


# -- 上下文与恢复 -----------------------------------------------------------


def test_streamed_deltas_do_not_enter_the_next_turn(
    tmp_path: Path, client_factory: Callable[[Any], TestClient]
) -> None:
    """下一轮只带回公开回复一次：展示增量与思考都不是模型上下文。"""
    provider = FlowProvider(
        [
            [
                _thinking_delta("内部判断。", index=0),
                _text_delta("公开结论。", index=1),
                _done([ThinkingContent(thinking="内部判断。"), TextContent(text="公开结论。")]),
            ],
            _text_turn("继续。"),
        ]
    )
    client = client_factory(provider)
    workspace_id = _workspace(client, tmp_path)
    conversation_id = _accept(client, workspace_id)["conversation_id"]
    _wait_until(client, workspace_id, conversation_id, _finished)

    second = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/turns",
        json={"text": "接着说", "request_key": "k2"},
    )
    assert second.status_code == 202, second.text
    _wait_until(
        client,
        workspace_id,
        conversation_id,
        lambda events: len(_of_type(events, "run_finished")) == 2,
    )

    request = provider.requests[1]
    users = [message.text for message in request["messages"] if isinstance(message, UserMessage)]
    replies = [
        "".join(block.text for block in message.content if isinstance(block, TextContent))
        for message in request["messages"]
        if isinstance(message, AssistantMessage)
    ]
    assert users == ["写第一章", "接着说"]
    assert replies == ["公开结论。"]
    assert "内部判断" not in str(request["messages"])


def test_restart_rebuilds_unfinished_blocks_once(
    tmp_path: Path, client_factory: Callable[[Any], TestClient]
) -> None:
    """响应结束前退出：按已保存增量重建块、补部分公开文本，修复只做一次。"""
    provider = FlowProvider([])
    client = client_factory(provider)
    workspace_id = _workspace(client, tmp_path)
    store = ConversationStore(conversation_root(tmp_path / "state", workspace_id))
    accepted = store.create_with_first_turn(text="写第一章", request_key="k1", turn_id="t_dead")
    conversation_id = accepted.conversation_id
    store.append_batch(
        conversation_id,
        [
            {"type": "assistant_started", "turn_id": "t_dead", "message_id": "m_dead"},
            {
                "type": "assistant_block_started",
                "turn_id": "t_dead",
                "message_id": "m_dead",
                "block_id": "m_dead:text:0",
                "kind": "text",
                "order": 0,
            },
            {
                "type": "assistant_block_delta",
                "turn_id": "t_dead",
                "message_id": "m_dead",
                "block_id": "m_dead:text:0",
                "kind": "text",
                "part": 0,
                "text": "雪夜的旅馆",
            },
            {
                "type": "assistant_block_delta",
                "turn_id": "t_dead",
                "message_id": "m_dead",
                "block_id": "m_dead:text:0",
                "kind": "text",
                "part": 1,
                "text": "很安静。",
            },
        ],
    )

    restarted = client_factory(provider)
    events = _events(restarted, workspace_id, conversation_id)

    blocks = _of_type(events, "assistant_block_finished")
    assert len(blocks) == 1
    assert blocks[0]["text"] == "雪夜的旅馆很安静。"
    assert blocks[0]["completion"] == "interrupted"
    assert blocks[0]["last_part"] == 1
    assert _of_type(events, "assistant_message")[0]["text"] == "雪夜的旅馆很安静。"
    assert _of_type(events, "assistant_message")[0]["completion"] == "interrupted"
    assert _of_type(events, "assistant_finished")[0]["completion"] == "interrupted"
    assert events[-1]["status"] == "interrupted"

    again = client_factory(provider)
    events = _events(again, workspace_id, conversation_id)
    assert len(_of_type(events, "assistant_message")) == 1
    assert len(_of_type(events, "run_finished")) == 1


def test_interrupted_reply_is_replayed_once_in_context(
    tmp_path: Path, client_factory: Callable[[Any], TestClient]
) -> None:
    """修复出的部分公开文本可以作为曾生成的内容回放，但只回放一次。"""
    provider = FlowProvider([_text_turn("接着写。")])
    client = client_factory(provider)
    workspace_id = _workspace(client, tmp_path)
    store = ConversationStore(conversation_root(tmp_path / "state", workspace_id))
    accepted = store.create_with_first_turn(text="写第一章", request_key="k1", turn_id="t_dead")
    conversation_id = accepted.conversation_id
    store.append_batch(
        conversation_id,
        [
            {"type": "assistant_started", "turn_id": "t_dead", "message_id": "m_dead"},
            {
                "type": "assistant_block_started",
                "turn_id": "t_dead",
                "message_id": "m_dead",
                "block_id": "m_dead:text:0",
                "kind": "text",
                "order": 0,
            },
            {
                "type": "assistant_block_delta",
                "turn_id": "t_dead",
                "message_id": "m_dead",
                "block_id": "m_dead:text:0",
                "kind": "text",
                "part": 0,
                "text": "只写了一半。",
            },
        ],
    )

    restarted = client_factory(provider)
    third = restarted.post(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/turns",
        json={"text": "继续", "request_key": "k2"},
    )
    assert third.status_code == 202, third.text
    _wait_until(
        restarted,
        workspace_id,
        conversation_id,
        lambda events: len(_of_type(events, "run_finished")) == 2,
    )

    request = provider.requests[0]
    replies = [
        "".join(block.text for block in message.content if isinstance(block, TextContent))
        for message in request["messages"]
        if isinstance(message, AssistantMessage)
    ]
    assert replies == ["只写了一半。"]
