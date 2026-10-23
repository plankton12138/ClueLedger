"""活动索引、真实请求边界与文件副作用的回归。"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.activity import ActivityStore, classify
from hastings.workspace.agent import RunContext, build_tools
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.git import GitRepo
from hastings.workspace.model_activity import RecordedProvider
from hastings.workspace.service import TurnService, WriteSession
from hastings.workspace.state import ConversationStore, RecentWorkspaces, conversation_root
from tau_agent.messages import AssistantMessage, TextContent, ToolCall, UserMessage
from tau_agent.provider_events import AssistantDoneEvent, AssistantStartEvent, TextDeltaEvent


def write_log(root: Path, name: str, events: list[dict[str, Any]]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.jsonl"
    path.write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8"
    )
    return path


def event(seq: int, **extra: Any) -> dict[str, Any]:
    return {
        "seq": seq,
        "at": f"2026-10-23T12:00:{seq:02d}+08:00",
        "type": "material_read",
        "turn_id": "t1",
        "path": "正文/第一章.md",
        **extra,
    }


def test_cross_conversation_order_filters_pagination_and_export(tmp_path: Path) -> None:
    # 文件名顺序与发生时间相反；首次建立索引仍按时间排列。
    write_log(tmp_path, "a", [event(4), event(5, type="error", message="版本已改变")])
    write_log(
        tmp_path,
        "z",
        [
            event(1),
            event(2, type="tool_started", tool="read_file"),
            event(3, type="assistant_block_delta", text="分片"),
        ],
    )
    store = ActivityStore(tmp_path)
    first = store.query(limit=2)
    assert [i["conversation_id"] for i in first["items"]] == ["a", "a"]
    older = store.query(before=first["next_before"], limit=2)
    assert [i["type"] for i in older["items"]] == ["tool_started", "material_read"]
    assert store.query(q="版本")["stats"]["errors"] == 1
    assert store.query(category="context")["stats"]["events"] == 2
    assert store.query(conversation_id="z")["stats"]["tools"] == 1
    assert store.query(verbose=True)["stats"]["events"] == 5
    assert store.query(since="2026-10-23T04:00:04Z")["stats"]["events"] == 2
    exported = [json.loads(line) for line in store.export(conversation_id="z")]
    assert [i["detail"]["seq"] for i in exported] == [1, 2]
    assert "detail" not in first["items"][0]
    assert store.detail(first["items"][0]["event_id"])["message"] == "版本已改变"


def test_cursor_skips_filtered_rows_and_drains_all_matching_batches(tmp_path: Path) -> None:
    path = write_log(tmp_path, "c", [event(1)])
    store = ActivityStore(tmp_path)
    cursor = store.query()["cursor"]
    with path.open("a", encoding="utf-8") as out:
        for n in range(2, 9):
            out.write(json.dumps(event(n, type="error" if n % 2 else "material_read")) + "\n")
    first = store.query(after=cursor, category="context", limit=2)
    assert first["has_more"]
    second = store.query(after=first["cursor"], category="context", limit=2)
    assert not second["has_more"]
    assert len({i["event_id"] for i in first["items"] + second["items"]}) == 4
    assert store.query(after=second["cursor"], category="context")["items"] == []


def test_partial_line_replacement_and_missing_source_reset_cursor(tmp_path: Path) -> None:
    path = write_log(tmp_path, "c", [event(1)])
    store = ActivityStore(tmp_path)
    first = store.query()
    raw = json.dumps(event(2)).encode()
    with path.open("ab") as out:
        out.write(raw[:20])
    assert store.query(after=first["cursor"])["items"] == []
    with path.open("ab") as out:
        out.write(raw[20:] + b"\n")
    second = store.query(after=first["cursor"])
    assert len(second["items"]) == 1 and not second["reset"]
    path.write_text(json.dumps(event(1, type="error")) + "\n", encoding="utf-8")
    replaced = store.query(after=second["cursor"])
    assert replaced["reset"] and replaced["items"][0]["type"] == "error"
    path.unlink()
    assert store.query(after=replaced["cursor"])["reset"]
    assert not store.query()["items"]


def test_bad_line_is_visible_and_derived_index_rebuild_keeps_event_ids(tmp_path: Path) -> None:
    path = write_log(tmp_path, "c", [event(1)])
    with path.open("ab") as out:
        out.write(b"broken\n")
    store = ActivityStore(tmp_path)
    first = store.query()
    assert first["stats"]["errors"] == 1
    ids = {e["event_id"] for e in first["items"]}
    (tmp_path / "activity/index.sqlite3").unlink()
    rebuilt = store.query(after=first["cursor"])
    assert rebuilt["reset"]
    assert {e["event_id"] for e in rebuilt["items"]} == ids


def test_operations_attachments_redaction_and_cleanup(tmp_path: Path) -> None:
    store = ActivityStore(tmp_path)
    with store.operation("补记版本", api_key="hidden") as operation:
        operation.result = {"commit": "abc"}
    with pytest.raises(ValueError), store.operation("导入正文"):
        raise ValueError("文件编码错误")
    page = store.query(details=True)
    assert page["stats"]["errors"] == 1
    assert "hidden" not in json.dumps(page)
    key = store.attachment({"api_key": "secret", "text": "Bearer abcdef", "path": "正文/第一章.md"})
    assert "secret" not in json.dumps(store.detail_attachment(key))
    assert store.clear_details() == 1
    assert store.detail_attachment(key)["available"] is False
    with pytest.raises(WorkspaceError):
        store.detail_attachment("../settings")
    assert store.settings() == {"capture_inputs": False, "retention_days": 30}
    assert store.configure(capture_inputs=True)["capture_inputs"]


def test_authorization_quote_is_kept_and_export_details_are_optional(tmp_path: Path) -> None:
    store = ActivityStore(tmp_path)
    key = store.attachment({"text": "实际模型输入", "textSignature": "private-signature"})
    store.append(
        {
            "type": "task_scope_set",
            "authorization": "只修改第一章",
            "authorization_seq": 12,
            "input_detail": key,
        }
    )
    ordinary = json.loads(next(store.export()))
    assert ordinary["detail"]["authorization"] == "只修改第一章"
    assert ordinary["detail"]["authorization_seq"] == 12
    assert "attachments" not in ordinary
    detailed = json.loads(next(store.export(include_details=True)))
    assert detailed["attachments"]["input_detail"]["text"] == "实际模型输入"
    assert "private-signature" not in json.dumps(detailed)


def test_detail_retention_expires_only_attachments(tmp_path: Path) -> None:
    store = ActivityStore(tmp_path)
    store.configure(capture_inputs=True, retention_days=7)
    key = store.attachment({"text": "旧快照"})
    store.append({"type": "model_request_started", "input_detail": key})
    old = time.time() - 8 * 86400
    os.utime(tmp_path / f"activity/details/{key}.json", (old, old))
    store.attachment({"text": "新快照"})
    assert store.detail_attachment(key)["available"] is False
    assert store.query()["stats"]["events"] == 1


def test_restart_closes_unconfirmed_operations_once_and_repairs_partial_tail(
    tmp_path: Path,
) -> None:
    store = ActivityStore(tmp_path)
    store.append({"type": "operation_started", "operation_id": "op1", "operation": "导入正文"})
    path = tmp_path / "activity/operations.jsonl"
    with path.open("ab") as out:
        out.write(b'{"type":')
    restarted = ActivityStore(tmp_path)
    restarted.recover()
    restarted.recover()
    items = restarted.query(details=True)["items"]
    assert len(items) == 2
    assert items[0]["detail"]["status"] == "interrupted"
    assert items[0]["detail"]["duration_ms"] is None


def test_active_run_is_independent_of_event_filter_and_terminal_is_current(tmp_path: Path) -> None:
    path = write_log(tmp_path, "c", [event(1, type="turn_started", model="online-model")])
    store = ActivityStore(tmp_path)
    first = store.query(category="file")
    assert first["items"] == []
    assert first["run"]["status"] == "running"
    with path.open("a", encoding="utf-8") as out:
        out.write(json.dumps(event(2, type="run_finished", status="stopped")) + "\n")
    assert store.query()["run"]["status"] == "stopped"
    assert store.query(conversation_id="other")["run"] is None


class Provider:
    def __init__(self, fail: bool = False) -> None:
        self.requests: list[dict[str, Any]] = []
        self.fail = fail

    async def stream_response(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        yield AssistantStartEvent(partial=AssistantMessage())
        if self.fail:
            raise RuntimeError("端点错误")
        yield TextDeltaEvent(content_index=0, delta="你好", partial=AssistantMessage())
        yield AssistantDoneEvent(
            reason="stop", message=AssistantMessage(content=[TextContent(text="你好")])
        )


@pytest.mark.parametrize("capture", [False, True])
def test_actual_model_input_capture_is_optional_and_matches_request(
    tmp_path: Path, capture: bool
) -> None:
    async def run() -> None:
        store = ActivityStore(tmp_path)
        store.configure(capture_inputs=capture)
        provider = Provider()
        records: list[tuple[str, dict[str, Any]]] = []
        wrapped = RecordedProvider(
            provider, store, lambda k, p: records.append((k, p)), stage="review"
        )
        received = [
            e
            async for e in wrapped.stream_response(
                model="online-name",
                system="实际上下文",
                messages=[UserMessage(content="检查第一章")],
                tools=[],
            )
        ]
        assert len(received) == 3
        assert [r[0] for r in records] == ["model_request_started", "model_request_finished"]
        assert records[0][1]["operation_id"] == records[1][1]["operation_id"]
        assert records[1][1]["usage"] is None
        assert records[1][1]["first_output_ms"] is not None
        key = records[0][1]["input_detail"]
        assert bool(key) == capture
        if key:
            detail = store.detail_attachment(key)
            assert detail["system"] == provider.requests[0]["system"]
            assert detail["messages"][0]["content"] == "检查第一章"

    asyncio.run(run())


def test_model_exception_has_paired_failure_without_retry(tmp_path: Path) -> None:
    async def run() -> None:
        provider = Provider(fail=True)
        records: list[tuple[str, dict[str, Any]]] = []
        wrapped = RecordedProvider(
            provider, ActivityStore(tmp_path), lambda k, p: records.append((k, p)), stage="write"
        )
        with pytest.raises(RuntimeError):
            async for _ in wrapped.stream_response(model="x", system="", messages=[], tools=[]):
                pass
        assert len(provider.requests) == 1
        assert records[-1][1]["status"] == "failed"
        assert records[-1][1]["first_output_ms"] is None

    asyncio.run(run())


def test_service_model_and_tool_events_keep_chat_replay_compatible(tmp_path: Path) -> None:
    async def run() -> None:
        workspace = Workspace.create(tmp_path / "book")
        store = ConversationStore(tmp_path / "state")
        provider = Provider()
        service = TurnService(
            workspace,
            store,
            lambda: ModelSnapshot(
                connection_id="test", display_name="测试", model="online-name", provider=provider
            ),
        )
        accepted = await service.accept("你好", "k1")
        await service.wait(accepted.turn_id)
        events = store.events(accepted.conversation_id)
        assert events[-1]["status"] == "completed"
        assert [e["type"] for e in events].count("model_request_finished") == 1
        assert service.activity.query(category="model")["stats"]["models"] == 1
        assert "你好" in json.dumps(events, ensure_ascii=False)

    asyncio.run(run())


def test_queued_tool_and_reply_events_keep_the_request_that_produced_them(tmp_path: Path) -> None:
    class FastProvider(Provider):
        async def stream_response(self, **kwargs: Any) -> Any:
            if not self.requests:
                self.requests.append(kwargs)
                yield AssistantStartEvent(partial=AssistantMessage())
                yield AssistantDoneEvent(
                    reason="toolUse",
                    message=AssistantMessage(
                        content=[
                            ToolCall(
                                id="read-call",
                                name="read_file",
                                arguments={"path": "正文/0001 雨夜.md"},
                            )
                        ],
                        stop_reason="toolUse",
                    ),
                )
            else:
                async for item in super().stream_response(**kwargs):
                    yield item

    async def run() -> None:
        workspace = Workspace.create(tmp_path / "book")
        workspace.write("正文/0001 雨夜.md", "# 雨夜\n", expected_digest=None)
        store = ConversationStore(tmp_path / "state")
        provider = FastProvider()
        service = TurnService(
            workspace,
            store,
            lambda: ModelSnapshot(
                connection_id="test", display_name="测试", model="test-model", provider=provider
            ),
        )
        accepted = await service.accept("读取第一章", "k1")
        await service.wait(accepted.turn_id)
        events = store.events(accepted.conversation_id)
        requests = [e for e in events if e["type"] == "model_request_started"]
        assert len(requests) == 2
        for item in events:
            if item["type"] in ("tool_started", "tool_finished"):
                assert item["parent_operation_id"] == requests[0]["operation_id"]
        replies = [e for e in events if e["type"] == "assistant_started"]
        assert [r["parent_operation_id"] for r in replies] == [r["operation_id"] for r in requests]

    asyncio.run(run())


def test_file_events_record_actual_versions_and_reject_without_change(tmp_path: Path) -> None:
    async def run() -> None:
        workspace = Workspace.create(tmp_path / "book")
        GitRepo(workspace.root).init()
        records: list[tuple[str, dict[str, Any]]] = []
        run = RunContext(
            workspace,
            WriteSession(workspace),
            author_messages=((1, "写第一章"),),
            emit=lambda k, p: records.append((k, p)),
        )
        tools = {t.name: t for t in build_tools(workspace, run)}
        path = "正文/0001 雨夜.md"
        with pytest.raises(WorkspaceError):
            await tools["write_file"].execute_fn("rejected", {"path": path, "text": "正文"})
        assert not any(k == "file_changed" for k, _ in records)
        await tools["set_task_scope"].execute_fn(
            "scope",
            {
                "mode": "edit",
                "summary": "写第一章",
                "authorization": "写第一章",
                "authorization_seq": 1,
                "grants": [{"action": "create", "path": path}],
            },
        )
        await tools["write_file"].execute_fn("write1", {"path": path, "text": "# 雨夜\n"})
        change = next(p for k, p in records if k == "file_changed")
        assert change["tool_call_id"] == "write1"
        assert change["before_digest"] is None
        assert change["after_digest"] == workspace.read(path).digest

    asyncio.run(run())


def test_activity_api_real_operations_validation_export_and_workspace_isolation(
    tmp_path: Path,
) -> None:
    book = RecentWorkspaces(tmp_path / "state")

    def no_model() -> Any:
        raise AssertionError("文件操作不应调用模型")

    with TestClient(create_workspace_app(workspaces=book, provider_factory=no_model)) as client:
        first = client.post(
            "/api/file-workspaces", json={"path": str(tmp_path / "one"), "create": True}
        ).json()["id"]
        second = client.post(
            "/api/file-workspaces", json={"path": str(tmp_path / "two"), "create": True}
        ).json()["id"]
        base = f"/api/file-workspaces/{first}"
        # 记录失败也必须可查询，不依赖模型。
        response = client.post(base + "/git/revert", json={"commit": "bad"})
        assert response.status_code >= 400
        page = client.get(base + "/activity").json()
        assert page["stats"]["events"] == 2 and page["stats"]["errors"] == 1
        detail = client.get(base + "/activity/" + page["items"][0]["event_id"])
        assert detail.json()["status"] == "failed"
        assert client.get(base + "/activity?after=bad").status_code == 422
        assert client.get(base + "/activity?limit=501").status_code == 422
        exported = client.get(base + "/activity/export?status=failed")
        assert len(exported.text.splitlines()) == 1
        assert client.get(f"/api/file-workspaces/{second}/activity").json()["items"] == []
        assert client.put(base + "/activity/settings", json={"capture_inputs": True}).json()[
            "capture_inputs"
        ]
        assert client.delete(base + "/activity/details").json()["removed"] == 0
        root = conversation_root(book.root, first)
        assert (root / "activity/operations.jsonl").exists()


def test_classify_maps_real_events_to_readable_actions() -> None:
    turn = classify(
        {
            "type": "turn_started",
            "turn_id": "t1",
            "text": "我想到一个点子。\n一部悬疑小说。",
            "model": "deepseek-flash",
        }
    )
    assert (turn.action, turn.phase, turn.title) == ("conversation", "started", "收到要求")
    assert turn.subject == "我想到一个点子。 一部悬疑小说。"
    assert turn.pair_key == "turn:t1"

    materials = classify(
        {
            "type": "turn_started",
            "turn_id": "t2",
            "origin": "materials",
            "model": "deepseek-flash",
            "material_scope": {
                "domain": "clues",
                "chapter_paths": ["正文/0001 六点的白鸦馆.md"],
                "as_of_chapter": 1,
            },
        }
    )
    assert (materials.action, materials.title) == ("materials", "开始整理")
    assert materials.subject == "线索与伏笔 · 第 1 章"

    model = classify(
        {
            "type": "model_request_finished",
            "operation_id": "op1",
            "model": "deepseek-flash",
            "status": "completed",
            "duration_ms": 313921,
            "usage": {"input": 3806, "output": 42704},
        }
    )
    assert (model.action, model.phase) == ("model", "completed")
    assert model.note == "输出 42,704 tokens · 5 分 14 秒"
    assert model.pair_key == "model:op1"

    tool = classify(
        {
            "type": "tool_finished",
            "tool_call_id": "call1",
            "tool": "read_file",
            "ok": True,
            "args": {"path": "设定集/真相.md"},
            "result_chars": 3920,
            "duration_ms": 120,
        }
    )
    assert (tool.action, tool.title, tool.subject) == ("tool", "读取", "设定集/真相.md")
    assert tool.note == "3,920 字 · 120 毫秒"
    assert tool.pair_key == "tool:call1"

    changed = classify(
        {
            "type": "file_changed",
            "tool_call_id": "call1",
            "action": "update",
            "path": "设定集/真相.md",
            "size": 10626,
        }
    )
    assert (changed.action, changed.title, changed.note) == ("file", "写入文件", "10.4 KB")
    assert changed.pair_key == "tool:call1"

    finished = classify(
        {
            "type": "run_finished",
            "turn_id": "t1",
            "status": "completed",
            "recording": "committed",
            "changed_paths": ["a", "b"],
            "commit": "1abef6f2a296",
        }
    )
    assert finished.phase == "completed"
    assert finished.note == "2 个文件 · 已提交"
    assert finished.subject == "1abef6f"
    assert finished.pair_key == "turn:t1"

    scope = classify(
        {"type": "task_scope_set", "summary": "先建人物小传", "grants": [{"path": "a"}]}
    )
    assert (scope.action, scope.title, scope.note) == ("scope", "登记修改范围", "1 项")

    denied = classify(
        {
            "type": "tool_finished",
            "tool_call_id": "c2",
            "tool": "set_task_scope",
            "ok": False,
            "summary": "[TASK_SCOPE_DENIED_CONFLICT] 范围只能登记作品内的确切文件路径",
        }
    )
    assert denied.phase == "failed"
    assert denied.note.startswith("[TASK_SCOPE_DENIED_CONFLICT]")

    error = classify(
        {
            "type": "error",
            "code": "ACTIVITY_INVALID_LINE",
            "message": "原始日志包含无法解析的完整行",
        }
    )
    assert (error.action, error.phase, error.subject) == (
        "error", "failed", "ACTIVITY_INVALID_LINE",
    )

    published = classify(
        {
            "type": "material_target_published",
            "domain": "chapter_index",
            "path": "正文/0001 六点的白鸦馆.md",
            "outcome": "found",
            "published": 1,
        }
    )
    assert (published.action, published.title) == ("materials", "发布整理结果")
    assert published.subject == "章节概览 · 正文/0001 六点的白鸦馆.md"
    assert published.note == "有结果 · 提交 1 条"

    finished = classify(
        {"type": "material_task_finished", "domain": "clues", "done": 1, "total": 1}
    )
    assert (finished.title, finished.phase) == ("整理任务结束", "completed")
    assert finished.note == "完成 1 / 1"
    partial = classify(
        {"type": "material_task_finished", "domain": "clues", "done": 1, "total": 2}
    )
    assert partial.phase == "partial"


def test_query_exposes_action_fields_and_schema_upgrade_rebuilds_index(tmp_path: Path) -> None:
    write_log(
        tmp_path,
        "c",
        [
            event(
                1,
                type="tool_started",
                tool="read_file",
                tool_call_id="call1",
                args={"path": "正文/第一章.md"},
            ),
            event(
                2,
                type="tool_finished",
                tool="read_file",
                tool_call_id="call1",
                args={"path": "正文/第一章.md"},
                ok=True,
                result_chars=12,
                duration_ms=5,
            ),
        ],
    )
    store = ActivityStore(tmp_path)
    items = store.query()["items"]
    assert [item["type"] for item in items] == ["tool_finished", "tool_started"]
    assert [item["action"] for item in items] == ["tool", "tool"]
    assert {item["pair_key"] for item in items} == {"tool:call1"}
    assert items[0]["phase"] == "completed"
    assert items[1]["phase"] == "started"
    assert items[0]["title"] == "读取"

    db = sqlite3.connect(tmp_path / "activity/index.sqlite3")
    db.execute("UPDATE meta SET value='1' WHERE key='schema'")
    db.commit()
    db.close()
    rebuilt = store.query()
    assert rebuilt["items"][1]["subject"] == "正文/第一章.md"
    assert rebuilt["items"][0]["note"] == "12 字 · 5 毫秒"


def test_every_writing_tool_has_a_readable_verb(tmp_path: Path) -> None:
    """写作工具在界面上说人话：没配动词的工具会直接显示英文名，等于漏配。"""

    workspace = Workspace.create(tmp_path / "book")
    GitRepo(workspace.root).init()
    run = RunContext(
        workspace,
        WriteSession(workspace),
        author_messages=((1, "写第一章"),),
        emit=lambda kind, payload: None,
    )
    missing = [
        tool.name
        for tool in build_tools(workspace, run)
        if classify({"type": "tool_started", "tool": tool.name}).title == tool.name
    ]
    assert missing == []
