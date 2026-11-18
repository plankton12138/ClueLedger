"""工作台跑测 Job：单进程内存表，同时只允许一个 active。"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from evaluation.harness.runner_api import RunBatchError, resolve_cases, run_batch
from evaluation.harness.runtime import AttemptResult

JobStatus = Literal["queued", "running", "cancelling", "completed", "failed", "cancelled"]

_LOCK = threading.Lock()
_JOBS: dict[str, Job] = {}
_ACTIVE_ID: str | None = None


def _now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _default_out_name(mode: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{mode}-{stamp}"


@dataclass
class Job:
    id: str
    status: JobStatus
    case_ids: list[str]
    mode: str
    repeat: int
    base_url: str
    max_requests: int
    out_name: str
    run_id: str
    out_dir: Path
    created_at: str
    baseline: Path | None = None
    timeout_s: float = 300.0
    cases_root: Path | None = None
    works_root: Path | None = None
    suite_id: str | None = None
    env_id: str | None = None
    evaluator_id: str | None = None
    evaluator_version: int | None = None
    capture_inputs: bool = True
    cancel_event: threading.Event = field(default_factory=threading.Event)
    log: list[str] = field(default_factory=list)
    completed_attempts: int = 0
    total_attempts: int = 0
    current_case: str | None = None
    current_attempt: int | None = None
    error: str | None = None
    gate_passed: bool | None = None
    finished_at: str | None = None
    thread: threading.Thread | None = field(default=None, repr=False)

    def append_log(self, line: str) -> None:
        self.log.append(line)
        if len(self.log) > 200:
            del self.log[:-200]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "case_ids": list(self.case_ids),
            "mode": self.mode,
            "repeat": self.repeat,
            "base_url": self.base_url,
            "max_requests": self.max_requests,
            "out_name": self.out_name,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "completed_attempts": self.completed_attempts,
            "total_attempts": self.total_attempts,
            "current_case": self.current_case,
            "current_attempt": self.current_attempt,
            "error": self.error,
            "gate_passed": self.gate_passed,
            "log": list(self.log[-40:]),
            "baseline": str(self.baseline) if self.baseline else None,
            "suite_id": self.suite_id,
            "env_id": self.env_id,
            "evaluator_id": self.evaluator_id,
            "evaluator_version": self.evaluator_version,
        }


def list_jobs() -> list[dict[str, Any]]:
    with _LOCK:
        jobs = sorted(_JOBS.values(), key=lambda item: item.created_at, reverse=True)
        return [job.to_dict() for job in jobs]


def get_job(job_id: str) -> dict[str, Any] | None:
    with _LOCK:
        job = _JOBS.get(job_id)
        return job.to_dict() if job else None


def active_job_id() -> str | None:
    with _LOCK:
        return _ACTIVE_ID


def create_job(
    *,
    runs_root: Path,
    cases_root: Path,
    works_root: Path,
    case_ids: list[str],
    mode: str,
    repeat: int,
    base_url: str,
    max_requests: int = 30,
    out_name: str | None = None,
    baseline: Path | None = None,
    timeout_s: float = 300.0,
    suite_id: str | None = None,
    env_id: str | None = None,
    evaluator_id: str | None = None,
    evaluator_version: int | None = None,
    capture_inputs: bool = True,
) -> Job:
    global _ACTIVE_ID
    if not case_ids:
        raise RunBatchError("[EVAL_CASE] workbench.jobs：未选择案例。建议：至少勾选一个 Case。")
    if mode not in {"smoke", "gate"}:
        raise RunBatchError(
            "[EVAL_MODE] workbench.jobs：mode 须为 smoke 或 gate。建议：改用冒烟或门禁。"
        )
    if mode == "gate" and repeat < 3:
        raise RunBatchError(
            "[EVAL_MODE] workbench.jobs：完整门禁要求 repeat ≥ 3。"
            "建议：提高试验次数，或改用 smoke。"
        )
    if not str(base_url or "").strip():
        raise RunBatchError(
            "[EVAL_BASE] workbench.jobs：缺少 base_url。建议：填写本地模式服务地址。"
        )

    name = (out_name or "").strip() or _default_out_name(mode)
    if "/" in name or "\\" in name or name in {".", ".."}:
        raise RunBatchError(
            f"[EVAL_OUT] workbench.jobs：非法输出名 {name}。建议：只用目录名，不含路径分隔符。"
        )
    out_dir = (runs_root / name).resolve()
    if not str(out_dir).startswith(str(runs_root.resolve())):
        raise RunBatchError("[EVAL_OUT] workbench.jobs：输出目录越界。建议：使用 runs 根下的名字。")
    if out_dir.exists():
        raise RunBatchError(
            f"[EVAL_OUT] workbench.jobs：输出目录已存在 {out_dir}。建议：换一个 out_name。"
        )

    # 启动前校验案例存在，避免空转线程
    resolve_cases(cases_root, works_root, case_ids)

    with _LOCK:
        if _ACTIVE_ID is not None:
            active = _JOBS.get(_ACTIVE_ID)
            if active and active.status in {"queued", "running", "cancelling"}:
                raise JobConflictError(
                    f"[EVAL_BUSY] workbench.jobs：已有任务 {_ACTIVE_ID} 在跑。"
                    "建议：等待完成或先取消。"
                )
        job_id = uuid.uuid4().hex[:12]
        job = Job(
            id=job_id,
            status="queued",
            case_ids=list(case_ids),
            mode=mode,
            repeat=int(repeat),
            base_url=str(base_url).rstrip("/"),
            max_requests=int(max_requests),
            out_name=name,
            run_id=name,
            out_dir=out_dir,
            created_at=_now_iso(),
            baseline=baseline,
            timeout_s=timeout_s,
            cases_root=cases_root,
            works_root=works_root,
            suite_id=suite_id,
            env_id=env_id,
            evaluator_id=evaluator_id,
            evaluator_version=evaluator_version,
            capture_inputs=capture_inputs,
            total_attempts=len(case_ids) * int(repeat),
        )
        _JOBS[job_id] = job
        _ACTIVE_ID = job_id

    thread = threading.Thread(target=_run_job, args=(job,), daemon=True)
    job.thread = thread
    thread.start()
    return job


class JobConflictError(RuntimeError):
    """已有任务在跑。"""


def cancel_job(job_id: str) -> dict[str, Any] | None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return None
        if job.status in {"queued", "running"}:
            job.status = "cancelling"
            job.cancel_event.set()
            job.append_log(f"{_now_iso()} 收到取消请求")
        return job.to_dict()


def _run_job(job: Job) -> None:
    global _ACTIVE_ID

    def on_progress(
        result: AttemptResult, _results: list[AttemptResult], _report: dict[str, Any]
    ) -> None:
        outcome = result.grade.outcome if result.grade else "-"
        line = f"{result.case_id}#{result.attempt}\t{result.status}\t{outcome}"
        if result.error:
            line = f"{line}\t{result.error}"
        with _LOCK:
            job.completed_attempts += 1
            job.current_case = result.case_id
            job.current_attempt = result.attempt
            job.append_log(line)

    with _LOCK:
        job.status = "running"
        job.append_log(f"{_now_iso()} 开始 {job.mode} → {job.out_name}")

    try:
        batch = run_batch(
            base_url=job.base_url,
            out_dir=job.out_dir,
            cases_dir=job.cases_root or Path("."),
            works_dir=job.works_root or Path("."),
            case_ids=job.case_ids,
            mode=job.mode,  # type: ignore[arg-type]
            repeat=job.repeat,
            max_requests=job.max_requests,
            timeout_s=job.timeout_s,
            baseline=job.baseline,
            cancel_event=job.cancel_event,
            on_progress=on_progress,
            strict_gate_repeat=True,
            suite_id=job.suite_id,
            env_id=job.env_id,
            evaluator_id=job.evaluator_id,
            evaluator_version=job.evaluator_version,
            capture_inputs=job.capture_inputs,
            job_id=job.id,
        )
        with _LOCK:
            job.gate_passed = batch.gate_passed
            if batch.status == "cancelled" or job.cancel_event.is_set():
                job.status = "cancelled"
                job.error = batch.error or "已取消"
            elif batch.status == "failed":
                job.status = "failed"
                job.error = batch.error or "跑测失败"
            else:
                job.status = "completed"
            job.append_log(f"{_now_iso()} 结束 status={job.status}")
            job.finished_at = _now_iso()
    except RunBatchError as exc:
        with _LOCK:
            job.status = "failed"
            job.error = str(exc)
            job.append_log(str(exc))
            job.finished_at = _now_iso()
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            job.status = "failed"
            job.error = str(exc)
            job.append_log(f"未捕获错误：{exc}")
            job.finished_at = _now_iso()
    finally:
        with _LOCK:
            if job.id == _ACTIVE_ID:
                _ACTIVE_ID = None
        # 给测试一点调度空隙
        time.sleep(0)


def reset_jobs_for_tests() -> None:
    """仅测试用：清空 Job 表。"""
    global _ACTIVE_ID
    with _LOCK:
        _JOBS.clear()
        _ACTIVE_ID = None
