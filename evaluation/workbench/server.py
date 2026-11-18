"""本机轻量服务：Case 库、已落盘运行、跑测 Job；托管工作台前端。"""

from __future__ import annotations

import json
import mimetypes
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from evaluation.harness.assets import AssetLoadError, load_evaluator_by_id
from evaluation.harness.report import apply_reviews, gate_decision, write_report
from evaluation.harness.runner_api import RunBatchError, resolve_run_env_knobs, resolve_suite_batch
from evaluation.harness.schema import CaseLoadError
from evaluation.workbench import data as wb
from evaluation.workbench import jobs as jobmod

STATIC = Path(__file__).resolve().parent / "static"
REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
DEFAULT_RUNS_ROOT = REPO_ROOT / "data" / "evaluation" / "runs"
DEFAULT_CASES_ROOT = Path(__file__).resolve().parents[1] / "cases"
DEFAULT_WORKS_ROOT = Path(__file__).resolve().parents[1] / "works"


def _error(code: str, where: str, why: str, fix: str) -> dict[str, Any]:
    return {"error": {"code": code, "where": where, "why": why, "fix": fix}}


def _parse_error_message(message: str) -> tuple[str, str, str, str]:
    """从 `[CODE] where：why。建议：fix` 拆字段；失败则整段进 why。"""
    code = "EVAL_ERROR"
    where = "workbench"
    why = message
    fix = "检查请求后重试"
    text = message.strip()
    if text.startswith("[") and "]" in text:
        code = text[1 : text.index("]")]
        rest = text[text.index("]") + 1 :].strip()
        if "：" in rest:
            where, rest = rest.split("：", 1)
        if "。建议：" in rest:
            why, fix = rest.split("。建议：", 1)
        else:
            why = rest
    return code, where, why, fix


