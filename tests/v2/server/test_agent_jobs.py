"""真实 HTTP 边界：持久任务、候选审阅和项目隔离。"""

import asyncio

from fastapi.testclient import TestClient

from hastings.server.v2 import V2Runtime, build_v2_app


class Planner:
    async def run(self, task_kind, context_package):
        return {
            "kind": "story_proposal",
            "base_commit_id": context_package["repository_commit_id"],
            "items": [
                {
                    "artifact_id": context_package["project_id"] + ":detective",
                    "kind": "story.character",
                    "content": {"name": "林探长", "role": "protagonist"},
                }
            ],
        }


def test_planning_job_waits_for_author_and_survives_restart(tmp_path):
    path = str(tmp_path / "v2.sqlite")
    runtime = V2Runtime.sqlite(path)
    runtime.configure_agent_runner(Planner())
    client = TestClient(build_v2_app(runtime))
    project = client.post("/api/v2/projects", json={"name": "新故事"}).json()["project_id"]
    prefix = f"/api/v2/projects/{project}"
    response = client.post(
        prefix + "/agent-jobs",
        json={"role": "planner", "instruction": "规划侦探", "idempotency_key": "plan"},
    )
    assert response.status_code == 200, response.text
    job = response.json()
    jid = job["job_id"]
    result = client.post(prefix + f"/agent-jobs/{jid}/run").json()
    assert result["state"] == "waiting", result
    assert client.get(prefix + "/story").json()["artifacts"] == []
    restarted = TestClient(build_v2_app(V2Runtime.sqlite(path)))
    assert restarted.get(prefix + f"/agent-jobs/{jid}").json()["state"] == "waiting"
    head = restarted.get(prefix + "/story").json()["commit_id"]
    accepted = restarted.post(prefix + f"/agent-jobs/{jid}/accept", json={"expected_head": head})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["commit_id"]
    story = restarted.get(prefix + "/story").json()
    assert story["artifacts"][0]["content"]["name"] == "林探长"
    assert (
        restarted.post(prefix + f"/agent-jobs/{jid}/accept", json={"expected_head": head}).json()
        == accepted.json()
    )
    events = restarted.get(prefix + f"/agent-jobs/{jid}/events?after=1").json()["events"]
    assert events and events[0]["sequence"] == 2
    assert all(e["commit_id"] is None for e in events)
    other = restarted.post("/api/v2/projects", json={"name": "其他项目"}).json()["project_id"]
    assert restarted.post(f"/api/v2/projects/{other}/agent-jobs/{jid}/cancel").status_code == 404


def test_save_event_no_longer_creates_analysis_job(tmp_path):
    """0.3（V03-0.4）：保存事件走确定性投影，不再自动创建分析/风格观察 Job。

    0.2 语义是「Outbox → AnalysisDispatcher → 分析 Job → 风格观察」；
    0.3 切断装配：事件照常发布标记（不积压），模型调度为零（I06）。
    """
    runtime = V2Runtime.sqlite(str(tmp_path / "v2.sqlite"))
    client = TestClient(build_v2_app(runtime))
    project = client.post("/api/v2/projects", json={"name": "编辑"}).json()["project_id"]
    prefix = f"/api/v2/projects/{project}"
    chapter = client.post(
        prefix + "/chapters/import",
        json={"idempotency_key": "import", "title": "一", "text": "他非常缓慢地走进房间。"},
    ).json()["chapter_id"]
    saved = client.post(
        prefix + f"/chapters/{chapter}/save",
        json={"idempotency_key": "edit", "full_text": "他走进房间。"},
    )
    assert saved.status_code == 200, saved.text
    assert runtime.dispatch_pending(project) >= 1  # 事件发布并标记，不积压
    assert runtime.event_projection.report().get("manuscript.chapter.committed", 0) >= 1
    jobs = client.get(prefix + "/agent-jobs").json()["jobs"]
    assert [j for j in jobs if j["job_type"] in ("manuscript.analysis", "style_observer")] == []
    assert asyncio.run(runtime.legacy_auto_report()) == {}


def test_small_context_budget_is_honored_at_http_boundary():
    runtime = V2Runtime.memory()
    client = TestClient(build_v2_app(runtime))
    pid = runtime.create_project("预算").value
    response = client.post(
        f"/api/v2/projects/{pid}/agent-jobs",
        json={
            "role": "planner",
            "idempotency_key": "small",
            "instruction": "规划角色",
            "token_budget": 1000,
        },
    )
    assert response.status_code == 400
    assert client.get(f"/api/v2/projects/{pid}/agent-jobs").json()["jobs"] == []


