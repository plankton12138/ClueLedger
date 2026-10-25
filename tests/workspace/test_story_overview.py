"""总览的作品隔离、来源、分页、只读权限与统一任务回归。"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.workspace.test_chat import ScriptedProvider, _snapshot, _text_turn, _tool_turn

from hastings.library.modules import ModuleLibrary
from hastings.library.records import EntryDraft
from hastings.server.file_app import create_workspace_app
from hastings.workspace.agent import RunContext, build_tools
from hastings.workspace.disclosure import ObservedEvidence
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.module_assembly import AssemblyStore, Binding, ModuleRef
from hastings.workspace.module_facets import module_projection
from hastings.workspace.overview_models import ModuleSource
from hastings.workspace.overview_updates import overview_tools
from hastings.workspace.service import TurnService, WriteSession, _tool_mode
from hastings.workspace.state import ConversationStore, RecentWorkspaces, conversation_root
from hastings.workspace.story_index import (
    ChapterProjection,
    EventObservation,
    StoryIndexStore,
    StoryTime,
)
from hastings.workspace.story_overview import StoryOverview, page
from tau_agent.messages import ToolCall


def setup(tmp_path: Path, *, chapter: bool = False) -> tuple[Workspace, Path, ModuleLibrary]:
    workspace = Workspace.create(tmp_path / "作品")
    root = tmp_path / "state"
    library = ModuleLibrary(tmp_path / "library")
    created = library.create_module(
        kind="reference",
        name="人物与物件",
        entries=[
            EntryDraft(
                "ent_0123456789abcdef", "reference", "内容", "𠮷是林远的姐姐。林远带着一枚铜钥匙。"
            )
        ],
        request_key="create-mixed-module",
    )
    binding = Binding(
        "bind_0123456789abcdef",
        ModuleRef(
            library.library_id or "",
            str(created["module"]["module_id"]),
            str(created["revision"]["revision_id"]),
        ),
        "旅馆设定",
    )  # type: ignore[index]
    AssemblyStore(workspace.root).replace(
        library_id=library.library_id,
        bindings=[binding],
        expected_digest=None,
        request_key="attach-mixed-module",
    )
    if chapter:
        workspace.write(
            "正文/0001 雨夜.md", "# 雨夜\n\n林远说他从没见过铜钥匙。", expected_digest=None
        )
    return workspace, root, library


def extraction(text: str) -> dict[str, Any]:
    def evidence(quote: str) -> dict[str, Any]:
        start = text.index(quote)
        return {"start": start, "end": start + len(quote), "quote": quote}

    return {
        "outcome": "found",
        "facets": [
            {
                "kind": "character",
                "name": "𠮷",
                "summary": "林远的姐姐",
                "evidence": evidence("𠮷是林远的姐姐。"),
            },
            {
                "kind": "character",
                "name": "林远",
                "summary": "带着铜钥匙",
                "evidence": evidence("林远带着一枚铜钥匙。"),
            },
            {
                "kind": "clue",
                "name": "铜钥匙",
                "summary": "林远携带的物件",
                "evidence": evidence("铜钥匙"),
            },
        ],
        "relations": [
            {"left": 0, "right": 1, "label": "姐弟", "evidence": evidence("𠮷是林远的姐姐。")}
        ],
    }


def source_and_text(overview: StoryOverview) -> tuple[dict[str, Any], str]:
    target = overview.module_targets[0]
    source = target["source"]
    assert overview.library
    text, _ = overview.library.storage.read_entry(
        source["module_id"], source["revision_id"], source["entry_id"]
    )
    return target, text


def publish_fixture(overview: StoryOverview) -> list[dict[str, Any]]:
    target, text = source_and_text(overview)
    projection = module_projection(ModuleSource(**target["source"]), extraction(text), text)
    overview.store.publish(target, projection, "fixture")
    return projection["facets"]


def test_reading_empty_and_mixed_modules_never_calls_a_model(tmp_path: Path) -> None:
    workspace, root, library = setup(tmp_path)
    before = StoryOverview(workspace, root, library)
    assert before.as_of == 0
    assert before.manifest()["bindings"]["items"][0]["display_name"] == "旅馆设定"
    assert before.bindings[0]["capabilities"] == ["content"]
    facets = publish_fixture(before)
    after = StoryOverview(workspace, root, library)
    assert after.bindings[0]["capabilities"] == ["content", "characters", "clues"]
    assert len({f["ref"]["facet_id"] for f in facets}) == 3
    result = after.relations(after.bindings[0]["binding_id"], facets[0]["ref"]["facet_id"], None)
    assert len(result["nodes"]) == 2
    assert result["items"][0]["label"] == "姐弟"
    assert result["items"][0]["nature"] == "declared"
    other = Workspace.create(tmp_path / "另一作品")
    assert StoryOverview(other, tmp_path / "other-state", library).facets() == []


def test_invalid_evidence_never_replaces_valid_projection(tmp_path: Path) -> None:
    workspace, root, library = setup(tmp_path)
    overview = StoryOverview(workspace, root, library)
    publish_fixture(overview)
    target, text = source_and_text(overview)
    previous = overview.store.read(target["id"])
    raw = extraction(text)
    raw["facets"][0]["evidence"]["start"] += 1  # Unicode 码点错位。
    with pytest.raises(WorkspaceError, match="OVERVIEW_EVIDENCE_INVALID"):
        module_projection(ModuleSource(**target["source"]), raw, text)
    assert overview.store.read(target["id"]) == previous


def test_two_characters_in_one_quote_have_distinct_source_anchors(tmp_path: Path) -> None:
    workspace, root, library = setup(tmp_path)
    overview = StoryOverview(workspace, root, library)
    target, text = source_and_text(overview)
    raw = extraction(text)
    raw["facets"] = raw["facets"][:2]
    raw["facets"][1]["evidence"] = dict(raw["facets"][0]["evidence"])
    result = module_projection(ModuleSource(**target["source"]), raw, text)
    assert result["facets"][0]["ref"]["facet_id"] != result["facets"][1]["ref"]["facet_id"]


def test_partial_attempt_preserves_last_published_result_and_invalidates_generation(
    tmp_path: Path,
) -> None:
    workspace, root, library = setup(tmp_path)
    overview = StoryOverview(workspace, root, library)
    publish_fixture(overview)
    target = overview.module_targets[0]
    previous = overview.store.read(target["id"])
    before = StoryOverview(workspace, root, library).generation
    overview.store.publish(
        target, {"outcome": "insufficient", "facets": [], "relations": []}, "failed-attempt"
    )
    assert overview.store.read(target["id"]) == previous
    assert overview.store.read(target["id"] + ":attempt")["outcome"] == "insufficient"
    assert StoryOverview(workspace, root, library).generation != before


def test_unmount_undo_and_same_name_new_revision_do_not_reuse_facets(tmp_path: Path) -> None:
    workspace, root, library = setup(tmp_path)
    first = StoryOverview(workspace, root, library)
    facets = publish_fixture(first)
    assembly = AssemblyStore(workspace.root)
    saved = assembly.read()
    assembly.replace(
        library_id=library.library_id,
        bindings=[],
        expected_digest=assembly.digest(),
        request_key="detach-current-binding",
    )
    assert StoryOverview(workspace, root, library).facets() == []
    assembly.replace(
        library_id=library.library_id,
        bindings=saved.bindings,
        expected_digest=assembly.digest(),
        request_key="undo-current-binding",
    )
    assert StoryOverview(workspace, root, library).facets()[0]["ref"] == facets[0]["ref"]
    binding = saved.bindings[0]
    result = library.save_workspace_revision(
        module_id=binding.module.module_id,
        base_revision=binding.module.revision_id,
        entries=[EntryDraft("ent_0123456789abcdef", "reference", "内容", "𠮷是另一位旅客。")],
        request_key="workspace-next-version",
    )
    revision = result.revision_id
    assembly.replace(
        library_id=library.library_id,
        bindings=[
            Binding(
                binding.id,
                ModuleRef(library.library_id or "", binding.module.module_id, str(revision)),
                binding.label,
            )
        ],
        expected_digest=assembly.digest(),
        request_key="bind-next-version",
    )
    current = StoryOverview(workspace, root, library)
    assert current.facets() == []
    assert current.bindings[0]["capabilities"] == ["content"]
    with pytest.raises(WorkspaceError, match="OVERVIEW_REF_UNAVAILABLE"):
        current.detail(binding.id, facets[0]["ref"]["facet_id"], None, 40)


def test_cursor_generation_and_query_cannot_be_mixed() -> None:
    rows = list(range(205))
    first = page(rows, ["generation-a", 8], None, 100)
    assert len(first["items"]) == 100
    second = page(rows, ["generation-a", 8], first["next_cursor"], 100)
    assert second["items"][0] == 100
    for key in (["generation-b", 8], ["generation-a", 3]):
        with pytest.raises(WorkspaceError, match="OVERVIEW_CURSOR_STALE"):
            page(rows, key, first["next_cursor"], 100)


def test_relation_pages_keep_endpoints_and_expose_remaining_edges(tmp_path: Path) -> None:
    workspace, root, library = setup(tmp_path)
    assembly = AssemblyStore(workspace.root)
    binding = assembly.read().bindings[0]
    body = "\n".join(f"人物{i}是主角的同事。" for i in range(1, 51))
    revision = library.save_workspace_revision(
        module_id=binding.module.module_id,
        base_revision=binding.module.revision_id,
        entries=[EntryDraft("ent_0123456789abcdef", "reference", "人物", body)],
        request_key="many-related-characters",
    )
    assembly.replace(
        library_id=library.library_id,
        bindings=[
            Binding(
                binding.id,
                ModuleRef(library.library_id or "", binding.module.module_id, revision.revision_id),
            )
        ],
        expected_digest=assembly.digest(),
        request_key="bind-many-related",
    )
    overview = StoryOverview(workspace, root, library)
    target, text = source_and_text(overview)

    def quote(value: str) -> dict[str, Any]:
        start = text.index(value)
        return {"start": start, "end": start + len(value), "quote": value}

    facets = [
        {
            "kind": "character",
            "name": "主角",
            "summary": "在场人物",
            "evidence": quote("人物1是主角的同事。"),
        }
    ] + [
        {
            "kind": "character",
            "name": f"人物{i}",
            "summary": "主角的同事",
            "evidence": quote(f"人物{i}是主角的同事。"),
        }
        for i in range(1, 51)
    ]
    relations = [
        {"left": 0, "right": i, "label": "同事", "evidence": quote(f"人物{i}是主角的同事。")}
        for i in range(1, 51)
    ]
    result = module_projection(
        ModuleSource(**target["source"]),
        {"outcome": "found", "facets": facets, "relations": relations},
        text,
    )
    overview.store.publish(target, result, "graph-fixture")
    current = StoryOverview(workspace, root, library)
    selected = result["facets"][0]["ref"]["facet_id"]
    first = current.relations(binding.id, selected, None)
    assert len(first["items"]) == 39 and len(first["nodes"]) == 40
    assert first["next_cursor"] and first["total"] == 50
    second = current.relations(binding.id, selected, first["next_cursor"])
    assert len(second["items"]) == 11 and len(second["nodes"]) == 12
    assert second["next_cursor"] is None


@pytest.mark.anyio
async def test_overview_tools_require_actual_full_read_and_have_no_write_capability(
    tmp_path: Path,
) -> None:
    workspace, root, library = setup(tmp_path)
    overview = StoryOverview(workspace, root, library)
    target, text = source_and_text(overview)
    run = RunContext(workspace, WriteSession(workspace), turn_id="test", library=library)
    scope = {"assembly_digest": overview.assembly_digest}
    tools = {t.name: t for t in overview_tools(run, scope, target, overview)}
    assert set(tools) == {"read_overview_source", "publish_overview_result"}
    assert _tool_mode("organize_overview") == "overview"
    with pytest.raises(WorkspaceError, match="OVERVIEW_SCOPE_INVALID"):
        build_tools(workspace, run, mode="overview")
    with pytest.raises(WorkspaceError, match="尚未完整读取"):
        await tools["publish_overview_result"].execute("premature", {"result": extraction(text)})
    read = await tools["read_overview_source"].execute("read", {})
    assert "𠮷是林远的姐姐" in read.text
    await tools["publish_overview_result"].execute("publish", {"result": extraction(text)})
    assert not run.session.prepared
    assert len(StoryOverview(workspace, root, library).facets()) == 3


@pytest.mark.anyio
async def test_pure_module_update_runs_in_one_turn_and_replays_same_key(tmp_path: Path) -> None:
    workspace, root, library = setup(tmp_path)
    overview = StoryOverview(workspace, root, library)
    _, text = source_and_text(overview)
    provider = ScriptedProvider(
        [
            _tool_turn(ToolCall(id="read", name="read_overview_source", arguments={})),
            _tool_turn(
                ToolCall(
                    id="publish",
                    name="publish_overview_result",
                    arguments={"result": extraction(text)},
                )
            ),
            _text_turn("已整理"),
        ]
    )
    service = TurnService(
        workspace, ConversationStore(root), lambda: _snapshot(provider), library=library
    )
    request = {
        "request_key": "overview-first-update",
        "as_of_chapter": 0,
        "observed_generation": overview.generation,
    }
    info = await service.accept(
        "更新故事总览",
        request["request_key"],
        purpose="organize_overview",
        material_scope={"domain": "overview", "request": request},
    )
    await service.wait(info.turn_id)
    events = service.store.events(info.conversation_id)
    assert next(e for e in events if e["type"] == "run_finished")["status"] == "completed"
    assert len(StoryOverview(workspace, root, library).facets()) == 3
    assert all(
        {t.name for t in r["tools"]} == {"read_overview_source", "publish_overview_result"}
        for r in provider.requests
    )
    replay = await service.accept(
        "更新故事总览",
        request["request_key"],
        purpose="organize_overview",
        material_scope={"domain": "overview", "request": request},
    )
    assert replay.turn_id == info.turn_id
    assert provider.calls == 3
    request["as_of_chapter"] = 1
    with pytest.raises(WorkspaceError, match="REQUEST_KEY_CONFLICT"):
        await service.accept(
            "更新故事总览",
            request["request_key"],
            purpose="organize_overview",
            material_scope={"domain": "overview", "request": request},
        )


def test_empty_api_update_is_noop_and_old_material_list_stays_compatible(tmp_path: Path) -> None:
    calls = []

    def factory() -> Any:
        calls.append(1)
        raise AssertionError("不能调用模型")

    workspaces = RecentWorkspaces(tmp_path / "app-state")
    with TestClient(
        create_workspace_app(workspaces=workspaces, provider_factory=factory)
    ) as client:
        book = client.post(
            "/api/file-workspaces", json={"path": str(tmp_path / "空作品"), "create": True}
        ).json()
        base = f"/api/file-workspaces/{book['id']}"
        manifest = client.get(base + "/story-overview").json()
        assert manifest["bindings"]["items"] == []
        result = client.post(
            base + "/story-overview/updates",
            json={
                "request_key": "empty-overview-update",
                "observed_generation": manifest["generation"],
                "as_of_chapter": 0,
            },
        )
        assert result.status_code == 200 and result.json()["state"] == "noop"
        assert client.get(base + "/material-tasks").status_code == 200
        assert calls == []


def chapter_projection(
    workspace: Workspace,
    root: Path,
    chapter: int,
    *,
    before: tuple[str, ...] = (),
    event_id: str = "",
    mode: str = "testimony",
) -> None:
    path = next(e.path for e in workspace.catalog().chapters if e.chapter_number == chapter)
    source = workspace.read(path)
    quote = source.text.splitlines()[-1]
    start = source.text.index(quote)
    event = EventObservation(
        text=quote,
        chapter=chapter,
        mode=mode,
        evidence=(
            ObservedEvidence(
                path=path, digest=source.digest, start=start, end=start + len(quote), quote=quote
            ),
        ),
        id=event_id,
        story_time=StoryTime(before=before),
    )  # type: ignore[arg-type]
    StoryIndexStore(root).publish(
        ChapterProjection(
            path=path, digest=source.digest, chapter=chapter, summary="一条证词。", events=(event,)
        ),
        expected_digest=source.digest,
    )


def test_story_time_computed_before_paging_and_cycle_sources_are_readable(tmp_path: Path) -> None:
    workspace, root, library = setup(tmp_path, chapter=True)
    workspace.write("正文/0002 后续.md", "# 后续\n\n她说自己见过钥匙。", expected_digest=None)
    chapter_projection(workspace, root, 1, before=("event-b",), event_id="event-a")
    chapter_projection(workspace, root, 2, before=("event-a",), event_id="event-b")
    overview = StoryOverview(workspace, root, library)
    first = overview.events("story", None, 1)
    assert first["conflict_count"] == 1
    assert (
        first["items"][0]["event"]["evidence"][0]["digest"]
        == workspace.read("正文/0001 雨夜.md").digest
    )
    second = overview.events("story", first["next_cursor"], 1)
    assert second["items"][0]["group"] == first["items"][0]["group"]
    clipped = StoryOverview(workspace, root, library, 1)
    assert clipped.events("story", None, 40)["conflict_count"] == 0
    assert len(clipped.manifest()["chapters"]["items"]) == 2  # 截止章不限制章节选择器。


def test_links_keep_testimony_cutoff_and_exact_version_and_expire_on_chapter_edit(
    tmp_path: Path,
) -> None:
    workspace, root, library = setup(tmp_path, chapter=True)
    original = StoryOverview(workspace, root, library)
    facets = publish_fixture(original)
    chapter_projection(workspace, root, 1)
    overview = StoryOverview(workspace, root, library)
    target = overview.link_targets[0]
    event = overview.index[target["path"]].events[0]
    quote = event.evidence[0]
    overview.store.publish(
        target,
        {
            "outcome": "found",
            "links": [
                {
                    "ref": facets[2]["ref"],
                    "event_id": event.id,
                    "text": "林远否认见过钥匙。",
                    "evidence": {"start": quote.start, "end": quote.end, "quote": quote.quote},
                }
            ],
        },
        "test",
    )
    current = StoryOverview(workspace, root, library)
    detail = current.detail(
        facets[2]["ref"]["source"]["binding_id"], facets[2]["ref"]["facet_id"], None, 40
    )
    assert detail["observations"]["items"][0]["mode"] == "testimony"
    assert current.events("chapter", None, 40)["items"][0]["related_facets"][0]["name"] == "铜钥匙"
    assert StoryOverview(workspace, root, library, 0).observations(facets[2]["ref"]) == []
    source = workspace.read(target["path"])
    workspace.write(
        target["path"], source.text + "\n原文有了新变化。", expected_digest=source.digest
    )
    changed = StoryOverview(workspace, root, library)
    assert changed.observations(facets[2]["ref"]) == []
    assert changed.bindings[0]["capabilities"] == ["content", "characters", "clues"]
    assert changed.events("chapter", None, 40)["items"][0]["chapter"]["status"] == "stale"


@pytest.mark.anyio
async def test_partial_and_stop_preserve_completed_module_and_restart_does_not_resume(
    tmp_path: Path,
) -> None:
    workspace, root, library = setup(tmp_path, chapter=True)
    overview = StoryOverview(workspace, root, library)
    _, text = source_and_text(overview)
    provider = ScriptedProvider(
        [
            _tool_turn(ToolCall(id="read", name="read_overview_source", arguments={})),
            _tool_turn(
                ToolCall(
                    id="publish",
                    name="publish_overview_result",
                    arguments={"result": extraction(text)},
                )
            ),
            _text_turn("模块已整理"),
        ],
        hang_from=3,
    )
    service = TurnService(
        workspace, ConversationStore(root), lambda: _snapshot(provider), library=library
    )
    scope = {
        "domain": "overview",
        "request": {
            "request_key": "stop-overview-command",
            "as_of_chapter": 1,
            "observed_generation": overview.generation,
        },
    }
    info = await service.accept(
        "更新故事总览", "stop-overview-command", purpose="organize_overview", material_scope=scope
    )
    for _ in range(100):
        if provider.calls >= 4:
            break
        await asyncio.sleep(0.01)
    assert provider.calls == 4
    with pytest.raises(WorkspaceError, match="WORKSPACE_BUSY_CONFLICT"):
        await service.accept("另一个任务", "other-command")
    assert await service.stop(info.turn_id)
    await service.wait(info.turn_id)
    assert len(StoryOverview(workspace, root, library).facets()) == 3
    assert [e for e in service.store.events(info.conversation_id) if e["type"] == "run_finished"][
        -1
    ]["status"] == "stopped"
    restarted = TurnService(
        workspace, ConversationStore(root), lambda: _snapshot(provider), library=library
    )
    restarted.mark_interrupted()
    assert provider.calls == 4
    assert restarted.active_turn() is None


@pytest.mark.anyio
async def test_changed_source_after_accept_is_rejected_before_read_or_publish(
    tmp_path: Path,
) -> None:
    workspace, root, library = setup(tmp_path, chapter=True)
    overview = StoryOverview(workspace, root, library)
    target = overview.chapter_targets[0]
    run = RunContext(workspace, WriteSession(workspace), library=library)
    tools = {
        t.name: t
        for t in overview_tools(
            run, {"assembly_digest": overview.assembly_digest}, target, overview
        )
    }
    source = workspace.read(target["path"])
    workspace.write(target["path"], source.text + "新版本", expected_digest=source.digest)
    with pytest.raises(WorkspaceError, match="OVERVIEW_SOURCE_CHANGED"):
        await tools["read_overview_source"].execute("read", {})
    assert StoryIndexStore(root).read_all() == {}


def test_long_book_pages_are_bounded_and_old_generation_is_rejected(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path / "长篇")
    root = tmp_path / "state"
    for chapter in range(1, 121):
        (workspace.root / "正文" / f"{chapter:04d} 章节.md").write_text(
            f"# 章节\n\n第{chapter}章。", encoding="utf-8"
        )
    started = time.monotonic()
    overview = StoryOverview(workspace, root, None)
    manifest = overview.manifest(limit=40)
    assert manifest["chapters"]["total"] == 120
    assert len(manifest["chapters"]["items"]) == 40
    events = overview.events("chapter", None, 40)
    assert len(events["items"]) == 40 and events["next_cursor"]
    assert (
        overview.events("chapter", events["next_cursor"], 40)["items"][0]["chapter"]["chapter"]
        == 41
    )
    assert time.monotonic() - started < 10
    source = workspace.read("正文/0001 章节.md")
    workspace.write(source.path, source.text + "新的内容。", expected_digest=source.digest)
    changed = StoryOverview(workspace, root, None)
    with pytest.raises(WorkspaceError, match="OVERVIEW_SOURCE_CHANGED"):
        changed.require_generation(overview.generation)


def test_http_update_replay_progress_noop_and_legacy_list(tmp_path: Path) -> None:
    workspace, _, library = setup(tmp_path)
    workspaces = RecentWorkspaces(tmp_path / "app")
    book = workspaces.bind(workspace.root)
    root = conversation_root(workspaces.root, book.id)
    overview = StoryOverview(workspace, root, library)
    _, text = source_and_text(overview)
    provider = ScriptedProvider(
        [
            _tool_turn(ToolCall(id="read", name="read_overview_source", arguments={})),
            _tool_turn(
                ToolCall(
                    id="publish",
                    name="publish_overview_result",
                    arguments={"result": extraction(text)},
                )
            ),
            _text_turn("已整理"),
        ]
    )
    with TestClient(
        create_workspace_app(
            workspaces=workspaces, provider_factory=lambda: _snapshot(provider), library=library
        )
    ) as client:
        base = f"/api/file-workspaces/{book.id}/story-overview"
        manifest = client.get(base).json()
        request = {
            "request_key": "http-overview-command",
            "observed_generation": manifest["generation"],
            "as_of_chapter": 0,
        }
        accepted = client.post(base + "/updates", json=request)
        assert accepted.status_code == 202
        turn_id = accepted.json()["turn_id"]
        deadline = time.monotonic() + 5
        progress = {}
        while time.monotonic() < deadline:
            progress = client.get(base + f"/updates/{turn_id}").json()
            if progress.get("run_status") != "running":
                break
            time.sleep(0.01)
        assert progress["outcome"] == "complete" and progress["completed"] == 1
        assert client.post(base + "/updates", json=request).json()["turn_id"] == turn_id
        assert provider.calls == 3
        assert client.get(f"/api/file-workspaces/{book.id}/material-tasks").json()["items"] == []
        current = client.get(base).json()
        noop = {
            **request,
            "request_key": "http-noop-command",
            "observed_generation": current["generation"],
        }
        assert client.post(base + "/updates", json=noop).json()["state"] == "noop"
        assert client.post(base + "/updates", json={**noop, "as_of_chapter": 1}).status_code == 409
        assert provider.calls == 3
