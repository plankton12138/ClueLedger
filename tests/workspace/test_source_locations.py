"""S2：真实来源、码点区间、移动记录和作品边界；浏览不调用模型。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from hastings.server.file_app import create_workspace_app
from hastings.workspace.clue_models import (
    ClueEntry,
    ClueReference,
)
from hastings.workspace.clue_views import clue_detail
from hastings.workspace.clues import ClueStore, publish_clues
from hastings.workspace.disclosure import ObservedEvidence
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.source_locations import resolve_source_location
from hastings.workspace.state import ConversationStore, RecentWorkspaces, conversation_root

FIRST = "正文/0001 遗书.md"
SECOND = "正文/0002 照片.md"


@pytest.fixture
def book(tmp_path):
    workspace = Workspace.create(tmp_path / "遗书")
    # 字节与文本坐标分别核验，重复引文必须引用第二处。
    raw = "\ufeff# 遗书\r\n\r\n🔑背面相同。\r\n\r\n前文𠮷。背面相同。\r\n".encode("utf-8")
    (workspace.root / FIRST).write_bytes(raw)
    workspace.write(SECOND, "# 照片\n\n公开照片也是来源。\n", expected_digest=None)
    store = ClueStore(tmp_path / "state")
    view = workspace.read(FIRST)
    start = view.text.rindex("背面相同。")
    entry = ClueEntry(
        "知道背面内容",
        1,
        None,
        "open",
        "",
        ObservedEvidence(FIRST, view.digest, start, start + len("背面相同。"), "背面相同。"),
        source_chapter=1,
    )
    publish_clues(workspace, store, (entry,), source_chapter=1)
    return workspace, store, raw


def only_clue_id(store) -> str:
    ledger = store.ledger.read().ledger
    assert ledger is not None and ledger.clues
    return next(iter(ledger.clues))


def source_id(workspace, store, as_of=2):
    return clue_detail(
        workspace, store.root, only_clue_id(store), as_of_chapter=as_of
    ).evidence[0].evidence_id


def test_raw_byte_digest_and_normalized_codepoints_target_second_quote(book):
    workspace, store, raw = book
    identifier = source_id(workspace, store)
    before = store.file.read_bytes()
    location = resolve_source_location(workspace, store.root, identifier, as_of_chapter=1)
    view = workspace.read(FIRST)
    assert location.freshness == "valid"
    assert location.source.digest == hashlib.sha256(raw).hexdigest()
    assert "\r" not in view.text and not view.text.startswith("\ufeff")
    assert location.start == view.text.rindex("背面相同。")
    assert view.text[location.start : location.end] == location.quote
    assert location.offset_unit == "unicode_codepoint"
    assert store.file.read_bytes() == before


def test_future_resolution_not_exposed_even_with_known_identifier(book):
    workspace, store, _ = book
    view = workspace.read(SECOND)
    start = view.text.index("公开照片也是来源。")
    entry = replace(
        store.entries()[0],
        status="resolved",
        resolution_chapter=2,
        resolution_evidence=ObservedEvidence(
            SECOND, view.digest, start, start + len("公开照片也是来源。"), "公开照片也是来源。"
        ),
    )
    publish_clues(workspace, store, (entry,), source_chapter=1)
    identifier = only_clue_id(store)
    late = clue_detail(workspace, store.root, identifier, as_of_chapter=2)
    early = clue_detail(workspace, store.root, identifier, as_of_chapter=1)
    assert len(late.evidence) == 2
    assert len(early.evidence) == 1
    # 后章才有的回收依据不能出现在前面的详情里。
    assert "公开照片也是来源" not in json.dumps(
        early.model_dump(mode="json"), ensure_ascii=False
    )
    identifier = late.evidence[1].evidence_id
    with pytest.raises(WorkspaceError, match="CLUE_SOURCE_NOT_FOUND"):
        resolve_source_location(workspace, store.root, identifier, as_of_chapter=1)


def test_source_change_does_not_rebind_to_identical_quote(book):
    workspace, store, _ = book
    identifier = source_id(workspace, store)
    old = workspace.read(FIRST)
    workspace.write(
        FIRST, "# 遗书\n\n背面相同。\n改稿后仍出现同一句话。\n", expected_digest=old.digest
    )
    location = resolve_source_location(workspace, store.root, identifier)
    assert location.freshness == "stale" and location.readable
    assert location.quote == "背面相同。"
    assert location.start is None and location.end is None
    assert location.current_digest != location.source.digest


def test_only_byte_change_still_invalidates_source(book):
    workspace, store, _ = book
    identifier = source_id(workspace, store)
    before = workspace.read(FIRST)
    (workspace.root / FIRST).write_bytes(before.text.encode("utf-8"))
    assert workspace.read(FIRST).text == before.text
    assert resolve_source_location(workspace, store.root, identifier).freshness == "stale"


def test_external_move_does_not_search_similar_files(book):
    workspace, store, _ = book
    identifier = source_id(workspace, store)
    (workspace.root / FIRST).rename(workspace.root / "正文/0001 改名.md")
    result = resolve_source_location(workspace, store.root, identifier)
    assert result.freshness == "source_missing"
    assert not result.readable and result.start is None


def test_successful_recorded_move_keeps_original_evidence_id(book):
    workspace, store, _ = book
    identifier = source_id(workspace, store)
    source = workspace.read(FIRST)
    moved = workspace.move(FIRST, "正文/0001 改名.md", expected_digest=source.digest)
    conversations = ConversationStore(store.root)
    accepted = conversations.create_with_first_turn(
        text="改名", request_key="rename", turn_id="move"
    )
    conversations.append(
        accepted.conversation_id,
        {
            "type": "file_changed",
            "action": "move",
            "path": FIRST,
            "target": moved.path,
            "before_digest": source.digest,
            "after_digest": moved.digest,
        },
    )
    result = resolve_source_location(workspace, store.root, identifier)
    assert result.evidence_id == identifier and result.freshness == "valid"
    assert result.source.path == moved.path and result.saved_path == FIRST


def test_renumbered_source_cannot_pass_early_boundary(book):
    workspace, store, _ = book
    identifier = source_id(workspace, store)
    source = workspace.read(FIRST)
    moved = workspace.move(FIRST, "正文/0008 遗书.md", expected_digest=source.digest)
    conversations = ConversationStore(store.root)
    accepted = conversations.create_with_first_turn(text="移章", request_key="move", turn_id="move")
    conversations.append(
        accepted.conversation_id,
        {
            "type": "file_changed",
            "action": "move",
            "path": FIRST,
            "target": moved.path,
            "before_digest": source.digest,
            "after_digest": moved.digest,
        },
    )
    with pytest.raises(WorkspaceError, match="CLUE_SOURCE_NOT_FOUND"):
        resolve_source_location(workspace, store.root, identifier, as_of_chapter=2)
    assert (
        resolve_source_location(workspace, store.root, identifier, as_of_chapter=8).freshness
        == "stale"
    )


def test_bad_saved_range_is_not_highlighted(book):
    workspace, store, _ = book
    ledger = store.ledger

    def corrupt(candidate):
        row = next(iter(candidate.contributions.values()))
        row.content.evidence.start = 0
        row.content.evidence.end = len(row.content.evidence.quote)
        return {}

    ledger.mutate(
        expected_revision=ledger.read().revision,
        request_key="range",
        kind="test",
        payload={},
        apply=corrupt,
    )
    result = resolve_source_location(workspace, store.root, source_id(workspace, store))
    assert result.freshness == "invalid_range" and result.start is None


def test_http_source_identifier_boundary_and_no_model_calls(book, tmp_path):
    workspace, store, _ = book
    registry = RecentWorkspaces(tmp_path / "app")
    binding = registry.bind(workspace.root)
    target = conversation_root(registry.root, binding.id)
    target.mkdir(parents=True)
    (target / "clue-ledger.json").write_bytes(store.file.read_bytes())
    other = Workspace.create(tmp_path / "other")
    other_binding = registry.bind(other.root)

    def unused():
        raise AssertionError("来源浏览不允许调用模型")

    with TestClient(create_workspace_app(workspaces=registry, provider_factory=unused)) as client:
        identifier = source_id(workspace, store)
        response = client.get(
            f"/api/file-workspaces/{binding.id}/source-locations/{identifier}?as_of_chapter=1"
        )
        assert response.status_code == 200 and response.json()["freshness"] == "valid"
        foreign = client.get(
            f"/api/file-workspaces/{other_binding.id}/source-locations/{identifier}"
        )
        assert (
            foreign.status_code == 403 and foreign.json()["error"]["code"] == "CLUE_ACCESS_DENIED"
        )
        bad = client.get(f"/api/file-workspaces/{binding.id}/source-locations/not-a-source")
        assert bad.status_code == 400
        unknown = identifier[:-32] + "0" * 32
        assert (
            client.get(f"/api/file-workspaces/{binding.id}/source-locations/{unknown}").status_code
            == 404
        )
        # 未初始化作品的 GET 不创建状态目录。
        assert not conversation_root(registry.root, other_binding.id).exists()


def test_precise_reference_structure_does_not_accept_client_facts():
    reference = dict(
        workspace_lineage="ws_1", clue_id="clue_1", clue_revision="rev_1", browse_as_of_chapter=2
    )
    assert ClueReference.model_validate(reference).model_dump()["browse_as_of_chapter"] == 2
    for extra in (
        {"browse_as_of_chapter": True},
        {"title": "客户端真相"},
        {"inference_id": "goal"},
    ):
        with pytest.raises(ValidationError):
            ClueReference.model_validate({**reference, **extra})
