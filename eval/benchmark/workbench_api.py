"""Loopback-only evaluation workbench; separate from the author-facing service."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import secrets
import shutil
import stat
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from pathlib import Path
from threading import Lock
from typing import Annotated, Any
from uuid import uuid4

from eval.benchmark import evaluate as grader
from eval.benchmark.workbench_history import (
    BASELINE_ID,
    RERUN_ID,
    Legacy,
    evidence_view,
    trace_view,
)
from eval.benchmark.workbench_index import (
    BENCHMARK,
    Archive,
    Catalog,
    RunKind,
    RunSummary,
    RunTiming,
    TaskRef,
)
from eval.benchmark.workbench_store import Store, bound_checks, grade_capture
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles

JsonObject = dict[str, Any]
REPO = BENCHMARK.parents[1]
HISTORY = BENCHMARK.parent / "data/runs/T01/2026-11-21/index.json"
STATE = BENCHMARK.parent / "data/workbench"
EVALUATOR_VERSION = "t01-v1"
ISOLATION_WARNING = "临时隔离目录未能清理；不要导出该目录。"
PAGE_OFFSET = Annotated[int, Query(ge=0)]
PAGE_LIMIT = Annotated[int, Query(ge=1, le=100)]


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    library_id: str
    task_id: str
    revision: int
    baseline_id: str | None = None

    def ref(self) -> TaskRef:
        return TaskRef(self.library_id, self.task_id, self.revision)


class Launch(Selection):
    approval_token: str | None = None


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    evidence_sha256: str
    checks: dict[str, JsonObject]


class Regrade(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    evidence_version: str


def _json(path: Path) -> JsonObject:
    return json.loads(path.read_bytes())  # type: ignore[no-any-return]


def _write(path: Path, data: JsonObject) -> None:
    scratch = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        scratch.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        scratch.replace(path)
    finally:
        scratch.unlink(missing_ok=True)


def _product_version() -> str:
    digest = hashlib.sha256()
    for path in sorted((REPO / "src").rglob("*.py")):
        digest.update(path.relative_to(REPO).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _retry_readonly_removal(remove: Callable[[str], Any], name: str, exc: BaseException) -> None:
    path = Path(name)
    if not isinstance(exc, PermissionError) or path.is_symlink():
        raise exc
    path.chmod(path.stat().st_mode | stat.S_IWRITE)
    remove(name)


def _permit(request: Request) -> None:
    origin = request.headers.get("origin")
    if request.headers.get("x-workbench-intent") != "local" or (
        origin is not None and origin != f"{request.url.scheme}://{request.url.netloc}"
    ):
        raise HTTPException(403, "工作台写操作只允许同源请求。")


def _preflight(
    ref: TaskRef, task: JsonObject, config: Path | None, approval_key: bytes
) -> JsonObject:
    if ref != TaskRef("writing", "T01", 1) or task != grader.read_json(grader.TASK_PATH):
        return {"valid": False, "reason": "当前 Runner 只支持正式 writing/T01 r1。"}
    if config is None:
        return {"valid": False, "reason": "服务未配置在线模型（--config）。"}
    from eval.benchmark.workbench_runner import prepare

    return {
        **prepare(task, config, approval_key=approval_key),
        "evaluator_version": EVALUATOR_VERSION,
    }


class Workbench:
    def __init__(
        self, state: Path, history: Path | None, libraries: dict[str, Path], config: Path | None
    ) -> None:
        self.state = state.resolve()
        self.history = history.resolve() if history else None
        self.libraries = libraries
        self.catalog = Catalog.load(libraries)
        self.config = config
        self._approval_secret = secrets.token_bytes(32)
        self.lock = Lock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="evaluation")
        self.active: str | None = None

    def approval_key(self, selection: Selection) -> bytes:
        subject = json.dumps([asdict(selection.ref()), selection.baseline_id, EVALUATOR_VERSION])
        return hmac.digest(self._approval_secret, subject.encode(), "sha256")

    def task(self, ref: TaskRef) -> JsonObject:
        try:
            return self.catalog.detail(ref.library_id, ref.task_id, ref.revision)
        except KeyError as exc:
            raise HTTPException(404, "任务或修订不存在。") from exc

    def _meta_path(self, run_id: str) -> Path:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", run_id):
            raise HTTPException(404, "运行不存在。")
        path = self.state / "runs" / run_id / "summary.json"
        if not path.resolve().is_relative_to(self.state):
            raise HTTPException(404, "运行不存在。")
        return path

    def _history(self) -> Archive:
        return Archive.load(self.history) if self.history else Archive([])

    def legacy(self, run_id: str) -> Legacy | None:
        if self.history is None:
            return None
        try:
            self._history().detail(run_id)
        except KeyError:
            return None
        return Legacy(self.history.parent, run_id)

    def meta(self, run_id: str) -> JsonObject:
        path = self._meta_path(run_id)
        if path.is_file():
            return self._local_meta(run_id, path)
        try:
            row = self._history().detail(run_id)
        except KeyError as exc:
            raise HTTPException(404, "运行不存在。") from exc
        return {
            "run": row.model_dump(mode="json"),
            "baseline_id": BASELINE_ID if run_id == RERUN_ID and self.legacy(BASELINE_ID) else None,
        }

    def _local_meta(self, run_id: str, path: Path) -> JsonObject:
        data = _json(path)
        if data["run"]["execution_status"] != "running":
            return data
        # Recover from the original publication, not a later repair or report.
        recovered = self.store().terminal_summary(run_id)
        if recovered is None:
            return data
        # The secondary summary may have failed after evidence publication and cleanup.
        # Use the actual directory state so recovery does not silently hide retained data.
        if (path.parent / "isolation").exists():
            if self.active == run_id:
                return data
            recovered.setdefault("milestones", []).append(
                {"type": "warning", "summary": ISOLATION_WARNING}
            )
        return recovered

    def archive(self) -> Archive:
        history = self._history()
        rows = [
            row
            for timing in ("started", "not_started", "unknown")
            for row in self._all(history, timing)
        ]
        for path in (self.state / "runs").glob("*/summary.json"):
            run_id = path.parent.name
            if self._meta_path(run_id) == path:
                rows.append(RunSummary.model_validate(self._local_meta(run_id, path)["run"]))
        return Archive(rows)

    @staticmethod
    def _all(archive: Archive, timing: RunTiming) -> list[RunSummary]:
        return list(archive.list(timing=timing, limit=2**31))

    def store(self) -> Store:
        return Store(self.state / "revisions")

    def legacy_packet(
        self, run: JsonObject, legacy: Legacy, evidence: JsonObject | None
    ) -> JsonObject | None:
        if evidence is None or not run.get("task") or legacy.report() is None:
            return None
        ref = TaskRef(**run["task"])
        try:
            task = self.catalog.detail(ref.library_id, ref.task_id, ref.revision)
            grader.validate_evidence(task, evidence)
        except (KeyError, ValueError):
            return None
        inputs = grader.judge_inputs(task, evidence)
        checks = {}
        # The archived verdict cannot vouch for a different capture.
        with suppress(ValueError, FileNotFoundError):
            if review := legacy.review():
                checks = bound_checks(inputs, review)
        return {
            **grade_capture(task, evidence, inputs, checks),
            "evidence_version": "legacy-current",
            "report_version": "legacy-current",
        }

    def detail(self, run_id: str, report_version: str | None = None) -> JsonObject:
        data = self.meta(run_id)
        run = data["run"]
        legacy = self.legacy(run_id)
        evidence = legacy.evidence() if legacy else None
        legacy_packet = self.legacy_packet(run, legacy, evidence) if legacy else None
        milestones = data.get("milestones", legacy.milestones() if legacy else [])
        if legacy and (not legacy_packet or legacy_packet["report"]["verdict"] != "pass"):
            milestones = [item for item in milestones if item["type"] != "review"]
        result: JsonObject = {
            "run": run,
            "report": None,
            "review_status": "not_applicable",
            "review_inputs": {},
            "baseline_id": data.get("baseline_id"),
            "milestones": milestones,
            "evidence_versions": legacy.versions() if legacy else [],
            "report_versions": ["legacy-current"] if legacy_packet else [],
        }
        store = self.store()
        selected = legacy_packet
        if report_version is None:
            if current := store.current(run_id):
                selected = current
                evidence = store.evidence(run_id, current["evidence_version"])
        elif report_version == "legacy-current":
            if selected is None:
                raise KeyError(report_version)
        else:
            selected = store.report(run_id, report_version)
            evidence = store.evidence(run_id, selected["evidence_version"])
        if selected is not None:
            for key in (
                "report",
                "review_status",
                "evidence_sha256",
                "evidence_version",
                "report_version",
            ):
                result[key] = selected[key]
            result["review_inputs"] = _review_inputs(selected["review_inputs"]["inputs"])
            run["verdict"] = selected["report"]["verdict"]
        elif legacy and run.get("task"):
            run["verdict"] = None
        for key, versions in store.versions(run_id).items():
            result[key].extend(versions)
        result["available_evidence"] = list(evidence_view(evidence or {}))
        return result

    def execute(
        self, run_id: str, task: JsonObject, approval: tuple[bytes, str] | None = None
    ) -> None:
        path = self._meta_path(run_id)
        data = _json(path)
        run = data["run"]
        published = False
        try:
            from eval.benchmark.workbench_runner import execute

            result = execute(task, self.config, path.parent, approval=approval)
            evidence = result["evidence"]
            started = result.get("started_at")
            turn = evidence.get("run", {})
            turn_id = turn.get("turn_id")
            run.update(
                execution_status=("completed" if turn.get("status") == "completed" else "failed")
                if turn_id
                else "not_started",
                started_at=started,
                turn_id=turn_id,
                model=turn.get("model") if turn_id else None,
            )
            data["connection_id"] = turn.get("connection_id") if turn_id else None
            data.update(milestones=result.get("milestones", []), trace=result.get("trace", []))
            data["initial_sha256"] = (
                grader.evidence_sha256({"workspace_before": evidence.get("workspace_before")})
                if "workspace_before" in evidence
                else None
            )
            data["product_version"] = _product_version()
            packet = self.store().append(run_id, task, evidence, terminal_summary=data)
            run["verdict"] = packet["report"]["verdict"]
            published = True
        except Exception as exc:
            if run["execution_status"] == "running":
                run["execution_status"] = "failed"
            run["verdict"] = "invalid"
            data["milestones"] = [
                *data.get("milestones", []),
                {"type": "error", "summary": f"运行档案未完成（{type(exc).__name__}）。"},
            ]
        finally:
            try:
                if published:
                    try:
                        shutil.rmtree(path.parent / "isolation", onexc=_retry_readonly_removal)
                    except FileNotFoundError:
                        pass
                    except OSError:
                        data.setdefault("milestones", []).append(
                            {"type": "warning", "summary": ISOLATION_WARNING}
                        )
                _write(path, data)
            finally:
                with self.lock:
                    self.active = None

    def repair(self, run_id: str, replacement: JsonObject) -> JsonObject:
        """Import corrected evidence for an existing turn; never launch a new one."""
        data = self.meta(run_id)
        run = data["run"]
        if run["execution_status"] == "running" or not run.get("task"):
            raise ValueError("只能补证已有终态的任务运行。")
        task = self.task(TaskRef(**run["task"]))
        grader.validate_evidence(task, replacement)
        capture_run = replacement.get("run")
        turn = capture_run.get("turn_id") if isinstance(capture_run, dict) else None
        if not isinstance(turn, str) or not turn or turn != run.get("turn_id"):
            raise ValueError("补证的轮次 ID 不属于此运行。")
        store = self.store()
        if (legacy := self.legacy(run_id)) is not None:
            with store.initializing(run_id) as archive:
                _seed(archive, run_id, task, legacy)
                return archive.append(run_id, task, replacement)
        return store.append(run_id, task, replacement)


def _review_inputs(inputs: JsonObject) -> JsonObject:
    return {
        key: {name: item[name] for name in ("rule", "evidence_refs") if name in item}
        for key, item in inputs.items()
    }


def _seed(store: Store, run_id: str, task: JsonObject, legacy: Legacy | None) -> None:
    """Import legacy captures into an initializing store before publishing the mutation."""
    if store.current(run_id) is not None:
        return
    current = legacy.evidence() if legacy else None
    if current is None or legacy is None:
        raise ValueError("没有可判定的本轮证据。")
    original = legacy.evidence("legacy-original")
    if original is not None:
        store.append(run_id, task, original)
    store.append(run_id, task, current, review=legacy.review())


def create_app(
    state: Path = STATE,
    *,
    history: Path | None = None,
    libraries: dict[str, Path] | None = None,
    config: Path | None = None,
    web: Path | None = None,
) -> FastAPI:
    book = Workbench(state, history, libraries or {"writing": BENCHMARK / "tasks"}, config)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            book.executor.shutdown(wait=True)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])

    @app.get("/api/tasks")
    def tasks(
        library_id: str | None = None,
        query: str = "",
        offset: PAGE_OFFSET = 0,
        limit: PAGE_LIMIT = 20,
    ) -> JsonObject:
        rows = book.catalog.list(library_id=library_id, query=query, offset=offset, limit=limit + 1)
        return {
            "items": [asdict(row) for row in rows[:limit]],
            "libraries": sorted(book.libraries),
            "has_more": len(rows) > limit,
        }

    @app.get("/api/tasks/{library_id}/{task_id}/{revision}")
    def task_detail(library_id: str, task_id: str, revision: int) -> JsonObject:
        ref = TaskRef(library_id, task_id, revision)
        task = book.task(ref)
        supported = ref == TaskRef("writing", "T01", 1) and task == grader.read_json(
            grader.TASK_PATH
        )
        runnable = supported and book.config is not None
        return {
            "task": task,
            "revisions": book.catalog.revisions(library_id, task_id),
            "runnable": runnable,
            "reason": None
            if runnable
            else ("服务未配置在线模型。" if supported else "此修订没有 Runner。"),
        }

    @app.get("/api/runs")
    def runs(
        library_id: str | None = None,
        task_id: str | None = None,
        revision: int | None = None,
        kind: RunKind | None = None,
        timing: RunTiming = "started",
        offset: PAGE_OFFSET = 0,
        limit: PAGE_LIMIT = 20,
    ) -> JsonObject:
        ref = None
        if library_id is not None or task_id is not None or revision is not None:
            if library_id is None or task_id is None or revision is None:
                raise HTTPException(422, "按任务筛选需要库、任务和修订。")
            ref = TaskRef(library_id, task_id, revision)
        rows = book.archive().list(
            task=ref, kind=kind, timing=timing, offset=offset, limit=limit + 1
        )
        items = []
        store = book.store()
        for row in rows[:limit]:
            item = row.model_dump(mode="json")
            current = store.current(row.run_id)
            if not current and row.task and (legacy := book.legacy(row.run_id)) is not None:
                # A missing or damaged attachment cannot verify the archived verdict.
                with suppress(OSError, ValueError):
                    current = book.legacy_packet(item, legacy, legacy.evidence())
                if not current:
                    item["verdict"] = None
            if current:
                item.update(
                    verdict=current["report"]["verdict"], review_status=current["review_status"]
                )
            items.append(item)
        return {
            "items": items,
            "has_more": len(rows) > limit,
            "default_run_id": BASELINE_ID
            if any(row.run_id == BASELINE_ID for row in rows[:limit])
            else None,
        }

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: str, report_version: str | None = None) -> JsonObject:
        try:
            return book.detail(run_id, report_version)
        except (ValueError, KeyError, FileNotFoundError) as exc:
            raise HTTPException(404, "报告版本不可用。") from exc

    @app.get("/api/runs/{run_id}/evidence")
    def evidence(run_id: str, version: str | None = None) -> JsonObject:
        book.meta(run_id)
        legacy = book.legacy(run_id)
        if (book.state / "revisions").is_dir() and not (version or "").startswith("legacy-"):
            store = book.store()
            if store.current(run_id):
                try:
                    return evidence_view(store.evidence(run_id, version))
                except (FileNotFoundError, KeyError, ValueError) as exc:
                    raise HTTPException(404, "证据版本不可用。") from exc
        data = legacy.evidence(version) if legacy else None
        if data is None:
            raise HTTPException(404, "证据未归档或版本不可用。")
        return evidence_view(data)

    @app.get("/api/runs/{run_id}/trace")
    def trace(run_id: str) -> JsonObject:
        meta = book.meta(run_id)
        legacy = book.legacy(run_id)
        return trace_view(meta.get("trace", legacy.trace() if legacy else []))

    @app.get("/api/runs/{run_id}/compare")
    def compare(run_id: str, baseline_id: str) -> JsonObject:
        current, baseline = book.meta(run_id), book.meta(baseline_id)
        differences = []
        for field in (
            "task",
            "model",
            "connection_id",
            "initial_sha256",
            "evaluator_version",
            "product_version",
        ):
            a = baseline["run"].get(field, baseline.get(field))
            b = current["run"].get(field, current.get(field))
            if a != b or a is None:
                differences.append({"field": field, "baseline": a, "current": b})
        return {
            "baseline": book.detail(baseline_id)["run"],
            "current": book.detail(run_id)["run"],
            "differences": differences,
            "comparable": not differences,
        }

    @app.post("/api/preflight")
    def preflight(selection: Selection, request: Request) -> JsonObject:
        _permit(request)
        ref = selection.ref()
        return _preflight(ref, book.task(ref), book.config, book.approval_key(selection))

    @app.post("/api/runs", status_code=202)
    def start(selection: Launch, request: Request) -> JsonObject:
        _permit(request)
        ref = selection.ref()
        task = book.task(ref)
        token = selection.approval_token
        if token is None or not re.fullmatch(r"[0-9a-f]{64}", token):
            raise HTTPException(409, "请先通过预检并确认运行设置。")
        approval_key = book.approval_key(selection)
        preview = _preflight(ref, task, book.config, approval_key)
        if not preview["valid"]:
            raise HTTPException(409, preview["reason"])
        if not hmac.compare_digest(preview.get("approval_token", ""), token):
            raise HTTPException(409, "运行设置已变化，请重新预检。")
        if selection.baseline_id is not None and book.meta(selection.baseline_id)["run"][
            "task"
        ] != asdict(ref):
            raise HTTPException(409, "基线必须属于同一任务修订。")
        with book.lock:
            if book.active is not None:
                raise HTTPException(409, "已有评测正在运行。")
            run_id = uuid4().hex
            path = book._meta_path(run_id)
            path.parent.mkdir(parents=True, exist_ok=False)
            _write(
                path,
                {
                    "run": dict(
                        run_id=run_id,
                        kind="task",
                        title=task["scenario"]["title"],
                        task=asdict(ref),
                        execution_status="running",
                    ),
                    "baseline_id": selection.baseline_id,
                    "evaluator_version": EVALUATOR_VERSION,
                    "milestones": [
                        {
                            "type": "preflight",
                            "summary": "连接与任务配置校验通过；开始准备隔离副本。",
                        }
                    ],
                },
            )
            book.active = run_id
            book.executor.submit(book.execute, run_id, task, (approval_key, token))
        return {"run_id": run_id}

    def editable(run_id: str) -> tuple[Store, JsonObject, Legacy | None]:
        run = book.meta(run_id)["run"]
        if not run.get("task") or run["execution_status"] == "running":
            raise HTTPException(409, "独立回归或运行中记录不能修改判定。")
        return book.store(), book.task(TaskRef(**run["task"])), book.legacy(run_id)

    @app.post("/api/runs/{run_id}/review")
    def review(run_id: str, body: Review, request: Request) -> JsonObject:
        _permit(request)
        with book.lock:
            store, task, legacy = editable(run_id)
            try:
                current = book.detail(run_id)
                if current.get("evidence_sha256") != body.evidence_sha256 or not body.checks:
                    raise ValueError("证据版本已变化，或没有提交检查项。")
                if any(
                    key not in current["review_inputs"] or not grader.valid_annotation(item)
                    for key, item in body.checks.items()
                ):
                    raise ValueError("检查项不适用，或标注没有有效结论和理由。")
                with store.initializing(run_id) as archive:
                    _seed(archive, run_id, task, legacy)
                    archive.review(run_id, task, body.evidence_sha256, body.checks)
            except (ValueError, KeyError) as exc:
                raise HTTPException(409, str(exc)) from exc
            except OSError as exc:
                raise HTTPException(409, "无法发布复核报告；未更改现有档案。") from exc
        return book.detail(run_id)

    @app.post("/api/runs/{run_id}/regrade")
    def regrade(run_id: str, body: Regrade, request: Request) -> JsonObject:
        _permit(request)
        with book.lock:
            store, task, legacy = editable(run_id)
            try:
                if body.evidence_version.startswith("legacy-"):
                    data = legacy.evidence(body.evidence_version) if legacy else None
                    if data is None:
                        raise ValueError("此历史证据版本不可用。")
                    with store.initializing(run_id) as archive:
                        _seed(archive, run_id, task, legacy)
                        digest = grader.evidence_sha256(data)
                        version = next(
                            (
                                v
                                for v in archive.versions(run_id)["evidence_versions"]
                                if grader.evidence_sha256(archive.evidence(run_id, v)) == digest
                            ),
                            None,
                        )
                        if version is None:
                            raise ValueError("历史证据未登记，无法重判。")
                        archive.regrade(run_id, task, version)
                else:
                    if store.current(run_id) is None:
                        raise ValueError("没有可重判的证据。")
                    store.regrade(run_id, task, body.evidence_version)
            except (ValueError, KeyError) as exc:
                raise HTTPException(409, str(exc)) from exc
            except OSError as exc:
                raise HTTPException(409, "无法发布重判报告；未更改现有档案。") from exc
        return book.detail(run_id)

    if web is not None and (web / "workbench.html").is_file():
        app.mount("/assets", StaticFiles(directory=web / "assets"), name="workbench-assets")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(web / "workbench.html")

    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=STATE)
    parser.add_argument("--history", type=Path, default=HISTORY if HISTORY.is_file() else None)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--web", type=Path, default=REPO / "frontend/dist-workbench")
    parser.add_argument("--library", action="append", default=[], metavar="ID=DIRECTORY")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--repair", metavar="RUN_ID", help="offline evidence repair")
    parser.add_argument("--evidence", type=Path, help="corrected evidence for --repair")
    args = parser.parse_args()
    libraries = {}
    for entry in args.library:
        library_id, sep, root = entry.partition("=")
        if not sep or not root or library_id in libraries:
            parser.error("--library expects unique ID=DIRECTORY entries")
        libraries[library_id] = Path(root)
    if bool(args.repair) != bool(args.evidence):
        parser.error("--repair and --evidence must be used together")
    if args.repair:
        book = Workbench(
            args.state, args.history, libraries or {"writing": BENCHMARK / "tasks"}, None
        )
        try:
            packet = book.repair(args.repair, _json(args.evidence))
        except (HTTPException, OSError, ValueError) as exc:
            parser.error(str(exc))
        print(
            json.dumps(
                {k: packet[k] for k in ("evidence_version", "report_version", "evidence_sha256")}
            )
        )
        return
    app = create_app(
        args.state, history=args.history, config=args.config, web=args.web, libraries=libraries
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
