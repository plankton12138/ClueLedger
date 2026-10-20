"""V03-0.3 显式启动凭据、派发门禁与零隐式模型调用（计划 §4.4、执行指导 §4.3）。

覆盖不变量 I05（每项模型功能有可追溯的作者启动依据）与 I06（打开作品、
保存、恢复、模型连接成功不自行创建模型任务）：

- 模型运行记录器（可注入任何 runner 构造点）：打开作品 / 手改保存 / 重启
  恢复全过程零调用；
- 无凭据 Job 拒绝派发（historical_paused），凭据无效（功能不匹配 / 预算
  耗尽）拒绝派发（not_authorized + 错误码）；
- 作者显式启动的 Job 带凭据放行，凭据字段即审计记录；
- 启动恢复：执行中任务标 interrupted，不自动重发；
- pause / mark_interrupted 的内存与 SQLite 双后端契约。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hastings.application.execution import (
    ConnectionSnapshot,
    ExecutionGrant,
    build_grant,
)
from hastings.infrastructure.jobs import MemoryJobStore, SqliteJobStore
from hastings.server.v2 import V2Runtime, build_v2_app


class ModelCallRecorder:
    """模型运行记录器（执行指导 §4.3）：注入 agent runner 与 chat runner 工厂。

    记录每次调用的功能、task_kind、凭据来源与模型绑定——测试据此断言
    「哪些调用发生了 / 没有发生」。
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    # -- AgentRunner 端口 ------------------------------------------------------
    async def run(self, task_kind: str, context_package: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(
            {
                "channel": "agent",
                "task_kind": task_kind,
                "commit_id": context_package.get("repository_commit_id"),
            }
        )
        return {"kind": "no_result", "reason": "recorded"}

    def model_binding(self) -> dict[str, Any]:
        return {"profile": "test", "model": "test-model", "api": "openai-completions"}

    # -- chat runner 工厂 ------------------------------------------------------
    def chat_runner(self) -> _RecordingChatRunner:
        return _RecordingChatRunner(self)


class _RecordingChatRunner:
    def __init__(self, recorder: ModelCallRecorder) -> None:
        self._recorder = recorder

    async def stream(self, run: Any, sink: Any) -> str:
        self._recorder.calls.append({"channel": "chat", "model": run.model})
        await sink("记录到的回复")
        return "记录到的回复"


def _bind_recorder(runtime: V2Runtime, recorder: ModelCallRecorder) -> None:
    """把记录器注入全部模型调用入口（agent runner + chat 两个工厂）。"""
    runtime.configure_agent_runner(recorder)
    runtime.chat.runner_factory = recorder.chat_runner
    runtime.chat.bound_runner_factory = lambda _binding: recorder.chat_runner()


def _grant_wire(
    *,
    grant_id: str = "grant_test",
    function: str = "draft",
    calls_used: int = 0,
    call_budget: int = 12,
    job_type: str = "planner",
) -> dict[str, Any]:
    return build_grant(
        grant_id=grant_id,
        source="author_button",
        function=function,  # type: ignore[arg-type]
        analysis_scope=("art_target",),
        connection=ConnectionSnapshot(
            connection_id="test", revision=0, provider="test", model="test-model"
        ),
        call_budget=call_budget,
    ).to_wire() | {"calls_used": calls_used, "_job_type": job_type}


# ---------------------------------------------------------------------------
# 零隐式模型调用（I06）
# ---------------------------------------------------------------------------


def test_no_implicit_model_call_on_reopen_and_save(tmp_path: Path) -> None:
    """执行指导 §4.3 失败测试样板：打开作品 / 手改保存 / 重启零模型调用。"""
    from hastings.application.workflows.manual_edit import ManualEditIntent
    from hastings.domain.repository.ids import ProjectId

    path = str(tmp_path / "zero-call.sqlite")
    recorder = ModelCallRecorder()
    runtime = V2Runtime.sqlite(path)
    _bind_recorder(runtime, recorder)
    client = TestClient(build_v2_app(runtime))
    project = client.post("/api/v2/projects", json={"name": "雪山旅馆"}).json()["project_id"]
    prefix = f"/api/v2/projects/{project}"

    # 打开作品（读取）+ 导入 + 手改保存：旧实现会经 Outbox 建分析 Job。
    assert client.get(prefix + "/manuscript").status_code == 200
    chapter = client.post(
        prefix + "/chapters/import",
        json={"idempotency_key": "import", "title": "一", "text": "她走进旅馆。"},
    ).json()["chapter_id"]
    pid = ProjectId(project)
    asyncio.run(
        runtime.workflow.save(
            ManualEditIntent(
                project_id=pid,
                chapter_id=chapter,
                idempotency_key="edit",
                full_text="她走进旅馆大堂。",
            )
        )
    )
    # 旧自动链（V03-0.4 已切断装配）：事件只走确定性投影，不建分析 Job；
    # 这里直接落库两条 0.2 遗留任务，验证重启后门禁暂停、零调用。
    assert runtime.dispatch_pending(project) >= 1
    asyncio.run(
        runtime.jobs.create(
            "manuscript.analysis",
            {"project_id": project, "commit_id": "cmt_legacy", "chapter_id": chapter},
        )
    )
    asyncio.run(
        runtime.jobs.create(
            "style_observer", {"project_id": project, "context": {"project_id": project}}
        )
    )
    assert recorder.calls == []  # 保存与旧队列落库本身不调用模型

    # 重启：新 runtime + 同一记录器；lifespan 行为（mark_interrupted）。
    restarted = V2Runtime.sqlite(path)
    _bind_recorder(restarted, recorder)
    assert asyncio.run(restarted.chat.mark_interrupted()) == 0
    assert asyncio.run(restarted.jobs.mark_interrupted()) == 0
    # worker_loop 会尝试派发旧队列 → 门禁全部暂停，零调用。
    for job in asyncio.run(restarted.jobs.list_jobs()):
        asyncio.run(restarted._execute_job(job["job_id"]))
    assert recorder.calls == []
    jobs = asyncio.run(restarted.jobs.list_jobs())
    auto = [j for j in jobs if j["job_type"] in ("manuscript.analysis", "style_observer")]
    assert auto, "旧自动链应留下 Job 记录供分类回看"
    assert all(j["state"] in ("waiting", "completed", "terminal_failed") for j in auto), (
        "旧队列不得残留可派发状态"
    )


# ---------------------------------------------------------------------------
# 派发门禁（I05）
# ---------------------------------------------------------------------------


def _runtime_with_recorder(
    tmp_path: Path, name: str = "gate.sqlite"
) -> tuple[V2Runtime, ModelCallRecorder]:
    recorder = ModelCallRecorder()
    runtime = V2Runtime.sqlite(str(tmp_path / name))
    _bind_recorder(runtime, recorder)
    return runtime, recorder


def test_unauthorized_job_refused_dispatch(tmp_path: Path) -> None:
    runtime, recorder = _runtime_with_recorder(tmp_path)
    jid = asyncio.run(runtime.jobs.create("planner", {"project_id": "prj_x"}))
    result = asyncio.run(runtime.execute_agent_job(jid))
    assert result["state"] == "waiting"
    assert result["output"] == {"kind": "no_result", "reason": "historical_paused"}
    assert recorder.calls == []
    events = asyncio.run(runtime.jobs.events(jid))
    paused = [e for e in events if e["type"] == "job.paused"]
    assert paused and paused[-1]["payload"]["details"]["job_type"] == "planner"


def test_grant_function_mismatch_refused(tmp_path: Path) -> None:
    runtime, recorder = _runtime_with_recorder(tmp_path)
    wire = _grant_wire(function="summary")  # 任务要 draft，凭据是 summary
    jid = asyncio.run(runtime.jobs.create("planner", {"project_id": "prj_x", "grant": wire}))
    result = asyncio.run(runtime.execute_agent_job(jid))
    assert result["state"] == "waiting"
    assert result["output"]["reason"] == "not_authorized"
    assert recorder.calls == []
    events = asyncio.run(runtime.jobs.events(jid))
    paused = [e for e in events if e["type"] == "job.paused"]
    assert paused[-1]["payload"]["details"]["error_code"] == "EXECUTION_NOT_AUTHORIZED"


def test_budget_exhausted_refused(tmp_path: Path) -> None:
    runtime, recorder = _runtime_with_recorder(tmp_path)
    wire = _grant_wire(calls_used=12, call_budget=12)
    jid = asyncio.run(runtime.jobs.create("planner", {"project_id": "prj_x", "grant": wire}))
    result = asyncio.run(runtime.execute_agent_job(jid))
    assert result["state"] == "waiting"
    assert result["output"]["reason"] == "not_authorized"
    assert recorder.calls == []
    events = asyncio.run(runtime.jobs.events(jid))
    paused = [e for e in events if e["type"] == "job.paused"]
    assert paused[-1]["payload"]["details"]["error_code"] == "TASK_BUDGET_EXHAUSTED"


def test_unknown_job_type_refused(tmp_path: Path) -> None:
    """未登记功能映射的 Job 类型 fail closed（style_observer 等旧自动链）。"""
    runtime, recorder = _runtime_with_recorder(tmp_path)
    wire = _grant_wire(function="suggestion")
    jid = asyncio.run(runtime.jobs.create("style_observer", {"project_id": "prj_x", "grant": wire}))
    result = asyncio.run(runtime.execute_agent_job(jid))
    assert result["state"] == "waiting"
    assert recorder.calls == []


def test_authorized_job_runs_with_grant(tmp_path: Path) -> None:
    """作者显式启动（按钮）→ 凭据随 Job 落库 → 放行并留下审计记录。"""
    runtime, recorder = _runtime_with_recorder(tmp_path)
    client = TestClient(build_v2_app(runtime))
    project = client.post("/api/v2/projects", json={"name": "授权启动"}).json()["project_id"]
    prefix = f"/api/v2/projects/{project}"
    job = client.post(
        prefix + "/agent-jobs",
        json={"role": "planner", "instruction": "规划人物", "idempotency_key": "plan-1"},
    ).json()
    grant = job["payload"]["grant"]
    assert grant["source"] == "author_button"
    assert grant["function"] == "draft"
    # 连接快照只含非敏感字段（本机解析到的真实连接，不断言具体模型名）
    assert set(grant["connection"]) == {"connection_id", "revision", "provider", "model"}
    assert "key" not in str(grant).lower()  # 凭据与连接快照绝不含 Key
    result = client.post(prefix + f"/agent-jobs/{job['job_id']}/run").json()
    assert result["state"] == "completed"  # no_result 正常终态（合法空结果）
    assert [c["task_kind"] for c in recorder.calls] == ["planner"]


def test_mark_interrupted_waits_for_author_without_redispatch(tmp_path: Path) -> None:
    runtime, recorder = _runtime_with_recorder(tmp_path)
    wire = _grant_wire()
    jid = asyncio.run(runtime.jobs.create("planner", {"project_id": "prj_x", "grant": wire}))
    claimed = asyncio.run(runtime.jobs.claim(jid, "worker-1", now=runtime.clock.now(), ttl=60))
    assert claimed["state"] == "running"
    assert asyncio.run(runtime.jobs.mark_interrupted()) == 1
    interrupted = asyncio.run(runtime.jobs.get(jid))
    assert interrupted["state"] == "waiting"
    assert interrupted["output"] == {"kind": "no_result", "reason": "interrupted"}
    # 重启后再派发：门禁放行（凭据有效）但任务已 waiting，不重复运行。
    result = asyncio.run(runtime.execute_agent_job(jid))
    assert result["state"] == "waiting"
    assert recorder.calls == []


# ---------------------------------------------------------------------------
# pause / mark_interrupted 双后端契约
# ---------------------------------------------------------------------------


def _stores(tmp_path: Path) -> list[Any]:
    return [MemoryJobStore(), SqliteJobStore(str(tmp_path / "jobs.sqlite"))]


@pytest.mark.parametrize("index", [0, 1])
def test_pause_contract_dual_backend(tmp_path: Path, index: int) -> None:
    store = _stores(tmp_path)[index]
    jid = asyncio.run(store.create("planner", {"project_id": "prj_x"}))
    paused = asyncio.run(store.pause(jid, reason="historical_paused", details={"k": "v"}))
    assert paused is not None
    assert paused["state"] == "waiting"
    assert paused["output"] == {"kind": "no_result", "reason": "historical_paused"}
    # 幂等：二次暂停不改变状态、不产生第二个事件
    again = asyncio.run(store.pause(jid, reason="historical_paused"))
    assert again is not None and again["state"] == "waiting"
    if isinstance(store, SqliteJobStore):
        events = asyncio.run(store.events(jid))
        assert len([e for e in events if e["type"] == "job.paused"]) == 1
    # 不存在的任务返回 None
    assert asyncio.run(store.pause("job_missing", reason="x")) is None


def test_mark_interrupted_contract_sqlite(tmp_path: Path) -> None:
    store = SqliteJobStore(str(tmp_path / "jobs.sqlite"))
    from datetime import UTC, datetime

    now = datetime(2026, 10, 17, tzinfo=UTC)
    running = asyncio.run(store.create("planner", {"project_id": "prj_x"}))
    asyncio.run(store.claim(running, "w", now=now, ttl=60))
    queued = asyncio.run(store.create("writer", {"project_id": "prj_x"}))
    assert asyncio.run(store.mark_interrupted()) == 1
    assert (asyncio.run(store.get(running)))["state"] == "waiting"
    assert (asyncio.run(store.get(running)))["output"]["reason"] == "interrupted"
    assert (asyncio.run(store.get(queued)))["state"] == "queued"  # 未执行任务不动
    assert asyncio.run(store.mark_interrupted()) == 0  # 幂等


def test_mark_interrupted_contract_memory() -> None:
    store = MemoryJobStore()
    jid = asyncio.run(store.create("planner", {"project_id": "prj_x"}))
    store._jobs[jid]["state"] = "running"  # 内存后端无 claim：直接置运行态
    assert asyncio.run(store.mark_interrupted()) == 1
    record = asyncio.run(store.get(jid))
    assert record is not None and record["state"] == "waiting"
    assert record["output"]["reason"] == "interrupted"


# ---------------------------------------------------------------------------
# 凭据单元（领域规则）
# ---------------------------------------------------------------------------


def test_execution_grant_consume_and_budget() -> None:
    grant = build_grant(
        grant_id="g1",
        source="author_message",
        source_message_id="msg_1",
        function="draft",
        connection=ConnectionSnapshot("c", 1, "p", "m"),
        call_budget=2,
    )
    assert grant.consume().calls_used == 1
    assert grant.consume().consume().calls_used == 2
    with pytest.raises(Exception) as excinfo:
        grant.consume().consume().consume()
    assert excinfo.value.code == "TASK_BUDGET_EXHAUSTED"


def test_build_grant_requires_source_message_for_message_source() -> None:
    with pytest.raises(Exception) as excinfo:
        build_grant(
            grant_id="g2",
            source="author_message",
            source_message_id=None,
            function="draft",
            connection=ConnectionSnapshot("c", 1, "p", "m"),
        )
    assert excinfo.value.code == "EXECUTION_NOT_AUTHORIZED"


def test_grant_wire_round_trip() -> None:
    grant = build_grant(
        grant_id="g3",
        source="author_button",
        function="suggestion",
        analysis_scope=("art_1", "art_2"),
        connection=ConnectionSnapshot("c", 3, "p", "m"),
    )
    restored = ExecutionGrant.from_wire(grant.to_wire())
    assert restored == grant
