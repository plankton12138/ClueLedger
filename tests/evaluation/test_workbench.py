"""评测工作台本机接口（不调模型）。"""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
from evaluation.harness.checks import AttemptGrade, CheckResult
from evaluation.harness.report import build_report, gate_decision, write_report
from evaluation.harness.runtime import AttemptResult
from evaluation.workbench.server import make_handler


def _seed_report(run_dir: Path) -> None:
    results = [
        AttemptResult(
            case_id="revise_ch1_ending_farm_tonight",
            attempt=1,
            status="completed",
            grade=AttemptGrade(
                outcome="pending_review",
                checks=(
                    CheckResult(
                        "committed_via_candidate",
                        "must",
                        "fail",
                        "未找到回执",
                        evidence=[
                            {"type": "missing", "expected": "候选提交回执", "turn_id": "t_1"},
                            {"type": "span", "turn_id": "t_1", "span_id": "tool:1"},
                        ],
                    ),
                    CheckResult("farm_tonight", "human", "pending_review", "是否当晚去农场？"),
                ),
            ),
            evidence=None,
            stages=[
                {"id": "scope", "verdict": "pass", "span_id": "tool:0"},
                {"id": "candidate", "verdict": "fail", "span_id": "tool:1"},
                {"id": "commit", "verdict": "fail"},
            ],
            first_failed_stage="candidate",
        )
    ]
    report = build_report(results)
    report["gate"] = gate_decision(report)
    report["identity"] = {"git_commit": "abc1234", "budget_used": 2}
    report["mode"] = "smoke"
    write_report(run_dir, report)