def test_restart_does_not_replay_auto_style_observation(tmp_path):
    """0.3（V03-0.3/0.4）：旧自动风格观察链重启后不派发、零模型调用。

    0.2 语义是「重启自行发现 Outbox 并完成观察记录」；0.3 切断自动模型
    调度（I06）：旧队列任务被派发门禁标 historical_paused，模型运行记录器
    为零，作者模型资料不因重启复活。
    """
    import time

    from hastings.application.handlers.manuscript_commands import manuscript_command
    from hastings.application.workflows.manual_edit import ManualEditIntent
    from hastings.domain.manuscript.commands import import_chapter
    from hastings.domain.repository.ids import ProjectId

    path = str(tmp_path / "outbox-restart.sqlite")
    initial = V2Runtime.sqlite(path)
    pid = initial.create_project("离线修改")
    initial.handler.execute(
        manuscript_command(
            import_chapter, chapter_id="ch", title="一", text="他非常缓慢地走进房间。"
        ),
        project_id=pid,
        idempotency_key="import",
        summary="导入",
    )
    asyncio.run(
        initial.workflow.save(
            ManualEditIntent(
                project_id=pid, chapter_id="ch", idempotency_key="edit", full_text="他走进房间。"
            )
        )
    )
    # 0.2 遗留的旧自动队列（直接落库，模拟升级前数据）：重启后不得派发。
    asyncio.run(
        initial.jobs.create(
            "manuscript.analysis",
            {"project_id": pid.value, "commit_id": initial.head(pid).value, "chapter_id": "ch"},
        )
    )
    asyncio.run(
        initial.jobs.create(
            "style_observer",
            {"project_id": pid.value, "context": {"project_id": pid.value}},
        )
    )
    restarted = V2Runtime.sqlite(path)
    observer_calls: list[str] = []

    class Observer:
        async def run(self, role, ctx):
            observer_calls.append(role)
            return {"kind": "no_result", "reason": "recorded"}

    restarted.configure_agent_runner(Observer())
    with TestClient(build_v2_app(restarted)) as client:
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            jobs = client.get(f"/api/v2/projects/{pid.value}/agent-jobs").json()["jobs"]
            auto = [j for j in jobs if j["job_type"] in ("manuscript.analysis", "style_observer")]
            pending = [j for j in auto if j["state"] in ("queued", "retryable_failed")]
            paused = [
                j
                for j in auto
                if j["state"] == "waiting" and j["output"]["reason"] == "historical_paused"
            ]
            if paused and not pending:
                break
            time.sleep(0.03)
        else:
            raise AssertionError("旧自动队列应被派发门禁暂停（historical_paused）")
    assert observer_calls == []
    # 入口已移除，但数据可读：用领域查询确认「重启没有凭空长出偏好」。
    from hastings.application.queries.author_profile import AuthorProfileQueries

    head = restarted.head(pid)
    assert not AuthorProfileQueries(restarted.service).preferences(pid, head)


def test_oversized_edit_no_longer_spawns_auto_analysis(tmp_path):
    """0.3（V03-0.4）：大改动保存只落正文，不再派生自动分析任务。

    0.2 的失败模式（分析上下文超预算 → terminal_failed）随自动链切断而
    消失：保存成功、零自动 Job、零模型调用；正文与历史不受影响。
    """
    from hastings.application.handlers.manuscript_commands import manuscript_command
    from hastings.application.workflows.manual_edit import ManualEditIntent
    from hastings.domain.manuscript.commands import import_chapter

    runtime = V2Runtime.sqlite(str(tmp_path / "large.sqlite"))
    pid = runtime.create_project("大改动")
    runtime.handler.execute(
        manuscript_command(import_chapter, chapter_id="ch", title="一", text="旧稿。"),
        project_id=pid,
        idempotency_key="import",
        summary="导入",
    )
    asyncio.run(
        runtime.workflow.save(
            ManualEditIntent(
                project_id=pid, chapter_id="ch", idempotency_key="large", full_text="长段落" * 10000
            )
        )
    )
    assert runtime.dispatch_pending(pid.value) >= 1
    jobs = asyncio.run(runtime.jobs.list_jobs(pid.value))
    assert [j for j in jobs if j["job_type"] in ("manuscript.analysis", "style_observer")] == []
    assert asyncio.run(runtime.legacy_auto_report()) == {}


def test_completed_analysis_replays_metadata_after_restart(tmp_path):
    from hastings.application.handlers.manuscript_commands import manuscript_command
    from hastings.domain.manuscript.commands import import_chapter
    from hastings.domain.repository.ids import ProjectId

    path = str(tmp_path / "analysis-recovery.sqlite")
    runtime = V2Runtime.sqlite(path)
    pid = runtime.create_project("恢复审计")
    runtime.handler.execute(
        manuscript_command(import_chapter, chapter_id="ch", title="一", text="怀表停了。"),
        project_id=pid,
        idempotency_key="import",
        summary="导入",
    )

    class Critic:
        async def run(self, role, ctx):
            item = next(i for i in ctx["selected_inputs"] if i["kind"] == "manuscript.text_unit")
            return {
                "kind": "finding_set",
                "base_commit_id": ctx["repository_commit_id"],
                "findings": [
                    {
                        "artifact_ids": [item["artifact_id"]],
                        "quote": "怀表停了。",
                        "reason": "复核停表原因",
                    }
                ],
            }

    runtime.configure_agent_runner(Critic())

    async def interrupted():
        from hastings.application.execution import ConnectionSnapshot, build_grant

        catalog = runtime.queries.catalog(pid, runtime.head(pid))
        # 0.3（V03-0.3）：审计 Job 由作者显式启动，须带凭据才能过派发门禁。
        grant = build_grant(
            grant_id="grant_audit-recovery",
            source="author_button",
            function="suggestion",
            analysis_scope=(catalog.units_of("ch")[0].artifact_id,),
            connection=ConnectionSnapshot(
                connection_id="test", revision=0, provider="test", model="test-model"
            ),
        ).to_wire()
        jid = await runtime.agent_workflows.audit(
            pid,
            runtime.head(pid).value,
            (catalog.units_of("ch")[0].artifact_id,),
            "audit",
            grant=grant,
        )
        await runtime.worker.execute(jid)
        return jid

    jid = asyncio.run(interrupted())
    restarted = V2Runtime.sqlite(path)
    asyncio.run(restarted._execute_job(jid))
    job = asyncio.run(restarted.jobs.get(jid))
    assert job["analysis"]["commit_id"]
    before = restarted.head(ProjectId(pid.value))
    asyncio.run(restarted._execute_job(jid))
    assert restarted.head(pid) == before
