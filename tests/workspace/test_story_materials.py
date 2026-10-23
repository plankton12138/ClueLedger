"""故事资料的作者视图、整理覆盖与任务契约（0.7）。

覆盖与有效性是两条轴：整理覆盖说明「读过并提交了什么」，版本有效性说明「依据还对得上
当前文件没有」。空结果同样带指纹，正文一改就失效。这里用真实临时作品与受控 provider
验证边界，不用它代替模型语义验收。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from hastings.server.file_app import create_workspace_app
from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.material_tasks import (
    MaterialTaskScope,
    freeze_material_scope,
    publish_material_result,
    targets,
)
from hastings.workspace.materials import MaterialLedger, SourceRef
from hastings.workspace.service import ProviderFactory
from hastings.workspace.state import RecentWorkspaces
from hastings.workspace.story_materials import author_view, entry_detail
from tau_agent.messages import AssistantMessage, TextContent, ToolCall
from tau_agent.provider_events import AssistantDoneEvent, AssistantStartEvent

FIRST = "正文/0001 雨夜.md"
SECOND = "正文/0002 清晨.md"
PERSON = "设定集/角色小传/林静.md"


def _book(tmp_path: Path) -> tuple[Workspace, Path]:
    workspace = Workspace.create(tmp_path / "book")
    workspace.write(
        PERSON,
        "---\nrevealed: [1:医生]\nplanned: [3:真凶]\n---\n# 林静\n",
        expected_digest=None,
    )
    workspace.write(FIRST, "# 雨夜\n\n10月21日深夜，林静在书房。\n", expected_digest=None)
    workspace.write(SECOND, "# 清晨\n\n天亮了，书房没有人。\n", expected_digest=None)
    return workspace, tmp_path / "state"


def _ledger(workspace: Workspace, *paths: str) -> MaterialLedger:
    ledger = MaterialLedger()
    for path in paths:
        view = workspace.read(path)
        ledger.record_read(
            SourceRef(path, view.digest), length=len(view.text), start=0, end=len(view.text)
        )
    return ledger


def _quote(workspace: Workspace, path: str, needle: str) -> dict[str, Any]:
    view = workspace.read(path)
    start = view.text.index(needle)
    return {
        "path": path,
        "digest": view.digest,
        "start": start,
        "end": start + len(needle),
        "quote": needle,
    }


def _timeline_scope(workspace: Workspace, paths: tuple[str, ...], as_of: int) -> dict[str, Any]:
    return freeze_material_scope(
        workspace,
        MaterialTaskScope(
            domain="timeline",
            chapter_paths=list(paths),
            as_of_chapter=as_of,
        ),
    )


def _publish(
    workspace: Workspace,
    root: Path,
    scope: dict[str, Any],
    *,
    path: str,
    entity: str = "",
    outcome: str,
    note: str = "",
    records: list[dict[str, Any]] | None = None,
    turn_id: str = "t_test",
    domain: str | None = None,
) -> dict[str, Any]:
    ledger = _ledger(workspace, path, *([entity] if entity else []))
    return publish_material_result(
        workspace=workspace,
        root=root,
        scope=scope,
        ledger=ledger,
        turn_id=turn_id,
        domain=domain or str(scope["domain"]),
        path=path,
        entity_path=entity,
        outcome=outcome,
        note=note,
        records=records or [],
        digest=workspace.read(path).digest,
    )


# -- 范围 -------------------------------------------------------------------


def test_people_domains_require_entities_and_others_refuse_them(tmp_path: Path) -> None:
    workspace, _ = _book(tmp_path)
    with pytest.raises(ValidationError):
        MaterialTaskScope(domain="disclosure", chapter_paths=[FIRST], as_of_chapter=1)
    with pytest.raises(ValidationError):
        MaterialTaskScope(
            domain="timeline",
            chapter_paths=[FIRST],
            entity_paths=[PERSON],
            as_of_chapter=1,
        )


def test_scope_rejects_duplicate_chapter_numbers(tmp_path: Path) -> None:
    workspace, _ = _book(tmp_path)
    workspace.write("正文/0001 另一章.md", "# 另一章\n", expected_digest=None)
    with pytest.raises(WorkspaceError, match="MATERIAL_SCOPE_CONFLICT"):
        freeze_material_scope(
            workspace,
            MaterialTaskScope(
                domain="timeline",
                chapter_paths=[FIRST, "正文/0001 另一章.md"],
                as_of_chapter=1,
            ),
        )


def test_scope_rejects_targets_beyond_the_cutoff(tmp_path: Path) -> None:
    workspace, _ = _book(tmp_path)
    with pytest.raises(WorkspaceError, match="MATERIAL_SCOPE_INVALID"):
        freeze_material_scope(
            workspace,
            MaterialTaskScope(domain="timeline", chapter_paths=[SECOND], as_of_chapter=1),
        )


def test_scope_rejects_stale_observed_digest(tmp_path: Path) -> None:
    workspace, _ = _book(tmp_path)
    with pytest.raises(WorkspaceError, match="CONTEXT_SOURCE_STALE_CONFLICT"):
        freeze_material_scope(
            workspace,
            MaterialTaskScope(
                domain="timeline",
                chapter_paths=[FIRST],
                as_of_chapter=1,
                observed_digests={FIRST: "seen-an-older-version"},
            ),
        )


def test_scope_freezes_server_digests_and_chapters(tmp_path: Path) -> None:
    workspace, _ = _book(tmp_path)
    frozen = _timeline_scope(workspace, (FIRST,), 2)
    assert frozen["chapters"] == {FIRST: 1}
    assert frozen["digests"][FIRST] == workspace.read(FIRST).digest
    assert frozen["as_of_chapter"] == 2


def test_entity_targets_expand_per_chapter(tmp_path: Path) -> None:
    workspace, _ = _book(tmp_path)
    frozen = freeze_material_scope(
        workspace,
        MaterialTaskScope(
            domain="roster",
            chapter_paths=[FIRST, SECOND],
            entity_paths=[PERSON],
            as_of_chapter=2,
        ),
    )
    rows = targets(frozen)
    assert len(rows) == 2
    assert {row["entity_path"] for row in rows} == {PERSON}


# -- 发布与覆盖 -------------------------------------------------------------


def test_publish_rejects_other_domain_and_foreign_target(tmp_path: Path) -> None:
    workspace, root = _book(tmp_path)
    scope = _timeline_scope(workspace, (FIRST,), 1)
    with pytest.raises(WorkspaceError, match="MATERIAL_DOMAIN_INVALID"):
        _publish(
            workspace, root, scope, path=FIRST, outcome="empty", note="没有时间", domain="clues"
        )
    with pytest.raises(WorkspaceError, match="MATERIAL_TARGET_INVALID"):
        _publish(workspace, root, scope, path=SECOND, outcome="empty", note="没有时间")
    with pytest.raises(WorkspaceError, match="MATERIAL_TARGET_INVALID"):
        _publish(
            workspace, root, scope, path=FIRST, entity=PERSON, outcome="empty", note="没有时间"
        )


def test_publish_requires_records_for_found_and_none_for_empty(tmp_path: Path) -> None:
    workspace, root = _book(tmp_path)
    scope = _timeline_scope(workspace, (FIRST,), 1)
    with pytest.raises(WorkspaceError, match="MATERIAL_RESULT_INVALID"):
        _publish(workspace, root, scope, path=FIRST, outcome="found")
    with pytest.raises(WorkspaceError, match="MATERIAL_RESULT_INVALID"):
        _publish(workspace, root, scope, path=FIRST, outcome="empty")
    with pytest.raises(WorkspaceError, match="MATERIAL_RESULT_INVALID"):
        _publish(
            workspace,
            root,
            scope,
            path=FIRST,
            outcome="empty",
            note="读完没有时间信息",
            records=[{"when": "雨夜", "evidence": _quote(workspace, FIRST, "雨夜")}],
        )


def test_publish_found_writes_record_and_coverage(tmp_path: Path) -> None:
    workspace, root = _book(tmp_path)
    scope = _timeline_scope(workspace, (FIRST,), 1)
    proof = _quote(workspace, FIRST, "10月21日深夜")
    result = _publish(
        workspace,
        root,
        scope,
        path=FIRST,
        outcome="found",
        records=[{"when": "10月21日深夜", "location": "书房", "evidence": proof}],
    )
    assert result["published"] == 1
    view = author_view(workspace, root, "timeline", as_of_chapter=1)
    entry = view["entries"][0]
    assert entry["coverage"] == "found"
    assert entry["freshness"] == "valid"
    assert entry["data"]["when"] == "10月21日深夜"
    assert entry["evidence"][0]["quote"] == "10月21日深夜"
    assert view["coverage"]["found"] == 1
    assert view["coverage"]["total"] == 1


def test_empty_result_is_coverage_and_expires_with_the_source(tmp_path: Path) -> None:
    workspace, root = _book(tmp_path)
    scope = _timeline_scope(workspace, (SECOND,), 2)
    _publish(
        workspace,
        root,
        scope,
        path=SECOND,
        outcome="empty",
        note="这一章没有写时间",
    )
    view = author_view(workspace, root, "timeline", as_of_chapter=2)
    entry = next(item for item in view["entries"] if item["chapter"] == 2)
    assert entry["coverage"] == "empty"
    assert entry["freshness"] == "valid"

    old = workspace.read(SECOND)
    workspace.write(SECOND, "# 清晨\n\n天亮了，书房还是没有人。\n", expected_digest=old.digest)
    view = author_view(workspace, root, "timeline", as_of_chapter=2)
    entry = next(item for item in view["entries"] if item["chapter"] == 2)
    # 空结果不是永久结论：正文改了，它同样失效。
    assert entry["coverage"] == "empty"
    assert entry["freshness"] == "stale"
    assert view["coverage"]["stale"] == 1


def test_author_view_lists_unrecorded_chapters_and_detail_by_id(tmp_path: Path) -> None:
    workspace, root = _book(tmp_path)
    view = author_view(workspace, root, "timeline", as_of_chapter=2)
    assert [item["chapter"] for item in view["entries"]] == [1, 2]
    assert view["coverage"] == {
        "total": 2,
        "found": 0,
        "empty": 0,
        "insufficient": 0,
        "unrecorded": 2,
        "stale": 0,
    }
    entry_id = view["entries"][0]["id"]
    detail = entry_detail(workspace, root, "timeline", entry_id, as_of_chapter=2)
    assert detail["entry"]["id"] == entry_id
    with pytest.raises(WorkspaceError, match="MATERIAL_ENTRY_NOT_FOUND"):
        entry_detail(workspace, root, "timeline", "does-not-exist", as_of_chapter=2)


def test_roster_without_evidence_reports_unknown_state(tmp_path: Path) -> None:
    workspace, root = _book(tmp_path)
    view = author_view(workspace, root, "roster", as_of_chapter=2)
    entry = view["entries"][0]
    # 没有核对记录时不能猜「还活着」。
    assert entry["data"]["alive"] is None
    assert entry["freshness"] == "none"


# -- 任务：受理、完成度与会话归属 -------------------------------------------


class ScriptedProvider:
    """按脚本吐事件；只用于受控接口验证，不代表模型语义。"""

    def __init__(self, turns: list[list[Any]]) -> None:
        self.turns = turns
        self.calls = 0

    def stream_response(
        self,
        *,
        model: str = "",
        system: str = "",
        messages: list[Any] | None = None,
        **_rest: Any,
    ) -> Any:
        del model, system, messages
        index = self.calls
        self.calls += 1

        async def generator() -> Any:
            yield AssistantStartEvent(partial=AssistantMessage())
            if index < len(self.turns):
                for event in self.turns[index]:
                    yield event

        return generator()


def _text_turn(text: str) -> list[Any]:
    return [
        AssistantDoneEvent(
            reason="stop",
            message=AssistantMessage(content=[TextContent(text=text)], stop_reason="stop"),
        )
    ]


def _tool_turn(*calls: ToolCall) -> list[Any]:
    return [
        AssistantDoneEvent(
            reason="toolUse",
            message=AssistantMessage(content=list(calls), stop_reason="toolUse"),
        )
    ]


def _snapshot(provider: Any) -> ModelSnapshot:
    return ModelSnapshot(
        connection_id="test", display_name="测试连接", model="fake-model", provider=provider
    )


@pytest.fixture()
def client_factory(tmp_path: Path) -> Any:
    opened: list[TestClient] = []

    def build(factory: ProviderFactory) -> TestClient:
        book = RecentWorkspaces(tmp_path / "app-state")
        client = TestClient(create_workspace_app(workspaces=book, provider_factory=factory))
        client.__enter__()
        opened.append(client)
        return client

    yield build
    for client in opened:
        client.__exit__(None, None, None)


def _workspace(client: TestClient, tmp_path: Path) -> tuple[str, Path]:
    root = tmp_path / "雪山旅馆"
    response = client.post("/api/file-workspaces", json={"path": str(root), "create": True})
    assert response.status_code == 200, response.text
    workspace = Workspace.open(root)
    workspace.write(PERSON, "---\nrevealed: [1:医生]\n---\n# 林静\n", expected_digest=None)
    workspace.write(FIRST, "# 雨夜\n\n10月21日深夜，林静在书房。\n", expected_digest=None)
    return str(response.json()["id"]), root


def _start_task(
    client: TestClient,
    workspace_id: str,
    *,
    key: str,
    chapter_paths: list[str],
    as_of: int = 1,
    observed: dict[str, str] | None = None,
) -> dict[str, Any]:
    body = {
        "request_key": key,
        "scope": {
            "domain": "timeline",
            "chapter_paths": chapter_paths,
            "entity_paths": [],
            "as_of_chapter": as_of,
            "observed_digests": observed or {},
        },
    }
    response = client.post(f"/api/file-workspaces/{workspace_id}/material-tasks", json=body)
    assert response.status_code == 202, response.text
    return dict(response.json())


def _wait_finished(client: TestClient, workspace_id: str, conversation_id: str) -> list[dict]:
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        events = client.get(
            f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/messages"
        ).json()["events"]
        if any(event["type"] == "run_finished" for event in events):
            return list(events)
        time.sleep(0.05)
    raise AssertionError("等待轮次结束超时")


def test_task_without_result_is_partial_and_hidden_from_writing_list(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    client = client_factory(lambda: _snapshot(ScriptedProvider([_text_turn("已经整理完")])))
    workspace_id, _ = _workspace(client, tmp_path)
    accepted = _start_task(client, workspace_id, key="k1", chapter_paths=[FIRST])
    assert accepted["origin"] == "materials"
    assert accepted["domain"] == "timeline"
    _wait_finished(client, workspace_id, accepted["conversation_id"])

    tasks = client.get(
        f"/api/file-workspaces/{workspace_id}/material-tasks?domain=timeline"
    ).json()["items"]
    assert len(tasks) == 1
    assert tasks[0]["status"] == "partial"
    assert tasks[0]["total"] == 1
    assert tasks[0]["done"] == 0

    detail = client.get(
        f"/api/file-workspaces/{workspace_id}/material-tasks/{accepted['turn_id']}"
    ).json()
    assert detail["unfinished"][0]["outcome"] == "unrecorded"

    # 资料任务有自己的会话归属，不出现在写作会话列表里。
    assert client.get(f"/api/file-workspaces/{workspace_id}/conversations").json()["items"] == []


def test_task_publishes_timeline_and_completes(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    root = tmp_path / "雪山旅馆"
    # 先建作品，拿到真实 digest 再写脚本；provider 在受理时才会被调用。
    book = Workspace.create(root)
    book.write(FIRST, "# 雨夜\n\n10月21日深夜，林静在书房。\n", expected_digest=None)
    view = book.read(FIRST)
    start = view.text.index("10月21日深夜")
    provider = ScriptedProvider(
        [
            _tool_turn(ToolCall(id="c1", name="read_file", arguments={"path": FIRST})),
            _tool_turn(
                ToolCall(
                    id="c2",
                    name="publish_material_result",
                    arguments={
                        "domain": "timeline",
                        "path": FIRST,
                        "digest": view.digest,
                        "outcome": "found",
                        "records": [
                            {
                                "when": "10月21日深夜",
                                "location": "书房",
                                "evidence": {
                                    "path": FIRST,
                                    "digest": view.digest,
                                    "start": start,
                                    "end": start + len("10月21日深夜"),
                                    "quote": "10月21日深夜",
                                },
                            }
                        ],
                    },
                )
            ),
            _text_turn("整理完成"),
        ]
    )
    client = client_factory(lambda: _snapshot(provider))
    response = client.post("/api/file-workspaces", json={"path": str(root)})
    assert response.status_code == 200, response.text
    workspace_id = str(response.json()["id"])

    accepted = _start_task(
        client,
        workspace_id,
        key="k2",
        chapter_paths=[FIRST],
        observed={FIRST: view.digest},
    )
    events = _wait_finished(client, workspace_id, accepted["conversation_id"])
    published = [event for event in events if event["type"] == "material_target_published"]
    assert published and published[0]["outcome"] == "found"

    detail = client.get(
        f"/api/file-workspaces/{workspace_id}/material-tasks/{accepted['turn_id']}"
    ).json()
    assert detail["status"] == "completed"
    assert detail["done"] == 1
    assert detail["targets"][0]["outcome"] == "found"

    materials = client.get(f"/api/file-workspaces/{workspace_id}/story-materials/timeline").json()
    assert materials["entries"][0]["coverage"] == "found"
    assert materials["entries"][0]["data"]["when"] == "10月21日深夜"
    assert materials["entries"][0]["evidence"][0]["quote"] == "10月21日深夜"


def test_same_request_key_does_not_create_a_second_task(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    client = client_factory(lambda: _snapshot(ScriptedProvider([_text_turn("完成")])))
    workspace_id, _ = _workspace(client, tmp_path)
    first = _start_task(client, workspace_id, key="same", chapter_paths=[FIRST])
    _wait_finished(client, workspace_id, first["conversation_id"])
    again = _start_task(client, workspace_id, key="same", chapter_paths=[FIRST])
    assert again["turn_id"] == first["turn_id"]
    tasks = client.get(f"/api/file-workspaces/{workspace_id}/material-tasks").json()["items"]
    assert len(tasks) == 1
