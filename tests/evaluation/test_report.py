"""报告汇总与门禁规则（合成试验结果，不调模型）。"""

from __future__ import annotations

from evaluation.harness.checks import AttemptGrade, CheckResult
from evaluation.harness.report import (
    apply_reviews,
    build_report,
    compare_to_baseline,
    gate_decision,
    render_report_md,
    write_report,
)
from evaluation.harness.runtime import AttemptResult


def _grade(*checks: CheckResult, outcome: str | None = None) -> AttemptGrade:
    items = tuple(checks)
    if outcome is None:
        if any(c.verdict == "fail" for c in items):
            outcome = "fail"
        elif any(c.verdict == "pending_review" for c in items):
            outcome = "pending_review"
        else:
            outcome = "pass"
    return AttemptGrade(outcome=outcome, checks=items)  # type: ignore[arg-type]


def _completed(case_id: str, attempt: int, grade: AttemptGrade) -> AttemptResult:
    return AttemptResult(
        case_id=case_id,
        attempt=attempt,
        status="completed",
        grade=grade,
        evidence=None,
    )


def test_build_report_counts() -> None:
    results = [
        _completed(
            "a",
            1,
            _grade(
                CheckResult("only_region_changed", "must", "pass", ""),
                CheckResult("snow", "human", "pending_review", "是否雪夜？"),
            ),
        ),
        AttemptResult("b", 1, "infrastructure_error", None, None, error="down"),
        AttemptResult("c", 1, "not_run", None, None, error="budget"),
        _completed(
            "d",
            1,
            _grade(CheckResult("only_region_changed", "must", "fail", "改出区域")),
        ),
    ]
    report = build_report(results, identity={"model": "test"})
    assert report["summary"]["pass"] == 0
    assert report["summary"]["pending_review"] == 1
    assert report["summary"]["fail"] == 1
    assert report["summary"]["infrastructure_error"] == 1
    assert report["summary"]["not_run"] == 1
    assert report["identity"]["model"] == "test"


def test_must_fail_blocks_gate() -> None:
    results = [
        _completed(
            "a",
            1,
            _grade(CheckResult("only_region_changed", "must", "fail", "x")),
        ),
        _completed(
            "a",
            2,
            _grade(CheckResult("only_region_changed", "must", "pass", "")),
        ),
        _completed(
            "a",
            3,
            _grade(CheckResult("only_region_changed", "must", "pass", "")),
        ),
    ]
    report = build_report(results)
    gate = gate_decision(report)
    assert gate["passed"] is False
    assert any(item["kind"] == "must_fail" for item in gate["blockers"])


def test_quality_regression_vs_baseline() -> None:
    def quality_pass(case: str, attempt: int, ok: bool) -> AttemptResult:
        return _completed(
            case,
            attempt,
            _grade(
                CheckResult("only_region_changed", "must", "pass", ""),
                CheckResult(
                    "chapter_in_model_input",
                    "quality",
                    "pass" if ok else "fail",
                    "",
                ),
            ),
        )

    # 基线：3/3 质量通过
    baseline = build_report(
        [quality_pass("a", 1, True), quality_pass("a", 2, True), quality_pass("a", 3, True)]
    )
    # 当前：1/3 → 少 2 → 阻断
    current = build_report(
        [quality_pass("a", 1, True), quality_pass("a", 2, False), quality_pass("a", 3, False)]
    )
    compared = compare_to_baseline(current, baseline)
    gate = gate_decision(compared)
    assert gate["passed"] is False
    assert any(item["kind"] == "quality_regression" for item in gate["blockers"])
    case_cmp = next(item for item in compared["baseline_compare"]["cases"] if item["case_id"] == "a")
    assert case_cmp["delta"] == "worse"
    assert case_cmp["quality_pass_count"] == 1
    assert case_cmp["baseline_quality_pass_count"] == 3


def test_quality_warn_only_when_down_by_one() -> None:
    def quality_pass(ok: bool, attempt: int) -> AttemptResult:
        return _completed(
            "a",
            attempt,
            _grade(
                CheckResult("m", "must", "pass", ""),
                CheckResult("q", "quality", "pass" if ok else "fail", ""),
            ),
        )

    baseline = build_report(
        [quality_pass(True, 1), quality_pass(True, 2), quality_pass(True, 3)]
    )
    current = build_report(
        [quality_pass(True, 1), quality_pass(True, 2), quality_pass(False, 3)]
    )
    compared = compare_to_baseline(current, baseline)
    gate = gate_decision(compared)
    assert gate["passed"] is True
    assert any(item["kind"] == "quality_warn" for item in gate["warnings"])


def test_apply_reviews_resolves_pending() -> None:
    results = [
        _completed(
            "a",
            1,
            _grade(
                CheckResult("m", "must", "pass", ""),
                CheckResult("snow", "human", "pending_review", "是否雪夜？"),
            ),
        )
    ]
    report = build_report(results)
    assert report["summary"]["pending_review"] == 1
    updated = apply_reviews(
        report,
        [
            {
                "case_id": "a",
                "attempt": 1,
                "check_id": "snow",
                "verdict": "pass",
                "evidence_ref": "after:正文/0003.md#雪",
            }
        ],
    )
    assert updated["summary"]["pass"] == 1
    assert updated["summary"]["pending_review"] == 0
    gate = gate_decision(updated)
    assert gate["passed"] is True


def test_write_report_files(tmp_path) -> None:
    report = build_report(
        [
            _completed(
                "a",
                1,
                _grade(CheckResult("m", "must", "pass", "")),
            )
        ]
    )
    report = {**report, "gate": gate_decision(report)}
    write_report(tmp_path, report)
    assert (tmp_path / "report.json").is_file()
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "门禁" in md
    assert "通过" in render_report_md(report)
