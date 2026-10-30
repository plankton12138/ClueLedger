"""有原文依据的写后检查（0.6 V06-C1）。

契约：报告只基于实际落盘的原文；每条问题都要有路径、版本、区间与引文；没读完不能报
「没有发现问题」；来源变化后报告标为过期，回看不重跑模型；检查工具集不含作品写工具。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.workspace.agent import RunContext, build_tools
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.git import GitRepo
from hastings.workspace.materials import MaterialLedger, SourceRef
from hastings.workspace.review import (
    ReviewDraft,
    ReviewEvidence,
    ReviewFinding,
    ReviewReport,
    ReviewStore,
    publish_report,
    validate_report,
)
from hastings.workspace.service import WriteSession
from hastings.workspace.state import RecentWorkspaces
from tau_agent.tools import AgentTool

_CHAPTER = "正文/0003 书房.md"
_TEXT = "# 书房\n\n侦探在书房发现一枚脚印。\n\n林远说他一直在客厅。\n"
_QUOTE = "侦探在书房发现一枚脚印。"


def _workspace(tmp_path: Path) -> Workspace:
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    GitRepo(workspace.root).init()
    workspace.write(_CHAPTER, _TEXT, expected_digest=None)
    return workspace


def _ledger(workspace: Workspace, *, full: bool = True) -> MaterialLedger:
    ledger = MaterialLedger()
    view = workspace.read(_CHAPTER)
    end = len(view.text) if full else 10
    ledger.record_read(SourceRef(view.path, view.digest), length=len(view.text), start=0, end=end)
    return ledger


def _evidence(workspace: Workspace, quote: str = _QUOTE) -> ReviewEvidence:
    view = workspace.read(_CHAPTER)
    start = view.text.index(quote)
    return ReviewEvidence(
        path=_CHAPTER, digest=view.digest, start=start, end=start + len(quote), quote=quote
    )


def _draft(workspace: Workspace, **overrides: Any) -> ReviewDraft:
    view = workspace.read(_CHAPTER)
    values: dict[str, Any] = {
        "target_paths": (_CHAPTER,),
        "target_digests": ((_CHAPTER, view.digest),),
        "requirement": "检查时间线是否自相矛盾",
        "status": "findings",
        "findings": (
            ReviewFinding(
                kind="continuity",
                certainty="supported",
                description="林远说一直在客厅，但脚印在书房。",
                evidence=(_evidence(workspace),),
                suggestion="补一句他进过书房的解释，或改掉不在场说法。",
            ),
        ),
        "uncovered": (),
    }
    values.update(overrides)
    return ReviewDraft(**values)


def _code(exc_info: pytest.ExceptionInfo[WorkspaceError]) -> str:
    return exc_info.value.code


# -- 校验 -------------------------------------------------------------------


def test_validate_accepts_evidence_matching_source(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    validate_report(
        _draft(workspace), workspace=workspace, ledger=_ledger(workspace), allowed_targets=()
    )


def test_validate_rejects_status_without_matching_findings(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    with pytest.raises(WorkspaceError) as empty_findings:
        validate_report(
            _draft(workspace, findings=()),
            workspace=workspace,
            ledger=_ledger(workspace),
            allowed_targets=(),
        )
    assert _code(empty_findings) == "REVIEW_EVIDENCE_INVALID"

    with pytest.raises(WorkspaceError):
        validate_report(
            _draft(workspace, status="no_findings"),
            workspace=workspace,
            ledger=_ledger(workspace),
            allowed_targets=(),
        )

    with pytest.raises(WorkspaceError):
        validate_report(
            _draft(workspace, status="incomplete", findings=(), uncovered=()),
            workspace=workspace,
            ledger=_ledger(workspace),
            allowed_targets=(),
        )


def test_validate_rejects_quote_that_does_not_match_range(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    view = workspace.read(_CHAPTER)
    start = view.text.index(_QUOTE)
    bad = ReviewEvidence(
        path=_CHAPTER, digest=view.digest, start=start, end=start + 3, quote=_QUOTE
    )
    finding = ReviewFinding(
        kind="other",
        certainty="supported",
        description="区间不对",
        evidence=(bad,),
        suggestion="重新摘录",
    )
    with pytest.raises(WorkspaceError):
        validate_report(
            _draft(workspace, findings=(finding,)),
            workspace=workspace,
            ledger=_ledger(workspace),
            allowed_targets=(),
        )


def test_validate_requires_evidence_from_a_read_file(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    with pytest.raises(WorkspaceError) as unread:
        validate_report(
            _draft(workspace),
            workspace=workspace,
            ledger=MaterialLedger(),
            allowed_targets=(),
        )
    assert _code(unread) == "REVIEW_EVIDENCE_INVALID"


def test_evidence_must_lie_inside_the_provided_range(tmp_path: Path) -> None:
    """读了开头一个字符，不能拿后面一段原文当证据。"""
    workspace = _workspace(tmp_path)
    view = workspace.read(_CHAPTER)
    ledger = MaterialLedger()
    ledger.record_read(SourceRef(view.path, view.digest), length=len(view.text), start=0, end=1)

    with pytest.raises(WorkspaceError) as unprovided:
        validate_report(_draft(workspace), workspace=workspace, ledger=ledger, allowed_targets=())
    assert _code(unprovided) == "REVIEW_EVIDENCE_INVALID"
    assert unprovided.value.details["path"] == _CHAPTER


def test_evidence_span_across_a_read_gap_is_rejected(tmp_path: Path) -> None:
    """区间分两段读过但中间缺口，证据横跨缺口仍不成立。"""
    workspace = _workspace(tmp_path)
    view = workspace.read(_CHAPTER)
    quote_start = view.text.index(_QUOTE)
    ledger = MaterialLedger()
    ref = SourceRef(view.path, view.digest)
    ledger.record_read(ref, length=len(view.text), start=0, end=quote_start + 2)
    ledger.record_read(ref, length=len(view.text), start=quote_start + 6, end=len(view.text))

    with pytest.raises(WorkspaceError):
        validate_report(_draft(workspace), workspace=workspace, ledger=ledger, allowed_targets=())


def test_evidence_inside_a_partial_read_passes(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    view = workspace.read(_CHAPTER)
    quote_start = view.text.index(_QUOTE)
    ledger = MaterialLedger()
    ledger.record_read(
        SourceRef(view.path, view.digest),
        length=len(view.text),
        start=0,
        end=quote_start + len(_QUOTE),
    )

    validate_report(_draft(workspace), workspace=workspace, ledger=ledger, allowed_targets=())


def test_no_findings_requires_full_coverage(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    with pytest.raises(WorkspaceError) as partial:
        validate_report(
            _draft(workspace, status="no_findings", findings=()),
            workspace=workspace,
            ledger=_ledger(workspace, full=False),
            allowed_targets=(),
        )
    assert _code(partial) == "REVIEW_EVIDENCE_INVALID"

    validate_report(
        _draft(workspace, status="no_findings", findings=()),
        workspace=workspace,
        ledger=_ledger(workspace),
        allowed_targets=(),
    )


# -- 发布与回看 -------------------------------------------------------------


def test_publish_reports_and_tracks_freshness(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = ReviewStore(tmp_path / "state")
    report = publish_report(
        _draft(workspace),
        workspace=workspace,
        ledger=_ledger(workspace),
        store=store,
        turn_id="t1",
    )
    assert report.status == "findings"
    assert report.report_id.startswith("r_")

    latest = store.reports(workspace)[0]
    assert latest.freshness == "valid"
    assert latest.findings[0].evidence[0].quote == _QUOTE

    (workspace.root / _CHAPTER).write_text(_TEXT + "\n外部改动。\n", encoding="utf-8")
    assert store.reports(workspace)[0].freshness == "stale"

    (workspace.root / _CHAPTER).unlink()
    assert store.reports(workspace)[0].freshness == "source_missing"


def test_publish_rejects_stale_target_digest(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = ReviewStore(tmp_path / "state")
    draft = _draft(workspace)
    (workspace.root / _CHAPTER).write_text(_TEXT + "\n改动。\n", encoding="utf-8")

    with pytest.raises(WorkspaceError) as stale:
        publish_report(
            draft, workspace=workspace, ledger=_ledger(workspace), store=store, turn_id="t1"
        )
    assert _code(stale) in {"REVIEW_SOURCE_STALE_CONFLICT", "REVIEW_EVIDENCE_INVALID"}


def test_reports_survive_corrupted_cache(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = ReviewStore(tmp_path / "state")
    store.file.parent.mkdir(parents=True, exist_ok=True)
    store.file.write_text("{ 不是 JSON", encoding="utf-8")
    assert store.reports(workspace) == ()


# -- 工具 -------------------------------------------------------------------


def _tools(tmp_path: Path) -> tuple[Workspace, dict[str, AgentTool], ReviewStore, RunContext]:
    workspace = _workspace(tmp_path)
    store = ReviewStore(tmp_path / "state")
    run = RunContext(
        workspace,
        WriteSession(workspace, instruction="检查第三章"),
        author_messages=((1, "检查第三章，改完检查时间线"),),
        turn_id="t1",
    )
    tools = {
        tool.name: tool
        for tool in build_tools(
            workspace, run, mode="review", review_targets=(_CHAPTER,), review_store=store
        )
    }
    return workspace, tools, store, run


async def _call(tool: AgentTool, **arguments: Any) -> dict[str, Any]:
    result = await tool.execute("call-1", arguments)
    return json.loads(result.text)


@pytest.mark.anyio
async def test_review_tools_cannot_write_the_manuscript(tmp_path: Path) -> None:
    _, tools, _, _ = _tools(tmp_path)
    assert set(tools) == {"list_files", "read_file", "search_text", "save_review_report"}


@pytest.mark.anyio
async def test_save_review_report_publishes(tmp_path: Path) -> None:
    workspace, tools, store, _ = _tools(tmp_path)
    first = await _call(tools["read_file"], path=_CHAPTER)
    quote = _QUOTE
    view = workspace.read(_CHAPTER)
    start = view.text.index(quote)

    saved = await _call(
        tools["save_review_report"],
        target_paths=[_CHAPTER],
        requirement="检查时间线",
        status="findings",
        findings=[
            {
                "kind": "continuity",
                "certainty": "supported",
                "description": "不在场说法与脚印冲突。",
                "suggestion": "补一句解释。",
                "evidence": [
                    {
                        "path": _CHAPTER,
                        "digest": first["digest"],
                        "start": start,
                        "end": start + len(quote),
                        "quote": quote,
                    }
                ],
            }
        ],
    )

    assert saved["status"] == "findings"
    assert store.reports(workspace)[0].report_id == saved["report_id"]


@pytest.mark.anyio
async def test_review_scope_error_lists_allowed_targets_before_reading(tmp_path: Path) -> None:
    workspace, tools, store, _ = _tools(tmp_path)
    with pytest.raises(WorkspaceError) as rejected:
        await _call(
            tools["save_review_report"],
            target_paths=["正文/9999 不存在.md"],
            requirement="检查时间线",
            status="no_findings",
        )
    assert rejected.value.code == "REVIEW_SCOPE_INVALID"
    assert _CHAPTER in rejected.value.message
    assert rejected.value.details["allowed"] == [_CHAPTER]
    assert store.reports(workspace) == ()


@pytest.mark.anyio
async def test_review_reference_can_supply_evidence_without_becoming_a_target(
    tmp_path: Path,
) -> None:
    workspace, tools, store, _ = _tools(tmp_path)
    reference = "正文/0001 雨夜.md"
    text = "林远在书房。"
    workspace.write(reference, text, expected_digest=None)
    source = await _call(tools["read_file"], path=reference)
    await _call(
        tools["save_review_report"],
        target_paths=[_CHAPTER],
        requirement="检查时间线",
        status="findings",
        findings=[
            {
                "kind": "continuity",
                "certainty": "suspected",
                "description": "需要核对林远所在位置。",
                "suggestion": "核对时间。",
                "evidence": [
                    {
                        "path": reference,
                        "digest": source["digest"],
                        "start": 0,
                        "end": len(text),
                        "quote": text,
                    }
                ],
            }
        ],
    )
    report = store.reports(workspace)[0]
    assert report.target_paths == (_CHAPTER,)
    assert report.findings[0].evidence[0].path == reference


@pytest.mark.anyio
async def test_request_review_requires_author_quote(tmp_path: Path) -> None:
    workspace, tools, _, run = _tools(tmp_path)
    write_tools = {tool.name: tool for tool in build_tools(workspace, run, mode="write")}
    recorded = await _call(
        write_tools["request_review"],
        requirement="检查时间线",
        authorization="改完检查时间线",
        paths=[_CHAPTER],
    )
    assert recorded["targets"] == [_CHAPTER]
    assert run.review_request is not None

    with pytest.raises(WorkspaceError) as invented:
        await _call(
            write_tools["request_review"],
            requirement="检查时间线",
            authorization="模型自己要求检查",
        )
    assert _code(invented) == "REFERENCE_EVIDENCE_INVALID"


# -- 接口 -------------------------------------------------------------------


def _client(tmp_path: Path) -> TestClient:
    def no_model() -> object:
        raise AssertionError("报告回看不应调用模型")

    return TestClient(
        create_workspace_app(  # type: ignore[arg-type]
            workspaces=RecentWorkspaces(tmp_path / "app-state"), provider_factory=no_model
        )
    )


def test_review_endpoint_lists_reports_with_freshness(tmp_path: Path) -> None:
    client = _client(tmp_path)
    root = tmp_path / "雪山旅馆"
    workspace_id = client.post(
        "/api/file-workspaces", json={"path": str(root), "create": True}
    ).json()["id"]
    workspace = Workspace.open(root)
    workspace.write(_CHAPTER, _TEXT, expected_digest=None)
    store = ReviewStore(tmp_path / "app-state" / "workspaces" / workspace_id)
    publish_report(
        _draft(workspace),
        workspace=workspace,
        ledger=_ledger(workspace),
        store=store,
        turn_id="t1",
    )

    body = client.get(f"/api/file-workspaces/{workspace_id}/reviews").json()
    assert len(body["items"]) == 1
    assert body["items"][0]["freshness"] == "valid"
    assert body["items"][0]["target_digests"] == [[_CHAPTER, workspace.read(_CHAPTER).digest]]
    assert body["items"][0]["findings"][0]["evidence"][0]["quote"] == _QUOTE

    (root / _CHAPTER).write_text(_TEXT + "\n改过。\n", encoding="utf-8")
    stale = client.get(f"/api/file-workspaces/{workspace_id}/reviews").json()
    assert stale["items"][0]["freshness"] == "stale"
    assert stale["items"][0]["target_digests"] == body["items"][0]["target_digests"]


def test_review_purpose_requires_targets(tmp_path: Path) -> None:
    client = _client(tmp_path)
    root = tmp_path / "雪山旅馆"
    workspace_id = client.post(
        "/api/file-workspaces", json={"path": str(root), "create": True}
    ).json()["id"]

    empty = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={"text": "检查第三章", "request_key": "k1", "purpose": "review"},
    )
    assert empty.status_code == 422

    mixed = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={
            "text": "检查第三章",
            "request_key": "k2",
            "review_paths": ["正文/0003 书房.md"],
        },
    )
    assert mixed.status_code == 422


def test_report_serialization_round_trip(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = ReviewStore(tmp_path / "state")
    report = publish_report(
        _draft(workspace),
        workspace=workspace,
        ledger=_ledger(workspace),
        store=store,
        turn_id="t1",
    )

    restored = ReviewReport.from_raw(report.to_dict())

    assert restored is not None
    assert restored.report_id == report.report_id
    assert restored.findings[0].evidence[0].path == _CHAPTER
