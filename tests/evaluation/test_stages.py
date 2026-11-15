"""过程环节与结构化证据引用（不调模型）。"""

from __future__ import annotations

from pathlib import Path

from evaluation.harness.checks import AttemptEvidence, Check, grade_attempt
from evaluation.harness.schema import Case, Expect, Region, Step
from evaluation.harness.stages import compute_stages


def _case() -> Case:
    return Case(
        id="revise_snow",
        version=1,
        work="base/inn",
        work_dir=Path("unused"),
        history="short",
        steps=(Step(author="改成雪夜", focus="正文/0003.md"),),
        expect=Expect(
            region=Region(path="正文/0003.md", from_anchor="林岚推开客栈的门", to_anchor="END"),
            preserve=("「你昨晚去了哪里？」",),
        ),
        checks=(
            Check(id="only_region_changed", level="must"),
            Check(id="region_actually_changed", level="must"),
            Check(id="preserved_verbatim", level="must"),
            Check(id="committed_via_candidate", level="must"),
            Check(id="chapter_in_model_input", level="quality"),
        ),
        source=Path("case.yaml"),
    )


BEFORE = {
    "正文/0003.md": "前文。\n林岚推开客栈的门，雨。\n「你昨晚去了哪里？」\n",
}
AFTER = {
    "正文/0003.md": "前文。\n林岚推开客栈的门，雪。\n「你昨晚去了哪里？」\n",
}


def test_committed_fail_emits_missing_evidence() -> None:
    evidence = AttemptEvidence(
        before_files=BEFORE,
        after_files=AFTER,
        commit_receipts=[],
        events=(
            {"type": "turn_started", "turn_id": "t1"},
            {
                "type": "tool_started",
                "tool": "ask_author",
                "tool_call_id": "call-1",
                "parent_operation_id": "op1",
                "turn_id": "t1",
            },
            {"type": "run_finished", "turn_id": "t1"},
        ),
    )
    result = grade_attempt(_case(), evidence)
    item = next(c for c in result.checks if c.check_id == "committed_via_candidate")
    assert item.verdict == "fail"
    assert isinstance(item.evidence, tuple)
    assert any(ref.get("type") == "missing" and "回执" in str(ref.get("expected")) for ref in item.evidence)
    assert any(ref.get("type") == "span" and ref.get("span_id", "").startswith("tool_call:") for ref in item.evidence)


def test_file_checks_emit_file_refs() -> None:
    evidence = AttemptEvidence(before_files=BEFORE, after_files=AFTER, commit_receipts=[])
    result = grade_attempt(
        Case(
            id="x",
            version=1,
            work="w",
            work_dir=Path("u"),
            history="short",
            steps=(Step(author="a", focus="正文/0003.md"),),
            expect=Expect(
                region=Region(path="正文/0003.md", from_anchor="林岚推开客栈的门", to_anchor="END"),
                preserve=(),
            ),
            checks=(Check(id="region_actually_changed", level="must"),),
            source=Path("c.yaml"),
        ),
        evidence,
    )
    item = result.checks[0]
    assert item.verdict == "pass"
    assert item.evidence[0]["type"] == "file"
    assert item.evidence[0]["path"] == "正文/0003.md"
    assert item.evidence[0]["anchor"] == "林岚推开客栈的门"


def test_stages_mark_candidate_as_first_failure_when_ask_author_only() -> None:
    evidence = AttemptEvidence(
        before_files=BEFORE,
        after_files=BEFORE,
        commit_receipts=[],
        model_inputs_before_candidate=[BEFORE["正文/0003.md"]],
        events=(
            {"type": "turn_started", "turn_id": "t1"},
            {
                "type": "model_request_started",
                "operation_id": "op1",
                "turn_id": "t1",
                "input_detail": "key1",
            },
            {
                "type": "tool_started",
                "tool": "read_file",
                "tool_call_id": "call-r",
                "parent_operation_id": "op1",
                "turn_id": "t1",
                "args": {"path": "正文/0003.md"},
            },
            {
                "type": "tool_started",
                "tool": "ask_author",
                "tool_call_id": "call-a",
                "parent_operation_id": "op1",
                "turn_id": "t1",
            },
            {"type": "run_finished", "turn_id": "t1", "recording": "no_change"},
        ),
    )
    stages, first = compute_stages(_case(), evidence)
    by_id = {s["id"]: s for s in stages}
    assert by_id["read"]["verdict"] == "pass"
    assert by_id["candidate"]["verdict"] == "fail"
    assert by_id["verify"]["verdict"] == "not_reached"
    assert by_id["repair"]["verdict"] == "not_reached"
    assert by_id["commit"]["verdict"] == "fail"
    assert first == "candidate"


def test_stages_pass_when_candidate_committed() -> None:
    evidence = AttemptEvidence(
        before_files=BEFORE,
        after_files=AFTER,
        commit_receipts=[{"path": "正文/0003.md", "via": "candidate"}],
        model_inputs_before_candidate=[BEFORE["正文/0003.md"]],
        scope_registrations=[{"paths": ["正文/0003.md"]}],
        events=(
            {"type": "turn_started", "turn_id": "t1"},
            {
                "type": "tool_started",
                "tool": "declare_scope",
                "tool_call_id": "call-s",
                "parent_operation_id": "op1",
                "turn_id": "t1",
            },
            {
                "type": "tool_started",
                "tool": "submit_candidate",
                "tool_call_id": "call-c",
                "parent_operation_id": "op1",
                "turn_id": "t1",
            },
            {
                "type": "story_candidate_committed",
                "path": "正文/0003.md",
                "turn_id": "t1",
            },
            {"type": "run_finished", "turn_id": "t1", "recording": "committed"},
        ),
    )
    stages, first = compute_stages(_case(), evidence)
    by_id = {s["id"]: s for s in stages}
    assert by_id["scope"]["verdict"] == "pass"
    assert by_id["candidate"]["verdict"] == "pass"
    assert by_id["commit"]["verdict"] == "pass"
    assert first is None
