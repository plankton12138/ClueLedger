"""作品内模块变更集：成组新建、更新、重试与撤销（0.8）。

契约：准备只写任务进度；应用先保存内容再一次性提交装配；已保存内容在装配失败后
可以继续使用；撤销只恢复本次涉及的绑定；作品内更新不推进库原稿，也不影响其他作品。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hastings.library import EntryDraft, ModuleLibrary
from hastings.library.bindings import AssemblyService
from hastings.library.changes import ChangeEdit, ChangeOperation, ModuleChangeService
from hastings.library.ids import source_path
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.materials import MaterialLedger, SourceRef
from hastings.workspace.module_assembly import AssemblyStore


def _setup(tmp_path: Path) -> tuple[ModuleLibrary, Workspace, ModuleChangeService]:
    library = ModuleLibrary(tmp_path / "模块库", cache_file=tmp_path / "cache" / "catalog.json")
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    return library, workspace, ModuleChangeService(library, workspace)


def _create_two(
    tmp_path: Path,
) -> tuple[ModuleLibrary, Workspace, ModuleChangeService, dict[str, object]]:
    library, workspace, service = _setup(tmp_path)
    prepared = service.prepare(
        turn_id="turn-1",
        operations=(
            ChangeOperation("cast", "create", name="人物小传", body="七名嫌疑人。"),
            ChangeOperation("premise", "create", name="聚集与调查前提", body="暴雪封山。"),
        ),
        base_digest=None,
    )
    result = service.apply(
        change_set_id=str(prepared["change_set_id"]),
        turn_id="turn-1",
        expected_digest=None,
    )
    return library, workspace, service, result


def _bound_module(library: ModuleLibrary, workspace: Workspace) -> tuple[str, str, str]:
    created = library.create_module(
        kind="characters",
        name="主角团",
        entries=[
            EntryDraft("lin", "character", "林静", "法医，旧识。旧识。"),
            EntryDraft("shen", "character", "沈舟", "侦探。"),
        ],
        request_key="create-characters-1",
    )
    module = created["module"]  # type: ignore[index]
    revision = created["revision"]  # type: ignore[index]
    module_id = str(module["module_id"])
    revision_id = str(revision["revision_id"])
    assembly = AssemblyService(library, workspace).bind(
        module_id=module_id,
        revision_id=revision_id,
        label="主角团",
        expected_digest=None,
        request_key="bind-characters-1",
    )
    binding_id = str(assembly["bindings"][0]["id"])
    return module_id, revision_id, binding_id


def test_create_multiple_modules_apply_in_one_assembly_commit(tmp_path: Path) -> None:
    library, workspace, _, result = _create_two(tmp_path)

    assert result["applied"] is True
    bindings = AssemblyStore(workspace.root).read().bindings
    assert [binding.label for binding in bindings] == ["人物小传", "聚集与调查前提"]
    assert len({binding.id for binding in bindings}) == 2
    assert len({binding.module.module_id for binding in bindings}) == 2
    for binding in bindings:
        assert library.module(binding.module.module_id).kind == "reference"


def test_apply_is_idempotent_and_does_not_create_duplicate_modules(tmp_path: Path) -> None:
    library, _, service, result = _create_two(tmp_path)

    replay = service.apply(
        change_set_id=str(result["change_set_id"]),
        turn_id="turn-1",
        expected_digest=None,
    )

    assert replay["applied"] is True
    assert replay["assembly"] == result["assembly"]
    assert len(library.modules()["items"]) == 2


def test_update_uses_workspace_revision_and_keeps_library_head(tmp_path: Path) -> None:
    library, workspace, service = _setup(tmp_path)
    module_id, base_revision_id, binding_id = _bound_module(library, workspace)
    raw, digest = library.storage.read_entry(module_id, base_revision_id, "lin")

    prepared = service.prepare(
        turn_id="turn-2",
        operations=(
            ChangeOperation(
                "lin-update",
                "update",
                binding_id=binding_id,
                module_id=module_id,
                base_revision=base_revision_id,
                edits=(
                    ChangeEdit("lin", base_digest=digest, find="法医，旧识。", replace="检察官。"),
                ),
            ),
        ),
        base_digest=AssemblyStore(workspace.root).digest(),
    )
    result = service.apply(
        change_set_id=str(prepared["change_set_id"]),
        turn_id="turn-2",
        expected_digest=AssemblyStore(workspace.root).digest(),
    )

    assert result["applied"] is True
    binding = AssemblyStore(workspace.root).read().binding(binding_id)
    assert binding is not None
    assert binding.module.revision_id != base_revision_id
    assert library.module(module_id).head == base_revision_id
    updated_raw, _ = library.storage.read_entry(module_id, binding.module.revision_id, "lin")
    assert "检察官。" in updated_raw
    assert "沈舟" in library.storage.read_entry(module_id, binding.module.revision_id, "shen")[0]
    _ = raw


def test_update_rejects_a_non_unique_find(tmp_path: Path) -> None:
    library, workspace, service = _setup(tmp_path)
    module_id, base_revision_id, binding_id = _bound_module(library, workspace)
    _raw, digest = library.storage.read_entry(module_id, base_revision_id, "lin")

    prepared = service.prepare(
        turn_id="turn-3",
        operations=(
            ChangeOperation(
                "lin-update",
                "update",
                binding_id=binding_id,
                module_id=module_id,
                base_revision=base_revision_id,
                edits=(ChangeEdit("lin", base_digest=digest, find="。", replace="！"),),
            ),
        ),
        base_digest=AssemblyStore(workspace.root).digest(),
    )
    result = service.apply(
        change_set_id=str(prepared["change_set_id"]),
        turn_id="turn-3",
        expected_digest=AssemblyStore(workspace.root).digest(),
    )

    assert result["applied"] is False
    assert result["stage"] == "content"
    assert result["error"]["code"] == "MODULE_CHANGE_MATCH_CONFLICT"


def test_full_rewrite_requires_complete_read_when_ledger_is_available(tmp_path: Path) -> None:
    library, workspace, service = _setup(tmp_path)
    module_id, base_revision_id, binding_id = _bound_module(library, workspace)
    raw, digest = library.storage.read_entry(module_id, base_revision_id, "lin")

    prepared = service.prepare(
        turn_id="turn-4",
        operations=(
            ChangeOperation(
                "lin-rewrite",
                "update",
                binding_id=binding_id,
                module_id=module_id,
                base_revision=base_revision_id,
                edits=(ChangeEdit("lin", base_digest=digest, body="全新小传。"),),
            ),
        ),
        base_digest=AssemblyStore(workspace.root).digest(),
    )
    ledger = MaterialLedger()
    partial = service.apply(
        change_set_id=str(prepared["change_set_id"]),
        turn_id="turn-4",
        ledger=ledger,
        expected_digest=AssemblyStore(workspace.root).digest(),
    )
    assert partial["applied"] is False
    assert partial["error"]["code"] == "REFERENCE_NOT_FULLY_READ_CONFLICT"

    ledger.record_read(
        SourceRef(
            source_path(library.library_id or "", module_id, base_revision_id, "lin"),
            digest,
        ),
        length=len(raw),
        start=0,
        end=len(raw),
    )
    retried = service.apply(
        change_set_id=str(prepared["change_set_id"]),
        turn_id="turn-4",
        ledger=ledger,
        expected_digest=AssemblyStore(workspace.root).digest(),
    )
    assert retried["applied"] is True


def test_prepare_and_apply_reject_a_stale_assembly_digest(tmp_path: Path) -> None:
    library, workspace, service = _setup(tmp_path)
    with pytest.raises(WorkspaceError) as stale:
        service.prepare(
            turn_id="turn-5",
            operations=(ChangeOperation("cast", "create", name="人物", body="内容"),),
            base_digest="stale-digest",
        )
    assert stale.value.code == "MODULE_CHANGES_STALE_CONFLICT"

    created = library.create_module(
        kind="characters",
        name="占位模块",
        entries=[EntryDraft("lin", "character", "林静", "法医。")],
        request_key="create-placeholder",
    )
    module = created["module"]  # type: ignore[index]
    prepared = service.prepare(
        turn_id="turn-5",
        operations=(ChangeOperation("cast", "create", name="人物", body="内容"),),
        base_digest=AssemblyStore(workspace.root).digest(),
    )
    AssemblyService(library, workspace).bind(
        module_id=str(module["module_id"]),
        revision_id=str(module["head"]),
        expected_digest=None,
        request_key="bind-after-prepare",
    )
    applied = service.apply(
        change_set_id=str(prepared["change_set_id"]),
        turn_id="turn-5",
        expected_digest=AssemblyStore(workspace.root).digest(),
    )
    assert applied["applied"] is False
    assert applied["stage"] == "precondition"


def test_undo_removes_created_modules_and_keeps_library_copies(tmp_path: Path) -> None:
    library, workspace, service, result = _create_two(tmp_path)

    undone = service.undo(
        change_set_id=str(result["change_set_id"]),
        expected_digest=AssemblyStore(workspace.root).digest(),
    )

    assert undone["reverted"] is True
    assert AssemblyStore(workspace.root).read().bindings == ()
    assert len(library.modules()["items"]) == 2


def test_undo_restores_the_previous_revision_and_refuses_later_edits(tmp_path: Path) -> None:
    library, workspace, service = _setup(tmp_path)
    module_id, base_revision_id, binding_id = _bound_module(library, workspace)
    _raw, digest = library.storage.read_entry(module_id, base_revision_id, "lin")
    prepared = service.prepare(
        turn_id="turn-6",
        operations=(
            ChangeOperation(
                "lin-update",
                "update",
                binding_id=binding_id,
                module_id=module_id,
                base_revision=base_revision_id,
                edits=(
                    ChangeEdit("lin", base_digest=digest, find="法医，旧识。", replace="检察官。"),
                ),
            ),
        ),
        base_digest=AssemblyStore(workspace.root).digest(),
    )
    applied = service.apply(
        change_set_id=str(prepared["change_set_id"]),
        turn_id="turn-6",
        expected_digest=AssemblyStore(workspace.root).digest(),
    )
    assert applied["applied"] is True
    updated_revision = str(applied["saved"][0]["revision_id"])  # type: ignore[index]

    undone = service.undo(
        change_set_id=str(applied["change_set_id"]),
        expected_digest=AssemblyStore(workspace.root).digest(),
    )
    restored = AssemblyStore(workspace.root).read().binding(binding_id)
    assert undone["reverted"] is True
    assert restored is not None
    assert restored.module.revision_id == base_revision_id

    # 再次修改后撤销旧变更集：必须冲突，不能覆盖后续成果。
    again = service.prepare(
        turn_id="turn-7",
        operations=(
            ChangeOperation(
                "lin-update",
                "update",
                binding_id=binding_id,
                module_id=module_id,
                base_revision=base_revision_id,
                edits=(
                    ChangeEdit("lin", base_digest=digest, find="法医，旧识。", replace="教师。"),
                ),
            ),
        ),
        base_digest=AssemblyStore(workspace.root).digest(),
    )
    service.apply(
        change_set_id=str(again["change_set_id"]),
        turn_id="turn-7",
        expected_digest=AssemblyStore(workspace.root).digest(),
    )
    with pytest.raises(WorkspaceError) as conflict:
        service.undo(change_set_id=str(applied["change_set_id"]))
    assert conflict.value.code == "MODULE_CHANGES_UNDO_CONFLICT"
    _ = updated_revision
