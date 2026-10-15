"""tests/v2/exporter 公共夹具：内存仓库 + V1 fixture 导入后的可导出项目。"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from hastings.bible.models import Clue
from hastings.bible.store import StoryWorkspace, load_ledger, save_ledger
from hastings.domain.repository.ids import Actor, ProjectId
from hastings.infrastructure.repository.memory import build_memory_repository
from hastings.interfaces.importers.v1_workspace_importer import V1WorkspaceImporter

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "v1_workspace" / "winter-manor-case"
SLUG = "winter-manor-case"
ACTOR = Actor(kind="human", id="exporter")


@dataclass
class ExportEnv:
    service: Any
    project: ProjectId
    head: str


def _imported_env(tmp_path: Path) -> ExportEnv:
    """V1 基准 fixture（阻断项已由作者解决）→ 幂等导入 → 可导出的 V2 项目。"""
    from hastings.infrastructure.runtime import SystemClock, UuidIdGenerator

    clock, ids = SystemClock(), UuidIdGenerator()
    repo = build_memory_repository(clock, ids)
    service = repo.service

    root = tmp_path / "root"
    root.mkdir()
    shutil.copytree(FIXTURE, root / SLUG)
    workspace = StoryWorkspace(root=root, slug=SLUG)
    clues = load_ledger(workspace.clues_path, Clue)
    save_ledger(workspace.clues_path, [c for c in clues if not c.is_red_herring])
    save_ledger(workspace.method_path, [])

    importer = V1WorkspaceImporter(service, service, actor=ACTOR)
    report = importer.dry_run(workspace)
    project = service.create_project("导出源").id
    result = importer.apply(workspace, project_id=project, confirm_run_id=report.run_id)
    assert result.validation.ok, f"夹具导入失败：{result.validation.failures}"
    return ExportEnv(service=service, project=project, head=result.new_head)


@pytest.fixture
def imported_project(tmp_path: Path) -> ExportEnv:
    return _imported_env(tmp_path)
