"""故事线计划叠加（0.8 E）。

计划来自已装的时间线／剧情模块，只读叠加；正文观察与计划分开，未装模块不出现。
"""

from __future__ import annotations

from pathlib import Path

from hastings.library import EntryDraft, ModuleLibrary
from hastings.library.bindings import AssemblyService
from hastings.workspace.files import Workspace
from hastings.workspace.module_assembly import AssemblyStore
from hastings.workspace.story_plans import planned_overlay


def _library(tmp_path: Path) -> ModuleLibrary:
    return ModuleLibrary(tmp_path / "模块库", cache_file=tmp_path / "state" / "catalog.json")


def _create(library: ModuleLibrary, *, kind: str, name: str, entries: list[EntryDraft], key: str):
    return library.create_module(kind=kind, name=name, entries=entries, request_key=key)


def test_plans_from_installed_timeline_and_plot_modules_are_merged(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path / "book")
    library = _library(tmp_path)
    timeline = _create(
        library,
        kind="timeline",
        name="主线时间",
        entries=[
            EntryDraft(
                "e22", "author_event", "第22章回收怀表", "计划。", (), {"planned_chapter": 22}
            ),
        ],
        key="plan-key-1",
    )
    plot = _create(
        library,
        kind="plot",
        name="支线",
        entries=[
            EntryDraft("e9", "plot_plan", "第9章误导", "计划。", (), {"planned_chapter": 9}),
        ],
        key="plan-key-2",
    )
    service = AssemblyService(library, workspace)
    for index, created in enumerate((timeline, plot), start=1):
        module = created["module"]  # type: ignore[index]
        service.bind(
            module_id=str(module["module_id"]),
            revision_id=str(module["head"]),
            label=str(module["name"]),
            expected_digest=AssemblyStore(workspace.root).digest(),
            request_key=f"bind-plan-{index}",
        )

    overlay = planned_overlay(workspace, library)

    assert [item["planned_chapter"] for item in overlay["plans"]] == [9, 22]
    assert {item["module_kind"] for item in overlay["plans"]} == {"timeline", "plot"}
    assert len(overlay["plan_modules"]) == 2


def test_detached_plan_module_disappears_from_overlay(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path / "book")
    library = _library(tmp_path)
    created = _create(
        library,
        kind="timeline",
        name="主线时间",
        entries=[
            EntryDraft("e10", "author_event", "第10章转折", "计划。", (), {"planned_chapter": 10})
        ],
        key="plan-key-1",
    )
    module = created["module"]  # type: ignore[index]
    service = AssemblyService(library, workspace)
    bound = service.bind(
        module_id=str(module["module_id"]),
        revision_id=str(module["head"]),
        label=str(module["name"]),
        expected_digest=None,
        request_key="bind-plan-1",
    )
    assert len(planned_overlay(workspace, library)["plans"]) == 1

    binding = next(
        item for item in bound["bindings"] if item["module"]["module_id"] == module["module_id"]
    )
    service.unbind(
        binding_id=str(binding["id"]),
        expected_digest=AssemblyStore(workspace.root).digest(),
        request_key="unbind-plan-1",
    )

    overlay = planned_overlay(workspace, library)
    assert overlay["plans"] == []
    assert overlay["plan_modules"] == []
