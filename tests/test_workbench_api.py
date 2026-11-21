import hashlib
import json
from pathlib import Path

import pytest
from eval.benchmark import evaluate as grader
from eval.benchmark import workbench_api, workbench_runner
from eval.benchmark.workbench_history import BASELINE_ID, trace_view
from eval.benchmark.workbench_store import Store
from fastapi.testclient import TestClient

BENCHMARK = Path(__file__).resolve().parents[1] / "eval" / "benchmark"
INTENT = {"X-Workbench-Intent": "local"}
SELECTION = {"library_id": "writing", "task_id": "T01", "revision": 1}


def test_publication_failure_keeps_private_isolation_and_allows_first_capture_repair(
    tmp_path, monkeypatch
):
    book = workbench_api.Workbench(tmp_path, None, {"writing": BENCHMARK / "tasks"}, None)
    path = tmp_path / "runs" / "trial" / "summary.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"run": {"task": SELECTION, "execution_status": "running"}}), encoding="utf-8"
    )
    evidence = {
        "task_id": "T01",
        "task_revision": 1,
        "preflight": {"valid": True},
        "run": {"turn_id": "original-turn", "status": "completed"},
    }

    def execute(*args, **kwargs):
        private = path.parent / "isolation" / "raw.jsonl"
        private.parent.mkdir()
        private.write_text("private model log", encoding="utf-8")
        return {"evidence": evidence, "started_at": "2026-11-21T11:00:00+08:00"}

    def fail_publication(*args):
        raise OSError("private filesystem details")

    monkeypatch.setattr(workbench_runner, "execute", execute)
    with monkeypatch.context() as patch:
        patch.setattr(Store, "_publish", staticmethod(fail_publication))
        book.execute("trial", book.task(workbench_api.TaskRef(**SELECTION)))

    with TestClient(workbench_api.create_app(tmp_path), base_url="http://127.0.0.1") as client:
        detail = client.get("/api/runs/trial").json()
        assert detail["run"]["turn_id"] == "original-turn"
        assert detail["report"] is None
        assert "private" not in json.dumps(detail)
        assert client.get("/api/runs/trial/evidence").status_code == 404
    assert (path.parent / "isolation" / "raw.jsonl").is_file()
    with pytest.raises(ValueError, match="轮次 ID"):
        book.repair("trial", {**evidence, "run": {"turn_id": "different-turn"}})
    with pytest.raises(ValueError, match="轮次 ID"):
        book.repair("trial", {**evidence, "run": "original-turn"})
    repaired = book.repair("trial", evidence)
    assert book.detail("trial")["evidence_version"] == repaired["evidence_version"]


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_final_summary_write_failure_recovers_published_terminal_run(
    tmp_path, monkeypatch, cleanup_fails
):
    book = workbench_api.Workbench(tmp_path, None, {"writing": BENCHMARK / "tasks"}, None)
    path = tmp_path / "runs" / "trial" / "summary.json"
    path.parent.mkdir(parents=True)
    summary = {
        "run": {"run_id": "trial", "kind": "task", "task": SELECTION, "execution_status": "running"}
    }
    workbench_api._write(path, summary)
    evidence = {
        "task_id": "T01",
        "task_revision": 1,
        "preflight": {"valid": True},
        "run": {"turn_id": "original-turn", "status": "completed", "model": "local-fixture"},
    }
    milestone = {"type": "evidence", "summary": "Capture saved."}
    trace = [{"seq": 0, "type": "turn_started", "at": "2026-11-21T11:00:00+08:00"}]
    private = path.parent / "isolation" / "raw.jsonl"

    def execute(*args, **kwargs):
        private.parent.mkdir()
        private.write_text("private model log", encoding="utf-8")
        return {
            "evidence": evidence,
            "started_at": "2026-11-21T11:00:00+08:00",
            "milestones": [milestone],
            "trace": trace,
        }

    def fail_summary_write(target, data):
        assert target == path
        assert book.store().current("trial") is not None
        raise OSError("simulated final summary write failure")

    monkeypatch.setattr(workbench_runner, "execute", execute)
    monkeypatch.setattr(workbench_api, "_write", fail_summary_write)
    if cleanup_fails:

        def fail_cleanup(target, **kwargs):
            assert target == private.parent
            assert book.detail("trial")["run"]["execution_status"] == "running"
            raise PermissionError("simulated cleanup failure")

        monkeypatch.setattr(workbench_api.shutil, "rmtree", fail_cleanup)
    book.active = "trial"
    try:
        book.execute("trial", book.task(workbench_api.TaskRef(**SELECTION)))
    except OSError as exc:
        assert str(exc) == "simulated final summary write failure"
    assert book.active is None
    assert json.loads(path.read_bytes()) == summary
    assert private.is_file() is cleanup_fails

    app = workbench_api.create_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        detail = client.get("/api/runs/trial").json()
        assert detail["run"]["execution_status"] == "completed"
        assert detail["run"]["turn_id"] == "original-turn"
        assert detail["run"]["model"] == "local-fixture"
        assert detail["milestones"][0] == milestone
        warnings = [item["summary"] for item in detail["milestones"] if item["type"] == "warning"]
        assert warnings == (["临时隔离目录未能清理；不要导出该目录。"] if cleanup_fails else [])
        assert "private model log" not in json.dumps(detail)
        assert client.get("/api/runs/trial/trace").json() == trace_view(trace)
        (row,) = client.get("/api/runs").json()["items"]
        assert row["run_id"] == "trial"
        assert row["execution_status"] == "completed"
        assert row["turn_id"] == detail["run"]["turn_id"]
        assert row["verdict"] == detail["run"]["verdict"]

        offline = workbench_api.Workbench(tmp_path, None, {"writing": BENCHMARK / "tasks"}, None)
        with pytest.raises(ValueError, match="轮次 ID"):
            offline.repair("trial", {**evidence, "run": {"turn_id": "different-turn"}})
        replacement = {
            **evidence,
            "run": {"turn_id": "original-turn", "status": "failed"},
            "assistant_reply": "Corrected capture.",
        }
        repaired = offline.repair("trial", replacement)
        updated = client.get("/api/runs/trial").json()
        assert updated["evidence_version"] == repaired["evidence_version"]
        assert updated["run"]["execution_status"] == "completed"
        assert updated["run"]["model"] == "local-fixture"