def make_handler(
    runs_root: Path,
    *,
    cases_root: Path | None = None,
    works_root: Path | None = None,
) -> type[BaseHTTPRequestHandler]:
    runs_root = runs_root.resolve()
    cases_root = (cases_root or DEFAULT_CASES_ROOT).resolve()
    works_root = (works_root or DEFAULT_WORKS_ROOT).resolve()
    dist = FRONTEND_DIST if (FRONTEND_DIST / "eval-workbench.html").is_file() else None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            print(f"[workbench] {self.address_string()} {format % args}")

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, code: int, payload: Any) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(code, data, "application/json; charset=utf-8")

        def _read_body(self) -> Any:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw.decode("utf-8"))

        def _serve_static(self, path: str) -> bool:
            if dist is not None:
                if path in {"/", "/index.html", "/eval-workbench.html"}:
                    html = (dist / "eval-workbench.html").read_bytes()
                    self._send(200, html, "text/html; charset=utf-8")
                    return True
                rel = path.lstrip("/")
                target = (dist / rel).resolve()
                if str(target).startswith(str(dist.resolve())) and target.is_file():
                    mime, _ = mimetypes.guess_type(str(target))
                    self._send(200, target.read_bytes(), mime or "application/octet-stream")
                    return True
                return False
            if path in {"/", "/index.html"}:
                html = (STATIC / "index.html").read_bytes()
                self._send(200, html, "text/html; charset=utf-8")
                return True
            return False

        def _run_api(self, run_id: str, rest: list[str], method: str) -> bool:
            run_dir = wb.resolve_run_dir(runs_root, run_id)
            if run_dir is None:
                self._send_json(
                    404,
                    _error(
                        "RUN_MISSING",
                        "workbench.run",
                        f"找不到运行 {run_id}",
                        "从运行列表重新选择",
                    ),
                )
                return True
            report_path = run_dir / "report.json"
            reviews_path = run_dir / "reviews.json"

            if method == "GET" and not rest:
                self._send_json(
                    200,
                    {
                        "id": run_id,
                        "report": wb.read_json(report_path, {}),
                        "reviews": wb.load_reviews_file(reviews_path),
                    },
                )
                return True

            if method == "GET" and rest == ["report"]:
                self._send_json(200, wb.read_json(report_path, {}))
                return True

            if rest == ["reviews"]:
                if method == "GET":
                    self._send_json(200, wb.load_reviews_file(reviews_path))
                    return True
                if method == "PUT":
                    body = self._read_body()
                    if not isinstance(body, dict):
                        self._send_json(
                            400,
                            _error(
                                "REVIEWS_INVALID",
                                "workbench.reviews",
                                "请求体须为对象",
                                "提交 reviews / attributions",
                            ),
                        )
                        return True
                    reviews = body.get("reviews")
                    if not isinstance(reviews, list):
                        self._send_json(
                            400,
                            _error(
                                "REVIEWS_INVALID",
                                "workbench.reviews",
                                "请求体需要 reviews 数组",
                                "按案例/试验/检查项提交复核",
                            ),
                        )
                        return True
                    problem = wb.validate_reviews(reviews)
                    if problem:
                        self._send_json(
                            400,
                            _error(
                                "EVIDENCE_REQUIRED",
                                "workbench.reviews",
                                problem,
                                "填写 evidence_ref",
                            ),
                        )
                        return True
                    try:
                        saved = wb.write_reviews_file(reviews_path, body)
                    except ValueError as exc:
                        self._send_json(
                            400,
                            _error("REVIEWS_INVALID", "workbench.reviews", str(exc), "检查请求体"),
                        )
                        return True
                    self._send_json(200, saved)
                    return True

            if method == "POST" and rest == ["score"]:
                report = wb.read_json(report_path, {})
                payload = wb.load_reviews_file(reviews_path)
                updated = apply_reviews(report, payload["reviews"])
                updated["attributions"] = payload["attributions"]
                updated["gate"] = gate_decision(updated)
                write_report(run_dir, updated)
                self._send_json(200, updated)
                return True

            if method == "GET" and rest and rest[0] == "attempts":
                if len(rest) < 3:
                    self._send_json(
                        400,
                        _error(
                            "ATTEMPT_INVALID",
                            "workbench.attempt",
                            "案例或试验号无效",
                            "检查路径",
                        ),
                    )
                    return True
                case_id = unquote(rest[1])
                try:
                    attempt = int(rest[2])
                except ValueError:
                    self._send_json(
                        400,
                        _error(
                            "ATTEMPT_INVALID",
                            "workbench.attempt",
                            "案例或试验号无效",
                            "检查路径",
                        ),
                    )
                    return True
                report = wb.read_json(report_path, {})
                tail = rest[3:]

                if not tail:
                    payload = wb.build_attempt_payload(run_dir, report, case_id, attempt)
                    if payload is None:
                        self._send_json(
                            404,
                            _error(
                                "ATTEMPT_MISSING",
                                "workbench.attempt",
                                "报告中无此试验",
                                "核对 case_id / attempt",
                            ),
                        )
                        return True
                    self._send_json(200, payload)
                    return True

                if len(tail) == 2 and tail[0] == "traces":
                    turn_id = unquote(tail[1])
                    trace = wb.load_trace(run_dir, case_id, attempt, turn_id)
                    if trace is None:
                        self._send_json(
                            404,
                            _error(
                                "TRACE_MISSING",
                                "workbench.trace",
                                f"无 Trace {turn_id}",
                                "确认证据包 traces/",
                            ),
                        )
                        return True
                    self._send_json(200, trace)
                    return True

                if len(tail) == 1 and tail[0] == "diff":
                    diff = wb.load_diff_view(run_dir, case_id, attempt)
                    if diff is None:
                        self._send_json(
                            404,
                            _error(
                                "DIFF_MISSING",
                                "workbench.diff",
                                "无证据包 before/diff",
                                "先跑带证据包的 evaluation run",
                            ),
                        )
                        return True
                    self._send_json(200, diff)
                    return True

                if len(tail) == 2 and tail[0] == "events":
                    event_id = unquote(tail[1])
                    event = wb.load_event(run_dir, case_id, attempt, event_id)
                    if event is None:
                        self._send_json(
                            404,
                            _error(
                                "EVENT_MISSING",
                                "workbench.event",
                                f"无事件 {event_id}",
                                "确认 events.jsonl",
                            ),
                        )
                        return True
                    self._send_json(200, event)
                    return True

                if len(tail) == 2 and tail[0] == "inputs":
                    operation_id = unquote(tail[1])
                    body = wb.load_input(run_dir, case_id, attempt, operation_id)
                    if body is None:
                        self._send_json(
                            404,
                            _error(
                                "INPUT_MISSING",
                                "workbench.input",
                                f"无输入 {operation_id}",
                                "确认 inputs/",
                            ),
                        )
                        return True
                    self._send_json(200, body)
                    return True

                if len(tail) == 1 and tail[0] == "capture":
                    self._send_json(
                        200,
                        {"capture_inputs": wb.capture_inputs_enabled(run_dir, case_id, attempt)},
                    )
                    return True

            return False

        def _cases_api(self, rest: list[str], method: str) -> bool:
            if method != "GET":
                return False
            if not rest:
                try:
                    cases = wb.list_cases(cases_root, works_root)
                except CaseLoadError as exc:
                    self._send_json(
                        400,
                        _error("EVAL_CASE", "workbench.cases", str(exc), "修正案例或作品初态"),
                    )
                    return True
                self._send_json(
                    200,
                    {
                        "cases": cases,
                        "cases_root": str(cases_root),
                        "works_root": str(works_root),
                    },
                )
                return True
            case_id = unquote(rest[0])
            if len(rest) == 1:
                detail = wb.get_case_detail(case_id, cases_root=cases_root, works_root=works_root)
                if detail is None:
                    self._send_json(
                        404,
                        _error(
                            "CASE_MISSING",
                            "workbench.cases",
                            f"找不到案例 {case_id}",
                            "用 Case 列表核对 id",
                        ),
                    )
                    return True
                self._send_json(200, detail)
                return True
            if len(rest) == 2 and rest[1] == "runs":
                self._send_json(
                    200,
                    {
                        "case_id": case_id,
                        "runs": wb.list_runs_for_case(runs_root, case_id),
                    },
                )
                return True
            return False

        def _jobs_api(self, rest: list[str], method: str) -> bool:
            if method == "GET" and not rest:
                self._send_json(
                    200, {"jobs": jobmod.list_jobs(), "active_id": jobmod.active_job_id()}
                )
                return True
            if method == "GET" and len(rest) == 1:
                payload = jobmod.get_job(unquote(rest[0]))
                if payload is None:
                    self._send_json(
                        404,
                        _error("JOB_MISSING", "workbench.jobs", "找不到任务", "从任务列表重选"),
                    )
                    return True
                self._send_json(200, payload)
                return True
            if method == "POST" and not rest:
                body = self._read_body()
                if not isinstance(body, dict):
                    self._send_json(
                        400,
                        _error("JOB_INVALID", "workbench.jobs", "请求体须为对象", "提交跑测参数"),
                    )
                    return True
                suite_id = body.get("suite_id")
                env_id = body.get("env_id")
                case_ids = body.get("case_ids") or []
                explicit_max = body.get("max_requests")
                explicit_timeout = body.get("timeout")
                if suite_id:
                    try:
                        (
                            suite,
                            _env,
                            suite_cases,
                            suite_mode,
                            suite_repeat,
                            _budget,
                            _env_timeout,
                        ) = resolve_suite_batch(str(suite_id))
                    except RunBatchError as exc:
                        code, where, why, fix = _parse_error_message(str(exc))
                        self._send_json(400, _error(code, where, why, fix))
                        return True
                    if not case_ids:
                        case_ids = suite_cases
                    mode = str(body.get("mode") or suite_mode)
                    try:
                        repeat = int(body.get("repeat") or suite_repeat)
                    except (TypeError, ValueError):
                        self._send_json(
                            400,
                            _error(
                                "JOB_INVALID",
                                "workbench.jobs",
                                "repeat 无效",
                                "改为数字",
                            ),
                        )
                        return True
                    if not env_id:
                        env_id = suite.default_env  # type: ignore[attr-defined]
                    evaluator_id = suite.default_evaluator  # type: ignore[attr-defined]
                else:
                    if not isinstance(case_ids, list):
                        self._send_json(
                            400,
                            _error(
                                "JOB_INVALID", "workbench.jobs", "case_ids 须为数组", "勾选案例"
                            ),
                        )
                        return True
                    mode = str(body.get("mode") or "smoke")
                    try:
                        repeat = int(body.get("repeat") or (3 if mode == "gate" else 1))
                    except (TypeError, ValueError):
                        self._send_json(
                            400,
                            _error(
                                "JOB_INVALID",
                                "workbench.jobs",
                                "repeat 无效",
                                "改为数字",
                            ),
                        )
                        return True
                    evaluator_id = body.get("evaluator_id")

                if not isinstance(case_ids, list):
                    self._send_json(
                        400,
                        _error("JOB_INVALID", "workbench.jobs", "case_ids 须为数组", "勾选案例"),
                    )
                    return True

                try:
                    parsed_max = int(explicit_max) if explicit_max is not None else None
                    parsed_timeout = (
                        float(explicit_timeout) if explicit_timeout is not None else None
                    )
                except (TypeError, ValueError):
                    self._send_json(
                        400,
                        _error(
                            "JOB_INVALID",
                            "workbench.jobs",
                            "max_requests / timeout 无效",
                            "改为数字",
                        ),
                    )
                    return True

                try:
                    env_id, max_requests, timeout_s, capture_inputs = resolve_run_env_knobs(
                        str(env_id) if env_id else None,
                        max_requests=parsed_max,
                        timeout_s=parsed_timeout,
                    )
                except RunBatchError as exc:
                    code, where, why, fix = _parse_error_message(str(exc))
                    self._send_json(400, _error(code, where, why, fix))
                    return True

                evaluator_version: int | None = None
                if evaluator_id:
                    try:
                        pack = load_evaluator_by_id(str(evaluator_id))
                        evaluator_version = pack.version
                    except AssetLoadError as exc:
                        self._send_json(
                            400,
                            _error(
                                "EVAL_ASSET",
                                "workbench.jobs",
                                str(exc),
                                "检查 evaluators 目录",
                            ),
                        )
                        return True

                out_name = body.get("out_name")
                baseline_name = body.get("baseline")
                baseline_path: Path | None = None
                if baseline_name:
                    baseline_dir = wb.resolve_run_dir(runs_root, str(baseline_name))
                    if baseline_dir is None:
                        self._send_json(
                            400,
                            _error(
                                "BASELINE_MISSING",
                                "workbench.jobs",
                                f"找不到基线运行 {baseline_name}",
                                "从结果列表选已有运行",
                            ),
                        )
                        return True
                    baseline_path = baseline_dir / "report.json"
                try:
                    job = jobmod.create_job(
                        runs_root=runs_root,
                        cases_root=cases_root,
                        works_root=works_root,
                        case_ids=[str(item) for item in case_ids],
                        mode=mode,
                        repeat=repeat,
                        base_url=str(body.get("base_url") or ""),
                        max_requests=max_requests,
                        out_name=str(out_name) if out_name else None,
                        baseline=baseline_path,
                        timeout_s=timeout_s,
                        suite_id=str(suite_id) if suite_id else None,
                        env_id=str(env_id) if env_id else None,
                        evaluator_id=str(evaluator_id) if evaluator_id else None,
                        evaluator_version=evaluator_version,
                        capture_inputs=capture_inputs,
                    )
                except jobmod.JobConflictError as exc:
                    code, where, why, fix = _parse_error_message(str(exc))
                    self._send_json(409, _error(code, where, why, fix))
                    return True
                except RunBatchError as exc:
                    code, where, why, fix = _parse_error_message(str(exc))
                    self._send_json(400, _error(code, where, why, fix))
                    return True
                self._send_json(201, job.to_dict())
                return True
            if method == "POST" and len(rest) == 2 and rest[1] == "cancel":
                payload = jobmod.cancel_job(unquote(rest[0]))
                if payload is None:
                    self._send_json(
                        404,
                        _error("JOB_MISSING", "workbench.jobs", "找不到任务", "从任务列表重选"),
                    )
                    return True
                self._send_json(200, payload)
                return True
            return False

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            parts = [part for part in path.split("/") if part]

            if path == "/api/runs":
                self._send_json(200, {"runs": wb.list_runs(runs_root), "runs_root": str(runs_root)})
                return

            if path == "/api/qa/queue":
                self._send_json(200, wb.build_qa_queue(runs_root))
                return

            if path == "/api/attribution-options":
                self._send_json(
                    200,
                    {
                        "options": [
                            {"id": key, "label": label, "hint": hint}
                            for key, label, hint in wb.ATTRIBUTION_OPTIONS
                        ],
                        "stage_labels": wb.STAGE_LABELS,
                    },
                )
                return

            if parts[:2] == ["api", "cases"] and self._cases_api(parts[2:], "GET"):
                return

            if path == "/api/suites":
                try:
                    self._send_json(200, {"suites": wb.list_suite_summaries()})
                except Exception as exc:  # noqa: BLE001
                    self._send_json(
                        400,
                        _error("EVAL_ASSET", "workbench.suites", str(exc), "检查 suites 目录"),
                    )
                return

            if path == "/api/envs":
                try:
                    self._send_json(200, {"envs": wb.list_env_summaries()})
                except Exception as exc:  # noqa: BLE001
                    self._send_json(
                        400,
                        _error("EVAL_ASSET", "workbench.envs", str(exc), "检查 envs 目录"),
                    )
                return

            if path == "/api/evaluators":
                try:
                    self._send_json(200, {"evaluators": wb.list_evaluator_summaries()})
                except Exception as exc:  # noqa: BLE001
                    self._send_json(
                        400,
                        _error(
                            "EVAL_ASSET",
                            "workbench.evaluators",
                            str(exc),
                            "检查 evaluators 目录",
                        ),
                    )
                return

            if parts[:2] == ["api", "jobs"] and self._jobs_api(parts[2:], "GET"):
                return

            if (
                len(parts) >= 3
                and parts[0] == "api"
                and parts[1] == "runs"
                and self._run_api(unquote(parts[2]), parts[3:], "GET")
            ):
                return

            if self._serve_static(path):
                return

            self._send_json(
                404,
                _error(
                    "NOT_FOUND",
                    "workbench",
                    f"未知路径 {path}",
                    "打开 / 从 Case 库、结果或跑测进入",
                ),
            )

        def do_PUT(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            parts = [part for part in path.split("/") if part]
            if (
                len(parts) >= 3
                and parts[0] == "api"
                and parts[1] == "runs"
                and self._run_api(unquote(parts[2]), parts[3:], "PUT")
            ):
                return
            self._send_json(
                404,
                _error(
                    "NOT_FOUND",
                    "workbench",
                    f"未知路径 {path}",
                    "使用 PUT /api/runs/<id>/reviews",
                ),
            )

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            parts = [part for part in path.split("/") if part]
            if parts[:2] == ["api", "jobs"] and self._jobs_api(parts[2:], "POST"):
                return
            if (
                len(parts) >= 3
                and parts[0] == "api"
                and parts[1] == "runs"
                and self._run_api(unquote(parts[2]), parts[3:], "POST")
            ):
                return
            self._send_json(
                404,
                _error(
                    "NOT_FOUND",
                    "workbench",
                    f"未知路径 {path}",
                    "使用 POST /api/jobs 或 /api/runs/<id>/score",
                ),
            )

    return Handler


def serve(
    runs_root: Path | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    initial_run: str | None = None,
    cases_root: Path | None = None,
    works_root: Path | None = None,
) -> None:
    root = (runs_root or DEFAULT_RUNS_ROOT).resolve()
    root.mkdir(parents=True, exist_ok=True)
    handler = make_handler(
        root,
        cases_root=cases_root or DEFAULT_CASES_ROOT,
        works_root=works_root or DEFAULT_WORKS_ROOT,
    )
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}/"
    if initial_run:
        url = f"{url}#/runs/{initial_run}"
    print(f"评测工作台：{url}")
    print(f"运行根目录：{root}")
    print(f"案例目录：{(cases_root or DEFAULT_CASES_ROOT).resolve()}")
    if (FRONTEND_DIST / "eval-workbench.html").is_file():
        print(f"前端：{FRONTEND_DIST / 'eval-workbench.html'}")
    else:
        print("前端：未找到 frontend/dist/eval-workbench.html，使用旧版静态页；请先 npm run build")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止工作台")
    finally:
        server.server_close()
