"""可调用的批量跑测入口：CLI 与工作台共用。"""

from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from evaluation.harness.assets import AssetLoadError, load_env_by_id, load_suite_by_id
from evaluation.harness.client import EvalClientError, HttpEvalClient
from evaluation.harness.report import (
    build_report,
    compare_to_baseline,
    gate_decision,
    write_report,
)
from evaluation.harness.runtime import AttemptResult, AttemptRunner, CallBudget
from evaluation.harness.schema import Case, CaseLoadError, load_cases

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JOBS_ROOT = REPO_ROOT / "data" / "evaluation" / "jobs"

BatchStatus = Literal["completed", "cancelled", "failed"]
ProgressCallback = Callable[[AttemptResult, list[AttemptResult], dict[str, Any]], None]


class RunBatchError(ValueError):
    """跑测参数或案例加载失败（启动前）。"""


@dataclass
class BatchResult:
    status: BatchStatus
    report: dict[str, Any]
    results: list[AttemptResult] = field(default_factory=list)
    error: str | None = None
    cancelled: bool = False

    @property
    def gate_passed(self) -> bool | None:
        gate = self.report.get("gate")
        if isinstance(gate, dict) and "passed" in gate:
            return bool(gate["passed"])
        return None


def git_identity(*, cwd: Path | None = None) -> dict[str, str]:
    identity: dict[str, str] = {}
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd or REPO_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        identity["git_commit"] = commit
    except (OSError, subprocess.CalledProcessError):
        pass
    return identity


def probe_base_url(base_url: str, *, timeout: float = 5.0) -> None:
    """确认本地模式服务可达；任意 HTTP 响应即视为存活。"""
    import httpx

    url = base_url.rstrip("/")
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            client.get(url)
    except Exception as exc:  # noqa: BLE001 — 统一成启动失败
        raise RunBatchError(
            f"[EVAL_BASE] evaluation.run：无法连接 {url}（{exc}）。"
            "建议：先启动本地模式服务，再检查 --base-url。"
        ) from exc


def resolve_cases(
    cases_dir: Path,
    works_dir: Path,
    case_ids: list[str] | None,
) -> list[Case]:
    try:
        cases = load_cases(cases_dir, works_root=works_dir)
    except CaseLoadError as exc:
        raise RunBatchError(
            f"[EVAL_CASE] evaluation.run：{exc}。建议：修正案例或作品初态后重试。"
        ) from exc
    if case_ids:
        wanted = set(case_ids)
        cases = [case for case in cases if case.id in wanted]
        missing = wanted - {case.id for case in cases}
        if missing:
            raise RunBatchError(
                f"[EVAL_CASE] evaluation.run：找不到案例 {sorted(missing)}。"
                "建议：用 evaluation list 核对 id。"
            )
    if not cases:
        raise RunBatchError(
            "[EVAL_CASE] evaluation.run：没有可跑案例。建议：先加入 evaluation/cases。"
        )
    return cases


def write_job_record(
    jobs_root: Path,
    payload: dict[str, Any],
) -> Path:
    """落盘 Pipeline Job 元数据；返回 job 目录。"""
    job_id = str(payload.get("id") or "")
    if not job_id:
        raise RunBatchError("[EVAL_JOB] evaluation.run：Job 缺少 id。建议：检查 Job 创建逻辑。")
    job_dir = jobs_root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    path = job_dir / "job.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return job_dir


