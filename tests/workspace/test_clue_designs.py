"""1.0 S4：作者计划关联与推理目标；真实文件与账本，不调用模型。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hastings.library import EntryDraft, ModuleLibrary
from hastings.library.bindings import AssemblyService
from hastings.server.file_app import create_workspace_app
from hastings.workspace.clue_designs import (
    create_clue_identity,
    link_clue_design,
    save_clue_inference,
    unlink_clue_design,
)
from hastings.workspace.clue_models import FileSource, InferenceStatement, ModuleSource
from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.clue_views import clue_detail, clue_list
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.source_locations import resolve_source_location
from hastings.workspace.state import RecentWorkspaces

FIRST = "正文/0001 遗书.md"
SECOND = "正文/0002 照片.md"
PLAN = "设定集/线索.md"


@pytest.fixture
def book(tmp_path: Path) -> tuple[Workspace, ClueLedgerStore]:
    workspace = Workspace.create(tmp_path / "遗书")
    workspace.write(FIRST, "# 遗书\n\n信封背面有字。\n", expected_digest=None)
    workspace.write(SECOND, "# 照片\n\n照片证明林岚看过背面。\n", expected_digest=None)
    workspace.write(
        PLAN, "# 线索\n\n原计划：第三章让照片出现。\n", expected_digest=None
    )
    return workspace, ClueLedgerStore(tmp_path / "state")


def identity(
    store: ClueLedgerStore, title: str, key: str = "create-1"
) -> str:
    result = create_clue_identity(
        store,
        title=title,
        request_key=key,
        expected_revision=store.read().revision,
    )
    return str(result["clue_id"])


def plan_source(
    workspace: Workspace, quote: str = "原计划：第三章让照片出现。"
) -> tuple[FileSource, int, str]:
    view = workspace.read(PLAN)
    start = view.text.index(quote)
    return FileSource(path=PLAN, digest=view.digest), start, quote


def test_planned_clue_has_no_body_records_and_is_visible_without_planting(book):
    workspace, store = book
    clue_id = identity(store, "遗书背面的字")
    ledger = store.read().ledger
    assert ledger is not None
    assert ledger.contributions == {} and ledger.coverage == {}
    assert ledger.clues[clue_id].first_chapter is None
    detail = clue_detail(workspace, store.root, clue_id, as_of_chapter=0)
    assert detail.title == "遗书背面的字"
    assert detail.lifecycle.status == "unknown"
    assert detail.lifecycle.planted_chapter is None
    assert detail.observations == [] and detail.reader_seen == []
    assert detail.ledger_revision == store.read().revision
    assert clue_list(workspace, store.root, as_of_chapter=0).items[0].title == "遗书背面的字"
    assert store.read().ledger.contributions == {}


def test_file_plan_range_is_verified_and_kept_as_stale_after_edit(book):
    workspace, store = book
    clue_id = identity(store, "遗书背面的字")
    source, start, quote = plan_source(workspace)
    base = store.read().revision
    with pytest.raises(WorkspaceError, match="CLUE_SCOPE_INVALID"):
        link_clue_design(
            workspace,
            store,
            clue_id=clue_id,
            source=FileSource(path=FIRST, digest=workspace.read(FIRST).digest),
            start=0,
            end=1,
            quote="信",
            relation="",
            request_key="chapter-source",
            expected_revision=base,
            created_by="author",
        )
    result = link_clue_design(
        workspace,
        store,
        clue_id=clue_id,
        source=source,
        start=start,
        end=start + len(quote),
        quote=quote,
        relation="计划让照片在第3章出现",
        request_key="link-1",
        expected_revision=base,
        created_by="author",
    )
    detail = clue_detail(workspace, store.root, clue_id)
    assert [row.link_id for row in detail.author_designs] == [result["link_id"]]
    assert detail.author_designs[0].freshness == "valid"
    assert detail.author_designs[0].relation == "计划让照片在第3章出现"
    location = resolve_source_location(
        workspace, store.root, detail.author_designs[0].evidence_id
    )
    assert location.freshness == "valid" and location.start == start
    before = store.read().ledger.clues[clue_id].clue_revision
    replay = link_clue_design(
        workspace,
        store,
        clue_id=clue_id,
        source=source,
        start=start,
        end=start + len(quote),
        quote=quote,
        relation="计划让照片在第3章出现",
        request_key="link-1",
        expected_revision=base,
        created_by="author",
    )
    assert replay["link_id"] == result["link_id"]
    assert len(store.read().ledger.design_links) == 1
    assert store.read().ledger.clues[clue_id].clue_revision == before
    old = workspace.read(PLAN)
    workspace.write(PLAN, old.text + "\n改稿。", expected_digest=old.digest)
    stale = clue_detail(workspace, store.root, clue_id)
    assert stale.author_designs[0].freshness == "stale"
    assert stale.author_designs[0].quote == quote
    assert store.read().ledger.contributions == {}


def test_module_plan_is_exact_revision_and_detached_after_upgrade_or_unbind(book, tmp_path: Path):
    workspace, store = book
    clue_id = identity(store, "遗书背面的字")
    library = ModuleLibrary(tmp_path / "library", cache_file=tmp_path / "cache.json")
    created = library.create_module(
        kind="plot",
        name="遗书计划",
        entries=[EntryDraft("letter", "plot_plan", "背面计划", "原计划🔑背面的字。")],
        request_key="create-plan-module",
    )
    module = created["module"]
    revision = created["revision"]
    module_id, revision_id = str(module["module_id"]), str(revision["revision_id"])
    mounted = AssemblyService(library, workspace).bind(
        module_id=module_id,
        revision_id=revision_id,
        expected_digest=None,
        request_key="bind-plan-module",
    )
    binding_id = str(mounted["bindings"][0]["id"])
    text, digest = library.storage.read_entry(module_id, revision_id, "letter")
    quote = "原计划🔑背面的字。"
    start = text.index(quote)
    link_clue_design(
        workspace,
        store,
        clue_id=clue_id,
        source=ModuleSource(
            library_id=str(library.library_id),
            module_id=module_id,
            revision_id=revision_id,
            entry_id="letter",
            content_digest=digest,
        ),
        start=start,
        end=start + len(quote),
        quote=quote,
        relation="模块计划",
        request_key="link-module",
        expected_revision=store.read().revision,
        created_by="author",
        library=library,
    )
    detail = clue_detail(workspace, store.root, clue_id, library=library)
    assert detail.author_designs[0].freshness == "valid"
    assert "遗书计划" in detail.author_designs[0].label
    location = resolve_source_location(
        workspace, store.root, detail.author_designs[0].evidence_id, library=library
    )
    assert location.freshness == "valid" and location.start == start
    newer = library.publish(
        module_id=module_id,
        base_revision=revision_id,
        expected_head=revision_id,
        entries=[EntryDraft("letter", "plot_plan", "背面计划", "新版计划不应替换旧依据。")],
        request_key="publish-plan-module",
    )
    AssemblyService(library, workspace).upgrade(
        binding_id=binding_id,
        revision_id=newer.revision_id,
        expected_digest=str(mounted["digest"]),
        request_key="upgrade-plan-module",
    )
    detached = clue_detail(workspace, store.root, clue_id, library=library)
    assert detached.author_designs[0].freshness == "detached"
    assert detached.author_designs[0].quote == quote
    assert any("计划关联" in warning for warning in detached.warnings)
    old_location = resolve_source_location(
        workspace, store.root, detached.author_designs[0].evidence_id, library=library
    )
    assert old_location.freshness == "detached"
    assert old_location.start is None and "新版计划" not in old_location.excerpt


def test_inference_links_multiple_clues_and_update_rejects_stale_revision(book):
    workspace, store = book
    first = identity(store, "遗书背面的字", "create-first")
    second = identity(store, "公开的照片", "create-second")
    source, start, quote = plan_source(workspace)
    linked = link_clue_design(
        workspace,
        store,
        clue_id=first,
        source=source,
        start=start,
        end=start + len(quote),
        quote=quote,
        relation="",
        request_key="link-inference",
        expected_revision=store.read().revision,
        created_by="author",
    )
    result = save_clue_inference(
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
        alternatives=[InferenceStatement(text="警方公开过背面照片", clue_ids=[second])],
        design_link_ids=[str(linked["link_id"])],
        request_key="inference-1",
        expected_revision=store.read().revision,
        created_by="author",
    )
    detail = clue_detail(workspace, store.root, first)
    view = detail.inferences[0]
    assert view.inference_id == result["inference_id"]
    assert [item.title for item in view.clues] == ["遗书背面的字", "公开的照片"]
    assert view.premises[0].design_link_ids == [linked["link_id"]]
    assert detail.lifecycle.status == "unknown"
    assert store.read().ledger.contributions == {}
    second_detail = clue_detail(workspace, store.root, second)
    assert second_detail.inferences[0].goal == "林岚提前接触过遗书原件"
    with pytest.raises(WorkspaceError, match="CLUE_REVISION_CONFLICT"):
        save_clue_inference(
            store,
            clue_ids=[first, second],
            goal="更新后的目标",
            intent="suspect",
            premises=[],
            alternatives=[],
            design_link_ids=[],
            request_key="inference-stale",
            expected_revision=store.read().revision,
            inference_id=str(result["inference_id"]),
            inference_revision="rev_wrong",
            created_by="author",
        )
    updated = save_clue_inference(
        store,
        clue_ids=[first],
        goal="更新后的目标",
        intent="suspect",
        premises=[],
        alternatives=[],
        design_link_ids=[],
        request_key="inference-2",
        expected_revision=store.read().revision,
        inference_id=str(result["inference_id"]),
        inference_revision=str(result["inference_revision"]),
        created_by="author",
    )
    assert updated["inference_id"] == result["inference_id"]
    assert clue_detail(workspace, store.root, second).inferences == []
    with pytest.raises(WorkspaceError, match="CLUE_NOT_FOUND"):
        save_clue_inference(
            store,
            clue_ids=["clue_missing"],
            goal="不存在的线索",
            intent="undecided",
            premises=[],
            alternatives=[],
            design_link_ids=[],
            request_key="inference-missing",
            expected_revision=store.read().revision,
            created_by="author",
        )
    with pytest.raises(WorkspaceError, match="CLUE_SCOPE_INVALID"):
        save_clue_inference(
            store,
            clue_ids=[second],
            goal="不属于目标线索的计划依据",
            intent="undecided",
            premises=[],
            alternatives=[],
            design_link_ids=[str(linked["link_id"])],
            request_key="inference-bad-link",
            expected_revision=store.read().revision,
            created_by="author",
        )
    with pytest.raises(WorkspaceError, match="CLUE_SCOPE_INVALID"):
        save_clue_inference(
            store,
            clue_ids=[second],
            goal="前提引用了目标之外的线索",
            intent="undecided",
            premises=[InferenceStatement(text="另一条线索的前提", clue_ids=[first])],
            alternatives=[],
            design_link_ids=[],
            request_key="inference-bad-premise",
            expected_revision=store.read().revision,
            created_by="author",
        )


def test_unlink_removes_plan_and_references_without_touching_module_content(book, tmp_path: Path):
    workspace, store = book
    clue_id = identity(store, "遗书背面的字")
    source, start, quote = plan_source(workspace)
    linked = link_clue_design(
        workspace,
        store,
        clue_id=clue_id,
        source=source,
        start=start,
        end=start + len(quote),
        quote=quote,
        relation="",
        request_key="link-then-unlink",
        expected_revision=store.read().revision,
        created_by="author",
    )
    target = save_clue_inference(
        store,
        clue_ids=[clue_id],
        goal="目标引用计划",
        intent="deduce",
        premises=[],
        alternatives=[],
        design_link_ids=[str(linked["link_id"])],
        request_key="inference-with-link",
        expected_revision=store.read().revision,
        created_by="author",
    )
    before = workspace.read(PLAN).text
    unlink_clue_design(
        store,
        clue_id=clue_id,
        link_id=str(linked["link_id"]),
        request_key="unlink-1",
        expected_revision=store.read().revision,
    )
    detail = clue_detail(workspace, store.root, clue_id)
    assert detail.author_designs == []
    assert detail.inferences[0].design_link_ids == []
    assert detail.inferences[0].inference_id == target["inference_id"]
    assert workspace.read(PLAN).text == before
    assert save_clue_inference_result(store, target["inference_id"]) is not None


def save_clue_inference_result(store: ClueLedgerStore, inference_id: object) -> object:
    ledger = store.read().ledger
    assert ledger is not None
    return ledger.inferences.get(str(inference_id))


def _client(tmp_path: Path) -> tuple[TestClient, str, ModuleLibrary]:
    library = ModuleLibrary(tmp_path / "模块库", cache_file=tmp_path / "state" / "catalog.json")
    registry = RecentWorkspaces(tmp_path / "state")

    def unused():
        raise AssertionError("线索写接口不调用模型")

    client = TestClient(
        create_workspace_app(
            workspaces=registry, provider_factory=unused, library=library  # type: ignore[arg-type]
        )
    )
    created = client.post(
        "/api/file-workspaces", json={"path": str(tmp_path / "遗书"), "create": True}
    )
    assert created.status_code == 200, created.text
    workspace_id = str(created.json()["id"])
    workspace = Workspace(tmp_path / "遗书")
    for path, text in [
        (FIRST, "# 遗书\n\n信封背面有字。\n"),
        (SECOND, "# 照片\n\n照片证明林岚看过背面。\n"),
        (PLAN, "# 线索\n\n原计划：第三章让照片出现。\n"),
    ]:
        workspace.write(path, text, expected_digest=None)
    return client, workspace_id, library


def test_http_plan_and_inference_flow_without_model_calls(tmp_path: Path):
    client, workspace_id, _ = _client(tmp_path)
    base = f"/api/file-workspaces/{workspace_id}"
    created = client.post(
        f"{base}/clues",
        json={
            "title": "遗书背面的字",
            "request_key": "http-create-1",
            "expected_revision": "empty",
        },
        params={"as_of_chapter": 2},
    )
    assert created.status_code == 200, created.text
    clue = created.json()
    assert clue["title"] == "遗书背面的字" and clue["author_designs"] == []
    view = Workspace(tmp_path / "遗书").read(PLAN)
    quote = "原计划：第三章让照片出现。"
    start = view.text.index(quote)
    linked = client.put(
        f"{base}/clues/{clue['clue_id']}/design",
        json={
            "request_key": "http-link-1",
            "expected_revision": clue["ledger_revision"],
            "source": {"kind": "file", "path": PLAN, "digest": view.digest},
            "start": start,
            "end": start + len(quote),
            "quote": quote,
            "relation": "第三章让照片出现",
        },
        params={"as_of_chapter": 2},
    )
    assert linked.status_code == 200, linked.text
    linked_body = linked.json()
    assert linked_body["author_designs"][0]["freshness"] == "valid"
    inference = client.post(
        f"{base}/clue-inferences",
        json={
            "request_key": "http-inference-1",
            "expected_revision": linked_body["ledger_revision"],
            "clue_ids": [clue["clue_id"]],
            "goal": "林岚提前接触过原件",
            "intent": "deduce",
            "premises": [{"text": "林岚能说出背面内容", "clue_ids": [clue["clue_id"]]}],
            "alternatives": [],
            "design_link_ids": [linked_body["author_designs"][0]["link_id"]],
        },
    )
    assert inference.status_code == 200, inference.text
    assert inference.json()["goal"] == "林岚提前接触过原件"
    detail = client.get(f"{base}/clues/{clue['clue_id']}", params={"as_of_chapter": 2})
    assert detail.status_code == 200
    body = detail.json()
    assert body["inferences"][0]["premises"][0]["text"] == "林岚能说出背面内容"
    stale = client.put(
        f"{base}/clues/{clue['clue_id']}/design",
        json={
            "request_key": "http-link-stale",
            "expected_revision": "rev_stale",
            "source": {"kind": "file", "path": PLAN, "digest": view.digest},
            "start": start,
            "end": start + len(quote),
            "quote": quote,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "CLUE_REVISION_CONFLICT"
    missing = client.get(f"{base}/clues/unknown")
    assert missing.status_code == 404
