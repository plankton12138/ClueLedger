"""导出测试直接创建 V2 项目，不依赖旧工作区或迁移器。"""

from dataclasses import dataclass
from typing import Any

import pytest

from hastings.application.handlers.manuscript_commands import (
    ManuscriptCommandHandler,
    manuscript_command,
)
from hastings.domain.manuscript.commands import import_chapter
from hastings.domain.repository.ids import Actor, ArtifactId, ProjectId
from hastings.domain.repository.requests import ArtifactChange, CommitRequest, RelationChange
from hastings.domain.story.canon.models import Character
from hastings.infrastructure.repository.memory import build_memory_repository
from hastings.infrastructure.runtime import SystemClock, UuidIdGenerator

ACTOR = Actor(kind="human", id="exporter")


@dataclass
class ExportEnv:
    service: Any
    project: ProjectId
    head: str


@pytest.fixture
def native_project() -> ExportEnv:
    ids = UuidIdGenerator()
    service = build_memory_repository(SystemClock(), ids).service
    project = service.create_project("导出源").id
    handler = ManuscriptCommandHandler(service, service, ids, ACTOR)
    for index in range(1, 4):
        handler.execute(
            manuscript_command(
                import_chapter,
                chapter_id=f"chapter-{index}",
                title=f"第{index}章",
                text=f"第{index}章的开场。\n\n雨声从窗外传来。",
                order_key=f"{index:04d}",
            ),
            project_id=project,
            idempotency_key=f"chapter-{index}",
            summary=f"创建第{index}章",
        )
    branch = service.get_branch(project, "main")
    result = service.commit(
        CommitRequest(
            project_id=project,
            branch_id=branch.id,
            expected_head=branch.head_commit_id,
            actor=ACTOR,
            idempotency_key="character",
            summary="登记角色及章节依赖",
            changes=(
                ArtifactChange(
                    artifact_id=ArtifactId("character-1"),
                    kind="story.character",
                    content=Character(name="侦探", identity="调查者", goal="查明真相").to_content(),
                ),
            ),
            relations=(
                RelationChange(
                    action="add",
                    source=ArtifactId("chapter-1"),
                    relation_type="references",
                    target=ArtifactId("character-1"),
                ),
            ),
        )
    )
    assert result.new_head is not None
    return ExportEnv(service=service, project=project, head=result.new_head.value)