def run_batch(
    *,
    base_url: str,
    out_dir: Path,
    cases_dir: Path,
    works_dir: Path,
    case_ids: list[str] | None = None,
    mode: Literal["smoke", "gate"] = "smoke",
    repeat: int = 1,
    max_requests: int = 30,
    timeout_s: float = 300.0,
    baseline: Path | None = None,
    cancel_event: threading.Event | None = None,
    on_progress: ProgressCallback | None = None,
    probe: bool = True,
    mkdir: bool = True,
    strict_gate_repeat: bool = False,
    suite_id: str | None = None,
    env_id: str | None = None,
    evaluator_id: str | None = None,
    evaluator_version: int | None = None,
    capture_inputs: bool = True,
    job_id: str | None = None,
    jobs_root: Path | None = None,
) -> BatchResult:
    """跑一批案例并写 report.json；可在 attempt 间隙取消。"""
    if out_dir.exists():
        raise RunBatchError(
            f"[EVAL_OUT] evaluation.run：输出目录已存在 {out_dir}。"
            "建议：换一个新目录，避免覆盖旧运行。"
        )
    if mode == "gate" and repeat < 3 and strict_gate_repeat:
        raise RunBatchError(
            "[EVAL_MODE] evaluation.run：完整门禁要求 --repeat ≥ 3。"
            "建议：提高试验次数，或改用 --mode smoke。"
        )
    if not base_url.strip():
        raise RunBatchError(
            "[EVAL_BASE] evaluation.run：缺少 base_url。建议：填写本地模式服务地址。"
        )
    if probe:
        probe_base_url(base_url)

    cases = resolve_cases(cases_dir, works_dir, case_ids)
    if evaluator_id is None:
        pack_ids = {case.evaluator_id for case in cases if case.evaluator_id}
        if len(pack_ids) == 1:
            evaluator_id = next(iter(pack_ids))
            evaluator_version = next(
                case.evaluator_version for case in cases if case.evaluator_id == evaluator_id
            )
    if mkdir:
        out_dir.mkdir(parents=True)
    attempts_root = out_dir / "attempts"
    client = HttpEvalClient(base_url)
    budget = CallBudget(max_requests=max_requests)
    runner = AttemptRunner(
        client=client,
        attempts_root=attempts_root,
        budget=budget,
        timeout_s=timeout_s,
        capture_inputs=capture_inputs,
    )
    results: list[AttemptResult] = []
    cancelled = False
    run_error: str | None = None

    def _write_partial() -> dict[str, Any]:
        report = build_report(
            results,
            identity={**git_identity(), "budget_used": budget.used, "budget_max": max_requests},
            mode=mode,
            suite_id=suite_id,
            env_id=env_id,
            evaluator_id=evaluator_id,
            evaluator_version=evaluator_version,
        )
        if baseline and baseline.is_file():
            try:
                baseline_data = json.loads(baseline.read_text(encoding="utf-8"))
                report = compare_to_baseline(report, baseline_data)
            except (OSError, json.JSONDecodeError):
                pass
        report["gate"] = gate_decision(report)
        if cancelled:
            report["cancelled"] = True
        write_report(out_dir, report)
        if job_id:
            write_job_record(
                jobs_root or DEFAULT_JOBS_ROOT,
                {
                    "id": job_id,
                    "suite": suite_id,
                    "env": env_id,
                    "evaluator": evaluator_id,
                    "evaluator_version": evaluator_version,
                    "mode": mode,
                    "repeat": repeat,
                    "out": str(out_dir),
                    "case_ids": [case.id for case in cases],
                    "status": "running" if not cancelled else "cancelling",
                    "base_url": base_url,
                },
            )
        return report

    try:
        for case in cases:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            for attempt in range(1, repeat + 1):
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    break
                try:
                    result = runner.run(case, attempt=attempt)
                except EvalClientError as exc:
                    result = AttemptResult(
                        case_id=case.id,
                        attempt=attempt,
                        status="infrastructure_error",
                        error=str(exc),
                        grade=None,
                        evidence=None,
                    )
                results.append(result)
                report = _write_partial()
                if on_progress is not None:
                    on_progress(result, results, report)
            if cancelled:
                break
    except Exception as exc:  # noqa: BLE001
        run_error = str(exc)
    finally:
        client.close()

    report = _write_partial()
    final_status: BatchStatus
    if run_error:
        final_status = "failed"
    elif cancelled:
        final_status = "cancelled"
    else:
        final_status = "completed"
    if job_id:
        write_job_record(
            jobs_root or DEFAULT_JOBS_ROOT,
            {
                "id": job_id,
                "suite": suite_id,
                "env": env_id,
                "evaluator": evaluator_id,
                "evaluator_version": evaluator_version,
                "mode": mode,
                "repeat": repeat,
                "out": str(out_dir),
                "case_ids": [case.id for case in cases],
                "status": final_status,
                "base_url": base_url,
                "error": run_error or ("已取消" if cancelled else None),
            },
        )
    if run_error:
        return BatchResult(status="failed", report=report, results=results, error=run_error)
    if cancelled:
        return BatchResult(
            status="cancelled",
            report=report,
            results=results,
            cancelled=True,
            error="已取消",
        )
    return BatchResult(status="completed", report=report, results=results)


def resolve_suite_batch(
    suite_id: str,
    *,
    suites_dir: Path | None = None,
    envs_dir: Path | None = None,
) -> tuple[object, object, list[str], Literal["smoke", "gate"], int, int, float]:
    """加载 Suite 与默认 Env，返回 (suite, env, case_ids, mode, repeat, budget, timeout_s)。"""
    try:
        suite = load_suite_by_id(suite_id, suites_dir=suites_dir)
        env = load_env_by_id(suite.default_env, envs_dir=envs_dir)
    except AssetLoadError as exc:
        raise RunBatchError(
            f"[EVAL_ASSET] evaluation.run：{exc}。建议：检查 suites/envs 目录。"
        ) from exc
    return (
        suite,
        env,
        list(suite.case_ids),
        suite.mode,
        suite.repeat,
        env.budget,
        env.timeout_s,
    )


def resolve_run_env_knobs(
    env_id: str | None,
    *,
    max_requests: int | None = None,
    timeout_s: float | None = None,
    envs_dir: Path | None = None,
) -> tuple[str | None, int, float, bool]:
    """按最终 Env 解析运行参数；显式 max_requests/timeout_s 可覆盖档案值。

    返回 (env_id, max_requests, timeout_s, capture_inputs)。
    """
    if not env_id:
        return (
            None,
            30 if max_requests is None else max_requests,
            300.0 if timeout_s is None else timeout_s,
            True,
        )
    try:
        env = load_env_by_id(env_id, envs_dir=envs_dir)
    except AssetLoadError as exc:
        raise RunBatchError(f"[EVAL_ASSET] evaluation.run：{exc}。建议：检查 envs 目录。") from exc
    return (
        env.id,
        env.budget if max_requests is None else max_requests,
        env.timeout_s if timeout_s is None else timeout_s,
        env.capture_inputs,
    )
