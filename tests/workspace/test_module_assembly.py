"""作品装配清单（0.8 B）。

契约：装配只记录库、模块与精确版本，不复制原文；写入按指纹 CAS，重试按请求键幂等；
同一实例在同一作品里只能有一个版本。清单放在 ``.clueledger/``，不属于受管正文路径。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import is_managed_path
from hastings.workspace.module_assembly import (
    ASSEMBLY_PATH,
    AssemblyStore,
    Binding,
    ModuleRef,
)

LIB = "lib_0123456789abcdef"
MODULE = "mod_0123456789abcdef"
REV_A = "rev_0123456789abcdef"
REV_B = "rev_fedcba9876543210"
OTHER_MODULE = "mod_fedcba9876543210"


def _ref(revision: str = REV_A, module: str = MODULE, library: str = LIB) -> ModuleRef:
    return ModuleRef(library_id=library, module_id=module, revision_id=revision)


def _binding(binding_id: str = "bnd-1", ref: ModuleRef | None = None) -> Binding:
    return Binding(id=binding_id, module=ref or _ref(), label="主角团")


def test_missing_file_is_an_empty_assembly(tmp_path: Path) -> None:
    store = AssemblyStore(tmp_path)
    assert store.digest() is None
    assembly = store.read()
    assert assembly.bindings == ()
    assert assembly.library_id is None


def test_replace_writes_under_clueledger_and_changes_digest(tmp_path: Path) -> None:
    store = AssemblyStore(tmp_path)
    before = store.digest()

    store.replace(
        library_id=LIB,
        bindings=[_binding()],
        expected_digest=before,
        request_key="assembly-1",
    )

    assert (tmp_path / ASSEMBLY_PATH).is_file()
    assert store.digest() != before
    assert store.read().bindings[0].module.revision_id == REV_A
    # 装配清单不属于作品受管路径，普通文件工具看不到它。
    assert not is_managed_path(ASSEMBLY_PATH)


def test_stale_digest_is_rejected(tmp_path: Path) -> None:
    store = AssemblyStore(tmp_path)
    store.replace(
        library_id=LIB, bindings=[_binding()], expected_digest=None, request_key="assembly-1"
    )

    with pytest.raises(WorkspaceError) as excinfo:
        store.replace(library_id=LIB, bindings=[], expected_digest=None, request_key="assembly-2")
    assert excinfo.value.code == "ASSEMBLY_STALE_CONFLICT"
    assert len(store.read().bindings) == 1


def test_same_module_cannot_be_bound_twice(tmp_path: Path) -> None:
    store = AssemblyStore(tmp_path)
    with pytest.raises(WorkspaceError) as excinfo:
        store.replace(
            library_id=LIB,
            bindings=[_binding("bnd-1"), _binding("bnd-2", _ref(REV_B))],
            expected_digest=None,
            request_key="assembly-1",
        )
    assert excinfo.value.code == "ASSEMBLY_BINDING_CONFLICT"


def test_two_libraries_are_rejected(tmp_path: Path) -> None:
    store = AssemblyStore(tmp_path)
    with pytest.raises(WorkspaceError) as excinfo:
        store.replace(
            library_id=LIB,
            bindings=[
                _binding("bnd-1"),
                _binding("bnd-2", _ref(module=OTHER_MODULE, library="lib_fedcba9876543210")),
            ],
            expected_digest=None,
            request_key="assembly-1",
        )
    assert excinfo.value.code == "ASSEMBLY_INVALID"


def test_request_key_replays_without_writing_again(tmp_path: Path) -> None:
    store = AssemblyStore(tmp_path)
    first = store.replace(
        library_id=LIB, bindings=[_binding()], expected_digest=None, request_key="assembly-key-1"
    )
    digest = store.digest()

    again = store.replace(
        library_id=LIB, bindings=[_binding()], expected_digest=digest, request_key="assembly-key-1"
    )
    assert again.view() == first.view()
    assert store.digest() == digest

    with pytest.raises(WorkspaceError) as excinfo:
        store.replace(
            library_id=LIB,
            bindings=[_binding(ref=_ref(REV_B))],
            expected_digest=digest,
            request_key="assembly-key-1",
        )
    assert excinfo.value.code == "REQUEST_KEY_CONFLICT"


def test_corrupt_file_is_not_treated_as_empty(tmp_path: Path) -> None:
    store = AssemblyStore(tmp_path)
    path = tmp_path / ASSEMBLY_PATH
    path.parent.mkdir(parents=True)
    path.write_text("{ 不是 JSON", encoding="utf-8")

    with pytest.raises(WorkspaceError) as excinfo:
        store.read()
    assert excinfo.value.code == "ASSEMBLY_INVALID"
