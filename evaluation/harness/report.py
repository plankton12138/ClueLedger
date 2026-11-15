"""运行报告汇总、基线对比与门禁判定。"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from evaluation.harness.checks import grade_attempt
from evaluation.harness.evidence import attempt_paths, load_attempt_evidence
from evaluation.harness.runtime import AttemptResult
from evaluation.harness.schema import Case
from evaluation.harness.stages import compute_stages

_SCORED = frozenset({"pass", "fail", "pending_review"})


def _serialize_evidence(evidence: Any) -> list[Any]:
    if evidence is None:
        return []
    if isinstance(evidence, str):
        return [{"type": "note", "text": evidence}] if evidence else []
    if isinstance(evidence, (list, tuple)):
        return [dict(item) if isinstance(item, dict) else {"type": "note", "text": str(item)} for item in evidence]
    return [{"type": "note", "text": str(evidence)}]


def _attempt_row(result: AttemptResult) -> dict[str, Any]:
    grade = result.grade
    return {
        "case_id": result.case_id,
        "attempt": result.attempt,
        "status": result.status,
        "error": result.error,
        "model_requests": result.model_requests,
        "outcome": grade.outcome if grade else None,
        "checks": [
            {
                "id": item.check_id,
                "level": item.level,
                "verdict": item.verdict,
                "reason": item.reason,
                "evidence": _serialize_evidence(item.evidence),
            }
            for item in (grade.checks if grade else ())
        ],
        "stages": list(result.stages),
        "first_failed_stage": result.first_failed_stage,
    }


def _quality_passed(row: dict[str, Any]) -> bool:
    if row.get("status") != "completed":
        return False
    checks = row.get("checks") or []
    quality = [c for c in checks if c.get("level") == "quality"]
    if not quality:
        return row.get("outcome") in {"pass", "pending_review"}
    return all(c.get("verdict") in {"pass", "skipped"} for c in quality) and not any(
        c.get("verdict") == "fail" for c in quality
    )


def _must_failed(row: dict[str, Any]) -> bool:
    return any(
        c.get("level") == "must" and c.get("verdict") == "fail"
        for c in (row.get("checks") or [])
    )


def _recount(attempts: list[dict[str, Any]]) -> dict[str, int]:
    summary = {
        "pass": 0,
        "fail": 0,
        "pending_review": 0,
        "infrastructure_error": 0,
        "not_run": 0,
    }
    for row in attempts:
        status = row.get("status")
        if status == "infrastructure_error":
            summary["infrastructure_error"] += 1
        elif status == "not_run":
            summary["not_run"] += 1
        elif status == "completed":
            outcome = row.get("outcome")
            if outcome in _SCORED:
                summary[str(outcome)] += 1
    return summary


def build_report(
    results: list[AttemptResult],
    *,
    identity: dict[str, Any] | None = None,
    mode: str = "smoke",
) -> dict[str, Any]:
    attempts = [_attempt_row(result) for result in results]
    cases: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in attempts:
        cases[str(row["case_id"])].append(row)
    case_views = []
    for case_id, rows in sorted(cases.items()):
        case_views.append(
            {
                "case_id": case_id,
                "attempts": sorted(rows, key=lambda item: int(item["attempt"])),
                "must_failed": any(_must_failed(row) for row in rows),
                "quality_pass_count": sum(1 for row in rows if _quality_passed(row)),
                "attempt_count": len(rows),
            }
        )
    return {
        "mode": mode,
        "identity": identity or {},
        "summary": _recount(attempts),
        "cases": case_views,
        "attempts": attempts,
        "reviews": [],
        "baseline_compare": None,
        "gate": None,
    }


def regrade_report(
    report: dict[str, Any],
    run_dir: Path,
    *,
    cases: dict[str, Case],
) -> dict[str, Any]:
    """用证据包重跑程序判分；人工项保留原结论（缺省仍为 pending_review）。"""
    updated = json.loads(json.dumps(report))
    for row in updated.get("attempts") or []:
        if row.get("status") != "completed":
            continue
        case_id = str(row.get("case_id") or "")
        case = cases.get(case_id)
        if case is None:
            continue
        attempt_dir = run_dir / "attempts" / f"{case_id}--{int(row['attempt'])}"
        if not attempt_paths(attempt_dir).before.is_file():
            continue
        evidence = load_attempt_evidence(attempt_dir)
        grade = grade_attempt(case, evidence)
        stages, first = compute_stages(case, evidence)
        previous = {
            str(item.get("id")): item
            for item in (row.get("checks") or [])
            if isinstance(item, dict)
        }
        checks: list[dict[str, Any]] = []
        for item in grade.checks:
            if item.level == "human":
                old = previous.get(item.check_id) or {}
                checks.append(
                    {
                        "id": item.check_id,
                        "level": item.level,
                        "verdict": old.get("verdict") or item.verdict,
                        "reason": old.get("reason") or item.reason,
                        "evidence": _serialize_evidence(
                            old.get("evidence") if old.get("evidence") is not None else item.evidence
                        ),
                    }
                )
            else:
                checks.append(
                    {
                        "id": item.check_id,
                        "level": item.level,
                        "verdict": item.verdict,
                        "reason": item.reason,
                        "evidence": _serialize_evidence(item.evidence),
                    }
                )
        row["checks"] = checks
        row["stages"] = stages
        row["first_failed_stage"] = first
        if any(c.get("verdict") == "fail" for c in checks):
            row["outcome"] = "fail"
        elif any(c.get("verdict") == "pending_review" for c in checks):
            row["outcome"] = "pending_review"
        else:
            row["outcome"] = "pass"
    updated["summary"] = _recount(updated.get("attempts") or [])
    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in updated.get("attempts") or []:
        by_case[str(row["case_id"])].append(row)
    updated["cases"] = [
        {
            "case_id": case_id,
            "attempts": sorted(rows, key=lambda item: int(item["attempt"])),
            "must_failed": any(_must_failed(row) for row in rows),
            "quality_pass_count": sum(1 for row in rows if _quality_passed(row)),
            "attempt_count": len(rows),
        }
        for case_id, rows in sorted(by_case.items())
    ]
    return updated


def apply_reviews(report: dict[str, Any], reviews: list[dict[str, Any]]) -> dict[str, Any]:
    """把人工复核写回各检查项，并重算 outcome / summary。"""
    updated = json.loads(json.dumps(report))
    index = {
        (row["case_id"], int(row["attempt"])): row for row in updated["attempts"]
    }
    applied: list[dict[str, Any]] = []
    for review in reviews:
        key = (review["case_id"], int(review["attempt"]))
        row = index.get(key)
        if row is None or row.get("status") != "completed":
            continue
        verdict = review["verdict"]
        if verdict not in {"pass", "fail", "pending_review"}:
            continue
        if not review.get("evidence_ref") and verdict != "pending_review":
            continue
        for check in row.get("checks") or []:
            if check.get("id") != review["check_id"]:
                continue
            if check.get("level") != "human":
                continue
            check["verdict"] = verdict
            check["reason"] = review.get("reason") or check.get("reason") or ""
            evidence_ref = str(review.get("evidence_ref") or "")
            check["evidence"] = (
                [{"type": "note", "text": evidence_ref}] if evidence_ref else []
            )
            applied.append(dict(review))
            break
        # 重算该次试验 outcome
        checks = row.get("checks") or []
        if any(c.get("verdict") == "fail" for c in checks):
            row["outcome"] = "fail"
        elif any(c.get("verdict") == "pending_review" for c in checks):
            row["outcome"] = "pending_review"
        else:
            row["outcome"] = "pass"
    updated["reviews"] = applied
    updated["summary"] = _recount(updated["attempts"])
    # 同步 cases 视图
    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in updated["attempts"]:
        by_case[str(row["case_id"])].append(row)
    updated["cases"] = [
        {
            "case_id": case_id,
            "attempts": sorted(rows, key=lambda item: int(item["attempt"])),
            "must_failed": any(_must_failed(row) for row in rows),
            "quality_pass_count": sum(1 for row in rows if _quality_passed(row)),
            "attempt_count": len(rows),
        }
        for case_id, rows in sorted(by_case.items())
    ]
    return updated


def compare_to_baseline(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    updated = json.loads(json.dumps(current))
    base_cases = {item["case_id"]: item for item in baseline.get("cases") or []}
    compared = []
    for item in updated.get("cases") or []:
        base = base_cases.get(item["case_id"])
        current_q = int(item.get("quality_pass_count") or 0)
        base_q = int(base.get("quality_pass_count") or 0) if base else current_q
        if current_q > base_q:
            delta = "better"
        elif current_q < base_q:
            delta = "worse"
        else:
            delta = "same"
        compared.append(
            {
                "case_id": item["case_id"],
                "quality_pass_count": current_q,
                "baseline_quality_pass_count": base_q,
                "delta": delta,
            }
        )
    updated["baseline_compare"] = {
        "baseline_identity": baseline.get("identity") or {},
        "cases": compared,
    }
    return updated


def gate_decision(report: dict[str, Any]) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for case in report.get("cases") or []:
        if case.get("must_failed"):
            blockers.append(
                {
                    "kind": "must_fail",
                    "case_id": case["case_id"],
                    "detail": "存在必须项失败",
                }
            )
    for item in (report.get("baseline_compare") or {}).get("cases") or []:
        cur = int(item["quality_pass_count"])
        base = int(item["baseline_quality_pass_count"])
        drop = base - cur
        if drop >= 2:
            blockers.append(
                {
                    "kind": "quality_regression",
                    "case_id": item["case_id"],
                    "detail": f"质量通过次数 {cur}，基线 {base}，少 {drop} 次",
                }
            )
        elif drop == 1:
            warnings.append(
                {
                    "kind": "quality_warn",
                    "case_id": item["case_id"],
                    "detail": f"质量通过次数 {cur}，基线 {base}，少 1 次",
                }
            )
    # 待复核不阻断门禁布尔，但报告里单独计数；完整门禁通常需复核完再确认基线
    pending = int((report.get("summary") or {}).get("pending_review") or 0)
    if pending:
        warnings.append(
            {
                "kind": "pending_review",
                "case_id": "*",
                "detail": f"仍有 {pending} 次试验待人工复核",
            }
        )
    return {
        "passed": len(blockers) == 0,
        "blockers": blockers,
        "warnings": warnings,
    }


def render_report_md(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    gate = report.get("gate") or gate_decision(report)
    lines = [
        "# 评测报告",
        "",
        f"- 模式：{report.get('mode', 'smoke')}",
        f"- 门禁：{'通过' if gate.get('passed') else '不通过'}",
        (
            f"- 汇总：通过 {summary.get('pass', 0)}，失败 {summary.get('fail', 0)}，"
            f"待复核 {summary.get('pending_review', 0)}，"
            f"基础设施错误 {summary.get('infrastructure_error', 0)}，"
            f"未运行 {summary.get('not_run', 0)}"
        ),
        "",
    ]
    identity = report.get("identity") or {}
    if identity:
        lines.append("## 版本身份")
        for key, value in identity.items():
            lines.append(f"- {key}：{value}")
        lines.append("")
    if gate.get("blockers"):
        lines.append("## 阻断项")
        for item in gate["blockers"]:
            lines.append(f"- [{item['kind']}] {item.get('case_id')}: {item.get('detail')}")
        lines.append("")
    if gate.get("warnings"):
        lines.append("## 提示")
        for item in gate["warnings"]:
            lines.append(f"- [{item['kind']}] {item.get('case_id')}: {item.get('detail')}")
        lines.append("")
    compare = report.get("baseline_compare")
    if compare:
        lines.append("## 相对基线")
        for item in compare.get("cases") or []:
            lines.append(
                f"- {item['case_id']}：{item['delta']} "
                f"（质量通过 {item['quality_pass_count']} / 基线 {item['baseline_quality_pass_count']}）"
            )
        lines.append("")
    lines.append("## 案例")
    for case in report.get("cases") or []:
        lines.append(f"### {case['case_id']}")
        lines.append(
            f"- 试验 {case['attempt_count']} 次；必须项失败："
            f"{'是' if case.get('must_failed') else '否'}；"
            f"质量通过次数 {case.get('quality_pass_count', 0)}"
        )
        for row in case.get("attempts") or []:
            lines.append(
                f"- #{row['attempt']} {row['status']} outcome={row.get('outcome')}"
            )
            if row.get("first_failed_stage"):
                lines.append(f"  - 最早失败环节：{row['first_failed_stage']}")
            for check in row.get("checks") or []:
                lines.append(
                    f"  - {check['id']} ({check['level']}): {check['verdict']} — {check.get('reason')}"
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_report(out_dir: Path, report: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = dict(report)
    if payload.get("gate") is None:
        payload["gate"] = gate_decision(payload)
    (out_dir / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "report.md").write_text(render_report_md(payload), encoding="utf-8")
