import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STRUCTURE = (
    "# 全书结构\n"
    "1. 暴雪封山，日本雪山温泉旅馆的客人因与老板的旧怨互相猜疑。\n"
    "2. 老板失踪，一位同行者只能从亲眼所见追查。\n"
    "失踪真相和结局留给作者决定。\n"
)
STRUCTURE_HASH = hashlib.sha256(STRUCTURE.encode()).hexdigest()


def good_evidence():
    return {
        "task_id": "T01",
        "task_revision": 1,
        "preflight": {"valid": True},
        "workspace_before": {},
        "workspace_after": {"STRUCTURE.md": STRUCTURE_HASH},
        "modules_after": {"library_module_ids": [], "binding_ids": []},
        "structure_text": STRUCTURE,
        "assistant_reply": "结构已保存。真相和结局待定，下轮可选补人物资料。",
    }


def human_passes():
    return {
        "usable_structure": {"verdict": "pass", "reason": "包括有限视角与递进的两幕"},
        "author_decisions_deferred": {"verdict": "pass", "reason": "稿件与回复均留白"},
        "no_extra_prerequisite": {"verdict": "pass", "reason": "建议不阻断交付"},
    }


def run_cli(tmp_path, evidence, *, human=None, judge=None, flags=()):
    args = [sys.executable, "-m", "eval.benchmark.evaluate"]
    for name, value in (("evidence", evidence), ("human", human), ("judge", judge)):
        if value is not None:
            if name != "evidence" and "evidence_sha256" not in value:
                payload = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
                digest = hashlib.sha256(payload.encode()).hexdigest()
                value = {"evidence_sha256": digest, "checks": value}
            path = tmp_path / f"{name}.json"
            path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            args.extend([str(path)] if name == "evidence" else [f"--{name}", str(path)])
    return subprocess.run(
        [*args, *flags], cwd=ROOT, capture_output=True, encoding="utf-8", check=False
    )


def report(result):
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def by_check(output):
    return {row["check_id"]: row for row in output["checks"]}


def test_rejects_human_review_of_different_evidence(tmp_path):
    old = good_evidence()
    packet = report(run_cli(tmp_path, old, flags=("--judge-inputs",)))
    evidence = {**old, "assistant_reply": "已经定下真相与结局。"}
    human = {"evidence_sha256": packet["evidence_sha256"], "checks": human_passes()}
    result = run_cli(tmp_path, evidence, human=human)
    assert result.returncode == 2
    assert "does not match" in result.stderr


def test_complete_evidence_with_human_passes(tmp_path):
    output = report(run_cli(tmp_path, good_evidence(), human=human_passes()))
    assert (output["task_id"], output["task_revision"], output["verdict"]) == ("T01", 1, "pass")
    rows = by_check(output)
    assert {check_id: row["verdict"] for check_id, row in rows.items()} == dict.fromkeys(
        ("structure_saved", "no_module_side_effects", *human_passes()), "pass"
    )
    assert rows["usable_structure"]["reason"] == human_passes()["usable_structure"]["reason"]
    assert rows["usable_structure"]["evidence_refs"] == [
        "workspace_after/STRUCTURE.md",
        "structure_text",
    ]
    assert all(row["reason"] and row["evidence_refs"] for row in rows.values())


def test_preflight_rejection_marks_every_check_not_run(tmp_path):
    evidence = good_evidence()
    evidence["preflight"] = {"valid": False, "reason": "起始作品已有历史"}
    output = report(run_cli(tmp_path, evidence, human=human_passes()))
    assert output["verdict"] == "invalid"
    assert len(output["checks"]) == 5
    for row in output["checks"]:
        assert (row["verdict"], row["reason"], row["evidence_refs"]) == (
            "not_run",
            "起始作品已有历史",
            ["preflight"],
        )


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("task_id", "T02"),
        ("task_revision", True),
        ("preflight", {"valid": "true"}),
        ("workspace_before", None),
        ("workspace_after", {"STRUCTURE.md": "a" * 64 + "\n"}),
        ("workspace_after", {"../STRUCTURE.md": STRUCTURE_HASH}),
        ("modules_after", {"library_module_ids": "", "binding_ids": []}),
        ("structure_text", None),
        ("assistant_reply", []),
    ],
)
def test_malformed_evidence_is_a_cli_error(tmp_path, field, bad_value):
    evidence = good_evidence()
    evidence[field] = bad_value
    result = run_cli(tmp_path, evidence, human=human_passes())
    assert result.returncode == 2
    assert not result.stdout
    assert field in result.stderr


