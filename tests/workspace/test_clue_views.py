"""S3：本章关联、共用详情、正文已见边界及分页失效；真实文件，不调用模型。"""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.workspace.clue_models import ClueCoverage, ClueEntry
from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.clue_views import clue_detail, clue_list
from hastings.workspace.disclosure import ObservedEvidence
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.source_locations import resolve_source_location
from hastings.workspace.state import RecentWorkspaces, conversation_root

FIRST = "正文/0001 雨夜.md"
SECOND = "正文/0002 证词.md"
THIRD = "正文/0003 真相.md"


def proof(workspace, path, quote):
    file = workspace.read(path)
    start = file.text.index(quote)
    return ObservedEvidence(path, file.digest, start, start + len(quote), quote)


def publish(workspace, store, entries, path=FIRST, outcome="found", key="one"):
    number = int(path.split("/")[1][:4])
    return store.publish(
        tuple(entries),
        source_chapter=number,
        expected_revision=store.read().revision,
        request_key=key,
        coverage=ClueCoverage(
            path=path,
            digest=workspace.read(path).digest,
            chapter=number,
            outcome=outcome,
            task_id=key,
            at=f"2026-10-27T10:00:0{number}",
            note="信息不足" if outcome == "insufficient" else "",
        ),
    )


@pytest.fixture
def book(tmp_path):
    workspace = Workspace.create(tmp_path / "雨夜")
    for path, text in [
        (FIRST, "# 雨夜\n\n信封背面有字。\n"),
        (SECOND, "# 证词\n\n林岚说她没看过信封。\n"),
        (THIRD, "# 真相\n\n照片证明林岚看过背面。\n"),
    ]:
        workspace.write(path, text, expected_digest=None)
    store = ClueLedgerStore(tmp_path / "state")
    entry = ClueEntry(
        "信封背面的字",
        1,
        None,
        "open",
        "作者猜测，尚未证实",
        proof(workspace, FIRST, "信封背面有字。"),
        source_chapter=1,
        mode="narration",
    )
    result = publish(workspace, store, [entry])
    entry = replace(
        store.read().entries()[0],
        source_chapter=2,
        mention_evidence=proof(workspace, SECOND, "林岚说她没看过信封。"),
        mode="testimony",
    )
    publish(workspace, store, [entry], SECOND, key="two")
    return workspace, store, result["clue_ids"][0]


def test_current_chapter_includes_earlier_plant_and_has_traceable_testimony(book):
    workspace, store, identifier = book
    listing = clue_list(workspace, store.root, chapter_path=SECOND, as_of_chapter=2)
    assert [row.clue_id for row in listing.items] == [identifier]
    assert listing.chapter.coverage.outcome == "found"
    detail = clue_detail(workspace, store.root, identifier, as_of_chapter=2)
    assert detail.mentioned_chapters == [1, 2]
    assert [(row.chapter, row.kind, row.mode) for row in detail.observations] == [
        (1, "plant", "narration"),
        (2, "mention", "testimony"),
    ]
    assert [row.text for row in detail.reader_seen] == ["信封背面有字。", "林岚说她没看过信封。"]
    assert detail.interpretation not in [row.text for row in detail.reader_seen]
    for basis in detail.evidence:
        source = resolve_source_location(workspace, store.root, basis.evidence_id, as_of_chapter=2)
        assert source.freshness == "valid" and source.quote == basis.quote


def test_future_contribution_does_not_leak_title_interpretation_or_resolution(book):
    workspace, store, identifier = book
    entry = replace(
        store.read().entries()[0],
        clue="已经证明的真相",
        supports="只有第三章揭露的解释",
        source_chapter=3,
        status="resolved",
        resolution_chapter=3,
        mention_evidence=None,
        resolution_evidence=proof(workspace, THIRD, "照片证明林岚看过背面。"),
    )
    publish(workspace, store, [entry], THIRD, key="three")
    early = clue_detail(workspace, store.root, identifier, as_of_chapter=2)
    assert early.title == "信封背面的字" and early.lifecycle.status == "open"
    assert "第三章" not in early.model_dump_json() and "照片证明" not in early.model_dump_json()
    assert early.lifecycle.resolution_chapter is None
    assert clue_list(workspace, store.root, chapter_path=THIRD, as_of_chapter=2).items == []
    assert clue_list(workspace, store.root, as_of_chapter=2, query="已经证明").items == []
    late = clue_detail(workspace, store.root, identifier, as_of_chapter=3)
    assert late.lifecycle.status == "resolved"
    assert any(row.kind == "resolve" and row.chapter == 3 for row in late.observations)
    assert next(row for row in late.observations if row.kind == "resolve").mode == "unknown"


def test_changed_source_keeps_saved_quote_but_excludes_it_from_reader_seen(book):
    workspace, store, identifier = book
    file = workspace.read(SECOND)
    workspace.write(SECOND, file.text + "\n改稿。", expected_digest=file.digest)
    detail = clue_detail(workspace, store.root, identifier)
    assert detail.freshness == "stale"
    assert detail.coverage.freshness == "stale"
    assert detail.evidence[1].quote == "林岚说她没看过信封。"
    assert [row.text for row in detail.reader_seen] == ["信封背面有字。"]
    assert (
        clue_list(workspace, store.root, chapter_path=SECOND).chapter.coverage.freshness == "stale"
    )


