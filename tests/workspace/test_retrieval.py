"""有界检索（0.8 F1）。

契约：只查本轮装配的精确版本；未装模块不出现；观察按作品隔离，过期的不当当前依据；
来源与版本随每条结果返回；排序确定性，分页游标绑定查询条件。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import hastings.workspace.retrieval as retrieval_module
from hastings.library import EntryDraft, ModuleLibrary
from hastings.library.bindings import AssemblyService
from hastings.workspace.chapter_index import ChapterIndexDraft, ChapterIndexStore, IndexEvidence
from hastings.workspace.disclosure import ObservedEvidence
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.materials import SourceRef
from hastings.workspace.module_assembly import Assembly, AssemblyStore
from hastings.workspace.retrieval import MaterialQuery, catalog_brief, query_materials
from hastings.workspace.story_index import (
    ChapterProjection,
    EventObservation,
    StoryIndexStore,
    StoryTime,
)

FIRST = "正文/0001 雨夜.md"
EIGHTH = "正文/0008 阁楼.md"
_TEXT_FIRST = "# 雨夜\n\n林静在书房。\n"
_TEXT_EIGHTH = "# 阁楼\n\n管家承认那晚去过书房。\n"


def _library(tmp_path: Path) -> ModuleLibrary:
    return ModuleLibrary(tmp_path / "模块库", cache_file=tmp_path / "cache" / "catalog.json")


def _create(
    library: ModuleLibrary,
    *,
    name: str,
    kind: str = "characters",
    entries: list[EntryDraft],
    key: str,
) -> dict:
    return library.create_module(kind=kind, name=name, entries=entries, request_key=key)


def _workspace(tmp_path: Path) -> Workspace:
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    workspace.write(FIRST, _TEXT_FIRST, expected_digest=None)
    workspace.write(EIGHTH, _TEXT_EIGHTH, expected_digest=None)
    return workspace


def _bind(workspace: Workspace, library: ModuleLibrary, created: dict) -> Assembly:
    module = created["module"]  # type: ignore[index]
    service = AssemblyService(library, workspace)
    service.bind(
        module_id=str(module["module_id"]),
        revision_id=str(module["head"]),
        label=str(module["name"]),
        expected_digest=None,
        request_key="bind-key",
    )
    return AssemblyStore(workspace.root).read()


def _evidence(workspace: Workspace, path: str, quote: str) -> ObservedEvidence:
    view = workspace.read(path)
    start = view.text.index(quote)
    return ObservedEvidence(
        path=path,
        digest=view.digest,
        start=start,
        end=start + len(quote),
        quote=quote,
    )


def _projection(
    workspace: Workspace,
    path: str,
    chapter: int,
    summary: str,
    events: tuple[EventObservation, ...] = (),
) -> ChapterProjection:
    return ChapterProjection(
        path=path,
        digest=workspace.read(path).digest,
        chapter=chapter,
        summary=summary,
        events=events,
    )


def test_only_installed_module_is_queried(tmp_path: Path) -> None:
    library = _library(tmp_path)
    workspace = _workspace(tmp_path)
    first = _create(
        library,
        name="主角团",
        entries=[EntryDraft("lin", "character", "林静", "法医。", ("法医",), {})],
        key="create-1",
    )
    other = _create(
        library,
        name="另一组",
        entries=[EntryDraft("lin", "character", "林静", "另一段人生。", (), {})],
        key="create-2",
    )
    assembly = _bind(workspace, library, first)

    view = query_materials(
        workspace, tmp_path / "state", assembly, library, MaterialQuery(text="林静")
    )

    module_ids = {item["module_ref"][1] for item in view["items"] if item["module_ref"]}
    assert module_ids == {str(first["module"]["module_id"])}  # type: ignore[index]
    assert str(other["module"]["module_id"]) not in module_ids  # type: ignore[index]
    assert all(item["source"] == "author" for item in view["items"])
    assert view["items"][0]["excerpt"].startswith("林静")


def test_explicit_module_scope_allows_one_relation_hop(tmp_path: Path) -> None:
    library = _library(tmp_path)
    workspace = _workspace(tmp_path)
    first = _create(
        library,
        name="主角团",
        entries=[EntryDraft("lin", "character", "林静", "法医。", (), {})],
        key="create-1",
    )
    second = _create(
        library,
        name="关联人物",
        entries=[EntryDraft("he", "character", "何医生", "旧识。", (), {})],
        key="create-2",
    )
    third = _create(
        library,
        name="无关人物",
        entries=[EntryDraft("wu", "character", "吴先生", "无关。", (), {})],
        key="create-3",
    )
    service = AssemblyService(library, workspace)
    for index, created in enumerate((first, second, third), start=1):
        module = created["module"]  # type: ignore[index]
        service.bind(
            module_id=str(module["module_id"]),
            revision_id=str(module["head"]),
            label=str(module["name"]),
            expected_digest=service.view()["digest"],
            request_key=f"bind-key-{index}",
        )
    store = AssemblyStore(workspace.root)
    assembly = store.read()
    ids = {binding.label: binding.id for binding in assembly.bindings}
    store.replace(
        library_id=assembly.library_id,
        bindings=assembly.bindings,
        links=((ids["主角团"], ids["关联人物"]),),
        expected_digest=store.digest(),
        request_key="links-key-1",
    )
    linked = store.read()

    view = query_materials(
        workspace,
        tmp_path / "state",
        linked,
        library,
        MaterialQuery(module_ids=(str(first["module"]["module_id"]),), text="何医生"),  # type: ignore[index]
    )
    assert [item["title"] for item in view["items"]] == ["何医生"]
    assert "relation" in view["items"][0]["matched_by"]

    none = query_materials(
        workspace,
        tmp_path / "state",
        linked,
        library,
        MaterialQuery(module_ids=(str(first["module"]["module_id"]),), text="吴先生"),  # type: ignore[index]
    )
    assert none["items"] == []


def test_uninstalled_module_is_reported_not_silently_queried(tmp_path: Path) -> None:
    library = _library(tmp_path)
    workspace = _workspace(tmp_path)
    created = _create(
        library,
        name="主角团",
        entries=[EntryDraft("lin", "character", "林静", "法医。", (), {})],
        key="create-1",
    )
    assembly = _bind(workspace, library, created)

    view = query_materials(
        workspace,
        tmp_path / "state",
        assembly,
        library,
        MaterialQuery(module_ids=("mod_0000000000000000",), text="林静"),
    )

    assert view["items"] == []
    assert view["scope"]["module_ids"] == []
    assert any("不在本轮装配" in warning for warning in view["warnings"])


def test_observations_keep_source_and_drop_stale_ones(tmp_path: Path) -> None:
    library = _library(tmp_path)
    workspace = _workspace(tmp_path)
    created = _create(
        library,
        name="主角团",
        entries=[EntryDraft("lin", "character", "林静", "法医。", (), {})],
        key="create-1",
    )
    assembly = _bind(workspace, library, created)
    root = tmp_path / "state"
    view = workspace.read(FIRST)
    quote = "林静在书房。"
    start = view.text.index(quote)
    ChapterIndexStore(root).publish(
        ChapterIndexDraft(
            source_path=FIRST,
            source_digest=view.digest,
            summary="林静深夜在书房。",
            evidence=(IndexEvidence(index=1, start=start, end=start + len(quote), quote=quote),),
        ),
        expected_source=SourceRef(FIRST, view.digest),
    )

    found = query_materials(
        workspace, root, assembly, library, MaterialQuery(text="书房", kinds=("chapter_summary",))
    )
    assert found["items"][0]["source"] == "legacy_unverified"
    assert found["items"][0]["kind"] == "chapter_summary"

    workspace.write(FIRST, _TEXT_FIRST + "另一句。\n", expected_digest=view.digest)
    stale = query_materials(
        workspace, root, assembly, library, MaterialQuery(text="书房", kinds=("chapter_summary",))
    )
    assert stale["items"] == []
    assert any("旧版概览" in warning for warning in stale["warnings"])


def test_story_events_keep_testimony_and_support_entity_filter(tmp_path: Path) -> None:
    library = _library(tmp_path)
    workspace = _workspace(tmp_path)
    created = _create(
        library,
        name="主角团",
        entries=[EntryDraft("lin", "character", "林静", "法医。", (), {})],
        key="create-1",
    )
    assembly = _bind(workspace, library, created)
    root = tmp_path / "state"
    ref = ("lib_0123456789abcdef", "mod_0123456789abcdef", "rev_0123456789abcdef", "ent_lin")
    event = EventObservation(
        text="管家声称自己那晚去过书房",
        chapter=8,
        mode="testimony",
        evidence=(_evidence(workspace, EIGHTH, "管家承认那晚去过书房。"),),
        story_time=StoryTime(label="那晚"),
        speaker=ref,
    )
    StoryIndexStore(root).publish(
        _projection(workspace, EIGHTH, 8, "管家承认去过书房。", (event,)),
        expected_digest=workspace.read(EIGHTH).digest,
    )

    hit = query_materials(
        workspace, root, assembly, library, MaterialQuery(kinds=("event",), text="声称")
    )
    assert hit["items"][0]["source"] == "observed"
    assert hit["items"][0]["excerpt"].startswith("管家声称")

    filtered = query_materials(
        workspace, root, assembly, library, MaterialQuery(kinds=("event",), entity_ids=("ent_lin",))
    )
    assert [item["id"] for item in filtered["items"]] == [f"event:{hit['items'][0]['id'][6:]}"]

    none = query_materials(
        workspace, root, assembly, library, MaterialQuery(kinds=("event",), entity_ids=("ent_x",))
    )
    assert none["items"] == []


def test_plans_can_be_excluded(tmp_path: Path) -> None:
    library = _library(tmp_path)
    workspace = _workspace(tmp_path)
    created = _create(
        library,
        name="主线安排",
        kind="plot",
        entries=[EntryDraft("p1", "author_event", "第22章回收怀表", "计划。", (), {})],
        key="create-1",
    )
    assembly = _bind(workspace, library, created)

    with_plans = query_materials(
        workspace, tmp_path / "state", assembly, library, MaterialQuery(text="怀表")
    )
    assert [item["title"] for item in with_plans["items"]] == ["第22章回收怀表"]

    without = query_materials(
        workspace,
        tmp_path / "state",
        assembly,
        library,
        MaterialQuery(text="怀表", include_plans=False),
    )
    assert without["items"] == []


def test_pagination_cursor_is_bound_to_the_query(tmp_path: Path) -> None:
    library = _library(tmp_path)
    workspace = _workspace(tmp_path)
    entries = [
        EntryDraft(f"e{index:02d}", "character", f"角色{index:02d}", "说明。", (), {})
        for index in range(25)
    ]
    created = _create(library, name="群像", entries=entries, key="create-1")
    assembly = _bind(workspace, library, created)

    first = query_materials(
        workspace, tmp_path / "state", assembly, library, MaterialQuery(text="角色", limit=20)
    )
    assert len(first["items"]) == 20
    assert first["next_cursor"]
    metrics = first["metrics"]
    assert metrics["candidates"] == 25
    assert metrics["returned"] == 20
    assert metrics["scanned_entries"] == 0
    assert metrics["sources"]["author"] == 25
    second = query_materials(
        workspace,
        tmp_path / "state",
        assembly,
        library,
        MaterialQuery(text="角色", limit=20, cursor=first["next_cursor"]),
    )
    assert len(second["items"]) == 5

    with pytest.raises(WorkspaceError) as excinfo:
        query_materials(
            workspace,
            tmp_path / "state",
            assembly,
            library,
            MaterialQuery(text="角色", limit=20, cursor=first["next_cursor"], kinds=("character",)),
        )
    assert excinfo.value.code == "RETRIEVAL_CURSOR_STALE_CONFLICT"


def test_pagination_cursor_expires_when_a_source_changes(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    root = tmp_path / "state"
    store = StoryIndexStore(root)
    store.publish(
        _projection(workspace, FIRST, 1, "林静在书房。"),
        expected_digest=workspace.read(FIRST).digest,
    )
    store.publish(
        _projection(workspace, EIGHTH, 8, "管家在书房承认那晚的事。"),
        expected_digest=workspace.read(EIGHTH).digest,
    )

    first = query_materials(workspace, root, Assembly(), None, MaterialQuery(text="书房", limit=1))
    assert len(first["items"]) == 1
    assert first["items"][0]["id"].endswith(FIRST)
    assert first["next_cursor"]

    # 翻页期间第一章被外部改写：旧摘要失效，结果集只剩第二章。
    workspace.write(FIRST, "# 雨夜\n\n林静离开了。\n", expected_digest=workspace.read(FIRST).digest)

    with pytest.raises(WorkspaceError) as excinfo:
        query_materials(
            workspace,
            root,
            Assembly(),
            None,
            MaterialQuery(text="书房", limit=1, cursor=first["next_cursor"]),
        )
    assert excinfo.value.code == "RETRIEVAL_CURSOR_STALE_CONFLICT"


def test_long_entry_body_is_found_while_detail_stays_complete(tmp_path: Path) -> None:
    library = _library(tmp_path)
    workspace = _workspace(tmp_path)
    body = "长" * 500
    created = _create(
        library,
        name="长条目",
        entries=[EntryDraft("long", "character", "长人物", body, (), {})],
        key="create-1",
    )
    assembly = _bind(workspace, library, created)

    title_hit = query_materials(
        workspace, tmp_path / "state", assembly, library, MaterialQuery(text="长人物")
    )
    # 结构字段命中只给定位，不把整段正文塞进列表。
    assert "长人物" in title_hit["items"][0]["excerpt"]
    assert len(title_hit["items"][0]["excerpt"]) < 240

    body_hit = query_materials(
        workspace, tmp_path / "state", assembly, library, MaterialQuery(text="长长长")
    )
    assert "长长长" in body_hit["items"][0]["excerpt"]
    assert len(body_hit["items"][0]["excerpt"]) <= 240
    assert body_hit["metrics"]["scanned_entries"] == 1
    assert body_hit["metrics"]["scanned_chars"] > 0
    assert body_hit["metrics"]["scan_limited"] is False

    entry = library.read_entry(
        module_id=str(created["module"]["module_id"]),  # type: ignore[index]
        revision_id=str(created["module"]["head"]),  # type: ignore[index]
        entry_id="long",
    )
    assert body in str(entry["text"])


def test_scan_limit_is_reported_instead_of_silently_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = _library(tmp_path)
    workspace = _workspace(tmp_path)
    created = _create(
        library,
        name="长条目",
        entries=[EntryDraft("long", "character", "长人物", "动机" + "长" * 400, (), {})],
        key="create-1",
    )
    assembly = _bind(workspace, library, created)
    monkeypatch.setattr(retrieval_module, "MAX_SCAN_CHARS", 10)

    view = query_materials(
        workspace, tmp_path / "state", assembly, library, MaterialQuery(text="动机")
    )

    assert view["metrics"]["scan_limited"] is True
    assert any("只扫了部分条目正文" in warning for warning in view["warnings"])


def test_catalog_brief_lists_installed_bindings_and_chapters(tmp_path: Path) -> None:
    library = _library(tmp_path)
    workspace = _workspace(tmp_path)
    created = _create(
        library,
        name="主角团",
        entries=[EntryDraft("lin", "character", "林静", "法医。", (), {})],
        key="create-1",
    )
    assembly = _bind(workspace, library, created)

    brief = catalog_brief(workspace, assembly, library)
    assert brief["current_chapter"] == 8
    assert brief["bindings"][0]["module_id"] == str(created["module"]["module_id"])  # type: ignore[index]
    assert brief["bindings"][0]["entry_count"] == 1
    assert [item["chapter"] for item in brief["chapters"]] == [1, 8]
    assert brief["total_chapters"] == 2