def test_duplicate_keys_are_rejected(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"task_id":"T02",' + json.dumps(good_evidence())[1:], encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "eval.benchmark.evaluate", str(path)],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 2
    assert "duplicate" in result.stderr.lower() and "task_id" in result.stderr


def test_missing_snapshots_are_not_empty_snapshots(tmp_path):
    evidence = good_evidence()
    del evidence["workspace_before"], evidence["workspace_after"]
    output = report(run_cli(tmp_path, evidence, human=human_passes()))
    rows = by_check(output)
    assert output["verdict"] == "inconclusive"
    assert rows["structure_saved"]["verdict"] == "unknown"
    assert all(
        key in rows["structure_saved"]["reason"] for key in ("workspace_before", "workspace_after")
    )
    assert rows["usable_structure"]["verdict"] == "unknown"


def test_reply_claim_does_not_replace_saved_structure(tmp_path):
    evidence = good_evidence()
    evidence["workspace_after"] = {}
    output = report(run_cli(tmp_path, evidence, human=human_passes()))
    rows = by_check(output)
    assert output["verdict"] == "fail"
    assert rows["structure_saved"]["verdict"] == "fail"
    assert rows["usable_structure"]["verdict"] == "unknown"
    assert rows["author_decisions_deferred"]["verdict"] == "unknown"
    assert rows["no_extra_prerequisite"]["verdict"] == "pass"


def test_workspace_diff_reports_creations_modifications_and_deletions(tmp_path):
    evidence = good_evidence()
    old = hashlib.sha256(b"old").hexdigest()
    evidence["workspace_before"] = {"STYLE.md": old, "removed.md": old, "same.md": old.upper()}
    evidence["workspace_after"].update(
        {"STYLE.md": STRUCTURE_HASH, "正文/第一章.md": STRUCTURE_HASH, "same.md": old}
    )
    output = report(run_cli(tmp_path, evidence))
    row = by_check(output)["structure_saved"]
    assert row["verdict"] == output["verdict"] == "fail"
    assert "created: 正文/第一章.md" in row["reason"]
    assert "modified: STYLE.md" in row["reason"]
    assert "deleted: removed.md" in row["reason"]
    assert "same.md" not in row["reason"]


@pytest.mark.parametrize("observed_list", ["library_module_ids", "binding_ids"])
@pytest.mark.parametrize("ids", [[], ["module-added"]])
def test_a_single_observed_module_list_can_prove_failure_but_not_pass(tmp_path, observed_list, ids):
    evidence = good_evidence()
    evidence["modules_after"] = {observed_list: ids}
    output = report(run_cli(tmp_path, evidence))
    row = by_check(output)["no_module_side_effects"]
    assert row["verdict"] == ("fail" if ids else "unknown")
    assert row["evidence_refs"] == [f"modules_after/{observed_list}"]
    assert output["verdict"] == ("fail" if ids else "inconclusive")


def test_provider_error_does_not_count_as_agent_failure(tmp_path):
    evidence = good_evidence()
    evidence["workspace_after"] = {}
    evidence["execution_error"] = "MODEL_CALL_FAILED"
    output = report(run_cli(tmp_path, evidence))
    assert output["verdict"] == "invalid"
    assert all(row["verdict"] == "not_run" for row in output["checks"])
    assert all(row["evidence_refs"] == ["execution_error"] for row in output["checks"])


def test_judge_disagreement_does_not_override_human(tmp_path):
    judge = {"usable_structure": {"verdict": "fail", "reason": "不同意见"}}
    output = report(run_cli(tmp_path, good_evidence(), human=human_passes(), judge=judge))
    row = by_check(output)["usable_structure"]
    assert output["verdict"] == row["verdict"] == "pass"
    assert row["judge"] == judge["usable_structure"]
    assert row["judge_disagrees"] is True


def test_invalid_judge_annotation_does_not_block_human_report(tmp_path):
    judge = {"usable_structure": {"verdict": "maybe", "reason": "bad"}}
    output = report(run_cli(tmp_path, good_evidence(), human=human_passes(), judge=judge))
    row = by_check(output)["usable_structure"]
    assert row["judge"]["verdict"] == "unknown"
    assert output["verdict"] == "pass"


def test_judge_cannot_replace_missing_human_verdict(tmp_path):
    output = report(run_cli(tmp_path, good_evidence(), judge=human_passes()))
    assert output["verdict"] == "inconclusive"
    assert by_check(output)["usable_structure"]["verdict"] == "unknown"


def test_judge_packet_only_contains_evidence_needed_for_each_check(tmp_path):
    evidence = good_evidence()
    del evidence["assistant_reply"]
    packet = report(run_cli(tmp_path, evidence, flags=("--judge-inputs",)))
    assert set(packet["inputs"]) == {"usable_structure"}
    assert packet["inputs"]["usable_structure"]["evidence"] == {"structure_text": STRUCTURE}
    assert packet["inputs"]["usable_structure"]["rule"]["criterion"]
    assert set(packet["missing"]) == {"author_decisions_deferred", "no_extra_prerequisite"}


def test_inconsistent_structure_text_cannot_support_semantic_pass(tmp_path):
    evidence = good_evidence()
    evidence["structure_text"] = "这不是快照中的结构。"
    output = report(run_cli(tmp_path, evidence, human=human_passes()))
    assert output["verdict"] == "inconclusive"
    assert by_check(output)["usable_structure"]["verdict"] == "unknown"
