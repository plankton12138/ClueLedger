"""派生新作品（0.8 D）。

契约：只复制装配、不带正文与灵感；替换项必须指向已装入的模块与存在的版本；
同一请求键重试继续未完成的写入；目标目录已存在且不是这次请求建的，直接冲突。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hastings.library import EntryDraft, ModuleLibrary
from hastings.library.bindings import AssemblyService
from hastings.library.derive import DERIVE_MARKER, derive_workspace
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.module_assembly import AssemblyStore


def _setup(tmp_path: Path) -> tuple[ModuleLibrary, AssemblyService, Workspace, dict, dict]:
    library = ModuleLibrary(tmp_path / "库", cache_file=tmp_path / "cache" / "catalog.json")
    characters = library.create_module(
        kind="characters",
        name="主角团",
        entries=[EntryDraft("lin", "character", "林静", "法医。", (), {"roles": ["suspect"]})],
        request_key="derive-lib-characters",
    )
    world = library.create_module(
        kind="world",
        name="海上城市",
        entries=[EntryDraft("port", "world_rule", "港口", "终年起雾。")],
        request_key="derive-lib-world",
    )
    workspace = Workspace.create(tmp_path / "源作品")
    service = AssemblyService(library, workspace)
    first = service.bind(
        module_id=str(characters["module"]["module_id"]),  # type: ignore[index]
        revision_id=str(characters["module"]["head"]),  # type: ignore[index]
        expected_digest=None,
        request_key="derive-bind-1",
    )
    service.bind(
        module_id=str(world["module"]["module_id"]),  # type: ignore[index]
        revision_id=str(world["module"]["head"]),  # type: ignore[index]
        expected_digest=str(first["digest"]),
        request_key="derive-bind-2",
    )
    return library, service, workspace, characters, world


def test_derive_copies_assembly_without_content(tmp_path: Path) -> None:
    library, service, source, _, _ = _setup(tmp_path)
    target = tmp_path / "新作品"

    result = derive_workspace(
        library,
        source=source,
        source_workspace_id="ws-source",
        target=target,
        expected_digest=str(service.view()["digest"]),
        request_key="derive-key-1",
    )

    assert result.root == target.resolve()
    assert result.recovered is False
    assert (target / DERIVE_MARKER).is_file()
    assert (target / "正文").is_dir()
    assert not any((target / "正文").iterdir())
    assert not (target / "灵感本").exists()
    assert [item["module"]["module_id"] for item in result.assembly["bindings"]] == [  # type: ignore[index]
        item["module"]["module_id"]
        for item in service.view()["bindings"]  # type: ignore[index]
    ]
    assert [item["module"]["revision_id"] for item in result.assembly["bindings"]] == [  # type: ignore[index]
        item["module"]["revision_id"]
        for item in service.view()["bindings"]  # type: ignore[index]
    ]


def test_replacements_switch_only_the_new_workspace(tmp_path: Path) -> None:
    library, service, source, characters, _ = _setup(tmp_path)
    module_id = str(characters["module"]["module_id"])  # type: ignore[index]
    head = str(characters["module"]["head"])  # type: ignore[index]
    published = library.publish(
        module_id=module_id,
        base_revision=head,
        expected_head=head,
        entries=[
            EntryDraft("lin", "character", "林静", "法医，旧识。", (), {"roles": ["suspect"]})
        ],
        request_key="derive-publish-2",
    )

    result = derive_workspace(
        library,
        source=source,
        source_workspace_id="ws-source",
        target=tmp_path / "另一条线",
        replacements={module_id: published.revision_id},
        expected_digest=str(service.view()["digest"]),
        request_key="derive-key-2",
    )

    derived = {
        item["module"]["module_id"]: item["module"]["revision_id"]
        for item in result.assembly["bindings"]  # type: ignore[index]
    }
    assert derived[module_id] == published.revision_id
    assert service.view()["bindings"][0]["module"]["revision_id"] == head  # type: ignore[index]


def test_replacement_must_be_bound_and_exist(tmp_path: Path) -> None:
    library, service, source, _, _ = _setup(tmp_path)

    with pytest.raises(WorkspaceError) as excinfo:
        derive_workspace(
            library,
            source=source,
            source_workspace_id="ws",
            target=tmp_path / "新作品",
            replacements={"mod_0000000000000000": "rev_0000000000000000"},
            expected_digest=str(service.view()["digest"]),
            request_key="derive-key-3",
        )
    assert excinfo.value.code == "DERIVE_REPLACEMENT_INVALID"
    assert not (tmp_path / "新作品").exists()

    with pytest.raises(WorkspaceError) as excinfo:
        module_id = str(service.view()["bindings"][0]["module"]["module_id"])  # type: ignore[index]
        derive_workspace(
            library,
            source=source,
            source_workspace_id="ws",
            target=tmp_path / "新作品",
            replacements={module_id: "rev_0000000000000000"},
            expected_digest=str(service.view()["digest"]),
            request_key="derive-key-4",
        )
    assert excinfo.value.code == "MODULE_REVISION_NOT_FOUND"
    assert not (tmp_path / "新作品").exists()


def test_stale_assembly_does_not_create_a_workspace(tmp_path: Path) -> None:
    library, _, source, _, _ = _setup(tmp_path)

    with pytest.raises(WorkspaceError) as excinfo:
        derive_workspace(
            library,
            source=source,
            source_workspace_id="ws",
            target=tmp_path / "新作品",
            expected_digest="过期指纹",
            request_key="derive-key-5",
        )
    assert excinfo.value.code == "ASSEMBLY_STALE_CONFLICT"
    assert not (tmp_path / "新作品").exists()


def test_existing_target_is_not_overwritten(tmp_path: Path) -> None:
    library, service, source, _, _ = _setup(tmp_path)
    target = tmp_path / "已有目录"
    target.mkdir()
    (target / "笔记.txt").write_text("作者自己的东西", encoding="utf-8")

    with pytest.raises(WorkspaceError) as excinfo:
        derive_workspace(
            library,
            source=source,
            source_workspace_id="ws",
            target=target,
            expected_digest=str(service.view()["digest"]),
            request_key="derive-key-6",
        )
    assert excinfo.value.code == "DERIVE_TARGET_CONFLICT"
    assert (target / "笔记.txt").read_text(encoding="utf-8") == "作者自己的东西"


def test_retry_recovers_a_missing_assembly(tmp_path: Path) -> None:
    library, service, source, _, _ = _setup(tmp_path)
    target = tmp_path / "新作品"
    digest = str(service.view()["digest"])
    derive_workspace(
        library,
        source=source,
        source_workspace_id="ws",
        target=target,
        expected_digest=digest,
        request_key="derive-key-7",
    )
    (target / ".clueledger/assembly.json").unlink()

    retried = derive_workspace(
        library,
        source=source,
        source_workspace_id="ws",
        target=target,
        expected_digest=digest,
        request_key="derive-key-7",
    )

    assert retried.recovered is True
    assert AssemblyStore(target).read().bindings != ()


def test_derive_inside_the_source_is_rejected(tmp_path: Path) -> None:
    library, service, source, _, _ = _setup(tmp_path)

    with pytest.raises(WorkspaceError) as excinfo:
        derive_workspace(
            library,
            source=source,
            source_workspace_id="ws",
            target=Path(source.root) / "子目录",
            expected_digest=str(service.view()["digest"]),
            request_key="derive-key-8",
        )
    assert excinfo.value.code == "DERIVE_TARGET_CONFLICT"


def test_retry_does_not_create_a_second_workspace(tmp_path: Path) -> None:
    library, service, source, _, _ = _setup(tmp_path)
    target = tmp_path / "新作品"
    digest = str(service.view()["digest"])
    first = derive_workspace(
        library,
        source=source,
        source_workspace_id="ws",
        target=target,
        expected_digest=digest,
        request_key="derive-key-9",
    )
    second = derive_workspace(
        library,
        source=source,
        source_workspace_id="ws",
        target=target,
        expected_digest=digest,
        request_key="derive-key-9",
    )

    assert first.root == second.root
    assert second.recovered is False
    assert sorted(item.name for item in target.iterdir()) == [".clueledger", "正文"]
    assert len([item for item in tmp_path.iterdir() if item.name == "新作品"]) == 1
