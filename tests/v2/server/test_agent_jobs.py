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


def test_outbox_analysis_is_persistent_and_uses_actual_diff(tmp_path):
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
    runtime.dispatch_pending(project)
    asyncio.run(runtime.prepare_analysis_jobs())
    jobs = client.get(prefix + "/agent-jobs").json()["jobs"]
    observer = next(j for j in jobs if j["job_type"] == "style_observer")
    edits = observer["payload"]["context"]["evidence"]["edits"]
    assert edits[0]["before"] == "他非常缓慢地走进房间。"
    assert edits[0]["after"] == "他走进房间。"


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


def test_service_start_discovers_unpublished_edit_without_client_wake(tmp_path):
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
    restarted = V2Runtime.sqlite(path)

    class Observer:
        async def run(self, role, ctx):
            return {
                "kind": "observation_set",
                "base_commit_id": ctx["repository_commit_id"],
                "observations": [
                    {
                        "unit_id": e["unit_id"],
                        "category": "style",
                        "statement": "精简",
                        "confidence": 0.95,
                    }
                    for e in ctx["evidence"]["edits"]
                ],
            }

    restarted.configure_agent_runner(Observer())
    with TestClient(build_v2_app(restarted)) as client:
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            jobs = client.get(f"/api/v2/projects/{pid.value}/agent-jobs").json()["jobs"]
            if any(j["job_type"] == "style_observer" and j.get("analysis") for j in jobs):
                break
            time.sleep(0.03)
        else:
            raise AssertionError("重启应自行发现 Outbox 并完成观察记录")
        assert restarted.author_queries.preferences(ProjectId(pid.value), restarted.head(pid))


def test_oversized_edit_analysis_fails_visibly_without_stopping_dispatch(tmp_path):
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
    runtime.dispatch_pending(pid.value)
    asyncio.run(runtime.prepare_analysis_jobs())
    jobs = asyncio.run(runtime.jobs.list_jobs(pid.value))
    failed = next(j for j in jobs if j["job_type"] == "manuscript.analysis")
    assert failed["state"] == "terminal_failed"
    assert failed["output"]["category"] == "violation"
    assert "预算" in failed["output"]["reason"]


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
        catalog = runtime.queries.catalog(pid, runtime.head(pid))
        jid = await runtime.agent_workflows.audit(
            pid, runtime.head(pid).value, (catalog.units_of("ch")[0].artifact_id,), "audit"
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
