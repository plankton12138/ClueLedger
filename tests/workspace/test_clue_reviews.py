"""1.0 S5：有原文依据的线索推理核对；真实文件与账本，不调用模型。"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.agent import RunContext, build_tools
from hastings.workspace.clue_designs import (
    create_clue_identity,
    link_clue_design,
    save_clue_inference,
)
from hastings.workspace.clue_models import FileSource, InferenceStatement
from hastings.workspace.clue_reviews import (
    ClueReviewDraft,
    ClueReviewFinding,
    clue_review_report,
    freeze_clue_review,
    publish_clue_review,
)
from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.materials import MaterialLedger, SourceRef
from hastings.workspace.review import ReviewEvidence, ReviewStore
from hastings.workspace.service import ProviderFactory, WriteSession
from hastings.workspace.source_locations import resolve_source_location
from hastings.workspace.state import RecentWorkspaces, conversation_root
from tau_agent.messages import ToolCall

FIRST = "正文/0001 遗书.md"
SECOND = "正文/0002 照片.md"
THIRD = "正文/0003 书房.md"
PLAN = "设定集/线索.md"
QUOTE = "林岚说她从未见过这封信。"


@pytest.fixture
def book(tmp_path: Path) -> tuple[Workspace, ClueLedgerStore, dict[str, str]]:
    workspace = Workspace.create(tmp_path / "遗书")
    workspace.write(FIRST, "# 遗书\n\n信封背面有字。\n", expected_digest=None)
    workspace.write(
        SECOND,
        f"# 照片\n\n照片证明林岚看过背面。\n\n{QUOTE}\n",
        expected_digest=None,
    )
    workspace.write(THIRD, "# 书房\n\n林岚在书房里核对账目。\n", expected_digest=None)
    workspace.write(PLAN, "# 线索\n\n原计划：第三章让照片出现。\n", expected_digest=None)
    store = ClueLedgerStore(tmp_path / "state")
    first = _identity(store, "遗书背面的字", "create-first")
    second = _identity(store, "公开的照片", "create-second")
    quote = "原计划：第三章让照片出现。"
    view = workspace.read(PLAN)
    linked = link_clue_design(
        workspace,
        store,
        clue_id=first,
        source=FileSource(path=PLAN, digest=view.digest),
        start=view.text.index(quote),
        end=view.text.index(quote) + len(quote),
        quote=quote,
        relation="",
        request_key="link-plan",
        expected_revision=store.read().revision,
        created_by="author",
    )
    inference = save_clue_inference(
        store,
        clue_ids=[first, second],
        goal="林岚提前接触过遗书原件",
        intent="deduce",
        premises=[
            InferenceStatement(
                text="林岚能说出背面内容",
                clue_ids=[first],
                design_link_ids=[str(linked["link_id"])],
            )
        ],
        alternatives=[InferenceStatement(text="照片本来就在公开档案里", clue_ids=[second])],
        design_link_ids=[str(linked["link_id"])],
        request_key="save-inference",
        expected_revision=store.read().revision,
        created_by="author",
    )
    return workspace, store, _inference(inference)


def _inference(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "inference_id": str(result["inference_id"]),
        "inference_revision": str(result["inference_revision"]),
        "clue_ids": [str(item) for item in result["clue_ids"]],
    }


def _identity(store: ClueLedgerStore, title: str, key: str) -> str:
    result = create_clue_identity(
        store, title=title, request_key=key, expected_revision=store.read().revision
    )
    return str(result["clue_id"])


def _ledger(workspace: Workspace, *, full: bool = True) -> MaterialLedger:
    ledger = MaterialLedger()
    view = workspace.read(SECOND)
    end = len(view.text) if full else 10
    ledger.record_read(SourceRef(view.path, view.digest), length=len(view.text), start=0, end=end)
    return ledger


def _scope(book: tuple[Workspace, ClueLedgerStore, dict[str, Any]], **overrides: Any):
    workspace, store, inference = book
    values: dict[str, Any] = {
        "inference_id": inference["inference_id"],
        "inference_revision": inference["inference_revision"],
        "clue_ids": [inference["clue_ids"][0], inference["clue_ids"][1]],
        "as_of_chapter": 2,
        "chapter_paths": [SECOND],
    }
    values.update(overrides)
    return freeze_clue_review(workspace, store, **values)


def _finding(workspace: Workspace, quote: str = QUOTE) -> ClueReviewFinding:
    view = workspace.read(SECOND)
    start = view.text.index(quote)
    return ClueReviewFinding(
        kind="counter_evidence",
        certainty="supported",
        description="林岚否认见过信，但照片显示她看过背面。",
        suggestion="补一句她当时是否记得这封信。",
        evidence=(
            ReviewEvidence(
                path=SECOND, digest=view.digest, start=start, end=start + len(quote), quote=quote
            ),
        ),
    )


def _publish(book, ledger: MaterialLedger, **overrides: Any):
    workspace, store, _ = book
    scope = _scope(book)
    values: dict[str, Any] = {
        "status": "findings",
        "findings": (_finding(workspace),),
        "uncovered": (),
    }
    values.update(overrides)
    return publish_clue_review(
        scope,
        ClueReviewDraft(**values),
        requirement="核对这组线索是否足以认定林岚提前接触过原件。",
        workspace=workspace,
        ledger=ledger,
        store=store,
        turn_id="t1",
    )


def _code(info: pytest.ExceptionInfo[WorkspaceError]) -> str:
    return info.value.code


# -- 冻结范围 ---------------------------------------------------------------


def test_freeze_requires_saved_inference_and_matching_revision(book):
    workspace, store, inference = book
    with pytest.raises(WorkspaceError) as missing:
        freeze_clue_review(
            workspace,
            store,
            inference_id="inference_missing",
            inference_revision="rev",
            clue_ids=inference["clue_ids"],
            as_of_chapter=2,
            chapter_paths=[SECOND],
        )
    assert _code(missing) == "CLUE_REVIEW_NOT_FOUND"

    with pytest.raises(WorkspaceError) as stale:
        freeze_clue_review(
            workspace,
            store,
            inference_id=inference["inference_id"],
            inference_revision="rev_stale",
            clue_ids=inference["clue_ids"],
            as_of_chapter=2,
            chapter_paths=[SECOND],
        )
    assert _code(stale) == "CLUE_REVISION_CONFLICT"

    with pytest.raises(WorkspaceError) as narrowed:
        freeze_clue_review(
            workspace,
            store,
            inference_id=inference["inference_id"],
            inference_revision=inference["inference_revision"],
            clue_ids=[inference["clue_ids"][0]],
            as_of_chapter=2,
            chapter_paths=[SECOND],
        )
    assert _code(narrowed) == "CLUE_SCOPE_INVALID"


def test_freeze_rejects_later_chapters_empty_and_too_many_targets(book):
    workspace, store, inference = book
    with pytest.raises(WorkspaceError):
        freeze_clue_review(
            workspace,
            store,
            inference_id=inference["inference_id"],
            inference_revision=inference["inference_revision"],
            clue_ids=inference["clue_ids"],
            as_of_chapter=2,
            chapter_paths=[THIRD],
        )
    deduped = freeze_clue_review(
        workspace,
        store,
        inference_id=inference["inference_id"],
        inference_revision=inference["inference_revision"],
        clue_ids=inference["clue_ids"],
        as_of_chapter=2,
        chapter_paths=[SECOND, SECOND],
    )
    assert deduped.target_paths == (SECOND,)
    with pytest.raises(WorkspaceError) as empty:
        freeze_clue_review(
            workspace,
            store,
            inference_id=inference["inference_id"],
            inference_revision=inference["inference_revision"],
            clue_ids=inference["clue_ids"],
            as_of_chapter=2,
            chapter_paths=[],
        )
    assert _code(empty) == "CLUE_REVIEW_TARGET_REQUIRED"
    with pytest.raises(WorkspaceError) as non_chapter:
        freeze_clue_review(
            workspace,
            store,
            inference_id=inference["inference_id"],
            inference_revision=inference["inference_revision"],
            clue_ids=inference["clue_ids"],
            as_of_chapter=2,
            chapter_paths=[PLAN],
        )
    assert _code(non_chapter) == "CLUE_SCOPE_INVALID"


def test_frozen_scope_keeps_goal_premises_and_revisions(book):
    workspace, store, inference = book
    scope = _scope(book)
    assert scope.goal == "林岚提前接触过遗书原件"
    assert scope.intent == "deduce"
    assert scope.clue_ids == (inference["clue_ids"][0], inference["clue_ids"][1])
    assert [row[1] for row in scope.clue_revisions] == [
        scope.clue_revisions[0][1],
        scope.clue_revisions[1][1],
    ]
    assert scope.premises == ("林岚能说出背面内容",)
    assert scope.alternatives == ("照片本来就在公开档案里",)
    assert scope.target_digests == ((SECOND, workspace.read(SECOND).digest),)
    # 目录摘要只证明目标集合，不等于已读全文。
    assert [row[0] for row in scope.directory] == [FIRST, SECOND]
    assert scope.design_links and scope.design_links[0][0].startswith("link_")


# -- 报告校验 ---------------------------------------------------------------


def test_report_with_evidence_is_published_and_readable(book):
    workspace, store, _ = book
    report = _publish(book, _ledger(workspace))
    assert report.status == "findings"
    assert report.findings[0]["kind"] == "counter_evidence"
    assert report.freshness == "valid"
    assert report.coverage[0]["complete"] is True
    view = clue_review_report(workspace, store, report.report_id)
    assert view.report_id == report.report_id
    assert view.inference_id == report.inference_id
    assert view.findings[0]["evidence"][0]["quote"] == QUOTE
    evidence_id = str(view.findings[0]["evidence"][0]["evidence_id"])
    location = resolve_source_location(workspace, store.root, evidence_id, as_of_chapter=2)
    assert location.freshness == "valid"
    assert location.quote == QUOTE
    ledger = store.read().ledger
    assert ledger is not None and report.report_id in ledger.reviews


def test_no_findings_requires_reading_the_whole_target(book):
    workspace, store, _ = book
    partial = _ledger(workspace, full=False)
    with pytest.raises(WorkspaceError) as blocked:
        _publish(book, partial, status="no_findings", findings=())
    assert _code(blocked) == "REVIEW_EVIDENCE_INVALID"
    full = _ledger(workspace)
    report = _publish(book, full, status="no_findings", findings=())
    assert report.status == "no_findings"
    assert report.freshness == "valid"


def test_forged_quote_and_unread_range_are_rejected(book):
    workspace, store, _ = book
    view = workspace.read(SECOND)
    forged = ClueReviewFinding(
        kind="inference_gap",
        certainty="supported",
        description="伪造引文",
        suggestion="无",
        evidence=(
            ReviewEvidence(
                path=SECOND,
                digest=view.digest,
                start=0,
                end=4,
                quote="不存在的原文",
            ),
        ),
    )
    with pytest.raises(WorkspaceError) as bad_quote:
        _publish(book, _ledger(workspace), findings=(forged,))
    assert _code(bad_quote) == "REVIEW_EVIDENCE_INVALID"

    unread = _finding(workspace)
    with pytest.raises(WorkspaceError) as not_read:
        _publish(book, _ledger(workspace, full=False), findings=(unread,))
    assert _code(not_read) == "REVIEW_EVIDENCE_INVALID"

    unknown_kind = ClueReviewFinding(
        kind="continuity",
        certainty="supported",
        description="不支持的问题类别",
        suggestion="无",
        evidence=unread.evidence,
    )
    with pytest.raises(WorkspaceError):
        _publish(book, _ledger(workspace), findings=(unknown_kind,))


def test_status_and_findings_must_agree(book):
    workspace, _, _ = book
    with pytest.raises(WorkspaceError):
        _publish(book, _ledger(workspace), findings=())
    with pytest.raises(WorkspaceError):
        _publish(
            book,
            _ledger(workspace),
            status="no_findings",
            findings=(_finding(workspace),),
        )
    with pytest.raises(WorkspaceError):
        _publish(book, _ledger(workspace), status="incomplete", findings=(), uncovered=())
    incomplete = _publish(
        book,
        _ledger(workspace, full=False),
        status="incomplete",
        findings=(),
        uncovered=("第 2 章后半段没有读完",),
    )
    assert incomplete.status == "incomplete"
    assert incomplete.uncovered == ["第 2 章后半段没有读完"]


# -- 失效与章界 -------------------------------------------------------------


def test_plan_file_cannot_be_used_as_review_evidence(book):
    """设定与计划是作者侧信息：核对报告的依据只能来自读者已见的正文。"""
    workspace, _, _ = book
    plan = workspace.read(PLAN)
    quote = "原计划：第三章让照片出现。"
    start = plan.text.index(quote)
    ledger = _ledger(workspace)
    ledger.record_read(
        SourceRef(PLAN, plan.digest), length=len(plan.text), start=0, end=len(plan.text)
    )
    with pytest.raises(WorkspaceError) as rejected:
        _publish(
            book,
            ledger,
            findings=(
                ClueReviewFinding(
                    kind="premise_undisclosed",
                    certainty="supported",
                    description="计划写了照片出现，但正文还没交代。",
                    suggestion="把计划里的安排先写进正文。",
                    evidence=(
                        ReviewEvidence(
                            path=PLAN,
                            digest=plan.digest,
                            start=start,
                            end=start + len(quote),
                            quote=quote,
                        ),
                    ),
                ),
            ),
        )
    assert _code(rejected) == "REVIEW_EVIDENCE_INVALID"
    assert "正文" in rejected.value.why


def test_changed_chapter_keeps_old_report_and_marks_stale(book):
    workspace, store, _ = book
    report = _publish(book, _ledger(workspace))
    before = workspace.read(SECOND)
    workspace.write(SECOND, before.text + "\n改稿。\n", expected_digest=before.digest)
    stale = clue_review_report(workspace, store, report.report_id)
    assert stale.freshness == "stale"
    assert stale.findings[0]["evidence"][0]["quote"] == QUOTE
    assert any("正文" in reason for reason in stale.reasons)


def test_report_is_marked_changed_when_inference_revision_moves(book):
    workspace, store, inference = book
    report = _publish(book, _ledger(workspace))
    save_clue_inference(
        store,
        clue_ids=inference["clue_ids"],
        goal="林岚提前接触过遗书原件",
        intent="suspect",
        premises=[],
        alternatives=[],
        design_link_ids=[],
        request_key="update-inference",
        expected_revision=store.read().revision,
        inference_id=inference["inference_id"],
        inference_revision=inference["inference_revision"],
        created_by="author",
    )
    changed = clue_review_report(workspace, store, report.report_id)
    assert changed.freshness == "target_changed"
    assert changed.status == "findings"
    assert any("目标" in reason for reason in changed.reasons)


def test_report_goes_stale_when_targets_change_within_cutoff(book):
    """TM04：截止章内新增或删除正文后，旧报告不能继续宣称覆盖了完整清单。"""
    workspace, store, _ = book
    report = _publish(book, _ledger(workspace))
    assert report.freshness == "valid"

    workspace.write("正文/0002 附录.md", "# 附录\n\n照片背面还有一行日期。\n", expected_digest=None)
    added = clue_review_report(workspace, store, report.report_id)
    assert added.freshness == "stale"
    assert any("正文清单" in reason for reason in added.reasons)
    assert any("正文/0002 附录.md" in reason for reason in added.reasons)
    # 旧结论与依据仍按保存值返回，不因清单变化被清空。
    assert added.findings[0]["evidence"][0]["quote"] == QUOTE

    (workspace.root / "正文/0002 附录.md").unlink()
    restored = clue_review_report(workspace, store, report.report_id)
    assert restored.freshness == "valid"

    # 截止章之后新增的章节不改变这份有界报告的范围，只体现在 later_chapters 上。
    workspace.write("正文/0004 尾聲.md", "# 尾声\n\n多年以后。\n", expected_digest=None)
    bounded = clue_review_report(workspace, store, report.report_id)
    assert bounded.freshness == "valid"
    assert bounded.later_chapters == 2

    (workspace.root / FIRST).unlink()
    removed = clue_review_report(workspace, store, report.report_id)
    assert removed.freshness == "stale"
    assert any("正文清单" in reason for reason in removed.reasons)
    assert any(FIRST in reason for reason in removed.reasons)


def test_report_does_not_claim_later_chapters(book):
    workspace, store, _ = book
    report = _publish(book, _ledger(workspace))
    assert report.later_chapters == 1
    narrowed = clue_review_report(workspace, store, report.report_id, as_of_chapter=1)
    assert narrowed.truncated is True
    assert narrowed.findings == []
    assert narrowed.target_paths == []


def test_report_survives_generic_review_trimming(book):
    workspace, store, _ = book
    report = _publish(book, _ledger(workspace))
    from hastings.workspace.review import ReviewReport, ReviewStore

    generic = ReviewStore(store.root)
    for index in range(60):
        generic.publish(
            ReviewReport(
                report_id=f"r_{index}",
                turn_id=f"t_{index}",
                target_paths=(SECOND,),
                target_digests=((SECOND, workspace.read(SECOND).digest),),
                requirement="通用检查",
                status="no_findings",
                findings=(),
                uncovered=(),
                created_at="2026-10-27T00:00:00+08:00",
            )
        )
    assert len(generic.reports(workspace)) == 50
    assert clue_review_report(workspace, store, report.report_id).report_id == report.report_id


def test_unknown_report_is_not_found(book):
    workspace, store, _ = book
    with pytest.raises(WorkspaceError) as missing:
        clue_review_report(workspace, store, "cr_missing")
    assert _code(missing) == "CLUE_REVIEW_NOT_FOUND"


# -- 接口 -------------------------------------------------------------------


def _client(tmp_path: Path) -> tuple[TestClient, str]:
    registry = RecentWorkspaces(tmp_path / "app-state")

    def no_model() -> object:
        raise AssertionError("核对回看不应调用模型")

    client = TestClient(
        create_workspace_app(workspaces=registry, provider_factory=no_model)  # type: ignore[arg-type]
    )
    created = client.post(
        "/api/file-workspaces", json={"path": str(tmp_path / "书"), "create": True}
    )
    assert created.status_code == 200, created.text
    return client, str(created.json()["id"])


def _api_book(tmp_path: Path, client: TestClient, workspace_id: str):
    """接口测试的数据放在应用状态目录里，报告与请求读写同一份账本。"""
    workspace = Workspace(tmp_path / "书")
    workspace.write(FIRST, "# 遗书\n\n信封背面有字。\n", expected_digest=None)
    workspace.write(SECOND, f"# 照片\n\n照片证明林岚看过背面。\n\n{QUOTE}\n", expected_digest=None)
    workspace.write(THIRD, "# 书房\n\n林岚在书房里核对账目。\n", expected_digest=None)
    workspace.write(PLAN, "# 线索\n\n原计划：第三章让照片出现。\n", expected_digest=None)
    store = ClueLedgerStore(conversation_root(tmp_path / "app-state", workspace_id))
    first = _identity(store, "遗书背面的字", "http-first")
    second = _identity(store, "公开的照片", "http-second")
    quote = "原计划：第三章让照片出现。"
    view = workspace.read(PLAN)
    link_clue_design(
        workspace,
        store,
        clue_id=first,
        source=FileSource(path=PLAN, digest=view.digest),
        start=view.text.index(quote),
        end=view.text.index(quote) + len(quote),
        quote=quote,
        relation="",
        request_key="http-link",
        expected_revision=store.read().revision,
        created_by="author",
    )
    inference = save_clue_inference(
        store,
        clue_ids=[first, second],
        goal="林岚提前接触过遗书原件",
        intent="deduce",
        premises=[InferenceStatement(text="林岚能说出背面内容", clue_ids=[first])],
        alternatives=[],
        design_link_ids=[],
        request_key="http-inference",
        expected_revision=store.read().revision,
        created_by="author",
    )
    return workspace, store, _inference(inference)


def test_http_review_scope_and_report(tmp_path: Path):
    client, workspace_id = _client(tmp_path)
    workspace, store, inference = _api_book(tmp_path, client, workspace_id)
    base = f"/api/file-workspaces/{workspace_id}"
    wrong_purpose = client.post(
        f"{base}/conversations",
        json={
            "text": "核对线索",
            "request_key": "chat-with-scope",
            "clue_review": {
                "inference_id": inference["inference_id"],
                "inference_revision": inference["inference_revision"],
                "clue_ids": inference["clue_ids"],
                "as_of_chapter": 2,
            },
        },
    )
    assert wrong_purpose.status_code == 422

    mismatched = client.post(
        f"{base}/conversations",
        json={
            "text": "核对线索",
            "request_key": "review-as-of-mismatch",
            "purpose": "review",
            "review_paths": [SECOND],
            "context": {"state_chapter": 3},
            "clue_review": {
                "inference_id": inference["inference_id"],
                "inference_revision": inference["inference_revision"],
                "clue_ids": inference["clue_ids"],
                "as_of_chapter": 2,
            },
        },
    )
    assert mismatched.status_code == 400
    assert mismatched.json()["error"]["code"] == "CLUE_SCOPE_INVALID"

    scope = freeze_clue_review(
        workspace,
        store,
        inference_id=inference["inference_id"],
        inference_revision=inference["inference_revision"],
        clue_ids=inference["clue_ids"],
        as_of_chapter=2,
        chapter_paths=[SECOND],
    )
    report = publish_clue_review(
        scope,
        ClueReviewDraft(status="findings", findings=(_finding(workspace),)),
        requirement="核对这组线索。",
        workspace=workspace,
        ledger=_ledger(workspace),
        store=store,
        turn_id="http-turn",
    )
    read = client.get(f"{base}/clue-reviews/{report.report_id}", params={"as_of_chapter": 2})
    assert read.status_code == 200, read.text
    body = read.json()
    assert body["report_id"] == report.report_id
    assert body["findings"][0]["evidence"][0]["quote"] == QUOTE
    assert body["freshness"] == "valid"
    missing = client.get(f"{base}/clue-reviews/cr_missing")
    assert missing.status_code == 404


# -- 工具 -------------------------------------------------------------------


@pytest.mark.anyio
async def test_review_tools_publish_clue_report_and_block_other_writes(book):
    workspace, store, _ = book
    scope = _scope(book)
    events: list[dict[str, Any]] = []
    run = RunContext(
        workspace,
        WriteSession(workspace, instruction="核对推理"),
        turn_id="t-tool",
        state_root=store.root,
        emit=lambda kind, payload: events.append({"kind": kind, **payload}),
    )
    run.materials.record_read(
        SourceRef(SECOND, workspace.read(SECOND).digest),
        length=len(workspace.read(SECOND).text),
        start=0,
        end=len(workspace.read(SECOND).text),
    )
    tools = {
        tool.name: tool
        for tool in build_tools(
            workspace,
            run,
            mode="review",
            review_targets=scope.target_paths,
            review_store=ReviewStore(store.root),
            clue_review=scope.to_dict(),
        )
    }
    assert "write_file" not in tools
    assert "link_clue_design" not in tools
    assert "save_clue_inference" not in tools
    report_tool = tools["save_review_report"]
    properties = report_tool.parameters["properties"]
    assert "clue_analysis" in properties
    assert "inference_gap" in properties["findings"]["items"]["properties"]["kind"]["enum"]

    view = workspace.read(SECOND)
    start = view.text.index(QUOTE)
    base = {
        "target_paths": [SECOND],
        "requirement": "核对这组线索。",
        "status": "findings",
        "findings": [
            {
                "kind": "counter_evidence",
                "certainty": "supported",
                "description": "林岚否认见过信，但照片显示她看过背面。",
                "suggestion": "补一句她是否记得这封信。",
                "clue_ids": [scope.clue_ids[0]],
                "evidence": [
                    {
                        "path": SECOND,
                        "digest": view.digest,
                        "start": start,
                        "end": start + len(QUOTE),
                        "quote": QUOTE,
                    }
                ],
            }
        ],
    }
    with pytest.raises(WorkspaceError) as missing:
        await report_tool.execute("call-1", base)
    assert missing.value.code == "TOOL_INPUT_INVALID"

    # 填了不一致的目标标识要拒绝；漏填时按受理时冻结的目标补齐。
    with pytest.raises(WorkspaceError) as wrong:
        await report_tool.execute(
            "call-2a",
            {
                **base,
                "clue_analysis": {"inference_id": "inference_other", "goal_snapshot": scope.goal},
            },
        )
    assert wrong.value.code == "TOOL_INPUT_INVALID"
    result = await report_tool.execute(
        "call-2",
        {**base, "clue_analysis": {"inference_id": "", "goal_snapshot": scope.goal}},
    )
    published = json.loads(result.text)
    assert published["status"] == "findings"
    stored = clue_review_report(workspace, store, published["report_id"])
    assert stored.findings[0]["clue_ids"] == [scope.clue_ids[0]]
    finished = [event for event in events if event["kind"] == "review_finished"]
    assert finished and finished[0]["clue_report_id"] == published["report_id"]
    assert finished[0]["inference_id"] == scope.inference_id


# -- 实际轮次 ---------------------------------------------------------------


def _snapshot(provider: Any) -> ModelSnapshot:
    return ModelSnapshot(
        connection_id="test", display_name="测试连接", model="fake-model", provider=provider
    )


@pytest.fixture
def live_client(tmp_path: Path) -> Iterator[Callable[[ProviderFactory], TestClient]]:
    opened: list[TestClient] = []

    def build(factory: ProviderFactory) -> TestClient:
        client = TestClient(
            create_workspace_app(
                workspaces=RecentWorkspaces(tmp_path / "live-state"), provider_factory=factory
            )
        )
        client.__enter__()
        opened.append(client)
        return client

    yield build
    for client in opened:
        client.__exit__(None, None, None)


def _wait_finished(client: TestClient, workspace_id: str, conversation_id: str) -> list[dict]:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        events = client.get(
            f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/messages"
        ).json()["events"]
        if any(event["type"] == "run_finished" for event in events):
            return events
        time.sleep(0.05)
    raise AssertionError("核对轮次没有结束")


def test_review_turn_publishes_clue_report(
    tmp_path: Path, live_client: Callable[[ProviderFactory], TestClient]
) -> None:
    from tests.workspace.test_chat import ScriptedProvider, _text_turn, _tool_turn

    workspace = Workspace.create(tmp_path / "书")
    workspace.write(FIRST, "# 遗书\n\n信封背面有字。\n", expected_digest=None)
    workspace.write(
        SECOND, f"# 照片\n\n照片证明林岚看过背面。\n\n{QUOTE}\n", expected_digest=None
    )
    workspace.write(THIRD, "# 书房\n\n林岚在书房里核对账目。\n", expected_digest=None)
    workspace.write(PLAN, "# 线索\n\n原计划：第三章让照片出现。\n", expected_digest=None)
    registry = RecentWorkspaces(tmp_path / "live-state")
    box: dict[str, Any] = {}
    client = live_client(lambda: _snapshot(box["provider"]))
    created = client.post(
        "/api/file-workspaces", json={"path": str(workspace.root), "create": False}
    )
    assert created.status_code == 200, created.text
    workspace_id = str(created.json()["id"])
    store = ClueLedgerStore(conversation_root(registry.root, workspace_id))
    first = _identity(store, "遗书背面的字", "live-first")
    second = _identity(store, "公开的照片", "live-second")
    inference = save_clue_inference(
        store,
        clue_ids=[first, second],
        goal="林岚提前接触过遗书原件",
        intent="deduce",
        premises=[],
        alternatives=[],
        design_link_ids=[],
        request_key="live-inference",
        expected_revision=store.read().revision,
        created_by="author",
    )
    view = workspace.read(SECOND)
    start = view.text.index(QUOTE)
    box["provider"] = ScriptedProvider(
        [
            _tool_turn(
                ToolCall(id="r1", name="read_file", arguments={"path": SECOND}),
                ToolCall(
                    id="v1",
                    name="save_review_report",
                    arguments={
                        "target_paths": [SECOND],
                        "requirement": "核对这组线索。",
                        "status": "findings",
                        "clue_analysis": {
                            "inference_id": str(inference["inference_id"]),
                            "goal_snapshot": "林岚提前接触过遗书原件",
                        },
                        "findings": [
                            {
                                "kind": "counter_evidence",
                                "certainty": "supported",
                                "description": "林岚否认见过信，但照片显示她看过背面。",
                                "suggestion": "补一句她是否记得这封信。",
                                "evidence": [
                                    {
                                        "path": SECOND,
                                        "digest": view.digest,
                                        "start": start,
                                        "end": start + len(QUOTE),
                                        "quote": QUOTE,
                                    }
                                ],
                            }
                        ],
                    },
                ),
            ),
            _text_turn("核对完成。"),
        ]
    )
    response = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={
            "text": "核对这组线索是否足以认定林岚提前接触过原件。",
            "request_key": "live-review-1",
            "purpose": "review",
            "review_paths": [SECOND],
            "context": {"state_chapter": 2, "selected_path": SECOND},
            "clue_review": {
                "inference_id": str(inference["inference_id"]),
                "inference_revision": str(inference["inference_revision"]),
                "clue_ids": [first, second],
                "as_of_chapter": 2,
            },
        },
    )
    assert response.status_code == 202, response.text
    events = _wait_finished(client, workspace_id, response.json()["conversation_id"])
    # 模型要能原样带回目标标识：冻结范围必须写进这一轮的系统提示。
    system = box["provider"].requests[0]["system"]
    assert "本轮冻结的核对范围" in system
    assert str(inference["inference_id"]) in system
    assert "林岚提前接触过遗书原件" in system
    finished = next(
        event
        for event in events
        if event["type"] == "review_finished" and event.get("inference_id")
    )
    assert finished["status"] == "findings"
    assert finished["clue_report_id"]
    report = clue_review_report(workspace, store, finished["clue_report_id"])
    assert report.findings[0]["evidence"][0]["quote"] == QUOTE
    assert report.freshness == "valid"
    ledger = store.read().ledger
    assert ledger is not None
    assert ledger.reviews[finished["clue_report_id"]]["status"] == "findings"


def test_incomplete_clue_report_is_saved_and_marks_partial(
    tmp_path: Path, live_client: Callable[[ProviderFactory], TestClient]
) -> None:
    from tests.workspace.test_chat import ScriptedProvider, _text_turn, _tool_turn

    workspace = Workspace.create(tmp_path / "半本")
    workspace.write(SECOND, f"# 照片\n\n{QUOTE}\n", expected_digest=None)
    registry = RecentWorkspaces(tmp_path / "live-state")
    box: dict[str, Any] = {}
    client = live_client(lambda: _snapshot(box["provider"]))
    created = client.post(
        "/api/file-workspaces", json={"path": str(workspace.root), "create": False}
    )
    workspace_id = str(created.json()["id"])
    store = ClueLedgerStore(conversation_root(registry.root, workspace_id))
    first = _identity(store, "照片里的时间", "partial-first")
    inference = save_clue_inference(
        store,
        clue_ids=[first],
        goal="照片拍于案发当晚",
        intent="undecided",
        premises=[],
        alternatives=[],
        design_link_ids=[],
        request_key="partial-inference",
        expected_revision=store.read().revision,
        created_by="author",
    )
    box["provider"] = ScriptedProvider(
        [
            _tool_turn(
                ToolCall(
                    id="v1",
                    name="save_review_report",
                    arguments={
                        "target_paths": [SECOND],
                        "requirement": "核对照片时间。",
                        "status": "incomplete",
                        "clue_analysis": {
                            "inference_id": str(inference["inference_id"]),
                            "goal_snapshot": "照片拍于案发当晚",
                        },
                        "uncovered": ["只读了开头，没有读完本章"],
                    },
                )
            ),
            _text_turn("核对没有读完。"),
        ]
    )
    response = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={
            "text": "核对照片时间。",
            "request_key": "partial-review",
            "purpose": "review",
            "review_paths": [SECOND],
            "context": {"state_chapter": 2, "selected_path": SECOND},
            "clue_review": {
                "inference_id": str(inference["inference_id"]),
                "inference_revision": str(inference["inference_revision"]),
                "clue_ids": [first],
                "as_of_chapter": 2,
            },
        },
    )
    assert response.status_code == 202, response.text
    events = _wait_finished(client, workspace_id, response.json()["conversation_id"])
    # 冻结范围同样写进这一轮的提示；模型不必猜目标标识。
    system = box["provider"].requests[0]["system"]
    assert "照片拍于案发当晚" in system
    finished = [event for event in events if event["type"] == "review_finished"]
    assert len(finished) == 1
    assert finished[0]["status"] == "incomplete"
    assert finished[0]["clue_report_id"]
    assert events[-1]["status"] == "partial"
    report = clue_review_report(workspace, store, finished[0]["clue_report_id"])
    assert report.uncovered == ["只读了开头，没有读完本章"]
