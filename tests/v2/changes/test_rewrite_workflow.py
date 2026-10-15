from typing import Any

import pytest

from hastings.application.errors import ViolationError
from hastings.application.workflows.rewrite import RewriteWorkflow
from hastings.domain.changes.execution import PatchProposal, PatchUnit

from .conftest import AUTHOR, head_of
from .test_branch_executor import _content_at, _seed_chain


def test_rewrite_locks_abort_compare_and_abandon(changes_env: Any) -> None:
    env = changes_env
    project, base = _seed_chain(env)
    flow = RewriteWorkflow(env.service, env.service, env.repo, env.ids, env.clock, AUTHOR)
    run = flow.open(project.id, base, ("tu_x",), "open-rewrite")
    assert flow.compare(project.id, run["rewrite_id"])["hunks"] == []
    assert "tu_x" in run["locked_unit_ids"]
    flow.abort(project.id, run["rewrite_id"], "abort")
    with pytest.raises(ViolationError):
        flow.execute(project.id, run["rewrite_id"], lambda c: PatchProposal(), "exec")
    flow.abandon(project.id, run["rewrite_id"], "abandon")
    assert _content_at(env, project, head_of(env, project.id), "tu_x") == _content_at(
        env, project, base, "tu_x"
    )


def test_rewrite_runs_by_chapter_and_never_passes_locked_units(changes_env: Any) -> None:
    env = changes_env
    project, base = _seed_chain(env)
    flow = RewriteWorkflow(env.service, env.service, env.repo, env.ids, env.clock, AUTHOR)
    run = flow.open(project.id, base, (), "open-rewrite")
    seen = []

    def worker(context: Any) -> PatchProposal:
        seen.append(context)
        return PatchProposal(
            tuple(
                PatchUnit(t.artifact_id, {**t.content, "text": "新正文"}) for t in context.targets
            )
        )

    result = flow.execute(project.id, run["rewrite_id"], worker, "exec")
    assert result["status"] == "review"
    assert len(seen) == 1
    assert flow.compare(project.id, run["rewrite_id"])["hunks"]
    assert _content_at(env, project, head_of(env, project.id), "tu_x")["text"] != "新正文"


def test_rewrite_recovers_commit_before_progress_record(changes_env: Any, monkeypatch: Any) -> None:
    env = changes_env
    project, base = _seed_chain(env)
    flow = RewriteWorkflow(env.service, env.service, env.repo, env.ids, env.clock, AUTHOR)
    run = flow.open(project.id, base, (), "rewrite-recovery")
    original_save = flow._save

    def crash(project_id: Any, body: Any, key: str) -> None:
        if key.endswith(":record"):
            raise KeyboardInterrupt("branch commit done, progress not recorded")
        original_save(project_id, body, key)

    monkeypatch.setattr(flow, "_save", crash)
    with pytest.raises(KeyboardInterrupt):
        flow.execute(
            project.id,
            run["rewrite_id"],
            lambda c: PatchProposal(
                tuple(PatchUnit(t.artifact_id, {**t.content, "text": "新稿"}) for t in c.targets)
            ),
            "first",
        )
    restored = RewriteWorkflow(env.service, env.service, env.repo, env.ids, env.clock, AUTHOR)
    result = restored.execute(
        project.id, run["rewrite_id"], lambda c: pytest.fail("不得重跑已提交章节"), "resume"
    )
    assert result["completed_chapters"] == ["ch1"]
