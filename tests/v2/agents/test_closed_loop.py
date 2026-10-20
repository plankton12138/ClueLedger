"""从空项目开始，不用预置 Story 对象，完整经过 API、Tau 和仓库。"""

import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

from hastings.domain.repository.ids import ProjectId
from hastings.server.v2 import V2Runtime, build_v2_app

from .scenario import ScenarioRunner


def test_v2_new_project_closed_loop_and_transcript(tmp_path):
    path = str(tmp_path / "closed-loop.sqlite")
    runtime = V2Runtime.sqlite(path)
    scenario = ScenarioRunner(runtime.service, violate_repair_once=True)
    runtime.configure_agent_runner(scenario)
    client = TestClient(build_v2_app(runtime))
    trace = []
    project = client.post("/api/v2/projects", json={"name": "怀表谜案"}).json()["project_id"]
    prefix = f"/api/v2/projects/{project}"

    def post(route, body=None):
        response = client.post(prefix + route, json=body or {})
        assert response.status_code == 200, response.text
        data = response.json()
        trace.append({"route": route, "response": data})
        return data

    def story():
        return client.get(prefix + "/story").json()

    def accept(jid):
        return post(f"/agent-jobs/{jid}/accept", {"expected_head": story()["commit_id"]})

    def run(role, **fields):
        job = post(
            "/agent-jobs",
            {"role": role, "instruction": "怀表谜案", "idempotency_key": role, **fields},
        )
        return post(f"/agent-jobs/{job['job_id']}/run")

    planned = run("planner")
    assert story()["artifacts"] == []
    accept(planned["job_id"])
    assert any(a["kind"] == "narrative.beat" for a in story()["artifacts"])
    drafted = run("writer", target_ids=[f"{project}:beat"], chapter_id=f"{project}:draft")
    draft_ctx = drafted["payload"]["context"]
    assert draft_ctx["evidence"]["beat_states"][0]["enter"]["at_beat_id"] == f"{project}:beat"
    assert any(i["artifact_id"] == f"{project}:scene" for i in draft_ctx["selected_inputs"])
    accepted = accept(drafted["job_id"])
    manuscript = client.get(prefix + "/manuscript").json()
    chapter = manuscript["chapters"][0]
    units = [u["unit_id"] for u in chapter["units"]]
    # 接受 Writer 时同事务建立 Beat → TextUnit 渲染血缘，后续影响才到得了正文。
    snapshot = runtime.service.get_snapshot(ProjectId(project), runtime.head(ProjectId(project)))
    assert any(r.relation_type == "renders" and r.target.value in units for r in snapshot.relations)
    edit = post(
        f"/chapters/{project}:draft/save",
        {
            "idempotency_key": "manual-edit",
            "full_text": "林岚递出怀表。\n\n他仍然信任这位协助者。",
            "expected_chapter_base": chapter["chapter_base"],
        },
    )
    # 0.3 排除项（执行指导 §2.3）：风格观察属旧作者模型自动链，API 不再签发
    # 启动凭据——不能绕过 UI 从旧入口创建此类 Job（计划 §8.3）。
    refused = client.post(
        prefix + "/agent-jobs",
        json={
            "role": "style_observer",
            "instruction": "怀表谜案",
            "idempotency_key": "style_observer",
            "chapter_id": f"{project}:draft",
            "before_commit_id": accepted["commit_id"],
            "base_commit_id": edit["commit_id"],
        },
    )
    assert refused.status_code == 400, refused.text
    assert refused.json()["error"]["code"] == "EXECUTION_NOT_AUTHORIZED"

    # V03-8.6：变更集逐章审批流与独立审计已退役（HTTP 410）。这里只验证
    # 变更集本身仍可打开与只读回看；启动类动作由 test_legacy_entry_guard 覆盖。
    current = story()
    helper = next(a for a in current["artifacts"] if a["artifact_id"].endswith(":helper"))
    cs = post(
        "/changes",
        {
            "idempotency_key": "open",
            "intent": "林岚从协助者改为操纵者",
            "base_commit_id": current["commit_id"],
            "seed_ids": [helper["artifact_id"]],
            "seed_patches": {helper["artifact_id"]: {**helper["content"], "role": "antagonist"}},
        },
    )["change_set_id"]
    detail = client.get(prefix + f"/changes/{cs}")
    assert detail.status_code == 200 and detail.json()["change_set"]["change_set_id"] == cs

    restored = TestClient(build_v2_app(V2Runtime.sqlite(path)))
    assert restored.get(prefix + "/manuscript").json() == client.get(prefix + "/manuscript").json()
    assert restored.get(prefix + f"/agent-jobs/{planned['job_id']}").json()["state"] == "completed"
    transcript = {
        "project_id": project,
        "trace": trace,
        "worker_calls": scenario.calls,
        "checks": {
            "new_project": True,
            "author_review": True,
            "style_observer_refused": True,
            "change_workflow_retired": True,
            "restart": True,
        },
    }
    if os.environ.get("PHASE16_TRANSCRIPT"):
        output = Path(os.environ["PHASE16_TRANSCRIPT"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")
