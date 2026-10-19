"""启动恢复与旧任务隔离（V03-8.1，计划 §8.1 八步的可代码化部分）。

验收口径（计划 §8.1 / I06 / I11）：重启后原历史不变、执行中的任务标中断等作者继续、
旧自动队列任务隔离不复活；恢复只改状态，不自动重发任何模型调用。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from hastings.server.v2 import V2Runtime, build_v2_app


def _prepare(tmp_path) -> tuple[V2Runtime, str, str]:
    """旧库 + 两类遗留任务：未执行的旧自动任务、执行中的作者任务。"""

    async def setup() -> tuple[V2Runtime, str, str]:
        runtime = V2Runtime(db_path=str(tmp_path / "v2recovery.db"))
        legacy = await runtime.jobs.create("manuscript.analysis", {"project_id": "prj_x"})
        running = await runtime.jobs.create(
            "draft",
            {"project_id": "prj_x", "grant": {"credential_ref": "credential://x"}},
        )
        await runtime.jobs.claim(
            running, "worker-1", now=datetime.now(UTC), ttl=60
        )
        return runtime, legacy, running

    return asyncio.run(setup())


def _job(runtime: V2Runtime, job_id: str) -> dict:
    return asyncio.run(runtime.jobs.get(job_id)) or {}


def _events(runtime: V2Runtime, job_id: str) -> list[str]:
    rows = asyncio.run(runtime.jobs.events(job_id))
    return [str(row.get("type", "")) for row in rows]


def test_before_startup_has_no_recovery_report() -> None:
    async def build() -> V2Runtime:
        return V2Runtime.memory()

    runtime = asyncio.run(build())
    report = asyncio.run(runtime.recovery_report())
    assert report["last_recovery"] is None
    assert report["task_states"] == {}
    assert report["note"]


def test_startup_interrupts_running_and_pauses_legacy_without_replaying(tmp_path) -> None:
    runtime, legacy_id, running_id = _prepare(tmp_path)
    assert _job(runtime, legacy_id)["state"] == "queued"
    assert _job(runtime, running_id)["state"] == "running"

    with TestClient(build_v2_app(runtime)) as client:
        report = client.get("/api/v2/recovery").json()

    summary = report["last_recovery"]["report"]
    assert summary["tasks_interrupted"] == 1
    assert summary["legacy_paused"] == {"manuscript.analysis:queued": 1}
    assert summary["chat_rounds_interrupted"] == 0

    # 没有任务停留在可派发状态：重启不会自动重发模型调用（I06/I11）。
    assert report["task_states"] == {"waiting": 2}
    assert sorted(report["waiting_task_ids"]) == sorted([legacy_id, running_id])
    assert report["legacy_auto_snapshot"] == {"manuscript.analysis:waiting": 1}

    interrupted = _job(runtime, running_id)
    assert interrupted["state"] == "waiting"
    assert interrupted["output"] == {"kind": "no_result", "reason": "interrupted"}
    assert "job.interrupted" in _events(runtime, running_id)

    paused = _job(runtime, legacy_id)
    assert paused["state"] == "waiting"
    assert paused["output"] == {"kind": "no_result", "reason": "historical_paused"}
    assert "job.paused" in _events(runtime, legacy_id)
    # 恢复本身不产生任何模型痕迹
    assert "agent.output.proposed" not in _events(runtime, running_id)


def test_clean_startup_reports_zero_recovery(tmp_path) -> None:
    runtime = V2Runtime(db_path=str(tmp_path / "clean.db"))
    with TestClient(build_v2_app(runtime)) as client:
        report = client.get("/api/v2/recovery").json()
    summary = report["last_recovery"]["report"]
    assert summary == {
        "chat_rounds_interrupted": 0,
        "tasks_interrupted": 0,
        "legacy_paused": {},
        "legacy_paused_total": 0,
    }
    assert report["task_states"] == {}
    assert report["legacy_auto_snapshot"] == {}


def test_recovery_is_idempotent_on_second_startup(tmp_path) -> None:
    runtime, legacy_id, running_id = _prepare(tmp_path)
    with TestClient(build_v2_app(runtime)) as client:
        first = client.get("/api/v2/recovery").json()["last_recovery"]["report"]
    assert first["legacy_paused_total"] == 1
    before = _events(runtime, legacy_id)

    # 模拟「再次重启」：进程内分类缓存清空，分类会重新跑一遍；但暂停幂等，
    # 已 waiting 的任务不重复写事件，执行中断也不重复标记。
    runtime._classified_legacy.clear()
    again = asyncio.run(runtime.recover_on_startup())
    assert again["tasks_interrupted"] == 0
    assert again["legacy_paused"] == {"manuscript.analysis:waiting": 1}
    assert _events(runtime, legacy_id) == before
    assert _job(runtime, running_id)["state"] == "waiting"
