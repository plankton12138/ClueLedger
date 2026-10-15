"""tests/v2/manuscript 公共夹具。

两层夹具：
- 领域层：由 (id, kind, content) 构造 Snapshot/ManuscriptCatalog（T14.1）；
- 应用层：内存仓库 + ManuscriptCommandHandler + 保存工作流 + 可注入失败的 JobStore
  （T14.2）。
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from hastings.application.handlers.manuscript_commands import ManuscriptCommandHandler
from hastings.application.queries.manuscript import ManuscriptQueries
from hastings.application.workflows.analysis_dispatch import AnalysisDispatcher
from hastings.application.workflows.manual_edit import CommitManualEdit
from hastings.domain.manuscript import kinds
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.manuscript.models import Chapter, TextUnit
from hastings.domain.repository.ids import (
    Actor,
    ArtifactId,
    CommitId,
    ContentHash,
    ProjectId,
    RevisionId,
)
from hastings.domain.repository.models import (
    Artifact,
    ArtifactView,
    RelationRevision,
    Revision,
    Snapshot,
)
from hastings.domain.repository.serialization import content_hash
from hastings.infrastructure.events.outbox import OutboxPublisher
from hastings.infrastructure.repository.memory import build_memory_repository

_TS = datetime(2026, 1, 1, tzinfo=UTC)
PROJECT_ID = ProjectId("prj_test")

Entry = tuple[str, str, dict[str, Any]]


def build_snapshot(
    entries: list[Entry],
    *,
    commit_id: str = "cmt_1",
    project_id: ProjectId = PROJECT_ID,
    relations: tuple[RelationRevision, ...] = (),
) -> Snapshot:
    """把 (artifact_id, kind, content) 列表构造成带 Revision 的 Snapshot。"""
    artifacts: dict[ArtifactId, ArtifactView] = {}
    for artifact_id, kind, content in entries:
        aid = ArtifactId(artifact_id)
        revision = Revision(
            id=RevisionId(f"rev_{artifact_id}"),
            artifact_id=aid,
            parent_id=None,
            content=content,
            content_hash=ContentHash(content_hash(content)),
            author=Actor("system", "sys"),
            created_at=_TS,
        )
        artifacts[aid] = ArtifactView(
            artifact=Artifact(id=aid, project_id=project_id, kind=kind, created_at=_TS),
            revision=revision,
        )
    return Snapshot(
        project_id=project_id,
        commit_id=CommitId(commit_id),
        artifacts=artifacts,
        relations=relations,
    )


def build_catalog(
    entries: list[Entry],
    *,
    commit_id: str = "cmt_1",
    relations: tuple[RelationRevision, ...] = (),
) -> ManuscriptCatalog:
    return ManuscriptCatalog.from_snapshot(
        build_snapshot(entries, commit_id=commit_id, relations=relations)
    )


def chapter_entry(
    chapter_id: str,
    unit_ids: tuple[str, ...] = (),
    *,
    title: str = "第一章",
    order_key: str = "0001",
    **fields: Any,
) -> Entry:
    return (
        chapter_id,
        kinds.KIND_CHAPTER,
        Chapter(
            chapter_id=chapter_id,
            title=title,
            order_key=order_key,
            text_unit_ids=tuple(unit_ids),
            **fields,
        ).to_content(),
    )


def unit_entry(unit_id: str, chapter_id: str, text: str, **fields: Any) -> Entry:
    return (
        unit_id,
        kinds.KIND_TEXT_UNIT,
        TextUnit(unit_id=unit_id, chapter_id=chapter_id, text=text, **fields).to_content(),
    )


def three_unit_chapter(
    *,
    chapter_id: str = "art_ch1",
    texts: tuple[str, ...] = ("第一段", "第二段", "第三段"),
) -> list[Entry]:
    """建立一个含三段正文的章（id 依次 art_u1/art_u2/art_u3）。"""
    unit_ids = tuple(f"art_u{i}" for i in range(1, len(texts) + 1))
    entries: list[Entry] = [chapter_entry(chapter_id, unit_ids)]
    entries.extend(
        unit_entry(uid, chapter_id, text) for uid, text in zip(unit_ids, texts, strict=True)
    )
    return entries


@pytest.fixture
def make_catalog() -> Callable[..., ManuscriptCatalog]:
    return build_catalog


@pytest.fixture
def next_id() -> Callable[[str], str]:
    counter = itertools.count(1)
    return lambda kind: f"art_new_{next(counter)}"


# ---------------------------------------------------------------------------
# 应用层夹具（T14.2）：内存仓库 + handler + 保存工作流 + 可注入失败的 JobStore
# ---------------------------------------------------------------------------
class FakeClock:
    """可手动推进的时钟；绝不 sleep。"""

    def __init__(self) -> None:
        self._now = datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC)

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


class FakeJobStore:
    """内存 JobStore 测试桩；``fail_with`` 注入环境故障以验证保存与分析解耦。"""

    def __init__(self) -> None:
        self.created: list[tuple[str, dict[str, object]]] = []
        self.fail_with: Exception | None = None

    async def create(self, job_type: str, payload: Mapping[str, object]) -> str:
        if self.fail_with is not None:
            raise self.fail_with
        job_id = f"job_{len(self.created) + 1:04d}"
        self.created.append((job_type, dict(payload)))
        return job_id

    async def mark_retryable(self, job_id: str, reason: str) -> None:
        del job_id, reason

    async def get(self, job_id: str) -> Mapping[str, object] | None:
        del job_id
        return None


@dataclass
class ManualEditEnv:
    """应用层环境：仓库门面 + handler + 保存工作流 + 查询 + 任务桩 + 调度消费者。"""

    repo: Any  # MemoryRepository（含 service 与 EventOutbox.pending）
    service: Any  # CommitService（读+写门面）
    handler: ManuscriptCommandHandler
    workflow: CommitManualEdit
    queries: ManuscriptQueries
    jobs: FakeJobStore
    dispatcher: AnalysisDispatcher
    publisher: OutboxPublisher
    ids: FakeIds
    clock: FakeClock


@pytest.fixture
def edit_env() -> ManualEditEnv:
    clock, ids, jobs = FakeClock(), FakeIds(), FakeJobStore()
    repo = build_memory_repository(clock, ids)
    handler = ManuscriptCommandHandler(
        repo.service, repo.service, ids, Actor(kind="human", id="author_test")
    )
    dispatcher = AnalysisDispatcher(jobs)
    return ManualEditEnv(
        repo=repo,
        service=repo.service,
        handler=handler,
        workflow=CommitManualEdit(handler),
        queries=ManuscriptQueries(repo.service),
        jobs=jobs,
        dispatcher=dispatcher,
        publisher=OutboxPublisher(repo, dispatcher),
        ids=ids,
        clock=clock,
    )
