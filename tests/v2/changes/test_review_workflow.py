"""作者选择、合并冲突与残余影响必须以仓库事实验收。"""

from typing import Any

import pytest

from hastings.application.errors import ViolationError
from hastings.application.workflows.review import ReviewRevision
from hastings.application.workflows.revision import ExecuteIntent
from hastings.domain.repository.ids import Actor, ArtifactId
from hastings.domain.repository.requests import ArtifactChange, CommitRequest

from .conftest import AUTHOR, head_of
from .test_branch_executor import FakeWorker, _approved, _content_at, _executor, _full_patches


def ready(env: Any) -> tuple[Any, str, Any, Any]:
    project, _base, cs_id = _approved(env)
    _executor(env).execute(ExecuteIntent(project.id, cs_id, "exec"), FakeWorker(_full_patches()))
    review = ReviewRevision(env.service, env.service, env.clock, AUTHOR)
    patches = review.prepare(project.id, cs_id, "prepare")
    return project, cs_id, review, patches


def test_partial_acceptance_keeps_rejected_impact_open(changes_env: Any) -> None:
    env = changes_env
    project, cs_id, review, patches = ready(env)
    chosen = tuple(h["hunk_id"] for h in patches["hunks"] if h["artifact_id"] != "tu_x")
    result = review.merge(project.id, cs_id, chosen, {}, "merge", head_of(env, project.id))
    head = head_of(env, project.id)
    assert _content_at(env, project, head, "art_b")["motive"] == "操纵者的算计"
    assert _content_at(env, project, head, "tu_x")["text"] == "他递出怀表时指尖在抖。"
    assert [i["target_artifact_id"] for i in result["items"] if i["status"] == "open"] == ["tu_x"]
    assert result["status"] == "committed"
    assert review.merge(project.id, cs_id, chosen, {}, "merge", head) == result
    with pytest.raises(ViolationError):
        review.merge(project.id, cs_id, (), {}, "merge", head)


def test_conflicting_manual_edit_is_not_overwritten(changes_env: Any) -> None:
    env = changes_env
    project, cs_id, review, patches = ready(env)
    branch = env.service.get_branch(project.id, "main")
    view = env.service.get_artifact(ArtifactId("art_b"), branch.head_commit_id)
    env.service.commit(
        CommitRequest(
            project.id,
            branch.id,
            branch.head_commit_id,
            AUTHOR,
            "manual",
            "人工修改",
            changes=(
                ArtifactChange(
                    ArtifactId("art_b"), {"name": "art_b", "motive": "作者想法"}, view.revision.id
                ),
            ),
        )
    )
    before = head_of(env, project.id)
    with pytest.raises(ViolationError) as error:
        review.merge(
            project.id, cs_id, tuple(h["hunk_id"] for h in patches["hunks"]), {}, "merge", before
        )
    assert error.value.code == "CHANGES_REVIEW_CONFLICT"
    assert head_of(env, project.id) == before


def test_agent_cannot_merge_and_unknown_hunks_are_rejected(changes_env: Any) -> None:
    env = changes_env
    project, cs_id, review, _patches = ready(env)
    with pytest.raises(ViolationError):
        review.merge(project.id, cs_id, ("forged",), {}, "bad", head_of(env, project.id))
    agent = ReviewRevision(env.service, env.service, env.clock, Actor("agent", "worker"))
    with pytest.raises(ViolationError):
        agent.merge(project.id, cs_id, (), {}, "agent", head_of(env, project.id))


def test_accepted_risk_remains_visible(changes_env: Any) -> None:
    project, cs_id, review, _patches = ready(changes_env)
    result = review.merge(
        project.id,
        cs_id,
        (),
        {"tu_x": "作者暂时保留旧稿"},
        "risk",
        head_of(changes_env, project.id),
    )
    assert (
        next(i for i in result["items"] if i["target_artifact_id"] == "tu_x")["status"]
        == "accepted_risk"
    )


def test_individual_text_hunks_can_be_selected() -> None:
    from hastings.domain.changes.review import apply_hunks, make_hunks

    before = {"text": "A line\nkeep\nB line\n"}
    after = {"text": "A revised\nkeep\nB revised\n"}
    hunks = make_hunks("unit", before, after)
    assert len(hunks) == 2
    content = apply_hunks(before, before, hunks[:1])
    assert content == {"text": "A revised\nkeep\nB line\n"}


def test_changed_plan_invalidates_review_authorization(changes_env: Any) -> None:
    env = changes_env
    project, cs_id, review, _patches = ready(env)
    branch = env.service.get_branch(project.id, "main")
    snapshot = env.service.get_snapshot(project.id, branch.head_commit_id)
    plan = next(
        v for v in snapshot.artifacts.values() if v.artifact.kind == "changes.revision_plan"
    )
    body = dict(plan.revision.content)
    body["steps"] = [{**s, "title": "作者调整了计划"} for s in body["steps"]]
    env.service.commit(
        CommitRequest(
            project.id,
            branch.id,
            branch.head_commit_id,
            AUTHOR,
            "alter-plan",
            "改变计划",
            changes=(ArtifactChange(plan.artifact.id, body, plan.revision.id),),
        )
    )
    with pytest.raises(ViolationError) as error:
        review.prepare(project.id, cs_id, "new-review")
    assert error.value.code == "CHANGES_RECEIPT_STALE"
