"""旧资料显式导入（0.8 B）。

导入只创建库实例并绑定精确版本，不移动或改写原文件；同一内容重复导入不产生第二份，
内容变化时发布新版本并更新绑定。导入后的固定设定不再重复注入上下文。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hastings.library.imports import IMPORT_PATH, import_legacy_materials, imported_paths
from hastings.library.modules import ModuleLibrary
from hastings.workspace.context import settings_section
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace

CLUES = "设定集/线索.md"
TRUTH = "设定集/真相.md"
PERSON = "设定集/角色小传/林静.md"


def _workspace(tmp_path: Path) -> Workspace:
    workspace = Workspace.create(tmp_path / "旧作品")
    workspace.write(CLUES, "# 线索\n\n门锁有擦痕。\n", expected_digest=None)
    workspace.write(TRUTH, "# 真相\n\n凶手是管家。\n", expected_digest=None)
    workspace.write(PERSON, "# 林静\n\n法医，与死者相识。\n", expected_digest=None)
    workspace.write("正文/0001 雨夜.md", "# 雨夜\n\n侦探抵达旅馆。\n", expected_digest=None)
    return workspace


def _library(tmp_path: Path) -> ModuleLibrary:
    return ModuleLibrary(tmp_path / "模块库", cache_file=tmp_path / "state" / "catalog.json")


def test_empty_workspace_imports_nothing_and_creates_no_library(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path / "空作品")
    library = _library(tmp_path)

    result = import_legacy_materials(
        library, workspace, expected_digest=None, request_key="import-key-empty"
    )

    assert result["items"] == []
    assert result["assembly"]["bindings"] == []
    assert not (tmp_path / "模块库" / "library.json").exists()
    assert not (workspace.root / IMPORT_PATH).exists()


def test_import_creates_modules_and_fixed_bindings_without_moving_sources(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    before = {path: workspace.read(path).digest for path in (CLUES, TRUTH, PERSON)}
    library = _library(tmp_path)

    result = import_legacy_materials(
        library, workspace, expected_digest=None, request_key="import-key-1"
    )

    assert {item["path"] for item in result["items"]} == {CLUES, TRUTH, PERSON}
    assert {item["action"] for item in result["items"]} == {"created"}
    assert {item["kind"] for item in result["items"]} == {"clues", "truth", "characters"}
    assert len(result["assembly"]["bindings"]) == 3
    assert imported_paths(workspace) == {CLUES, TRUTH, PERSON}
    for path, digest in before.items():
        assert workspace.read(path).digest == digest


def test_reimport_is_idempotent_and_reuses_the_same_revision(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    library = _library(tmp_path)
    first = import_legacy_materials(
        library, workspace, expected_digest=None, request_key="import-key-1"
    )

    second = import_legacy_materials(
        library,
        workspace,
        expected_digest=str(first["digest"]),
        request_key="import-key-2",
    )

    assert {item["action"] for item in second["items"]} == {"unchanged"}
    assert {item["revision_id"] for item in second["items"]} == {
        item["revision_id"] for item in first["items"]
    }
    assert len(second["assembly"]["bindings"]) == 3


def test_changed_source_publishes_a_new_revision_and_updates_binding(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    library = _library(tmp_path)
    first = import_legacy_materials(
        library, workspace, expected_digest=None, request_key="import-key-1"
    )
    clue_item = next(item for item in first["items"] if item["path"] == CLUES)

    current = workspace.read(CLUES)
    workspace.write(CLUES, "# 线索\n\n门锁内侧有擦痕。\n", expected_digest=current.digest)
    second = import_legacy_materials(
        library,
        workspace,
        expected_digest=str(first["digest"]),
        request_key="import-key-2",
    )

    changed = next(item for item in second["items"] if item["path"] == CLUES)
    assert changed["action"] == "published"
    assert changed["module_id"] == clue_item["module_id"]
    assert changed["revision_id"] != clue_item["revision_id"]
    binding = next(
        row
        for row in second["assembly"]["bindings"]
        if row["module"]["module_id"] == changed["module_id"]
    )
    assert binding["module"]["revision_id"] == changed["revision_id"]
    assert len(second["assembly"]["bindings"]) == 3


def test_imported_settings_are_not_injected_twice(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    before = settings_section(workspace)
    assert "门锁有擦痕" in before

    library = _library(tmp_path)
    import_legacy_materials(library, workspace, expected_digest=None, request_key="import-key-1")

    after = settings_section(workspace)
    assert "门锁有擦痕" not in after
    assert "凶手是管家" not in after


def test_broken_manifest_is_reported_instead_of_treated_as_empty(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    path = workspace.root / IMPORT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json", encoding="utf-8")
    library = _library(tmp_path)

    with pytest.raises(WorkspaceError) as excinfo:
        import_legacy_materials(
            library, workspace, expected_digest=None, request_key="import-key-1"
        )

    assert excinfo.value.code == "LIBRARY_IMPORT_INVALID"


def test_stale_manifest_digest_is_rejected(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    library = _library(tmp_path)
    path = workspace.root / IMPORT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "imports": {}}), encoding="utf-8")

    with pytest.raises(WorkspaceError) as excinfo:
        import_legacy_materials(
            library, workspace, expected_digest="stale-digest", request_key="import-key-1"
        )

    assert excinfo.value.code == "LIBRARY_IMPORT_STALE_CONFLICT"
