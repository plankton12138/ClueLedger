"""tests/v2/migration 公共夹具：内存仓库 + 迁移导入器 + 临时工作区副本。"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from hastings.bible.models import Clue
from hastings.bible.store import StoryWorkspace, load_ledger, save_ledger
from hastings.domain.repository.ids import Actor
from hastings.infrastructure.repository.memory import build_memory_repository
from hastings.interfaces.importers.v1_workspace_importer import V1WorkspaceImporter

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "v1_workspace" / "winter-manor-case"
SLUG = "winter-manor-case"


class FakeClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)


class FakeIds:
    def __init__(self) -> None:
        self._n = 0

    def new_id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}_{self._n:04d}"


@dataclass
class MigrationEnv:
    repo: Any
    service: Any
    importer: V1WorkspaceImporter
    workspace: StoryWorkspace


@pytest.fixture
def fixture_workspace() -> StoryWorkspace:
    """只读地指向仓库内基准 fixture（dry-run 用例不改它）。"""
    return StoryWorkspace(root=FIXTURE.parent, slug=SLUG)


def _copy_fixture(tmp_path: Path) -> StoryWorkspace:
    root = tmp_path / "root"
    root.mkdir()
    shutil.copytree(FIXTURE, root / SLUG)
    return StoryWorkspace(root=root, slug=SLUG)


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> StoryWorkspace:
    """fixture 的可写副本，用于「源变化 / 未知文件」等需要改动源的用例。"""
    return _copy_fixture(tmp_path)


@pytest.fixture
def resolved_workspace(tmp_path: Path) -> StoryWorkspace:
    """已由作者解决阻断项的源：移除缺依据的干扰项与缺真凶的手法。

    基准 fixture 刻意含两个须作者决策的阻断实体（red herring 缺 basis、method 缺
    culprit）。迁移要求作者先把它们补齐或排除，再产生可导出的报告——本夹具模拟
    「作者排除」这一路径，用于 T17.2/T17.3 的写入与校验用例。
    """
    workspace = _copy_fixture(tmp_path)
    clues = load_ledger(workspace.clues_path, Clue)
    save_ledger(workspace.clues_path, [c for c in clues if not c.is_red_herring])
    save_ledger(workspace.method_path, [])
    return workspace


@pytest.fixture
def migration_env() -> MigrationEnv:
    clock, ids = FakeClock(), FakeIds()
    repo = build_memory_repository(clock, ids)
    importer = V1WorkspaceImporter(repo.service, repo.service, actor=Actor("human", "migrator"))
    return MigrationEnv(
        repo=repo,
        service=repo.service,
        importer=importer,
        workspace=StoryWorkspace(FIXTURE.parent, SLUG),
    )
