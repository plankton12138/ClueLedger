"""章节索引（0.6 V06-A3）。

契约：索引只从当前原文生成，发布前要读完、引文与区间一致、源指纹未变；状态按当前磁盘
算出，过期记录不再注入；索引任务只开放只读工具与保存索引，不能改作品。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.workspace.agent import RunContext, build_tools
from hastings.workspace.chapter_index import (
    RECIPE_VERSION,
    ChapterIndexDraft,
    ChapterIndexEntry,
    ChapterIndexStore,
    IndexEvidence,
    index_injection,
    publish_index,
    validate_draft,
)
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.git import GitRepo
from hastings.workspace.materials import MaterialLedger, SourceRef
from hastings.workspace.service import WriteSession
from hastings.workspace.state import RecentWorkspaces
from tau_agent.tools import AgentTool

_CHAPTER = "正文/0003 书房里的脚印.md"
_TEXT = "# 书房\n\n侦探在书房发现一枚脚印。\n\n林远说他一直在客厅。\n"


def _workspace(tmp_path: Path) -> Workspace:
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    GitRepo(workspace.root).init()
    workspace.write(_CHAPTER, _TEXT, expected_digest=None)
    return workspace


def _evidence(text: str, quote: str, index: int = 1) -> IndexEvidence:
    start = text.index(quote)
    return IndexEvidence(index=index, start=start, end=start + len(quote), quote=quote)


def _draft(workspace: Workspace, **overrides: Any) -> ChapterIndexDraft:
    view = workspace.read(_CHAPTER)
    quote = "侦探在书房发现一枚脚印。"
    values: dict[str, Any] = {
        "source_path": _CHAPTER,
        "source_digest": view.digest,
        "summary": "侦探在书房发现脚印，林远声称一直在客厅。",
        "evidence": (_evidence(view.text, quote),),
        "events": (ChapterIndexEntry(kind="event", text="发现脚印", evidence=(1,)),),
        "characters": (ChapterIndexEntry(kind="character", text="林远", evidence=(1,)),),
        "clues": (ChapterIndexEntry(kind="clue", text="脚印", evidence=(1,)),),
    }
    values.update(overrides)
    return ChapterIndexDraft(**values)


def _read_all(ledger: MaterialLedger, view_text: str, ref: SourceRef) -> None:
    ledger.record_read(ref, length=len(view_text), start=0, end=len(view_text))


def _code(exc_info: pytest.ExceptionInfo[WorkspaceError]) -> str:
    return exc_info.value.code


# -- 校验 -------------------------------------------------------------------


def test_validate_accepts_matching_evidence(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    validate_draft(_draft(workspace), workspace.read(_CHAPTER).text)


def test_validate_rejects_quote_that_does_not_match_range(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    view = workspace.read(_CHAPTER)
    start = view.text.index("侦探在书房发现一枚脚印。")
    draft = _draft(
        workspace,
        evidence=(
            IndexEvidence(index=1, start=start, end=start + 5, quote="侦探在书房发现一枚脚印。"),
        ),
    )
    with pytest.raises(WorkspaceError) as invalid:
        validate_draft(draft, view.text)
    assert _code(invalid) == "CHAPTER_INDEX_EVIDENCE_INVALID"


def test_validate_rejects_out_of_range_and_bad_numbering(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    view = workspace.read(_CHAPTER)
    with pytest.raises(WorkspaceError):
        validate_draft(
            _draft(
                workspace,
                evidence=(IndexEvidence(index=1, start=0, end=len(view.text) + 5, quote="x"),),
            ),
            view.text,
        )
    with pytest.raises(WorkspaceError):
        validate_draft(
            _draft(
                workspace,
                evidence=(IndexEvidence(index=2, start=0, end=2, quote="# "),),
            ),
            view.text,
        )


def test_validate_requires_entry_evidence_and_bounded_summary(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    view = workspace.read(_CHAPTER)
    with pytest.raises(WorkspaceError):
        validate_draft(
            _draft(
                workspace,
                events=(ChapterIndexEntry(kind="event", text="没有证据", evidence=()),),
            ),
            view.text,
        )
    with pytest.raises(WorkspaceError):
        validate_draft(_draft(workspace, summary="长" * 501), view.text)


def test_validate_rejects_unknown_evidence_number(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    view = workspace.read(_CHAPTER)
    with pytest.raises(WorkspaceError):
        validate_draft(
            _draft(
                workspace,
                events=(ChapterIndexEntry(kind="event", text="引用不存在", evidence=(9,)),),
            ),
            view.text,
        )


# -- 发布 -------------------------------------------------------------------


def test_publish_requires_scope_full_read_and_current_digest(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = ChapterIndexStore(tmp_path / "state")
    ledger = MaterialLedger()
    view = workspace.read(_CHAPTER)
    ref = SourceRef(view.path, view.digest)

    with pytest.raises(WorkspaceError) as outside:
        publish_index(
            _draft(workspace),
            workspace=workspace,
            ledger=ledger,
            store=store,
            allowed_paths=("正文/0004 别章.md",),
        )
    assert _code(outside) == "CHAPTER_INDEX_SCOPE_INVALID"

    ledger.record_read(ref, length=len(view.text), start=0, end=10)
    with pytest.raises(WorkspaceError) as partial:
        publish_index(
            _draft(workspace),
            workspace=workspace,
            ledger=ledger,
            store=store,
            allowed_paths=(_CHAPTER,),
        )
    assert _code(partial) == "REFERENCE_NOT_FULLY_READ_CONFLICT"

    _read_all(ledger, view.text, ref)
    # 草稿按改动前的内容生成：发布时源指纹已经不同，不能按旧内容发布。
    stale_draft = _draft(workspace)
    (workspace.root / _CHAPTER).write_text(_TEXT + "\n外部补了一句。\n", encoding="utf-8")
    with pytest.raises(WorkspaceError) as stale:
        publish_index(
            stale_draft,
            workspace=workspace,
            ledger=ledger,
            store=store,
            allowed_paths=(_CHAPTER,),
        )
    assert _code(stale) == "CONTEXT_SOURCE_STALE_CONFLICT"


def test_publish_then_read_back_with_status(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = ChapterIndexStore(tmp_path / "state")
    ledger = MaterialLedger()
    view = workspace.read(_CHAPTER)
    _read_all(ledger, view.text, SourceRef(view.path, view.digest))

    record = publish_index(
        _draft(workspace),
        workspace=workspace,
        ledger=ledger,
        store=store,
        allowed_paths=(_CHAPTER,),
    )
    assert record.recipe_version == RECIPE_VERSION
    assert record.status == "valid"

    stored = store.find(workspace, _CHAPTER)
    assert stored is not None
    assert stored.status == "valid"
    assert stored.summary.startswith("侦探在书房")
    assert [item.summary for item in store.valid(workspace)] == [
        "侦探在书房发现脚印，林远声称一直在客厅。"
    ]


def test_status_tracks_source_and_recipe_changes(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = ChapterIndexStore(tmp_path / "state")
    ledger = MaterialLedger()
    view = workspace.read(_CHAPTER)
    _read_all(ledger, view.text, SourceRef(view.path, view.digest))
    publish_index(
        _draft(workspace),
        workspace=workspace,
        ledger=ledger,
        store=store,
        allowed_paths=(_CHAPTER,),
    )

    (workspace.root / _CHAPTER).write_text(_TEXT + "\n改写。\n", encoding="utf-8")
    assert store.find(workspace, _CHAPTER).status == "stale"  # type: ignore[union-attr]
    assert store.valid(workspace) == ()

    (workspace.root / _CHAPTER).unlink()
    assert store.find(workspace, _CHAPTER).status == "source_missing"  # type: ignore[union-attr]


def test_corrupted_cache_is_ignored(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = ChapterIndexStore(tmp_path / "state")
    store.file.parent.mkdir(parents=True, exist_ok=True)
    store.file.write_text("{ 不是 JSON", encoding="utf-8")
    assert store.records(workspace) == ()

    store.file.write_text(
        json.dumps({"entries": {"x": {"source_path": "正文/1.md"}}}), encoding="utf-8"
    )
    assert store.records(workspace) == ()


def test_injection_stops_at_budget(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = ChapterIndexStore(tmp_path / "state")
    ledger = MaterialLedger()
    view = workspace.read(_CHAPTER)
    _read_all(ledger, view.text, SourceRef(view.path, view.digest))
    publish_index(
        _draft(workspace),
        workspace=workspace,
        ledger=ledger,
        store=store,
        allowed_paths=(_CHAPTER,),
    )
    records = store.valid(workspace)

    assert "书房" in index_injection(records)
    truncated = index_injection(records, budget=20)
    assert "其余章节未装入" in truncated


# -- 工具与接口 -------------------------------------------------------------


def _tools(tmp_path: Path) -> tuple[Workspace, dict[str, AgentTool], ChapterIndexStore]:
    workspace = _workspace(tmp_path)
    store = ChapterIndexStore(tmp_path / "state")
    run = RunContext(
        workspace,
        WriteSession(workspace, instruction="更新索引"),
        author_messages=((1, "更新第三章的索引"),),
    )
    tools = {
        tool.name: tool
        for tool in build_tools(
            workspace, run, mode="index", index_allowed=(_CHAPTER,), index_store=store
        )
    }
    return workspace, tools, store


async def _call(tool: AgentTool, **arguments: Any) -> dict[str, Any]:
    result = await tool.execute("call-1", arguments)
    return json.loads(result.text)


@pytest.mark.anyio
async def test_index_tools_cannot_write_the_manuscript(tmp_path: Path) -> None:
    workspace, tools, store = _tools(tmp_path)

    assert set(tools) == {"list_files", "read_file", "search_text", "save_chapter_index"}
    assert "write_file" not in tools
    assert "edit_file" not in tools
    assert "delete_file" not in tools


@pytest.mark.anyio
async def test_save_chapter_index_publishes_and_reports(tmp_path: Path) -> None:
    workspace, tools, store = _tools(tmp_path)
    view = workspace.read(_CHAPTER)
    first = await _call(tools["read_file"], path=_CHAPTER)
    assert first["truncated"] is False

    quote = "侦探在书房发现一枚脚印。"
    start = view.text.index(quote)
    saved = await _call(
        tools["save_chapter_index"],
        path=_CHAPTER,
        digest=first["digest"],
        summary="侦探发现脚印。",
        evidence=[{"index": 1, "start": start, "end": start + len(quote), "quote": quote}],
        events=[{"text": "发现脚印", "evidence": [1]}],
    )

    assert saved["status"] == "valid"
    assert store.find(workspace, _CHAPTER) is not None


@pytest.mark.anyio
async def test_save_chapter_index_rejects_unread_chapter(tmp_path: Path) -> None:
    _, tools, _ = _tools(tmp_path)
    quote = "侦探在书房发现一枚脚印。"
    with pytest.raises(WorkspaceError) as unread:
        await _call(
            tools["save_chapter_index"],
            path=_CHAPTER,
            digest="0" * 64,
            summary="没有读过",
            evidence=[{"index": 1, "start": 0, "end": len(quote), "quote": quote}],
        )
    assert _code(unread) in {"REFERENCE_NOT_FULLY_READ_CONFLICT", "CONTEXT_SOURCE_STALE_CONFLICT"}


def _client(tmp_path: Path) -> TestClient:
    def no_model() -> object:
        raise AssertionError("索引查询不应调用模型")

    return TestClient(
        create_workspace_app(  # type: ignore[arg-type]
            workspaces=RecentWorkspaces(tmp_path / "app-state"), provider_factory=no_model
        )
    )


def test_chapter_index_endpoint_reports_status(tmp_path: Path) -> None:
    client = _client(tmp_path)
    root = tmp_path / "雪山旅馆"
    workspace_id = client.post(
        "/api/file-workspaces", json={"path": str(root), "create": True}
    ).json()["id"]
    workspace = Workspace.open(root)
    workspace.write(_CHAPTER, _TEXT, expected_digest=None)
    store = ChapterIndexStore(tmp_path / "app-state" / "workspaces" / workspace_id)
    ledger = MaterialLedger()
    view = workspace.read(_CHAPTER)
    _read_all(ledger, view.text, SourceRef(view.path, view.digest))
    publish_index(
        _draft(workspace),
        workspace=workspace,
        ledger=ledger,
        store=store,
        allowed_paths=(_CHAPTER,),
    )

    body = client.get(f"/api/file-workspaces/{workspace_id}/chapter-index").json()

    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["source_path"] == _CHAPTER
    assert item["status"] == "valid"
    assert item["evidence"][0]["quote"] == "侦探在书房发现一枚脚印。"
    assert [entry["text"] for entry in item["events"]] == ["发现脚印"]

    (root / _CHAPTER).write_text(_TEXT + "\n改过。\n", encoding="utf-8")
    stale = client.get(f"/api/file-workspaces/{workspace_id}/chapter-index").json()
    assert stale["items"][0]["status"] == "stale"


def test_index_purpose_requires_targets(tmp_path: Path) -> None:
    client = _client(tmp_path)
    root = tmp_path / "雪山旅馆"
    workspace_id = client.post(
        "/api/file-workspaces", json={"path": str(root), "create": True}
    ).json()["id"]

    empty = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={"text": "更新索引", "request_key": "k1", "purpose": "update_chapter_index"},
    )
    assert empty.status_code == 422

    mixed = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={
            "text": "更新索引",
            "request_key": "k2",
            "index_paths": ["正文/0003 书房里的脚印.md"],
        },
    )
    assert mixed.status_code == 422
