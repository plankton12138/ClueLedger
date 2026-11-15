"""本机轻量服务：浏览已落盘运行、读证据包、写复核与归因；托管工作台前端。"""

from __future__ import annotations

import json
import mimetypes
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from evaluation.harness.report import apply_reviews, gate_decision, write_report
from evaluation.workbench import data as wb

STATIC = Path(__file__).resolve().parent / "static"
REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
DEFAULT_RUNS_ROOT = REPO_ROOT / "data" / "evaluation" / "runs"


def _error(code: str, where: str, why: str, fix: str) -> dict[str, Any]:
    return {"error": {"code": code, "where": where, "why": why, "fix": fix}}


def make_handler(runs_root: Path) -> type[BaseHTTPRequestHandler]:
    runs_root = runs_root.resolve()
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

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            parts = [part for part in path.split("/") if part]

            if path == "/api/runs":
                self._send_json(200, {"runs": wb.list_runs(runs_root), "runs_root": str(runs_root)})
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
                    "打开 / 从运行列表进入",
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
                    "使用 POST /api/runs/<id>/score",
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
) -> None:
    root = (runs_root or DEFAULT_RUNS_ROOT).resolve()
    root.mkdir(parents=True, exist_ok=True)
    handler = make_handler(root)
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}/"
    if initial_run:
        url = f"{url}#/runs/{initial_run}"
    print(f"评测工作台：{url}")
    print(f"运行根目录：{root}")
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
