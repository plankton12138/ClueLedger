"""tests/v2/author_model 公共夹具。

三层夹具：
- 领域层：由 (id, kind, content) 构造 Snapshot/AuthorModelCatalog（T14.5）；
- 应用层：内存仓库 + ManuscriptCommandHandler（造正文与编辑 Commit）+
  AuthorModelCommandHandler（登记观察）；
- 用例层：AuthorLearning（规则优先分类 → 可选模型补充 → 登记观察）。
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from hastings.application.handlers.author_model_commands import AuthorModelCommandHandler
from hastings.application.handlers.manuscript_commands import ManuscriptCommandHandler
from hastings.application.workflows.author_learning import AuthorLearning
from hastings.domain.author_model import kinds
from hastings.domain.author_model.catalog import AuthorModelCatalog
from hastings.domain.author_model.models import (
    EXAMPLE_ACTIVE,
    ORIGIN_EXPLICIT,
    POLARITY_POSITIVE,
    PREFERENCE_ACTIVE,
    SCOPE_PROJECT,
    SOURCE_RULE,
    AuthorExample,
    AuthorObservation,
    Classification,
    EditSample,
    PreferenceHypothesis,
)
from hastings.domain.manuscript import operations as ops
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


def build_catalog(entries: list[Entry], *, commit_id: str = "cmt_1") -> AuthorModelCatalog:
    return AuthorModelCatalog.from_snapshot(build_snapshot(entries, commit_id=commit_id))


def observation_entry(
    observation_id: str,
    *,
    commit_id: str,
    unit_id: str,
    category: str | None,
    confidence: float = 0.9,
    status: str = "confirmed",
    source: str = SOURCE_RULE,
    before: str = "",
    after: str = "",
    operation: str = ops.OP_REPLACE,
    chapter_id: str = "art_ch1",
    signals: tuple[str, ...] = (),
    alternatives: tuple[str, ...] = (),
) -> Entry:
    """构造一条已分类观察的 Entry（内容经领域模型编码，保持 schema 同步）。"""
    observation = AuthorObservation(
        observation_id=observation_id,
        commit_id=commit_id,
        edit=EditSample(
            unit_id=unit_id,
            before=before,
            after=after,
            chapter_id=chapter_id,
            operation=operation,
        ),
        classification=Classification(
            category=category,
            confidence=confidence,
            status=status,
            signals=signals,
            alternatives=alternatives,
            source=source,
        ),
    )
    return (observation_id, kinds.KIND_OBSERVATION, observation.to_content())


def preference_entry(
    preference_id: str,
    *,
    category: str,
    statement: str,
    scope_kind: str = SCOPE_PROJECT,
    scope_target: str = "",
    status: str = PREFERENCE_ACTIVE,
    origin: str = ORIGIN_EXPLICIT,
    evidence: tuple[str, ...] = (),
    superseded_by: str | None = None,
    reason: str = "",
) -> Entry:
    """构造一条偏好的 Entry（内容经领域模型编码，保持 schema 同步）。"""
    preference = PreferenceHypothesis(
        preference_id=preference_id,
        category=category,
        statement=statement,
        scope_kind=scope_kind,
        scope_target=scope_target,
        status=status,
        origin=origin,
        evidence=evidence,
        superseded_by=superseded_by,
        reason=reason,
    )
    return (preference_id, kinds.KIND_PREFERENCE, preference.to_content())


def example_entry(
    example_id: str,
    *,
    unit_id: str,
    revision_id: str,
    commit_id: str,
    polarity: str = POLARITY_POSITIVE,
    note: str = "",
    status: str = EXAMPLE_ACTIVE,
    superseded_by: str | None = None,
    reason: str = "",
) -> Entry:
    """构造一条作者样例的 Entry（内容经领域模型编码，保持 schema 同步）。"""
    example = AuthorExample(
        example_id=example_id,
        unit_id=unit_id,
        revision_id=revision_id,
        commit_id=commit_id,
        polarity=polarity,
        note=note,
        status=status,
        superseded_by=superseded_by,
        reason=reason,
    )
    return (example_id, kinds.KIND_EXAMPLE, example.to_content())


@pytest.fixture
def make_catalog() -> Callable[..., AuthorModelCatalog]:
    return build_catalog


@pytest.fixture
def next_id() -> Callable[[str], str]:
    counter = itertools.count(1)
    return lambda kind: f"art_new_{next(counter)}"


# ---------------------------------------------------------------------------
# 应用层夹具：内存仓库 + 双 handler + 用例
# ---------------------------------------------------------------------------
class FakeClock:
    """可手动推进的时钟；绝不 sleep。"""

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
class AuthorEnv:
    """应用层环境：仓库门面 + 正文/作者模型双 handler + 学习用例。"""

    repo: Any  # MemoryRepository（含 service 与 EventOutbox.pending）
    service: Any  # CommitService（读+写门面）
    manuscript_handler: ManuscriptCommandHandler
    author_handler: AuthorModelCommandHandler
    learning: AuthorLearning
    clock: FakeClock
    ids: FakeIds

    def catalog_at(self, project_id: ProjectId, head: str) -> AuthorModelCatalog:
        return AuthorModelCatalog.from_snapshot(
            self.service.get_snapshot(project_id, CommitId(head))
        )


@pytest.fixture
def author_env() -> AuthorEnv:
    clock, ids = FakeClock(), FakeIds()
    repo = build_memory_repository(clock, ids)
    actor = Actor(kind="human", id="author_test")
    manuscript_handler = ManuscriptCommandHandler(repo.service, repo.service, ids, actor)
    author_handler = AuthorModelCommandHandler(repo.service, repo.service, ids, actor)
    return AuthorEnv(
        repo=repo,
        service=repo.service,
        manuscript_handler=manuscript_handler,
        author_handler=author_handler,
        learning=AuthorLearning(repo.service, author_handler),
        clock=clock,
        ids=ids,
    )
