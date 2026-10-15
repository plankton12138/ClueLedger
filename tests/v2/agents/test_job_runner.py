import asyncio

from changes.test_branch_executor import _seed_chain

from hastings.application.contexts import ContextBuilder
from hastings.application.errors import EnvironmentFailure
from hastings.application.workflows.worker_job import WorkerJobRunner
from hastings.infrastructure.jobs import SqliteJobStore


def test_cancel_discards_late_output(changes_env, tmp_path):
    async def check():
        env = changes_env
        project, base = _seed_chain(env)
        ctx = ContextBuilder(env.service).build("writer", project.id, base, ("art_b",))
        jobs = SqliteJobStore(str(tmp_path / "jobs.sqlite"))
        started, release = asyncio.Event(), asyncio.Event()

        class Slow:
            async def run(self, role, context):
                started.set()
                await release.wait()
                return {"kind": "no_result", "reason": "late"}

        flow = WorkerJobRunner(jobs, Slow(), env.repo, env.ids, env.clock, poll_seconds=0.01)
        jid = await flow.submit(ctx, "one")
        task = asyncio.create_task(flow.execute(jid))
        await started.wait()
        await flow.cancel(jid)
        release.set()
        assert (await task)["state"] == "cancelled"
        assert (await jobs.get(jid))["checkpoint"] is None

    asyncio.run(check())


def test_environment_failure_is_retryable_and_checkpoint_replays(changes_env, tmp_path):
    async def check():
        env = changes_env
        project, base = _seed_chain(env)
        ctx = ContextBuilder(env.service).build("auditor", project.id, base, ("art_b",))
        jobs = SqliteJobStore(str(tmp_path / "jobs.sqlite"))

        class Offline:
            async def run(self, role, context):
                raise EnvironmentFailure(
                    code="PROVIDER_OFFLINE", where="provider", why="断线", fix="重连后重试"
                )

        flow = WorkerJobRunner(jobs, Offline(), env.repo, env.ids, env.clock)
        jid = await flow.submit(ctx, "one")
        assert (await flow.execute(jid))["state"] == "retryable_failed"
        env.clock.advance(10)

        class Good:
            async def run(self, role, context):
                return {"kind": "no_result", "reason": "检查无问题"}

        again = WorkerJobRunner(jobs, Good(), env.repo, env.ids, env.clock)
        assert (await again.execute(jid))["state"] == "completed"

    asyncio.run(check())


def test_restart_uses_checkpoint_and_does_not_call_model(changes_env, tmp_path):
    async def check():
        env = changes_env
        project, base = _seed_chain(env)
        ctx = ContextBuilder(env.service).build("auditor", project.id, base, ("art_b",))
        path = str(tmp_path / "jobs.sqlite")
        store = SqliteJobStore(path)
        jid = await store.create(
            "auditor", {"project_id": project.id.value, "context": ctx.model_dump(mode="json")}
        )
        claimed = await store.claim(jid, "crashed", now=env.clock.now(), ttl=1)
        await store.checkpoint(
            jid, claimed["generation"], {"kind": "no_result", "reason": "无一致性问题"}
        )
        env.clock.advance(2)
        restarted = SqliteJobStore(path)
        await restarted.recover(env.clock.now())

        class Unreachable:
            async def run(self, role, context):
                raise AssertionError("已有有效检查点不应再调模型")

        runner = WorkerJobRunner(restarted, Unreachable(), env.repo, env.ids, env.clock)
        assert (await runner.execute(jid))["state"] == "completed"

    asyncio.run(check())


def test_retry_exhaustion_keeps_environment_category(changes_env, tmp_path):
    async def check():
        env = changes_env
        project, base = _seed_chain(env)
        ctx = ContextBuilder(env.service).build("auditor", project.id, base, ("art_b",))

        class Offline:
            async def run(self, role, context):
                raise OSError("断线")

        runner = WorkerJobRunner(
            SqliteJobStore(str(tmp_path / "jobs.sqlite")),
            Offline(),
            env.repo,
            env.ids,
            env.clock,
            max_attempts=1,
        )
        job = await runner.execute(await runner.submit(ctx, "offline"))
        assert job["state"] == "terminal_failed" and job["output"]["category"] == "environment"

    asyncio.run(check())
