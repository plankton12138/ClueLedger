"""任务模型绑定测试（Phase 19.5 T19.5b）。

计划 §19.5：每个任务持久化 provider 连接 ID/model/协议快照；切模型不改变
已启动任务；重试保持已绑定模型；来源失效明确失败，不静默换厂商。
"""

from __future__ import annotations

from typing import Any

import pytest
from changes.test_branch_executor import _seed_chain

from hastings.application.errors import EnvironmentFailure
from hastings.application.workflows.agent_workflows import AgentWorkflows
from hastings.application.workflows.worker_job import WorkerJobRunner
from hastings.infrastructure.jobs import SqliteJobStore
from hastings.server.provider_config import ServerConfig
from hastings.server.v2_agents import ConfiguredV2Runner


class BoundAwareStubRunner:
    """记录绑定调用；返回合法 writer 输出。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.output: dict[str, Any] = {
            "kind": "manuscript_patch",
            "base_commit_id": "unset",
            "chapter_id": "chapter_new",
            "text": "他把怀表藏进口袋。",
        }

    def with_base(self, base: str) -> BoundAwareStubRunner:
        self.output = {**self.output, "base_commit_id": base}
        return self

    async def run_with_events(self, task_kind: str, context_package: Any, sink: Any) -> dict:
        return await self.run_with_events_bound(task_kind, context_package, sink, {})

    async def run_with_events_bound(
        self, task_kind: str, context_package: Any, sink: Any, binding: dict[str, Any]
    ) -> dict:
        self.calls.append((task_kind, dict(binding)))
        return dict(self.output)


BINDING = {"profile": "book-a", "model": "m-7", "api": "openai-completions"}


@pytest.mark.anyio
async def test_submit_persists_binding_and_execute_dispatches_to_bound_runner(
    changes_env, tmp_path
) -> None:
    env = changes_env
    project, base = _seed_chain(env)
    stub = BoundAwareStubRunner().with_base(base)
    jobs = SqliteJobStore(str(tmp_path / "jobs.sqlite"))
    runner = WorkerJobRunner(jobs, stub, env.repo, env.ids, env.clock)
    app = AgentWorkflows(env.service, env.service, runner, env.ids, env.clock)

    jid = await app.draft(
        project.id, base, ("art_b",), "chapter_new", "draft-1", model_binding=BINDING
    )
    job = await jobs.get(jid)
    assert job["payload"]["model_binding"] == BINDING  # 快照随任务持久化

    result = await runner.execute(jid)
    assert result["state"] == "waiting"
    assert stub.calls == [("writer", BINDING)]  # 执行用绑定模型，不是当前选择


@pytest.mark.anyio
async def test_submit_without_binding_keeps_legacy_path(changes_env, tmp_path) -> None:
    env = changes_env
    project, base = _seed_chain(env)
    stub = BoundAwareStubRunner().with_base(base)
    jobs = SqliteJobStore(str(tmp_path / "jobs.sqlite"))
    runner = WorkerJobRunner(jobs, stub, env.repo, env.ids, env.clock)
    app = AgentWorkflows(env.service, env.service, runner, env.ids, env.clock)

    jid = await app.audit(project.id, base, ("art_b",), "audit-1")
    await runner.execute(jid)
    job = await jobs.get(jid)
    assert "model_binding" not in job["payload"]


def test_unavailable_bound_source_fails_without_fallback() -> None:
    """绑定来源失效明确失败：不静默换用其他厂商。"""
    config = ServerConfig(
        providers=[
            {
                "name": "gone",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-test",
                "default_model": "m-7",
            }
        ]
    )
    runner = ConfiguredV2Runner(reader=None, config=config)  # type: ignore[arg-type]
    with pytest.raises(EnvironmentFailure) as excinfo:
        runner._resolve_bound({"profile": "missing", "model": "m-1", "api": "openai-completions"})
    assert excinfo.value.code == "JOB_MODEL_SOURCE_UNAVAILABLE"
    assert "不会自动" in excinfo.value.why or "不静默" in excinfo.value.why


def test_model_binding_snapshots_current_selection() -> None:
    config = ServerConfig(
        providers=[
            {
                "name": "book-a",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-test",
                "default_model": "m-7",
            }
        ],
        active_provider="book-a",
        active_model="m-7",
    )
    runner = ConfiguredV2Runner(reader=None, config=config)  # type: ignore[arg-type]
    assert runner.model_binding() == {
        "profile": "book-a",
        "model": "m-7",
        "api": "openai-completions",
    }