@pytest.mark.parametrize("outcome", ["empty", "insufficient"])
def test_empty_and_insufficient_are_completed_coverage_not_unrecorded(book, outcome):
    workspace, store, identifier = book
    publish(workspace, store, [], SECOND, outcome, key="replacement")
    listing = clue_list(workspace, store.root, chapter_path=SECOND)
    assert listing.chapter.coverage.outcome == outcome
    assert listing.chapter.coverage.freshness == "valid"
    assert len(listing.items) == (1 if outcome == "insufficient" else 0)
    assert (
        clue_list(workspace, store.root, chapter_path=THIRD).chapter.coverage.outcome
        == "unrecorded"
    )
    assert clue_detail(workspace, store.root, identifier).title == "信封背面的字"


def test_empty_body_is_visible_and_has_nothing_to_read(book):
    workspace, store, _ = book
    view = workspace.read(THIRD)
    workspace.write(THIRD, "# 真相\n\n## 待写\n", expected_digest=view.digest)
    chapter = clue_list(workspace, store.root, chapter_path=THIRD).chapter
    # 正文可以还没写；这一章只是没有可读内容，不伪造「已经看过」。
    assert not chapter.has_content
    assert chapter.coverage.outcome == "unrecorded"


def test_cursor_is_bounded_and_invalidated_by_query_source_or_ledger_change(book):
    workspace, store, _ = book
    entries = [replace(store.read().entries()[0], source_chapter=1, mention_evidence=None)]
    for number in range(3):
        entries.append(
            replace(
                entries[0],
                clue=f"不同线索{number}",
                clue_id="",
                new_identity=True,
                source_chapter=1,
                mention_evidence=None,
            )
        )
    publish(workspace, store, entries, key="many")
    first = clue_list(workspace, store.root, limit=2)
    second = clue_list(workspace, store.root, limit=2, cursor=first.next_cursor)
    assert first.total == 4 and len(first.items) == len(second.items) == 2
    assert not second.next_cursor
    assert set(row.clue_id for row in first.items).isdisjoint(row.clue_id for row in second.items)
    for kwargs in [{"query": "不同"}, {"limit": 1}, {"as_of_chapter": 1}, {"chapter_path": SECOND}]:
        options = {"limit": 2, "cursor": first.next_cursor, **kwargs}
        with pytest.raises(WorkspaceError, match="CLUE_CURSOR_CONFLICT"):
            clue_list(workspace, store.root, **options)
    for cursor in ["%%%", "W10=", "eyJvZmZzZXQiOi0xfQ=="]:
        with pytest.raises(WorkspaceError, match="CLUE_CURSOR_CONFLICT"):
            clue_list(workspace, store.root, limit=2, cursor=cursor)
    file = workspace.read(FIRST)
    workspace.write(FIRST, file.text + "改动", expected_digest=file.digest)
    with pytest.raises(WorkspaceError, match="CLUE_CURSOR_CONFLICT"):
        clue_list(workspace, store.root, limit=2, cursor=first.next_cursor)


def test_http_contract_no_model_or_write_and_unsafe_chapter_rejected(book, tmp_path):
    workspace, store, identifier = book
    registry = RecentWorkspaces(tmp_path / "app")
    binding = registry.bind(workspace.root)
    root = conversation_root(registry.root, binding.id)
    root.mkdir(parents=True)
    (root / "clue-ledger.json").write_bytes(store.file.read_bytes())
    before = {path.name: path.read_bytes() for path in root.iterdir()}

    def unused():
        raise AssertionError("只读接口不调用模型")

    with TestClient(create_workspace_app(workspaces=registry, provider_factory=unused)) as client:
        base = f"/api/file-workspaces/{binding.id}/clues"
        response = client.get(base, params={"chapter_path": SECOND, "as_of_chapter": 2})
        assert response.status_code == 200 and response.json()["total"] == 1
        response = client.get(f"{base}/{identifier}", params={"as_of_chapter": 2})
        assert (
            response.status_code == 200 and response.json()["reader_seen"][1]["mode"] == "testimony"
        )
        assert client.get(base, params={"chapter_path": "../../.env"}).status_code == 404
        assert client.get(base, params={"limit": 51}).status_code == 422
        assert client.get(f"{base}/unknown").status_code == 404
    assert {path.name: path.read_bytes() for path in root.iterdir()} == before


def test_identity_after_empty_rebuild_does_not_claim_planting_or_leak_future_title(book):
    workspace, store, identifier = book
    entry = replace(
        store.read().entries()[0],
        source_chapter=3,
        clue="未来才出现的身份标题",
        mention_evidence=None,
    )
    publish(workspace, store, [entry], THIRD, key="future")
    for index, path in enumerate([FIRST, SECOND, THIRD]):
        publish(workspace, store, [], path, "empty", key=f"clear-{index}")
    before = store.file.read_bytes()
    early = clue_detail(workspace, store.root, identifier, as_of_chapter=2)
    assert early.lifecycle.status == "unknown" and early.lifecycle.planted_chapter is None
    assert early.title == "暂无正文依据的线索" and early.reader_seen == []
    assert "未来才出现" not in early.model_dump_json()
    current = clue_detail(workspace, store.root, identifier)
    assert current.title == "未来才出现的身份标题" and current.observations == []
    assert current.lifecycle.status == "unknown" and current.lifecycle.planted_chapter is None
    assert store.file.read_bytes() == before
