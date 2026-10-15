"""Phase 14 T14.2 补充：重启后分析调度的补偿与去重（真实 SQLite 后端）。

评审 #3 要求「重启后补偿」不是一句空话。本文件用**真实 SQLite 仓库**验证——保存只
写 Commit + Outbox；进程内一切状态丢弃后，未发布事件仍被消费者重新投递（补偿），
已发布事件不再出现（去重）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hastings.application.handlers.manuscript_commands import (
    ManuscriptCommandHandler,
    manuscript_command,
)
from hastings.application.workflows.analysis_dispatch import (
    ANALYSIS_JOB_TYPE,
    AnalysisDispatcher,
)
from hastings.application.workflows.manual_edit import CommitManualEdit, ManualEditIntent
from hastings.domain.manuscript import commands, operations
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.repository.ids import Actor
from hastings.infrastructure.events.outbox import OutboxPublisher
from hastings.infrastructure.repository.sqlite import build_sqlite_repository

from .conftest import FakeClock, FakeIds, FakeJobStore

CHAPTER = "art_ch1"


def _handler(service: Any, ids: FakeIds) -> ManuscriptCommandHandler:
    return ManuscriptCommandHandler(service, service, ids, Actor(kind="human", id="author_test"))


@pytest.mark.anyio
async def test_analysis_dispatch_compensates_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "repository.db"
    clock, ids = FakeClock(), FakeIds()
    repo = build_sqlite_repository(path, clock, ids)
    handler = _handler(repo.service, ids)
    project = handler.create_project("冬馆")
    handler.execute(
        manuscript_command(
            commands.import_chapter,
            chapter_id=CHAPTER,
            title="第一章",
            text="第一段\n\n第二段\n\n第三段",
        ),
        project_id=project.id,
        idempotency_key="import",
        summary="导入",
    )
    branch = repo.service.get_branch(project.id, "main")
    assert branch is not None and branch.head_commit_id is not None
    catalog = ManuscriptCatalog.from_snapshot(
        repo.service.get_snapshot(project.id, branch.head_commit_id)
    )
    units = [entry.artifact_id for entry in catalog.units_of(CHAPTER)]

    result = await CommitManualEdit(handler).save(
        ManualEditIntent(
            project_id=project.id,
            chapter_id=CHAPTER,
            idempotency_key="save-1",
            operations=(operations.Replace(unit_id=units[1], text="改后的第二段"),),
        )
    )
    assert result.is_committed

    # 「重启」：进程内状态全丢，仅保留磁盘库；新一批内存对象（含空去重集的新消费者）
    clock2, ids2, jobs2 = FakeClock(), FakeIds(), FakeJobStore()
    repo2 = build_sqlite_repository(path, clock2, ids2)
    publisher2 = OutboxPublisher(repo2, AnalysisDispatcher(jobs2))

    assert jobs2.created == []  # 无任何进程内记忆
    await publisher2.publish_pending(project.id.value)  # 待发布事件被重新投递（补偿）
    assert len(jobs2.created) == 1
    job_type, payload = jobs2.created[0]
    assert job_type == ANALYSIS_JOB_TYPE
    assert payload["commit_id"] == result.commit_id
    assert payload["chapter_id"] == CHAPTER

    # 已发布是持久标记：再次推进不再重复调度（去重跨重启生效）
    await publisher2.publish_pending(project.id.value)
    assert len(jobs2.created) == 1
