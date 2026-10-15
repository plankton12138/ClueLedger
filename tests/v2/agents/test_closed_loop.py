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
    observed = run(
        "style_observer",
        chapter_id=f"{project}:draft",
        before_commit_id=accepted["commit_id"],
        base_commit_id=edit["commit_id"],
    )
    assert observed["state"] == "completed"
    prefs = client.get(prefix + "/author-model/preferences").json()["preferences"]
    assert prefs and all(p["status"] == "candidate" for p in prefs)

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
    post(f"/changes/{cs}/analyze", {"idempotency_key": "impact"})
    post(f"/changes/{cs}/plan", {"idempotency_key": "revision-plan"})
    detail = client.get(prefix + f"/changes/{cs}").json()
    post(
        f"/changes/{cs}/approve",
        {"idempotency_key": "approve", "expected_head": detail["head_commit_id"]},
    )
    execution = post(f"/changes/{cs}/execute-agent", {"idempotency_key": "repair"})
    assert execution["status"] == "executed"
    patch = post(f"/changes/{cs}/review", {"idempotency_key": "review"})
    assert any(h["artifact_id"] in units for h in patch["hunks"])
    selected = [h["hunk_id"] for h in patch["hunks"] if h["artifact_id"] != units[-1]]
    merged = post(
        f"/changes/{cs}/merge",
        {"idempotency_key": "merge", "expected_head": story()["commit_id"], "selected": selected},
    )
    audited = run("auditor")
    assert audited["state"] == "completed" and audited["output"]["findings"]
    restored = TestClient(build_v2_app(V2Runtime.sqlite(path)))
    assert restored.get(prefix + "/manuscript").json() == client.get(prefix + "/manuscript").json()
    assert restored.get(prefix + f"/agent-jobs/{planned['job_id']}").json()["state"] == "completed"
    assert any(c["role"] == "reviser" and c["provider_calls"] == 2 for c in scenario.calls)
    transcript = {
        "project_id": project,
        "trace": trace,
        "worker_calls": scenario.calls,
        "merge": merged,
        "checks": {
            "new_project": True,
            "author_review": True,
            "style_candidate": True,
            "out_of_scope_corrected_once": True,
            "partial_merge": True,
            "localized_audit": True,
            "restart": True,
        },
    }
    if os.environ.get("PHASE16_TRANSCRIPT"):
        output = Path(os.environ["PHASE16_TRANSCRIPT"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")
