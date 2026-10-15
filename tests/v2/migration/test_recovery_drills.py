"""Phase 17 演练（T17.8）：磁盘满 / 迁移中断的注入式故障演练。

演练语义（plan §5 T17.8）：
- **磁盘满**：写路径中途 OSError → 不产生半项目，active 不切换（V1 工作区无映射）；
- **迁移中断**：故障后重试同一 confirm_run_id → 幂等落成完整迁移；
- **源变化**：故障排查期间源被改动 → 重试拒绝（要求重新 dry-run），不静默覆盖。

真实磁盘满无法在测试里制造，用故障注入的 CommitRepository 模拟「写中途失败」；
故障语义与真实 ENOSPC 一致（commit 抛 OSError、部分状态不留存——内存/SQLite 后端
的提交都在事务里，失败即回滚）。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from hastings.application.errors import ViolationError
from hastings.bible.models import Clue
from hastings.bible.store import StoryWorkspace, load_ledger, save_ledger
from hastings.domain.repository.ids import Actor, ProjectId
from hastings.domain.repository.requests import CommitRequest
from hastings.domain.repository.results import CommitResult
from hastings.infrastructure.repository.memory import build_memory_repository
from hastings.interfaces.importers.v1_workspace_importer import V1WorkspaceImporter
from hastings.settings import v2_project_id

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "v1_workspace" / "winter-manor-case"
SLUG = "winter-manor-case"
ACTOR = Actor(kind="human", id="drill")


@dataclass
class FlakyCommitter:
    """注入式提交器：前 ``fail_times`` 次 commit 抛 OSError（模拟磁盘满/断电）。"""

    inner: Any
    fail_times: int

    def commit(self, request: CommitRequest) -> CommitResult:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise OSError(28, "模拟磁盘空间不足（ENOSPC）")
        return self.inner.commit(request)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


@pytest.fixture
def resolved_workspace(tmp_path: Path) -> StoryWorkspace:
    root = tmp_path / "root"
    root.mkdir()
    shutil.copytree(FIXTURE, root / SLUG)
    workspace = StoryWorkspace(root=root, slug=SLUG)
    clues = load_ledger(workspace.clues_path, Clue)
    save_ledger(workspace.clues_path, [c for c in clues if not c.is_red_herring])
    save_ledger(workspace.method_path, [])
    return workspace


def _record_mapping(workspace: StoryWorkspace, project_id: ProjectId) -> None:
    from hastings.settings import record_v2_migration

    record_v2_migration(workspace, project_id.value)


def test_disk_full_leaves_no_partial_state_and_retry_succeeds(
    resolved_workspace: StoryWorkspace,
) -> None:
    repo = build_memory_repository(_clock(), _ids())
    service = repo.service
    flaky = FlakyCommitter(inner=service, fail_times=1)
    importer = V1WorkspaceImporter(service, flaky, actor=ACTOR)  # 写走故障提交器

    report = importer.dry_run(resolved_workspace)
    project = service.create_project(SLUG).id

    # 第一次 apply：写中途磁盘满 → 异常上抛，目标项目仍为空
    with pytest.raises(OSError):
        importer.apply(resolved_workspace, project_id=project, confirm_run_id=report.run_id)
    branch = service.get_branch(project, "main")
    assert branch.head_commit_id is None, "磁盘满不得留下半项目"
    assert v2_project_id(resolved_workspace) is None, "故障未落成映射，active 不切换"

    # 重试（同一 run_id，模拟修复磁盘后用户重试）：完整落成
    healthy = V1WorkspaceImporter(service, service, actor=ACTOR)
    result = healthy.apply(resolved_workspace, project_id=project, confirm_run_id=report.run_id)
    assert result.status == "committed"
    assert result.validation.ok
    _record_mapping(resolved_workspace, project)
    assert v2_project_id(resolved_workspace) == project.value


def test_interrupted_migration_source_change_requires_new_dry_run(
    resolved_workspace: StoryWorkspace,
) -> None:
    """中断后源被改动：旧 run_id 失效，必须重新 dry-run（不静默用旧报告覆盖新源）。"""
    repo = build_memory_repository(_clock(), _ids())
    service = repo.service
    flaky = FlakyCommitter(inner=service, fail_times=1)
    importer = V1WorkspaceImporter(service, flaky, actor=ACTOR)

    report = importer.dry_run(resolved_workspace)
    project = service.create_project(SLUG).id
    with pytest.raises(OSError):
        importer.apply(resolved_workspace, project_id=project, confirm_run_id=report.run_id)

    # 排查期间作者改正文（真实场景：中断与重试之间源可能漂移）
    chapter = resolved_workspace.chapters_dir / "ch01.md"
    chapter.write_text(
        chapter.read_text(encoding="utf-8") + "\n\n中断期间新增的一段。", encoding="utf-8"
    )

    healthy = V1WorkspaceImporter(service, service, actor=ACTOR)
    with pytest.raises(ViolationError) as exc:
        healthy.apply(resolved_workspace, project_id=project, confirm_run_id=report.run_id)
    assert "重新运行 dry-run" in exc.value.fix

    fresh = healthy.dry_run(resolved_workspace)
    assert fresh.run_id != report.run_id
    result = healthy.apply(resolved_workspace, project_id=project, confirm_run_id=fresh.run_id)
    assert result.validation.ok, f"新报告导入应完整落成：{result.validation.failures}"


def _clock() -> Any:
    from hastings.infrastructure.runtime import SystemClock

    return SystemClock()


def _ids() -> Any:
    from hastings.infrastructure.runtime import UuidIdGenerator

    return UuidIdGenerator()
