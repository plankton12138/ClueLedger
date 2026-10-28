"""S2：真实来源、码点区间、移动记录和作品边界；浏览不调用模型。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from hastings.library import EntryDraft, ModuleLibrary
from hastings.library.bindings import AssemblyService
from hastings.server.file_app import create_workspace_app
from hastings.workspace.clue_models import (
    ClueDesignLink,
    ClueEntry,
    ClueReference,
    FileSource,
    ModuleSource,
)
from hastings.workspace.clues import ClueStore, publish_clues
from hastings.workspace.disclosure import ObservedEvidence
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.source_locations import evidence_records, resolve_source_location
from hastings.workspace.state import ConversationStore, RecentWorkspaces, conversation_root
from hastings.workspace.story_materials import author_view

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


def source_id(workspace, store, as_of=2):
    return author_view(workspace, store.root, "clues", as_of_chapter=as_of)["entries"][0][
        "evidence"
    ][0]["evidence_id"]


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
    late = author_view(workspace, store.root, "clues", as_of_chapter=2)
    early = author_view(workspace, store.root, "clues", as_of_chapter=1)
    assert len(late["entries"][0]["evidence"]) == 2
    assert len(early["entries"][0]["evidence"]) == 1
    assert "公开照片也是来源" not in json.dumps(early, ensure_ascii=False)
    identifier = late["entries"][0]["evidence"][1]["evidence_id"]
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


def test_character_metadata_uses_full_source_positions(book):
    workspace, store, _ = book
    path = "设定集/角色小传/林岚.md"
    workspace.write(
        path, "---\nroles: [suspect]\n---\n# 林岚\n\n🔑背面计划。", expected_digest=None
    )
    view = workspace.read(path)
    quote = "🔑背面计划。"
    start = view.text.index(quote)

    def link(ledger):
        ledger.design_links["plan"] = ClueDesignLink(
            link_id="plan",
            clue_id=store.entries()[0].clue_id,
            source=FileSource(path=path, digest=view.digest),
            start=start,
            end=start + len(quote),
            quote=quote,
            created_at="2026-10-27T10:00:00+08:00",
            created_by="test",
            created_revision=ledger.ledger_revision,
        )
        return {}

    store.ledger.mutate(
        expected_revision=store.ledger.read().revision,
        request_key="plan",
        kind="test",
        payload={},
        apply=link,
    )
    identifier = next(
        row.evidence_id
        for row in evidence_records(workspace, store.ledger.read())
        if row.chapter is None
    )
    result = resolve_source_location(workspace, store.root, identifier)
    assert view.text != view.display_text and result.start == start
    assert result.freshness == "valid"


@pytest.mark.parametrize("change", ["head_only", "upgrade", "unbind"])
def test_module_location_reads_exact_revision_and_detects_detachment(book, tmp_path, change):
    workspace, store, _ = book
    library = ModuleLibrary(tmp_path / "library", cache_file=tmp_path / "cache.json")
    created = library.create_module(
        kind="reference",
        name="遗书计划",
        entries=[EntryDraft("letter", "reference", "背面", "原计划🔑背面的字。")],
        request_key="create-module",
    )
    module = created["module"]
    module_id, revision_id = str(module["module_id"]), str(module["head"])
    assembly = AssemblyService(library, workspace)
    mounted = assembly.bind(
        module_id=module_id,
        revision_id=revision_id,
        expected_digest=None,
        request_key="bind-module",
    )
    binding = mounted["bindings"][0]
    text, digest = library.storage.read_entry(module_id, revision_id, "letter")
    quote = "原计划🔑背面的字。"
    start = text.index(quote)

    def link(ledger):
        ledger.design_links["plan"] = ClueDesignLink(
            link_id="plan",
            clue_id=store.entries()[0].clue_id,
            source=ModuleSource(
                library_id=str(library.library_id),
                module_id=module_id,
                revision_id=revision_id,
                entry_id="letter",
                content_digest=digest,
            ),
            binding_id=binding["id"],
            start=start,
            end=start + len(quote),
            quote=quote,
            created_at="2026-10-27T10:00:00+08:00",
            created_by="test",
            created_revision=ledger.ledger_revision,
        )
        return {}

    store.ledger.mutate(
        expected_revision=store.ledger.read().revision,
        request_key="plan",
        kind="test",
        payload={},
        apply=link,
    )
    identifier = next(
        row.evidence_id
        for row in evidence_records(workspace, store.ledger.read())
        if row.chapter is None
    )
    assert (
        resolve_source_location(workspace, store.root, identifier, library=library).freshness
        == "valid"
    )
    newer = library.publish(
        module_id=module_id,
        base_revision=revision_id,
        expected_head=revision_id,
        entries=[EntryDraft("letter", "reference", "背面", "新版秘密不应替换旧依据。")],
        request_key="new-version",
    )
    if change == "upgrade":
        assembly.upgrade(
            binding_id=binding["id"],
            revision_id=newer.revision_id,
            expected_digest=mounted["digest"],
            request_key="upgrade-module",
        )
    if change == "unbind":
        assembly.unbind(
            binding_id=binding["id"], expected_digest=mounted["digest"], request_key="unbind-module"
        )
    result = resolve_source_location(workspace, store.root, identifier, library=library)
    assert result.source.revision_id == revision_id and "新版秘密" not in result.excerpt
    assert result.freshness == ("valid" if change == "head_only" else "detached")
    if change != "head_only":
        assert result.start is None and result.end is None


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
