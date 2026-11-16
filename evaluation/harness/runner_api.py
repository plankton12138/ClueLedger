"""可调用的批量跑测入口：CLI 与工作台共用。"""

from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

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
    )
    results: list[AttemptResult] = []
    cancelled = False
    run_error: str | None = None

    def _write_partial() -> dict[str, Any]:
        report = build_report(
            results,
            identity={**git_identity(), "budget_used": budget.used, "budget_max": max_requests},
            mode=mode,
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
