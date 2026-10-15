from typing import Any

from hastings.application.workflows.audit import AuditWorkflow
from hastings.domain.repository.ids import ArtifactId
from hastings.domain.repository.requests import ArtifactChange, CommitRequest

from .conftest import AUTHOR


def test_finding_fixed_requires_real_corresponding_repair_commit(changes_env: Any) -> None:
    env = changes_env
    project = env.handler.create_project("审计")
    branch = env.service.get_branch(project.id, "main")
    env.service.commit(
        CommitRequest(
            project.id,
            branch.id,
            branch.head_commit_id,
            AUTHOR,
            "seed",
            "播种冲突时间线",
            changes=tuple(
                ArtifactChange(
                    ArtifactId(aid),
                    {
                        "event_id": aid,
                        "time": "12:00",
                        "place_id": place,
                        "participants": ["hero"],
                        "description": "目击",
                        "schema_version": 1,
                    },
                    kind="story.timeline_event",
                )
                for aid, place in (("event1", "room1"), ("event2", "room2"))
            ),
        )
    )
    flow = AuditWorkflow(env.service, env.service, env.clock, AUTHOR)
    first = flow.run(project.id, "audit1")
    finding = next(f for f in first["findings"] if f["code"] == "STORY_TIMELINE_CONFLICT")
    assert finding["status"] == "open" and finding["evidence_commit_id"]
    second = flow.run(project.id, "audit2")
    assert (
        next(f for f in second["findings"] if f["finding_id"] == finding["finding_id"])["status"]
        == "open"
    )
    branch = env.service.get_branch(project.id, "main")
    view = env.service.get_artifact(ArtifactId("event2"), branch.head_commit_id)
    repair = env.service.commit(
        CommitRequest(
            project.id,
            branch.id,
            branch.head_commit_id,
            AUTHOR,
            "repair",
            "修复时间冲突",
            changes=(
                ArtifactChange(
                    ArtifactId("event2"),
                    {**view.revision.content, "time": "13:00"},
                    view.revision.id,
                ),
            ),
        )
    )
    branch = env.service.get_branch(project.id, "main")
    env.service.commit(
        CommitRequest(
            project.id,
            branch.id,
            branch.head_commit_id,
            AUTHOR,
            "metadata",
            "保存无关工作流记录",
            changes=(
                ArtifactChange(ArtifactId("note"), {"note": "unrelated"}, kind="changes.note"),
            ),
        )
    )
    third = flow.run(project.id, "audit3")
    fixed = next(f for f in third["findings"] if f["finding_id"] == finding["finding_id"])
    assert fixed["status"] == "fixed" and fixed["fixed_by_commit_id"] == repair.commit_id.value
