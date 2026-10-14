"""tests/v2/story 公共夹具：由 (id, kind, content) 构造 Repository Snapshot/Catalog。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from hastings.domain.repository.ids import (
    Actor,
    ArtifactId,
    CommitId,
    ContentHash,
    ProjectId,
    RevisionId,
)
from hastings.domain.repository.models import Artifact, ArtifactView, Revision, Snapshot
from hastings.domain.repository.serialization import content_hash
from hastings.domain.story.catalog import StoryCatalog

_TS = datetime(2026, 1, 1, tzinfo=UTC)
PROJECT_ID = ProjectId("prj_test")


def build_snapshot(
    entries: list[tuple[str, str, dict[str, Any]]],
    *,
    commit_id: str = "cmt_1",
    project_id: ProjectId = PROJECT_ID,
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
        relations=(),
    )


def build_catalog(
    entries: list[tuple[str, str, dict[str, Any]]],
    *,
    commit_id: str = "cmt_1",
) -> StoryCatalog:
    return StoryCatalog.from_snapshot(build_snapshot(entries, commit_id=commit_id))


@pytest.fixture
def make_catalog() -> Callable[..., StoryCatalog]:
    return build_catalog