def _seed_evidence(run_dir: Path) -> None:
    attempt = run_dir / "attempts" / "revise_ch1_ending_farm_tonight--1"
    evidence = attempt / "evidence"
    work = attempt / "work" / "正文"
    work.mkdir(parents=True)
    evidence.mkdir(parents=True)
    (evidence / "traces").mkdir()
    (evidence / "inputs").mkdir()
    text = "前文\n\n天黑得比钟点早。\n\n霍布斯说他今年挖了五个坑，账上只有三个名。\n"
    (work / "0001 拔牙人进镇.md").write_text(text, encoding="utf-8")
    (evidence / "before.json").write_text(
        json.dumps({"正文/0001 拔牙人进镇.md": text}, ensure_ascii=False),
        encoding="utf-8",
    )
    (evidence / "diff.json").write_text(
        json.dumps(
            {
                "changed": [],
                "unchanged": ["正文/0001 拔牙人进镇.md"],
                "added": [],
                "removed": [],
            }
        ),
        encoding="utf-8",
    )
    (attempt / "meta.json").write_text(
        json.dumps(
            {
                "case_id": "revise_ch1_ending_farm_tonight",
                "attempt": 1,
                "turns": [
                    {
                        "turn_id": "t_1",
                        "author": "改成当晚去农场",
                        "focus": "正文/0001 拔牙人进镇.md",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (evidence / "events.jsonl").write_text(
        json.dumps(
            {
                "event_id": "e1",
                "type": "tool_finished",
                "summary": "失败",
                "input_detail": "inputs/op1.json",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (evidence / "traces" / "t_1.json").write_text(
        json.dumps(
            {
                "turn": {
                    "turn_id": "t_1",
                    "seq": 1,
                    "text": "改成当晚去农场",
                    "started_at": "2026-11-15T00:00:00+08:00",
                    "finished_at": "2026-11-15T00:00:10+08:00",
                    "duration_ms": 10000,
                    "status": "completed",
                    "trace_available": True,
                },
                "events": [
                    {
                        "span_id": "tool:1",
                        "parent_span_id": None,
                        "kind": "tool_call",
                        "name": "ask_author",
                        "start_offset_ms": 100,
                        "duration_ms": 50,
                        "point": False,
                        "started_at": "2026-11-15T00:00:00+08:00",
                        "finished_at": "2026-11-15T00:00:01+08:00",
                        "status": "failed",
                        "summary": "询问作者",
                        "error_code": None,
                        "start_event_id": "e1",
                        "finish_event_id": "e1",
                        "usage": None,
                        "retry_of_span_id": None,
                    }
                ],
                "range_ms": 10000,
                "cursor": "",
                "usage": {"input": 1, "output": 1, "partial": False},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (evidence / "inputs" / "op1.json").write_text(json.dumps({"messages": []}), encoding="utf-8")


def _serve(runs_root: Path) -> tuple[ThreadingHTTPServer, str]:
    handler = make_handler(runs_root)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    host, port = server.server_address[0], int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://{host}:{port}"


def test_workbench_lists_runs_and_opens(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    run_a = runs_root / "smoke-a"
    run_b = runs_root / "smoke-b"
    _seed_report(run_a)
    _seed_report(run_b)
    server, base = _serve(runs_root)
    try:
        with httpx.Client(base_url=base, timeout=5.0, trust_env=False) as client:
            listed = client.get("/api/runs").json()
            assert {item["id"] for item in listed["runs"]} == {"smoke-a", "smoke-b"}
            report = client.get("/api/runs/smoke-a/report").json()
            assert report["summary"]["pending_review"] == 1
            page = client.get("/")
            assert page.status_code == 200
            assert "评测工作台" in page.text
    finally:
        server.shutdown()
        server.server_close()


def test_workbench_review_and_score(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "demo"
    _seed_report(run_dir)
    server, base = _serve(runs_root)
    try:
        with httpx.Client(base_url=base, timeout=5.0, trust_env=False) as client:
            bad = client.put(
                "/api/runs/demo/reviews",
                json={
                    "reviews": [
                        {
                            "case_id": "revise_ch1_ending_farm_tonight",
                            "attempt": 1,
                            "check_id": "farm_tonight",
                            "verdict": "pass",
                            "evidence_ref": "",
                        }
                    ]
                },
            )
            assert bad.status_code == 400
            ok = client.put(
                "/api/runs/demo/reviews",
                json={
                    "reviews": [
                        {
                            "case_id": "revise_ch1_ending_farm_tonight",
                            "attempt": 1,
                            "check_id": "farm_tonight",
                            "verdict": "pass",
                            "evidence_ref": "after:正文/0003.md#雪",
                        }
                    ],
                    "attributions": [
                        {
                            "case_id": "revise_ch1_ending_farm_tonight",
                            "attempt": 1,
                            "category": "tool",
                            "note": "转而 ask_author",
                        }
                    ],
                },
            )
            assert ok.status_code == 200
            scored = client.post("/api/runs/demo/score").json()
            assert scored["summary"]["fail"] == 1
            assert scored["attributions"][0]["category"] == "tool"
    finally:
        server.shutdown()
        server.server_close()


def test_workbench_attempt_evidence_apis(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "demo"
    _seed_report(run_dir)
    _seed_evidence(run_dir)
    server, base = _serve(runs_root)
    try:
        with httpx.Client(base_url=base, timeout=5.0, trust_env=False) as client:
            attempt = client.get("/api/runs/demo/attempts/revise_ch1_ending_farm_tonight/1").json()
            assert attempt["default_attribution"] == "tool"
            assert attempt["turns"][0]["turn_id"] == "t_1"
            trace = client.get(
                "/api/runs/demo/attempts/revise_ch1_ending_farm_tonight/1/traces/t_1"
            ).json()
            assert trace["events"][0]["span_id"] == "tool:1"
            diff = client.get(
                "/api/runs/demo/attempts/revise_ch1_ending_farm_tonight/1/diff"
            ).json()
            assert diff["unchanged"] is True
    finally:
        server.shutdown()
        server.server_close()


def test_workbench_cases_and_related_runs(tmp_path: Path) -> None:
    from evaluation.workbench import jobs as jobmod

    jobmod.reset_jobs_for_tests()
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "smoke-related"
    _seed_report(run_dir)
    server, base = _serve(runs_root)
    try:
        with httpx.Client(base_url=base, timeout=5.0, trust_env=False) as client:
            listed = client.get("/api/cases").json()
            ids = {item["id"] for item in listed["cases"]}
            assert "revise_ch1_ending_farm_tonight" in ids
            detail = client.get("/api/cases/revise_ch1_ending_farm_tonight").json()
            assert detail["expect"]["region"]["path"].endswith(".md")
            assert detail["checks"]
            related = client.get("/api/cases/revise_ch1_ending_farm_tonight/runs").json()
            assert {item["id"] for item in related["runs"]} == {"smoke-related"}
    finally:
        server.shutdown()
        server.server_close()
        jobmod.reset_jobs_for_tests()


def test_workbench_jobs_validation_and_conflict(tmp_path: Path, monkeypatch) -> None:
    from evaluation.harness import runner_api
    from evaluation.harness.runner_api import BatchResult
    from evaluation.workbench import jobs as jobmod

    jobmod.reset_jobs_for_tests()
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    server, base = _serve(runs_root)

    started = threading.Event()
    release = threading.Event()

    def fake_run_batch(**kwargs):  # type: ignore[no-untyped-def]
        out_dir = kwargs["out_dir"]
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "report.json").write_text("{}", encoding="utf-8")
        started.set()
        # 等取消或超时
        for _ in range(50):
            if kwargs.get("cancel_event") is not None and kwargs["cancel_event"].is_set():
                return BatchResult(status="cancelled", report={}, cancelled=True, error="已取消")
            if release.wait(0.05):
                break
        return BatchResult(status="completed", report={"gate": {"passed": True}}, results=[])

    monkeypatch.setattr(jobmod, "run_batch", fake_run_batch)
    monkeypatch.setattr(runner_api, "resolve_cases", lambda *_a, **_k: [])
    # resolve_cases is imported into jobs — patch jobs.resolve_cases
    monkeypatch.setattr(jobmod, "resolve_cases", lambda *_a, **_k: ["ok"])

    try:
        with httpx.Client(base_url=base, timeout=5.0, trust_env=False) as client:
            empty = client.post(
                "/api/jobs",
                json={
                    "case_ids": [],
                    "mode": "smoke",
                    "repeat": 1,
                    "base_url": "http://127.0.0.1:9",
                },
            )
            assert empty.status_code == 400

            gate_bad = client.post(
                "/api/jobs",
                json={
                    "case_ids": ["revise_ch1_ending_farm_tonight"],
                    "mode": "gate",
                    "repeat": 1,
                    "base_url": "http://127.0.0.1:9",
                },
            )
            assert gate_bad.status_code == 400

            first = client.post(
                "/api/jobs",
                json={
                    "case_ids": ["revise_ch1_ending_farm_tonight"],
                    "mode": "smoke",
                    "repeat": 1,
                    "base_url": "http://127.0.0.1:9",
                    "out_name": "job-one",
                },
            )
            assert first.status_code == 201, first.text
            job_id = first.json()["id"]
            assert started.wait(2.0)

            second = client.post(
                "/api/jobs",
                json={
                    "case_ids": ["revise_ch1_ending_farm_tonight"],
                    "mode": "smoke",
                    "repeat": 1,
                    "base_url": "http://127.0.0.1:9",
                    "out_name": "job-two",
                },
            )
            assert second.status_code == 409

            cancelled = client.post(f"/api/jobs/{job_id}/cancel")
            assert cancelled.status_code == 200
            # 等线程收尾
            for _ in range(40):
                payload = client.get(f"/api/jobs/{job_id}").json()
                if payload["status"] in {"cancelled", "completed", "failed"}:
                    break
                threading.Event().wait(0.05)
            else:
                release.set()
            final = client.get(f"/api/jobs/{job_id}").json()
            assert final["status"] in {"cancelled", "completed", "failed"}
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        jobmod.reset_jobs_for_tests()


def test_workbench_jobs_rejects_existing_out(tmp_path: Path, monkeypatch) -> None:
    from evaluation.workbench import jobs as jobmod

    jobmod.reset_jobs_for_tests()
    runs_root = tmp_path / "runs"
    (runs_root / "exists").mkdir(parents=True)
    server, base = _serve(runs_root)
    monkeypatch.setattr(jobmod, "resolve_cases", lambda *_a, **_k: ["ok"])
    try:
        with httpx.Client(base_url=base, timeout=5.0, trust_env=False) as client:
            resp = client.post(
                "/api/jobs",
                json={
                    "case_ids": ["revise_ch1_ending_farm_tonight"],
                    "mode": "smoke",
                    "repeat": 1,
                    "base_url": "http://127.0.0.1:9",
                    "out_name": "exists",
                },
            )
            assert resp.status_code == 400
            assert "EVAL_OUT" in resp.text
    finally:
        server.shutdown()
        server.server_close()
        jobmod.reset_jobs_for_tests()
