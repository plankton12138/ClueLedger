import asyncio
import json

import pytest
from changes.test_branch_executor import _seed_chain

from hastings.application.workflows.agent_workflows import AgentWorkflows
from hastings.application.workflows.worker_job import WorkerJobRunner
from hastings.domain.repository.ids import ArtifactId
from hastings.infrastructure.agents.tau_runner import TauAgentRunner
from hastings.infrastructure.jobs import SqliteJobStore
from tau_agent.messages import AssistantMessage
from tau_ai import AssistantDoneEvent, FakeProvider


def scripted(body):
    return FakeProvider(
        [
            [
                AssistantDoneEvent(
                    reason="stop",
                    message=AssistantMessage(content=json.dumps(body, ensure_ascii=False)),
                )
            ]
        ]
    )


def test_draft_is_waiting_until_author_accepts(changes_env, tmp_path):
    async def check():
        env = changes_env
        project, base = _seed_chain(env)
        fake = scripted(
            {
                "kind": "manuscript_patch",
                "base_commit_id": base,
                "chapter_id": "chapter_new",
                "text": "他把怀表藏进口袋。",
            }
        )
        jobs = SqliteJobStore(str(tmp_path / "jobs.sqlite"))
        runner = WorkerJobRunner(
            jobs, TauAgentRunner(lambda: fake, "fake", env.service), env.repo, env.ids, env.clock
        )
        app = AgentWorkflows(env.service, env.service, runner, env.ids, env.clock)
        jid = await app.draft(project.id, base, ("art_b",), "chapter_new", "draft")
        result = await runner.execute(jid)
        assert result["state"] == "waiting"
        head = env.service.get_branch(project.id, "main").head_commit_id
        assert env.service.get_artifact(ArtifactId("chapter_new"), head) is None
        accepted = await app.accept(jid, head.value)
        assert accepted["commit_id"]
        assert env.service.get_artifact(
            ArtifactId("chapter_new"), env.service.get_branch(project.id, "main").head_commit_id
        )
        assert await app.accept(jid, accepted["commit_id"]) == accepted
        from hastings.application.errors import ViolationError

        with pytest.raises(ViolationError, match="STYLE_HUMAN_DIFF_REQUIRED"):
            await app.style(
                project.id, head.value, accepted["commit_id"], "chapter_new", "do-not-learn-ai"
            )

    asyncio.run(check())


def test_draft_runs_without_beat_targets(changes_env, tmp_path):
    """V03-3.1：起草不再要求节拍；空 targets 时上下文不装配 beat_states。"""

    async def check():
        env = changes_env
        project, base = _seed_chain(env)
        fake = scripted(
            {
                "kind": "manuscript_patch",
                "base_commit_id": base,
                "chapter_id": "chapter_no_beat",
                "text": "他从口袋里摸出怀表，又放了回去。",
            }
        )
        jobs = SqliteJobStore(str(tmp_path / "jobs-no-beat.sqlite"))
        runner = WorkerJobRunner(
            jobs, TauAgentRunner(lambda: fake, "fake", env.service), env.repo, env.ids, env.clock
        )
        app = AgentWorkflows(env.service, env.service, runner, env.ids, env.clock)
        jid = await app.draft(project.id, base, (), "chapter_no_beat", "draft-no-beat")
        ctx = (await jobs.get(jid))["payload"]["context"]
        assert ctx["target_ids"] == []
        assert "beat_states" not in ctx["evidence"]
        result = await runner.execute(jid)
        assert result["state"] == "waiting"
        accepted = await app.accept(jid, base)
        assert accepted["commit_id"]
        head = env.service.get_branch(project.id, "main").head_commit_id
        assert env.service.get_artifact(ArtifactId("chapter_no_beat"), head) is not None

    asyncio.run(check())


def test_draft_rejects_unknown_beat_target(changes_env, tmp_path):
    async def check():
        env = changes_env
        project, base = _seed_chain(env)
        jobs = SqliteJobStore(str(tmp_path / "jobs-bad-target.sqlite"))
        runner = WorkerJobRunner(
            jobs,
            TauAgentRunner(lambda: scripted({}), "fake", env.service),
            env.repo,
            env.ids,
            env.clock,
        )
        app = AgentWorkflows(env.service, env.service, runner, env.ids, env.clock)
        from hastings.application.errors import ViolationError

        with pytest.raises(ViolationError, match="DRAFT_TARGET_INVALID"):
            await app.draft(project.id, base, ("art_missing",), "chapter_x", "draft-bad")

    asyncio.run(check())


def test_style_uses_real_diff_and_fact_change_is_not_style(changes_env, tmp_path):
    async def check():
        env = changes_env
        project, base = _seed_chain(env)
        from hastings.domain.repository.requests import ArtifactChange, CommitRequest

        branch = env.service.get_branch(project.id, "main")
        view = env.service.get_artifact(ArtifactId("tu_x"), branch.head_commit_id)
        saved = env.service.commit(
            CommitRequest(
                project.id,
                branch.id,
                branch.head_commit_id,
                view.revision.author,
                "edit",
                "修正事实时间",
                changes=(
                    ArtifactChange(
                        ArtifactId("tu_x"),
                        {
                            **view.revision.content,
                            "text": view.revision.content["text"] + "时间是2026年10月16日。",
                        },
                        view.revision.id,
                    ),
                ),
            )
        )
        fake = scripted(
            {
                "kind": "observation_set",
                "base_commit_id": saved.commit_id.value,
                "observations": [
                    {
                        "unit_id": "tu_x",
                        "category": "style",
                        "statement": "偏好短句",
                        "confidence": 1.0,
                    }
                ],
            }
        )
        jobs = SqliteJobStore(str(tmp_path / "jobs.sqlite"))
        runner = WorkerJobRunner(
            jobs, TauAgentRunner(lambda: fake, "fake", env.service), env.repo, env.ids, env.clock
        )
        app = AgentWorkflows(env.service, env.service, runner, env.ids, env.clock)
        jid = await app.style(project.id, base, saved.commit_id.value, "ch1", "style")
        await runner.execute(jid)
        await app.apply_analysis(jid)
        snapshot = env.service.get_snapshot(
            project.id, env.service.get_branch(project.id, "main").head_commit_id
        )
        observations = [
            v.revision.content
            for v in snapshot.artifacts.values()
            if v.artifact.kind == "author_model.observation"
        ]
        assert observations and all(
            o["classification"]["category"] != "language_style" for o in observations
        )

    asyncio.run(check())
