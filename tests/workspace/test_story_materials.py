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
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from hastings.server.file_app import create_workspace_app
from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.api import _material_prompt
from hastings.workspace.clues import ClueStore, build_clues
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.glossary import GlossaryStore
from hastings.workspace.material_tasks import (
    MaterialTaskScope,
    freeze_material_scope,
    publish_material_result,
    targets,
)
from hastings.workspace.materials import MaterialLedger, SourceRef
from hastings.workspace.service import ProviderFactory
from hastings.workspace.state import RecentWorkspaces
from hastings.workspace.story_index import StoryIndexStore, chapter_view, story_view
from hastings.workspace.story_materials import author_view, entry_detail
from hastings.workspace.timeline import TimelineStore
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


def _clues_scope(workspace: Workspace, paths: tuple[str, ...], as_of: int) -> dict[str, Any]:
    return freeze_material_scope(
        workspace,
        MaterialTaskScope(domain="clues", chapter_paths=list(paths), as_of_chapter=as_of),
    )


def _story_scope(workspace: Workspace, paths: tuple[str, ...], as_of: int) -> dict[str, Any]:
    return freeze_material_scope(
        workspace,
        MaterialTaskScope(domain="story_index", chapter_paths=list(paths), as_of_chapter=as_of),
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
    turn_id: str = "",
    domain: str | None = None,
) -> dict[str, Any]:
    ledger = _ledger(workspace, path, *([entity] if entity else []))
    return publish_material_result(
        workspace=workspace,
        root=root,
        scope=scope,
        ledger=ledger,
        turn_id=turn_id or "t_" + uuid4().hex,
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


def test_clue_due_chapter_may_be_a_future_plan(tmp_path: Path) -> None:
    """计划回收章是作者计划，不是正文证据：截至第 1 章也能登记第 22 章回收。"""
    workspace, root = _book(tmp_path)
    scope = _clues_scope(workspace, (FIRST,), 1)
    proof = _quote(workspace, FIRST, "书房")

    result = _publish(
        workspace,
        root,
        scope,
        path=FIRST,
        outcome="found",
        records=[
            {
                "clue": "书房有第二把椅子",
                "planted_chapter": 1,
                "due_chapter": 22,
                "evidence": proof,
            }
        ],
    )

    assert result["published"] == 1
    entry = ClueStore(root).entries()[0]
    assert (entry.planted_chapter, entry.due_chapter) == (1, 22)
    assert entry.status == "open"


def test_clue_body_evidence_still_stops_at_the_cutoff(tmp_path: Path) -> None:
    """正文证据不能提前：埋设或回收写在第 2 章，就不能按第 1 章的截止章登记。"""
    workspace, root = _book(tmp_path)
    scope = _clues_scope(workspace, (FIRST,), 1)
    proof = _quote(workspace, FIRST, "书房")

    with pytest.raises(WorkspaceError, match="MATERIAL_RESULT_INVALID"):
        _publish(
            workspace,
            root,
            scope,
            path=FIRST,
            outcome="found",
            records=[{"clue": "雨夜的门", "planted_chapter": 2, "evidence": proof}],
        )
    with pytest.raises(WorkspaceError, match="MATERIAL_RESULT_INVALID"):
        _publish(
            workspace,
            root,
            scope,
            path=FIRST,
            outcome="found",
            records=[
                {
                    "clue": "雨夜的门",
                    "planted_chapter": 1,
                    "status": "resolved",
                    "resolution_chapter": 2,
                    "resolution_evidence": proof,
                    "evidence": proof,
                }
            ],
        )


def test_clue_plan_cannot_precede_its_planting(tmp_path: Path) -> None:
    """计划与证据分开以后，计划早于埋设仍然是错的。"""
    workspace, root = _book(tmp_path)
    scope = _clues_scope(workspace, (FIRST, SECOND), 2)
    proof = _quote(workspace, SECOND, "书房没有人")

    with pytest.raises(WorkspaceError, match="STATE_EVIDENCE_INVALID"):
        _publish(
            workspace,
            root,
            scope,
            path=SECOND,
            outcome="found",
            records=[
                {
                    "clue": "书房没有人",
                    "planted_chapter": 2,
                    "due_chapter": 1,
                    "evidence": proof,
                }
            ],
        )


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
    domain: str = "timeline",
) -> dict[str, Any]:
    body = {
        "request_key": key,
        "scope": {
            "domain": domain,
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


def test_material_prompt_reads_chapter_number_from_file_name() -> None:
    """任务提示里的章号取文件名，不取带目录的整个路径。"""

    scope = MaterialTaskScope(
        domain="chapter_index",
        chapter_paths=[FIRST, SECOND],
        as_of_chapter=2,
    )
    assert _material_prompt(scope) == "整理章节概览：第 1—2 章"


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


@pytest.mark.parametrize("domain", ["timeline", "clues"])
def test_task_publishes_material_and_completes(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient], domain: str
) -> None:
    root = tmp_path / "雪山旅馆"
    # 先建作品，拿到真实 digest 再写脚本；provider 在受理时才会被调用。
    book = Workspace.create(root)
    book.write(FIRST, "# 雨夜\n\n10月21日深夜，林静在书房。\n", expected_digest=None)
    view = book.read(FIRST)
    start = view.text.index("10月21日深夜")
    record = {
        **(
            {"when": "10月21日深夜", "location": "书房"}
            if domain == "timeline"
            else {
                "clue": "深夜书房有人",
                "planted_chapter": 1,
                "status": "open",
            }
        ),
        "evidence": {
            "path": FIRST,
            "digest": view.digest,
            "start": start,
            "end": start + len("10月21日深夜"),
            "quote": "10月21日深夜",
        },
    }
    provider = ScriptedProvider(
        [
            _tool_turn(ToolCall(id="c1", name="read_file", arguments={"path": FIRST})),
            _tool_turn(
                ToolCall(
                    id="c2",
                    name="publish_material_result",
                    arguments={
                        "domain": domain,
                        "path": FIRST,
                        "digest": view.digest,
                        "outcome": "found",
                        "records": [record],
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
        domain=domain,
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

    materials = client.get(f"/api/file-workspaces/{workspace_id}/story-materials/{domain}").json()
    assert materials["entries"][0]["coverage"] == "found"
    field, value = ("when", "10月21日深夜") if domain == "timeline" else ("clue", "深夜书房有人")
    assert materials["entries"][0]["data"][field] == value
    assert materials["entries"][0]["evidence"][0]["quote"] == "10月21日深夜"
    if domain == "clues":
        entry = materials["entries"][0]
        assert entry["id"] == entry["data"]["clue_id"] == published[0]["clue_ids"][0]
        opened = client.get(
            f"/api/file-workspaces/{workspace_id}/story-materials/clues/entries/{entry['id']}"
        )
        assert opened.status_code == 200 and opened.json()["entry"]["id"] == entry["id"]
    # 受理响应丢失后的同键请求只返回原任务，不再次调用 provider。
    calls = provider.calls
    again = _start_task(
        client,
        workspace_id,
        key="k2",
        chapter_paths=[FIRST],
        observed={FIRST: view.digest},
        domain=domain,
    )
    assert again["turn_id"] == accepted["turn_id"]
    assert provider.calls == calls


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


# -- 统一章节索引（0.8 E） ---------------------------------------------------


def _story_record(
    workspace: Workspace,
    path: str,
    *,
    text: str,
    mode: str = "narration",
    label: str = "",
    needle: str,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "text": text,
        "mode": mode,
        "evidence": [_quote(workspace, path, needle)],
    }
    if label:
        event["story_time"] = {"label": label}
    return {"summary": f"{text}。", "events": [event]}


def test_story_index_publish_writes_projection_and_both_views(tmp_path: Path) -> None:
    workspace, root = _book(tmp_path)
    scope = _story_scope(workspace, (FIRST,), 1)
    result = _publish(
        workspace,
        root,
        scope,
        path=FIRST,
        outcome="found",
        records=[
            _story_record(
                workspace,
                FIRST,
                text="林静声称自己深夜在书房",
                mode="testimony",
                label="10月21日深夜",
                needle="10月21日深夜",
            )
        ],
    )
    assert result["published"] == 1

    projection = StoryIndexStore(root).read_all()[FIRST]
    assert projection.summary == "林静声称自己深夜在书房。"
    assert projection.events[0].mode == "testimony"
    assert projection.events[0].id

    chapters = chapter_view(workspace, root)["chapters"]
    assert chapters[0]["source"] == "story_index"
    assert chapters[0]["status"] == "valid"
    assert chapters[0]["events"][0]["mode_label"] == "证词"

    story = story_view(workspace, root)
    # 证词不自动变成事实，也不自动获得先后关系：单独列在未知组。
    assert [item["text"] for item in story["unknown"]] == ["林静声称自己深夜在书房"]
    assert story["groups"] == []
    assert story["conflicts"] == []


def test_story_index_empty_removes_only_that_chapter(tmp_path: Path) -> None:
    workspace, root = _book(tmp_path)
    scope = _story_scope(workspace, (FIRST, SECOND), 2)
    _publish(
        workspace,
        root,
        scope,
        path=FIRST,
        outcome="found",
        records=[_story_record(workspace, FIRST, text="雨夜的事", needle="10月21日深夜")],
    )
    _publish(
        workspace,
        root,
        scope,
        path=SECOND,
        outcome="found",
        records=[_story_record(workspace, SECOND, text="清晨的事", needle="天亮了")],
    )

    _publish(
        workspace,
        root,
        scope,
        path=FIRST,
        outcome="empty",
        note="重读后确认这一章没有可发布的事件",
    )

    rows = StoryIndexStore(root).read_all()
    assert FIRST not in rows
    assert SECOND in rows
    # 覆盖记录单独更新，不冒充正文事实。
    coverage = author_view(workspace, root, "story_index", as_of_chapter=2)["entries"]
    first = next(item for item in coverage if item["data"]["summary"] == "")
    assert first["coverage"] == "empty"


def test_clue_and_glossary_empty_remove_only_that_chapter(tmp_path: Path) -> None:
    workspace, root = _book(tmp_path)
    clue_scope = _clues_scope(workspace, (FIRST, SECOND), 2)
    _publish(
        workspace,
        root,
        clue_scope,
        path=FIRST,
        outcome="found",
        records=[
            {
                "clue": "书房有第二把椅子",
                "planted_chapter": 1,
                "evidence": _quote(workspace, FIRST, "书房"),
            }
        ],
    )
    _publish(
        workspace,
        root,
        clue_scope,
        path=SECOND,
        outcome="found",
        records=[
            {
                "clue": "书房没有人",
                "planted_chapter": 2,
                "evidence": _quote(workspace, SECOND, "书房没有人"),
            }
        ],
    )
    _publish(
        workspace,
        root,
        clue_scope,
        path=SECOND,
        outcome="empty",
        note="重读后确认这一章没有线索",
    )
    clues = ClueStore(root).entries()
    assert [item.key for item in clues] == ["书房有第二把椅子"]
    assert clues[0].source_chapter == 1

    glossary_scope = freeze_material_scope(
        workspace,
        MaterialTaskScope(domain="glossary", chapter_paths=[FIRST, SECOND], as_of_chapter=2),
    )
    _publish(
        workspace,
        root,
        glossary_scope,
        path=FIRST,
        outcome="found",
        records=[
            {
                "term": "雪山旅馆",
                "kind": "place",
                "first_chapter": 1,
                "evidence": _quote(workspace, FIRST, "书房"),
            }
        ],
    )
    _publish(
        workspace,
        root,
        glossary_scope,
        path=SECOND,
        outcome="found",
        records=[
            {
                "term": "书房",
                "kind": "place",
                "first_chapter": 2,
                "evidence": _quote(workspace, SECOND, "书房没有人"),
            }
        ],
    )
    _publish(
        workspace,
        root,
        glossary_scope,
        path=SECOND,
        outcome="empty",
        note="重读后确认这一章没有新专名",
    )
    terms = GlossaryStore(root).entries()
    assert [term.term for term in terms] == ["雪山旅馆"]


def test_same_clue_uses_the_latest_chapter_contribution(tmp_path: Path) -> None:
    workspace, root = _book(tmp_path)
    scope = _clues_scope(workspace, (FIRST, SECOND), 2)
    proof = _quote(workspace, FIRST, "书房")
    first = _publish(
        workspace,
        root,
        scope,
        path=FIRST,
        outcome="found",
        records=[{"clue": "书房有人", "planted_chapter": 1, "supports": "甲", "evidence": proof}],
    )
    _publish(
        workspace,
        root,
        scope,
        path=SECOND,
        outcome="found",
        records=[
            {
                "clue": "书房有人",
                "clue_id": first["clue_ids"][0],
                "planted_chapter": 2,
                "supports": "乙",
                "evidence": _quote(workspace, SECOND, "书房没有人"),
            }
        ],
    )

    state = build_clues(workspace, ClueStore(root), as_of_chapter=2)
    assert [item.supports for item in state.open] == ["乙"]
    # 两份贡献都留在存储里，作者视图不出现重复行。
    assert len(ClueStore(root).entries()) == 2


def test_timeline_empty_removes_only_that_chapter(tmp_path: Path) -> None:
    workspace, root = _book(tmp_path)
    scope = _timeline_scope(workspace, (FIRST, SECOND), 2)
    _publish(
        workspace,
        root,
        scope,
        path=FIRST,
        outcome="found",
        records=[{"when": "十月二十日深夜", "evidence": _quote(workspace, FIRST, "书房")}],
    )
    _publish(
        workspace,
        root,
        scope,
        path=SECOND,
        outcome="found",
        records=[{"when": "次日清晨", "evidence": _quote(workspace, SECOND, "书房没有人")}],
    )

    _publish(
        workspace,
        root,
        scope,
        path=FIRST,
        outcome="empty",
        note="重读后确认这一章没有时间锚点",
    )

    entries = TimelineStore(root).entries()
    assert [entry.chapter for entry in entries] == [2]


def test_story_index_task_publishes_and_story_events_endpoint_reads_it(
    tmp_path: Path, client_factory: Callable[[ProviderFactory], TestClient]
) -> None:
    root = tmp_path / "雪山旅馆"
    book = Workspace.create(root)
    book.write(FIRST, "# 雨夜\n\n10月21日深夜，林静在书房。\n", expected_digest=None)
    view = book.read(FIRST)
    needle = "10月21日深夜"
    start = view.text.index(needle)
    provider = ScriptedProvider(
        [
            _tool_turn(ToolCall(id="c1", name="read_file", arguments={"path": FIRST})),
            _tool_turn(
                ToolCall(
                    id="c2",
                    name="publish_material_result",
                    arguments={
                        "domain": "story_index",
                        "path": FIRST,
                        "digest": view.digest,
                        "outcome": "found",
                        "records": [
                            {
                                "summary": "林静深夜在书房。",
                                "events": [
                                    {
                                        "text": "林静深夜在书房",
                                        "mode": "narration",
                                        "story_time": {"label": needle},
                                        "evidence": [
                                            {
                                                "path": FIRST,
                                                "digest": view.digest,
                                                "start": start,
                                                "end": start + len(needle),
                                                "quote": needle,
                                            }
                                        ],
                                    }
                                ],
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
        key="story-index",
        chapter_paths=[FIRST],
        observed={FIRST: view.digest},
        domain="story_index",
    )
    events = _wait_finished(client, workspace_id, accepted["conversation_id"])
    published = [event for event in events if event["type"] == "material_target_published"]
    assert published and published[0]["outcome"] == "found"

    chapter_view_response = client.get(
        f"/api/file-workspaces/{workspace_id}/story-events?order=chapter"
    )
    assert chapter_view_response.status_code == 200, chapter_view_response.text
    chapters = chapter_view_response.json()["chapters"]
    assert chapters[0]["source"] == "story_index"
    assert chapters[0]["summary"] == "林静深夜在书房。"
    assert chapters[0]["events"][0]["mode"] == "narration"

    story_response = client.get(f"/api/file-workspaces/{workspace_id}/story-events?order=story")
    assert story_response.status_code == 200, story_response.text
    story = story_response.json()
    assert story["mode"] == "story"
    assert [item["text"] for item in story["unknown"]] == ["林静深夜在书房"]