@pytest.fixture
def legacy_archive(tmp_path):
    directory = tmp_path / "02-completed"
    directory.mkdir()

    def write(name, data):
        (directory / name).write_text(json.dumps(data), encoding="utf-8")

    manifest = tmp_path / "index.json"
    manifest.write_text(
        json.dumps(
            {
                "archive_version": 1,
                "runs": [
                    {
                        "run_id": BASELINE_ID,
                        "turn_id": BASELINE_ID,
                        "kind": "task",
                        "task": SELECTION,
                        "execution_status": "completed",
                        "started_at": "2026-11-21T11:00:00+08:00",
                        "verdict": "pass",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    text = "A saved structure."
    original = {
        "task_id": "T01",
        "task_revision": 1,
        "preflight": {"valid": True},
        "run": {"turn_id": BASELINE_ID, "status": "completed"},
        "workspace_before": {},
        "workspace_after": {},
        "modules_after": {"library_module_ids": [], "binding_ids": []},
    }
    current = {
        **original,
        "workspace_after": {"STRUCTURE.md": hashlib.sha256(text.encode()).hexdigest()},
        "structure_text": text,
        "assistant_reply": "Saved it.",
    }
    task = grader.read_json(grader.TASK_PATH)
    checks = {key: {"verdict": "pass", "reason": "Checked."} for key in task["llm_graders"]}
    write("evidence-original.json", original)
    write("evidence.json", current)
    write(
        "human-bound.json", {"evidence_sha256": grader.evidence_sha256(current), "checks": checks}
    )
    write("report-reviewed.json", grader.evaluate(task, current, checks, None))
    state = tmp_path / "state"
    app = workbench_api.create_app(state, history=manifest)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, state, write, current


def test_legacy_verdict_drops_stale_review_when_capture_changes(legacy_archive):
    client, _, write, current = legacy_archive
    url = f"/api/runs/{BASELINE_ID}"
    checked = client.get(url).json()
    assert checked["report"]["verdict"] == "pass"
    assert checked["review_status"] == "reviewed"
    changed = {**current, "assistant_reply": "A different capture."}
    write("evidence.json", changed)
    detail = client.get(url).json()
    assert detail["evidence_sha256"] == grader.evidence_sha256(changed)
    assert detail["report"]["verdict"] == "inconclusive"
    assert detail["run"]["verdict"] != "pass"
    assert detail["review_status"] == "pending"
    assert client.get("/api/runs").json()["items"][0]["verdict"] != "pass"


@pytest.mark.parametrize("damage", ["truncated", "unreadable", "malformed"])
def test_runs_list_isolates_bad_legacy_evidence(legacy_archive, tmp_path, monkeypatch, damage):
    client, _, write, current = legacy_archive
    manifest = tmp_path / "index.json"
    history = json.loads(manifest.read_bytes())
    history["runs"].append(
        {
            "run_id": "other",
            "kind": "regression",
            "execution_status": "completed",
            "started_at": "2026-11-21T12:00:00+08:00",
            "verdict": "pass",
        }
    )
    manifest.write_text(json.dumps(history), encoding="utf-8")
    before = {row["run_id"]: row for row in client.get("/api/runs").json()["items"]}
    assert before[BASELINE_ID]["verdict"] == "pass"
    evidence = tmp_path / "02-completed" / "evidence.json"
    if damage == "truncated":
        evidence.write_text("{", encoding="utf-8")
    elif damage == "malformed":
        write("evidence.json", {**current, "run": None})
    else:
        read_bytes = Path.read_bytes

        def deny_evidence(path):
            if path == evidence:
                raise PermissionError("private attachment path")
            return read_bytes(path)

        monkeypatch.setattr(Path, "read_bytes", deny_evidence)

    response = client.get("/api/runs")
    assert response.status_code == 200
    after = {row["run_id"]: row for row in response.json()["items"]}
    assert after.keys() == before.keys()
    assert after["other"] == before["other"]
    assert after[BASELINE_ID]["execution_status"] == "completed"
    assert after[BASELINE_ID]["verdict"] is None
    assert after[BASELINE_ID].get("review_status") != "reviewed"


def test_failed_legacy_regrade_does_not_publish_original_as_current(legacy_archive):
    client, state, write, current = legacy_archive
    url = f"/api/runs/{BASELINE_ID}"
    write("evidence.json", {**current, "assistant_reply": "Stale bound review."})
    before = client.get(url).json()
    response = client.post(
        f"{url}/regrade", json={"evidence_version": "legacy-original"}, headers=INTENT
    )
    assert response.status_code == 409
    assert client.get(url).json() == before
    assert Store(state / "revisions").current(BASELINE_ID) is None


def test_malformed_legacy_review_is_unverified_and_cannot_be_imported(legacy_archive, tmp_path):
    client, state, _, _ = legacy_archive
    url = f"/api/runs/{BASELINE_ID}"
    (tmp_path / "02-completed" / "human-bound.json").write_text("{", encoding="utf-8")
    assert client.get(url).json()["review_status"] == "pending"
    assert client.get("/api/runs").json()["items"][0]["verdict"] == "inconclusive"
    response = client.post(
        f"{url}/regrade", json={"evidence_version": "legacy-original"}, headers=INTENT
    )
    assert response.status_code == 409
    assert Store(state / "revisions").current(BASELINE_ID) is None


def test_legacy_regrade_publication_failure_preserves_prior_state(legacy_archive, monkeypatch):
    client, state, _, _ = legacy_archive
    url = f"/api/runs/{BASELINE_ID}"
    before = client.get(url).json()
    publish = Store._publish

    def fail_regrade(path, packet, record):
        if packet["report_version"] == "r-000000000003":
            raise OSError("private filesystem details")
        return publish(path, packet, record)

    monkeypatch.setattr(Store, "_publish", staticmethod(fail_regrade))
    response = client.post(
        f"{url}/regrade", json={"evidence_version": "legacy-original"}, headers=INTENT
    )
    assert response.status_code == 409
    assert "private filesystem details" not in response.text
    assert Store(state / "revisions").current(BASELINE_ID) is None
    assert client.get(url).json() == before


def test_historical_report_metadata_is_for_its_bound_capture(legacy_archive):
    client, _, _, _ = legacy_archive
    url = f"/api/runs/{BASELINE_ID}"
    response = client.post(
        f"{url}/regrade", json={"evidence_version": "legacy-original"}, headers=INTENT
    )
    assert response.status_code == 200
    current = response.json()
    assert current["report"]["verdict"] == "pass"
    assert current["review_status"] == "reviewed"
    old = client.get(url, params={"report_version": current["report_versions"][-1]}).json()
    assert old["evidence_sha256"] != current["evidence_sha256"]
    assert old["report"]["verdict"] != "pass"
    assert old["review_status"] == "pending"
    assert not old["review_inputs"]
    assert "structure_text" not in old["available_evidence"]
    assert "assistant_reply" not in old["available_evidence"]


@pytest.fixture
def client(tmp_path):
    manifest = tmp_path / "index.json"
    task = {"library_id": "writing", "task_id": "T01", "revision": 1}
    manifest.write_text(
        json.dumps(
            {
                "archive_version": 1,
                "runs": [
                    {
                        "run_id": "last",
                        "kind": "regression",
                        "execution_status": "completed",
                        "started_at": "2026-11-21T12:00:00+08:00",
                    },
                    {
                        "run_id": "base",
                        "kind": "task",
                        "execution_status": "completed",
                        "started_at": "2026-11-21T11:00:00+08:00",
                        "task": task,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    app = workbench_api.create_app(tmp_path / "state", history=manifest)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


def test_queries_page_and_dont_invent_missing_archival_evidence(client):
    tasks = client.get("/api/tasks?query=t01").json()
    assert [row["task_id"] for row in tasks["items"]] == ["T01"]
    detail = client.get("/api/tasks/writing/T01/1").json()
    assert detail["task"]["scenario"]["revision"] == 1
    assert detail["runnable"] is False
    runs = client.get("/api/runs?limit=1").json()
    assert [row["run_id"] for row in runs["items"]] == ["base"]
    assert runs["has_more"] is True
    assert [row["run_id"] for row in client.get("/api/runs?offset=1").json()["items"]] == ["last"]
    assert (
        len(client.get("/api/runs?library_id=writing&task_id=T01&revision=1").json()["items"]) == 1
    )
    assert client.get("/api/runs/base").json()["report"] is None
    assert client.get("/api/runs/base/evidence?version=../private").status_code == 404
    assert client.get("/api/runs/base/trace").json()["events"] == []


def test_unsafe_origins_and_missing_runner_cannot_start(client):
    task = {"library_id": "writing", "task_id": "T01", "revision": 1}
    assert client.post("/api/runs", json=task).status_code == 403
    assert (
        client.post(
            "/api/runs", json=task, headers={**INTENT, "Origin": "https://example.org"}
        ).status_code
        == 403
    )
    assert client.get("/api/tasks", headers={"Host": "untrusted.test"}).status_code == 400
    preflight = client.post("/api/preflight", json=task, headers=INTENT)
    assert preflight.json()["valid"] is False
    assert client.post("/api/runs", json=task, headers=INTENT).status_code == 409
    assert not client.get("/api/runs?timing=not_started").json()["items"]


def test_trace_links_same_turn_retry_and_omits_unapproved_fields():
    def event(seq, kind, **fields):
        return {"seq": seq, "type": kind, "at": f"2026-11-21T11:00:0{seq}+08:00", **fields}

    result = trace_view(
        [
            event(0, "turn_started"),
            event(1, "tool_started", tool="write_structure", args={"sensitive": True}),
            event(2, "tool_finished", tool="write_structure", ok=False, code="FORMAT_REQUIRED"),
            event(3, "tool_started", tool="write_structure"),
            event(4, "tool_finished", tool="write_structure", ok=True),
        ]
    )
    first, second = [item for item in result["events"] if item["kind"] == "tool_call"]
    assert first["status"] == "failed"
    assert second["status"] == "ok"
    assert second["retry_of_span_id"] == first["span_id"]
    assert "args" not in result["details"]["1"]
