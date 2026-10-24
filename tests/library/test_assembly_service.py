"""作品装配服务：精确版本、装卸、升级与两步保存（0.8 D）。

契约：装配只写作品的 assembly 清单；同一实例只能绑一个版本；依赖缺失只标记不替换；
「保存并用于本作品」先发布后绑定，两步结果分别返回，重试不重复发布。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hastings.library import EntryDraft, ModuleLibrary
from hastings.library.bindings import AssemblyService
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace


def _library(tmp_path: Path) -> ModuleLibrary:
    root = tmp_path / "模块库"
    return ModuleLibrary(root, cache_file=tmp_path / "cache" / "library-catalog.json")


def _module(lib: ModuleLibrary, *, name: str = "主角团", key: str = "lib-module-1") -> dict:
    return lib.create_module(
        kind="characters",
        name=name,
        entries=[
            EntryDraft("lin", "character", "林静", "法医。", ("法医",), {"roles": ["suspect"]})
        ],
        request_key=key,
    )


def _service(tmp_path: Path, *, workspace_name: str = "雪山旅馆") -> tuple[AssemblyService, dict]:
    lib = _library(tmp_path)
    created = _module(lib)
    workspace = Workspace.create(tmp_path / workspace_name)
    return AssemblyService(lib, workspace), created


def test_bind_records_the_exact_revision(tmp_path: Path) -> None:
    service, created = _service(tmp_path)
    module = created["module"]  # type: ignore[index]

    result = service.bind(
        module_id=str(module["module_id"]),
        revision_id=str(module["head"]),
        label="主角团",
        expected_digest=None,
        request_key="bind-key-1",
    )

    assert result["digest"]
    binding = result["bindings"][0]  # type: ignore[index]
    assert binding["module"]["revision_id"] == module["head"]
    assert binding["kind"] == "characters"
    assert binding["name"] == "主角团"
    assert binding["problems"] == []
    assert service.view()["bindings"] == result["bindings"]


def test_bind_rejects_missing_module_or_revision(tmp_path: Path) -> None:
    service, created = _service(tmp_path)
    module = created["module"]  # type: ignore[index]

    with pytest.raises(WorkspaceError) as excinfo:
        service.bind(
            module_id=str(module["module_id"]),
            revision_id="rev_0000000000000000",
            expected_digest=None,
            request_key="bind-bad-rev",
        )
    assert excinfo.value.code == "MODULE_REVISION_NOT_FOUND"

    with pytest.raises(WorkspaceError) as excinfo:
        service.bind(
            module_id="mod_0000000000000000",
            revision_id=str(module["head"]),
            expected_digest=None,
            request_key="bind-bad-module",
        )
    assert excinfo.value.code == "MODULE_NOT_FOUND"
    assert service.view()["bindings"] == []


def test_same_instance_cannot_bind_two_revisions(tmp_path: Path) -> None:
    service, created = _service(tmp_path)
    module = created["module"]  # type: ignore[index]
    head = str(module["head"])
    published = service.library.publish(
        module_id=str(module["module_id"]),
        base_revision=head,
        expected_head=head,
        entries=[
            EntryDraft("lin", "character", "林静", "法医，旧识。", (), {"roles": ["suspect"]})
        ],
        request_key="publish-2",
    )
    first = service.bind(
        module_id=str(module["module_id"]),
        revision_id=head,
        expected_digest=None,
        request_key="bind-key-1",
    )

    with pytest.raises(WorkspaceError) as excinfo:
        service.bind(
            module_id=str(module["module_id"]),
            revision_id=published.revision_id,
            expected_digest=str(first["digest"]),
            request_key="bind-key-2",
        )
    assert excinfo.value.code == "ASSEMBLY_BINDING_CONFLICT"


def test_upgrade_keeps_the_binding_identity_and_old_version_readable(tmp_path: Path) -> None:
    service, created = _service(tmp_path)
    module = created["module"]  # type: ignore[index]
    head = str(module["head"])
    bound = service.bind(
        module_id=str(module["module_id"]),
        revision_id=head,
        label="主角团",
        expected_digest=None,
        request_key="bind-key-1",
    )
    binding_id = str(bound["bindings"][0]["id"])  # type: ignore[index]
    published = service.library.publish(
        module_id=str(module["module_id"]),
        base_revision=head,
        expected_head=head,
        entries=[
            EntryDraft("lin", "character", "林静", "法医，旧识。", (), {"roles": ["suspect"]})
        ],
        request_key="publish-2",
    )

    upgraded = service.upgrade(
        binding_id=binding_id,
        revision_id=published.revision_id,
        expected_digest=str(bound["digest"]),
        request_key="upgrade-key-1",
    )

    row = upgraded["bindings"][0]  # type: ignore[index]
    assert row["id"] == binding_id
    assert row["module"]["revision_id"] == published.revision_id
    assert row["label"] == "主角团"
    old = service.library.read_entry(
        module_id=str(module["module_id"]), revision_id=head, entry_id="lin"
    )
    assert "旧识" not in str(old["text"])


def test_unbind_removes_only_that_binding(tmp_path: Path) -> None:
    service, created = _service(tmp_path)
    module = created["module"]  # type: ignore[index]
    second = _module(service.library, name="反派", key="lib-module-2")
    first_binding = service.bind(
        module_id=str(module["module_id"]),
        revision_id=str(module["head"]),
        expected_digest=None,
        request_key="bind-key-1",
    )
    second_binding = service.bind(
        module_id=str(second["module"]["module_id"]),  # type: ignore[index]
        revision_id=str(second["module"]["head"]),  # type: ignore[index]
        expected_digest=str(first_binding["digest"]),
        request_key="bind-key-2",
    )

    result = service.unbind(
        binding_id=str(first_binding["bindings"][0]["id"]),  # type: ignore[index]
        expected_digest=str(second_binding["digest"]),
        request_key="unbind-key-1",
    )

    assert len(result["bindings"]) == 1  # type: ignore[arg-type]
    assert result["bindings"][0]["name"] == "反派"  # type: ignore[index]
    with pytest.raises(WorkspaceError) as excinfo:
        service.unbind(
            binding_id="bnd_0000000000000000",
            expected_digest=str(result["digest"]),
            request_key="unbind-key-2",
        )
    assert excinfo.value.code == "ASSEMBLY_BINDING_NOT_FOUND"


def test_stale_digest_does_not_overwrite(tmp_path: Path) -> None:
    service, created = _service(tmp_path)
    module = created["module"]  # type: ignore[index]
    other = _module(service.library, name="反派", key="lib-module-2")
    bound = service.bind(
        module_id=str(module["module_id"]),
        revision_id=str(module["head"]),
        expected_digest=None,
        request_key="bind-key-1",
    )

    with pytest.raises(WorkspaceError) as excinfo:
        service.bind(
            module_id=str(other["module"]["module_id"]),  # type: ignore[index]
            revision_id=str(other["module"]["head"]),  # type: ignore[index]
            expected_digest=None,
            request_key="bind-key-2",
        )
    assert excinfo.value.code == "ASSEMBLY_STALE_CONFLICT"
    assert len(service.view()["bindings"]) == 1  # type: ignore[arg-type]
    assert service.view()["digest"] == bound["digest"]


def test_missing_dependency_is_reported_not_replaced(tmp_path: Path) -> None:
    service, created = _service(tmp_path)
    module = created["module"]  # type: ignore[index]
    bound = service.bind(
        module_id=str(module["module_id"]),
        revision_id=str(module["head"]),
        expected_digest=None,
        request_key="bind-key-1",
    )
    binding_id = str(bound["bindings"][0]["id"])  # type: ignore[index]

    service.library.trash(
        module_id=str(module["module_id"]),
        expected_head=str(module["head"]),
        request_key="trash-key-1",
    )
    trashed = service.view()
    assert "module_trashed" in trashed["bindings"][0]["problems"]  # type: ignore[index]
    # 旧版本仍可读，绑定没有被自动改成最新版或卸载。
    assert service.library.read_entry(
        module_id=str(module["module_id"]), revision_id=str(module["head"]), entry_id="lin"
    )
    assert binding_id

    service.library.storage.revision_dir(str(module["module_id"]), str(module["head"])).rename(
        tmp_path / "移走的版本"
    )
    missing = service.view()
    assert "revision_missing" in missing["bindings"][0]["problems"]  # type: ignore[index]


def test_publish_and_bind_updates_the_binding(tmp_path: Path) -> None:
    service, created = _service(tmp_path)
    module = created["module"]  # type: ignore[index]
    head = str(module["head"])
    bound = service.bind(
        module_id=str(module["module_id"]),
        revision_id=head,
        expected_digest=None,
        request_key="bind-key-1",
    )

    result = service.publish_and_bind(
        module_id=str(module["module_id"]),
        base_revision=head,
        expected_head=head,
        entries=[
            {
                "entry_id": "lin",
                "title": "林静",
                "body": "法医，也是死者旧识。",
                "fields": {"roles": ["suspect"]},
            }
        ],
        expected_digest=str(bound["digest"]),
        request_key="save-and-use-1",
    )

    assert result["published"] is True
    assert result["binding_updated"] is True
    new_revision = result["module"]["revision_id"]  # type: ignore[index]
    assert service.view()["bindings"][0]["module"]["revision_id"] == new_revision  # type: ignore[index]


def test_publish_survives_a_binding_conflict_and_retries_only_the_binding(
    tmp_path: Path,
) -> None:
    service, created = _service(tmp_path)
    module = created["module"]  # type: ignore[index]
    head = str(module["head"])
    service.bind(
        module_id=str(module["module_id"]),
        revision_id=head,
        expected_digest=None,
        request_key="bind-key-1",
    )
    entries = [
        {
            "entry_id": "lin",
            "title": "林静",
            "body": "法医，也是死者旧识。",
            "fields": {"roles": ["suspect"]},
        }
    ]

    conflict = service.publish_and_bind(
        module_id=str(module["module_id"]),
        base_revision=head,
        expected_head=head,
        entries=entries,
        expected_digest="过期指纹",
        request_key="save-and-use-1",
    )
    assert conflict["published"] is True
    assert conflict["binding_updated"] is False
    assert conflict["error"]["code"] == "ASSEMBLY_STALE_CONFLICT"  # type: ignore[index]
    published = str(conflict["module"]["revision_id"])  # type: ignore[index]
    assert service.library.module(str(module["module_id"])).head == published
    revisions_after_publish = service.library.revisions(module_id=str(module["module_id"]))["total"]

    binding_id = str(service.view()["bindings"][0]["id"])  # type: ignore[index]
    retried = service.upgrade(
        binding_id=binding_id,
        revision_id=published,
        expected_digest=str(service.view()["digest"]),
        request_key="retry-bind-key-1",
    )
    assert retried["bindings"][0]["module"]["revision_id"] == published  # type: ignore[index]
    # 只重试绑定，没有再发布一版。
    assert service.library.revisions(module_id=str(module["module_id"]))["total"] == (
        revisions_after_publish
    )


def test_publish_and_bind_replays_the_whole_command(tmp_path: Path) -> None:
    service, created = _service(tmp_path)
    module = created["module"]  # type: ignore[index]
    head = str(module["head"])
    bound = service.bind(
        module_id=str(module["module_id"]),
        revision_id=head,
        expected_digest=None,
        request_key="bind-key-1",
    )
    args = dict(
        module_id=str(module["module_id"]),
        base_revision=head,
        expected_head=head,
        entries=[
            {
                "entry_id": "lin",
                "title": "林静",
                "body": "同一命令。",
                "fields": {"roles": ["suspect"]},
            }
        ],
        expected_digest=str(bound["digest"]),
        request_key="save-and-use-2",
    )
    first = service.publish_and_bind(**args)  # type: ignore[arg-type]
    total = service.library.revisions(module_id=str(module["module_id"]))["total"]
    second = service.publish_and_bind(**args)  # type: ignore[arg-type]

    assert first["published"] is True and first["binding_updated"] is True
    assert second["published"] is True and second["binding_updated"] is True
    assert second["module"]["revision_id"] == first["module"]["revision_id"]  # type: ignore[index]
    assert service.library.revisions(module_id=str(module["module_id"]))["total"] == total


def test_two_workspaces_share_modules_without_sharing_bindings(tmp_path: Path) -> None:
    lib = _library(tmp_path)
    created = _module(lib)
    module = created["module"]  # type: ignore[index]
    head = str(module["head"])
    first = AssemblyService(lib, Workspace.create(tmp_path / "作品A"))
    second = AssemblyService(lib, Workspace.create(tmp_path / "作品B"))
    first.bind(
        module_id=str(module["module_id"]),
        revision_id=head,
        expected_digest=None,
        request_key="bind-key-a1",
    )
    second.bind(
        module_id=str(module["module_id"]),
        revision_id=head,
        expected_digest=None,
        request_key="bind-key-b1",
    )

    published = lib.publish(
        module_id=str(module["module_id"]),
        base_revision=head,
        expected_head=head,
        entries=[EntryDraft("lin", "character", "林静", "新版本。", (), {"roles": ["suspect"]})],
        request_key="publish-2",
    )
    first.upgrade(
        binding_id=str(first.view()["bindings"][0]["id"]),  # type: ignore[index]
        revision_id=published.revision_id,
        expected_digest=str(first.view()["digest"]),
        request_key="upgrade-key-a",
    )

    assert first.view()["bindings"][0]["module"]["revision_id"] == published.revision_id  # type: ignore[index]
    assert second.view()["bindings"][0]["module"]["revision_id"] == head  # type: ignore[index]
