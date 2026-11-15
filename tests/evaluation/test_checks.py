"""程序判分：受控证据快照，不调模型。"""

from __future__ import annotations

from pathlib import Path

from evaluation.harness.checks import AttemptEvidence, grade_attempt
from evaluation.harness.schema import Case, Check, Expect, Region, Step


def _case(*, preserve: tuple[str, ...] = ("「你昨晚去了哪里？」",), checks: list[Check] | None = None) -> Case:
    return Case(
        id="revise_snow",
        version=1,
        work="base/inn",
        work_dir=Path("unused"),
        history="short",
        steps=(Step(author="改成雪夜", focus="正文/0003.md"),),
        expect=Expect(
            region=Region(path="正文/0003.md", from_anchor="林岚推开客栈的门", to_anchor="END"),
            preserve=preserve,
        ),
        checks=tuple(
            checks
            or [
                Check(id="only_region_changed", level="must"),
                Check(id="preserved_verbatim", level="must"),
                Check(id="committed_via_candidate", level="must"),
                Check(id="snow_present", level="human", question="是否雪夜？"),
            ]
        ),
        source=Path("case.yaml"),
    )


BEFORE = {
    "正文/0003.md": (
        "前文保留。\n"
        "林岚推开客栈的门，雨还在下。\n"
        "「你昨晚去了哪里？」\n"
        "她没有回答。\n"
    ),
    "写作约定.md": "约定：人名勿混。\n",
}

AFTER_OK = {
    "正文/0003.md": (
        "前文保留。\n"
        "林岚推开客栈的门，雪落在石阶上。\n"
        "「你昨晚去了哪里？」\n"
        "她没有回答。\n"
    ),
    "写作约定.md": "约定：人名勿混。\n",
}


def test_grade_pass_must_and_pending_human() -> None:
    evidence = AttemptEvidence(
        before_files=BEFORE,
        after_files=AFTER_OK,
        commit_receipts=[{"path": "正文/0003.md", "via": "candidate"}],
    )
    result = grade_attempt(_case(), evidence)
    by_id = {item.check_id: item for item in result.checks}
    assert by_id["only_region_changed"].verdict == "pass"
    assert by_id["preserved_verbatim"].verdict == "pass"
    assert by_id["committed_via_candidate"].verdict == "pass"
    assert by_id["snow_present"].verdict == "pending_review"
    assert result.outcome == "pending_review"


def test_only_region_changed_fails_when_prefix_edited() -> None:
    after = dict(AFTER_OK)
    after["正文/0003.md"] = (
        "前文被改。\n"
        "林岚推开客栈的门，雪落在石阶上。\n"
        "「你昨晚去了哪里？」\n"
        "她没有回答。\n"
    )
    evidence = AttemptEvidence(
        before_files=BEFORE,
        after_files=after,
        commit_receipts=[{"path": "正文/0003.md", "via": "candidate"}],
    )
    result = grade_attempt(_case(), evidence)
    assert next(c for c in result.checks if c.check_id == "only_region_changed").verdict == "fail"
    assert result.outcome == "fail"


def test_only_region_changed_fails_when_other_file_edited() -> None:
    after = dict(AFTER_OK)
    after["写作约定.md"] = "约定被改。\n"
    evidence = AttemptEvidence(
        before_files=BEFORE,
        after_files=after,
        commit_receipts=[{"path": "正文/0003.md", "via": "candidate"}],
    )
    result = grade_attempt(_case(), evidence)
    assert next(c for c in result.checks if c.check_id == "only_region_changed").verdict == "fail"


def test_preserved_verbatim_fails_when_dialogue_missing() -> None:
    after = dict(AFTER_OK)
    after["正文/0003.md"] = (
        "前文保留。\n"
        "林岚推开客栈的门，雪落在石阶上。\n"
        "她没有回答。\n"
    )
    evidence = AttemptEvidence(
        before_files=BEFORE,
        after_files=after,
        commit_receipts=[{"path": "正文/0003.md", "via": "candidate"}],
    )
    result = grade_attempt(_case(), evidence)
    assert next(c for c in result.checks if c.check_id == "preserved_verbatim").verdict == "fail"


def test_committed_via_candidate_fails_without_receipt() -> None:
    evidence = AttemptEvidence(before_files=BEFORE, after_files=AFTER_OK, commit_receipts=[])
    result = grade_attempt(_case(), evidence)
    assert next(c for c in result.checks if c.check_id == "committed_via_candidate").verdict == "fail"


def test_region_actually_changed_fails_when_unchanged() -> None:
    case = _case(checks=[Check(id="region_actually_changed", level="must")])
    evidence = AttemptEvidence(before_files=BEFORE, after_files=BEFORE, commit_receipts=[])
    result = grade_attempt(case, evidence)
    assert next(c for c in result.checks if c.check_id == "region_actually_changed").verdict == "fail"


def test_region_actually_changed_passes_when_region_edited() -> None:
    case = _case(checks=[Check(id="region_actually_changed", level="must")])
    evidence = AttemptEvidence(
        before_files=BEFORE,
        after_files=AFTER_OK,
        commit_receipts=[{"path": "正文/0003.md", "via": "candidate"}],
    )
    result = grade_attempt(case, evidence)
    assert next(c for c in result.checks if c.check_id == "region_actually_changed").verdict == "pass"


def test_quality_skipped_without_model_input() -> None:
    case = _case(
        checks=[
            Check(id="only_region_changed", level="must"),
            Check(id="chapter_in_model_input", level="quality"),
        ]
    )
    evidence = AttemptEvidence(
        before_files=BEFORE,
        after_files=AFTER_OK,
        commit_receipts=[{"path": "正文/0003.md", "via": "candidate"}],
    )
    result = grade_attempt(case, evidence)
    assert next(c for c in result.checks if c.check_id == "chapter_in_model_input").verdict == "skipped"


def test_quality_pass_when_chapter_in_input() -> None:
    case = _case(
        checks=[
            Check(id="chapter_in_model_input", level="quality"),
        ]
    )
    evidence = AttemptEvidence(
        before_files=BEFORE,
        after_files=AFTER_OK,
        commit_receipts=[],
        model_inputs_before_candidate=[BEFORE["正文/0003.md"]],
    )
    result = grade_attempt(case, evidence)
    assert next(c for c in result.checks if c.check_id == "chapter_in_model_input").verdict == "pass"


def test_external_change_rejected() -> None:
    case = _case(
        checks=[
            Check(id="stale_candidate_rejected", level="must"),
        ]
    )
    evidence = AttemptEvidence(
        before_files=BEFORE,
        after_files=BEFORE,  # 外部改动后应拒绝提交，文件保持外部版或未覆盖
        commit_receipts=[],
        rejected_commits=[{"error_code": "VERSION_CONFLICT", "path": "正文/0003.md"}],
    )
    result = grade_attempt(case, evidence)
    assert next(c for c in result.checks if c.check_id == "stale_candidate_rejected").verdict == "pass"


def test_unknown_check_fails() -> None:
    case = _case(checks=[Check(id="not_a_real_check", level="must")])
    evidence = AttemptEvidence(before_files=BEFORE, after_files=AFTER_OK, commit_receipts=[])
    result = grade_attempt(case, evidence)
    assert next(c for c in result.checks if c.check_id == "not_a_real_check").verdict == "fail"
