"""CLI V2 读路径（T17.5）测试：``/bible`` 在已迁移工作区经 StoryQueries 读 V2 项目。

覆盖：未迁移提示（三要素）、v2 工作区路由（bible_command_output 判定点）、
按账本类型读取与缺账本空输出、未知类型错误、迁移映射不抹既有设置。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import hastings.cli_v2 as cli_v2
from hastings.application.queries.story_queries import StoryQueries
from hastings.bible.models import Clue
from hastings.bible.store import StoryWorkspace, load_ledger, save_ledger
from hastings.cli import bible_command_output
from hastings.cli_v2 import V2_LEDGER_KINDS, v2_bible_output
from hastings.domain.repository.ids import Actor
from hastings.infrastructure.repository.memory import build_memory_repository
from hastings.infrastructure.runtime import SystemClock, UuidIdGenerator
from hastings.interfaces.importers.v1_workspace_importer import V1WorkspaceImporter
from hastings.settings import (
    load_settings,
    record_v2_migration,
    v2_project_id,
    workspace_architecture_version,
)

FIXTURE = Path(__file__).parent / "fixtures" / "v1_workspace" / "winter-manor-case"
SLUG = "winter-manor-case"


@pytest.fixture
def workspace(tmp_path: Path) -> StoryWorkspace:
    root = tmp_path / "root"
    root.mkdir()
    shutil.copytree(FIXTURE, root / SLUG)
    return StoryWorkspace(root=root, slug=SLUG)


@pytest.fixture
def v2_ready(workspace: StoryWorkspace) -> tuple[StoryWorkspace, StoryQueries, str]:
    """fixture 阻断项解决 → 导入 → 记录迁移映射（db 路径用测试桩键）。"""
    clues = load_ledger(workspace.clues_path, Clue)
    save_ledger(workspace.clues_path, [c for c in clues if not c.is_red_herring])
    save_ledger(workspace.method_path, [])

    repo = build_memory_repository(SystemClock(), UuidIdGenerator())
    service = repo.service
    importer = V1WorkspaceImporter(service, service, actor=Actor("human", "cli-test"))
    report = importer.dry_run(workspace)
    project = service.create_project(SLUG).id
    result = importer.apply(workspace, project_id=project, confirm_run_id=report.run_id)
    assert result.validation.ok

    record_v2_migration(workspace, project.value, db_path="memory://test")
    queries = StoryQueries(service)
    cli_v2._READERS["memory://test"] = queries  # 测试桩：免开 sqlite
    return workspace, queries, project.value


def test_unmigrated_workspace_gets_actionable_hint(workspace: StoryWorkspace) -> None:
    output = v2_bible_output(workspace)
    assert "尚未迁移" in output
    assert "v2_project_id" in output


def test_v2_workspace_reads_via_story_queries(v2_ready) -> None:
    workspace, queries, project_id = v2_ready
    output = v2_bible_output(workspace, None, queries=queries)
    assert project_id[:4] in output, "输出应带项目标识"
    assert "角色账本共" in output, "角色账本经 V2 查询读取"
    assert "线索账本共" in output

    single = v2_bible_output(workspace, "character", queries=queries)
    assert "角色账本共" in single
    assert "线索账本" not in single


def test_v2_empty_ledger_and_unknown_type(v2_ready) -> None:
    workspace, queries, _project = v2_ready
    # fixture 的手法账本被作者清空（阻断项解决），V2 的 mystery_truth 为空
    assert "手法账本为空" in v2_bible_output(workspace, "method", queries=queries)
    unknown = v2_bible_output(workspace, "unknown", queries=queries)
    assert "未知账本类型" in unknown
    assert "、".join(V2_LEDGER_KINDS)[:6] in unknown


def test_bible_command_routes_v2_workspace(v2_ready) -> None:
    """唯一判定点：architecture_version=v2 的工作区 /bible 走 V2 查询，不读 YAML。"""
    workspace, _queries, _project = v2_ready
    assert workspace_architecture_version(workspace) == "v2"
    output = bible_command_output(workspace, "character")
    assert "角色账本共" in output
    assert "YAML" not in output


def test_migration_mapping_preserves_existing_settings(v2_ready) -> None:
    workspace, _queries, project_id = v2_ready
    assert v2_project_id(workspace) == project_id
    settings = load_settings(workspace)
    assert settings.writing_language, "既有 StorySettings 字段不得被迁移映射抹掉"
    assert workspace_architecture_version(workspace) == "v2"


def test_v2_without_db_path_hint(v2_ready) -> None:
    """v2_project_id 存在但 db 路径缺失：提示重跑迁移/检查配置，而非静默读旧账本。"""
    workspace, _queries, _project = v2_ready
    (workspace.story_dir / "settings.yaml").write_text(
        "architecture_version: v2\nv2_project_id: prj_x\n", encoding="utf-8"
    )
    cli_v2._READERS.pop("memory://test", None)
    output = v2_bible_output(workspace, None)
    assert "数据库路径" in output
