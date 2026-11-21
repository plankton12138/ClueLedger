import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from eval.benchmark import evaluate
from eval.benchmark.workbench_store import Store


@pytest.fixture
def task():
    return evaluate.read_json(evaluate.TASK_PATH)


@pytest.fixture
def evidence():
    text = "A saved structure."
    return {
        "task_id": "T01",
        "task_revision": 1,
        "preflight": {"valid": True},
        "workspace_before": {},
        "workspace_after": {"STRUCTURE.md": hashlib.sha256(text.encode()).hexdigest()},
        "structure_text": text,
        "assistant_reply": "Saved it.",
        "modules_after": {"library_module_ids": [], "binding_ids": []},
        "turn_id": "first-turn",
    }


def test_append_persists_capture_and_pending_report(tmp_path, task, evidence):
    store = Store(tmp_path)
    assert store.current("trial-a") is None
    packet = store.append("trial-a", task, evidence)
    assert packet["report"] == evaluate.evaluate(task, evidence, {}, None)
    assert packet["report"]["verdict"] == "inconclusive"
    assert packet["review_status"] == "pending"
    assert packet["review_inputs"] == evaluate.judge_inputs(task, evidence)
    assert packet["evidence_sha256"] == evaluate.evidence_sha256(evidence)
    assert Store(tmp_path).current("trial-a") == packet
    assert store.evidence("trial-a") == evidence
    assert store.evidence("trial-a", packet["evidence_version"]) == evidence
    assert store.versions("trial-a") == {
        "evidence_versions": [packet["evidence_version"]],
        "report_versions": [packet["report_version"]],
    }


def test_partial_review_merges_and_identical_retry_is_a_noop(tmp_path, task, evidence):
    store = Store(tmp_path)
    initial = store.append("trial-a", task, evidence)
    digest = initial["evidence_sha256"]
    checks = {"usable_structure": {"verdict": "pass", "reason": "Usable."}}
    partial = store.review("trial-a", task, digest, checks)
    assert partial["review_status"] == "partial"
    assert partial["report"]["verdict"] == "inconclusive"
    assert store.review("trial-a", task, digest, checks) == partial
    remaining = {
        "author_decisions_deferred": {"verdict": "pass", "reason": "Undecided."},
        "no_extra_prerequisite": {"verdict": "unknown", "reason": "Not sure."},
    }
    complete = store.review("trial-a", task, digest, remaining)
    assert complete["review_status"] == "reviewed"
    assert complete["report"]["verdict"] == "inconclusive"
    assert complete["report"] == evaluate.evaluate(task, evidence, checks | remaining, None)
    assert store.versions("trial-a")["report_versions"] == [
        p["report_version"] for p in (initial, partial, complete)
    ]


def test_regrade_uses_only_bound_human_checks_and_keeps_latest_capture(tmp_path, task, evidence):
    store = Store(tmp_path)
    initial = store.append("trial-a", task, evidence)
    checks = {key: {"verdict": "pass", "reason": "Checked."} for key in task["llm_graders"]}
    reviewed = store.review("trial-a", task, initial["evidence_sha256"], checks)
    assert reviewed["report"]["verdict"] == "pass"
    first = store.regrade("trial-a", task, initial["evidence_version"])
    second = store.regrade("trial-a", task, initial["evidence_version"])
    assert first["report"] == second["report"] == reviewed["report"]
    assert first["report_version"] != second["report_version"] != reviewed["report_version"]
    assert store.current("trial-a") == second
    corrected = store.append("trial-a", task, {**evidence, "assistant_reply": "Corrected."})
    assert corrected["evidence_version"] != initial["evidence_version"]
    assert corrected["review_status"] == "pending"
    assert corrected["report"]["verdict"] == "inconclusive"
    with pytest.raises(ValueError, match="stale"):
        store.review("trial-a", task, initial["evidence_sha256"], checks)
    old = store.regrade("trial-a", task, initial["evidence_version"])
    assert old["report"] == reviewed["report"]
    assert store.current("trial-a") == corrected
    assert store.evidence("trial-a", initial["evidence_version"]) == evidence
    assert Store(tmp_path).report("trial-a", initial["report_version"]) == initial
    assert store.report("trial-a", old["report_version"]) == old
    with pytest.raises(ValueError, match="report_version"):
        store.report("trial-a", "../report")
    with pytest.raises(KeyError):
        store.report("trial-a", "r-999999999999")


def test_import_requires_bound_per_check_review(tmp_path, task, evidence):
    store = Store(tmp_path)
    checks = {"usable_structure": {"verdict": "pass", "reason": "Checked."}}
    bound = {"evidence_sha256": evaluate.evidence_sha256(evidence), "checks": checks}
    with pytest.raises(ValueError, match="evidence_sha256"):
        store.append("trial-a", task, evidence, review={**bound, "evidence_sha256": "0" * 64})
    with pytest.raises(ValueError, match="evidence_sha256"):
        store.append("trial-a", task, evidence, review=checks)
    assert store.current("trial-a") is None
    packet = store.append("trial-a", task, evidence, review=bound)
    assert packet["review_status"] == "partial"
    assert packet["report"] == evaluate.evaluate(task, evidence, checks, None)


@pytest.mark.parametrize(
    "checks",
    [
        [],
        {"structure_saved": {"verdict": "pass", "reason": "Not a human check."}},
        {"usable_structure": {"verdict": "pass", "reason": " "}},
        {"author_decisions_deferred": {"verdict": "pass", "reason": "Missing reply."}},
    ],
)
def test_review_rejects_bad_or_unavailable_checks_without_publishing(
    tmp_path, task, evidence, checks
):
    del evidence["assistant_reply"]
    store = Store(tmp_path)
    packet = store.append("trial-a", task, evidence)
    with pytest.raises(ValueError):
        store.review("trial-a", task, packet["evidence_sha256"], checks)
    assert store.current("trial-a") == packet
    assert store.versions("trial-a")["report_versions"] == [packet["report_version"]]


def test_invalid_run_review_is_not_applicable(tmp_path, task, evidence):
    store = Store(tmp_path)
    evidence["preflight"] = {"valid": False}
    packet = store.append("trial-a", task, evidence)
    assert packet["review_status"] == "not_applicable"
    assert packet["report"]["verdict"] == "invalid"
    assert not packet["review_inputs"]["inputs"]


def test_identical_append_retries_do_not_clear_review(tmp_path, task, evidence):
    store = Store(tmp_path)
    initial = store.append("trial-a", task, evidence)
    reviewed = store.review(
        "trial-a",
        task,
        initial["evidence_sha256"],
        {"usable_structure": {"verdict": "pass", "reason": "Checked."}},
    )
    assert store.append("trial-a", task, evidence) == reviewed
    assert store.versions("trial-a")["evidence_versions"] == [initial["evidence_version"]]


@pytest.mark.parametrize(
    "value", ["../outside", "x/y", "x\\y", "C:outside", "x\n", "a\0b", "", "é"]
)
def test_invalid_run_id_never_creates_store(tmp_path, task, evidence, value):
    root = tmp_path / "store"
    with pytest.raises(ValueError, match="run_id"):
        Store(root).append(value, task, evidence)
    assert not root.exists()


@pytest.mark.parametrize("value", ["../outside", "e-000000000001\n", "e-1", "r-000000000001", ""])
def test_malformed_evidence_version_rejected(tmp_path, task, evidence, value):
    store = Store(tmp_path)
    initial = store.append("trial-a", task, evidence)
    with pytest.raises(ValueError, match="evidence_version"):
        store.evidence("trial-a", value)
    with pytest.raises(ValueError, match="evidence_version"):
        store.regrade("trial-a", task, value)
    assert store.current("trial-a") == initial


def _symlink(link, target, *, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (NotImplementedError, PermissionError) as exc:
        pytest.skip(f"Symlinks are unavailable: {exc}")
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip(f"Symlinks are unavailable: {exc}")
        raise


def test_refuses_symlinked_store_ancestor(tmp_path, task, evidence):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "link"
    _symlink(link, outside, directory=True)
    with pytest.raises(ValueError, match="symlink|reparse"):
        Store(link / "store").append("trial-a", task, evidence)
    assert not list(outside.iterdir())


def test_refuses_symlinked_run_directory(tmp_path, task, evidence):
    root = tmp_path / "store"
    store = Store(root)
    store.append("trial-a", task, evidence)
    (run_dir,) = root.iterdir()
    outside = run_dir.rename(tmp_path / "outside")
    _symlink(run_dir, outside, directory=True)
    with pytest.raises(ValueError, match="symlink|reparse"):
        store.current("trial-a")


def test_refuses_symlinked_generation(tmp_path, task, evidence):
    root = tmp_path / "store"
    store = Store(root)
    store.append("trial-a", task, evidence)
    (generation,) = root.rglob("*.json")
    outside = generation.rename(tmp_path / "outside.json")
    _symlink(generation, outside)
    with pytest.raises(ValueError, match="symlink|reparse"):
        store.evidence("trial-a")


def test_failed_publication_leaves_previous_generation_intact(
    tmp_path, task, evidence, monkeypatch
):
    store = Store(tmp_path)
    first = store.append("trial-a", task, evidence)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}

    def fail_replace(*args):
        raise OSError("simulated atomic replace failure")

    corrected = {**evidence, "assistant_reply": "Corrected."}
    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", fail_replace)
        with pytest.raises(OSError, match="atomic replace failure"):
            store.append("trial-a", task, corrected)
    assert not list(tmp_path.rglob(".staging-*"))
    assert store.current("trial-a") == first
    assert store.evidence("trial-a") == evidence
    assert {p: p.read_bytes() for p in tmp_path.rglob("*.json")} == before
    second = store.append("trial-a", task, corrected)
    assert store.evidence("trial-a", second["evidence_version"]) == corrected


def test_does_not_grade_or_show_tampered_capture(tmp_path, task, evidence):
    store = Store(tmp_path)
    first = store.append("trial-a", task, evidence)
    (generation,) = tmp_path.rglob("*.json")
    document = json.loads(generation.read_bytes())
    document["evidence"]["assistant_reply"] = "Tampered."
    generation.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="evidence_sha256"):
        store.current("trial-a")
    with pytest.raises(ValueError, match="evidence_sha256"):
        store.regrade("trial-a", task, first["evidence_version"])


def test_gating_checks_previous_stage_and_ignores_diagnostic_checks(task, evidence):
    from eval.benchmark.workbench_store import can_advance

    stages = task["scenario"]["cases"]["main"]["steps"]
    stages.append({"id": "follow_up", "message": "request"})
    task["code_graders"]["next_stage_done"] = {
        **task["code_graders"]["structure_saved"],
        "checkpoint": {"case": "main", "step": "follow_up"},
        "rule": {
            "type": "workspace_diff",
            "required": {"NEXT.md": "created"},
            "allow_other_changes": True,
        },
    }
    task["llm_graders"]["no_extra_prerequisite"]["role"] = "diagnostic"

    def follow_up_is_sent(report):
        sent = [stages[0]["id"]]
        if can_advance(task, "main", sent[-1], report):
            sent.append(stages[1]["id"])
        return sent[-1] == "follow_up"

    human = {key: {"verdict": "pass", "reason": "Checked."} for key in task["llm_graders"]}
    human["no_extra_prerequisite"]["verdict"] = "fail"
    passing = evaluate.evaluate(task, evidence, human, None)
    assert passing["verdict"] == "fail"  # The later checkpoint is not a gate on this step.
    assert follow_up_is_sent(passing)
    missing = {
        **passing,
        "checks": [r for r in passing["checks"] if r["check_id"] != "usable_structure"],
    }
    assert not follow_up_is_sent(missing)
    for verdict in ("fail", "unknown"):
        human["usable_structure"]["verdict"] = verdict
        assert not follow_up_is_sent(evaluate.evaluate(task, evidence, human, None))


def test_concurrent_partial_reviews_across_store_instances_do_not_lose_checks(
    tmp_path, task, evidence
):
    store = Store(tmp_path)
    packet = store.append("trial-a", task, evidence)
    keys = list(task["llm_graders"])

    def submit(key):
        return Store(tmp_path).review(
            "trial-a",
            task,
            packet["evidence_sha256"],
            {key: {"verdict": "pass", "reason": "Checked."}},
        )

    with ThreadPoolExecutor(max_workers=len(keys)) as pool:
        published = {item["report_version"] for item in pool.map(submit, keys)}
    assert len(published) == len(keys)
    current = store.current("trial-a")
    assert current["review_status"] == "reviewed"
    assert current["report"]["verdict"] == "pass"


def test_replay_real_archive_without_writing_originals(tmp_path, task):
    archive = evaluate.TASK_PATH.parents[2] / "data" / "runs" / "T01" / "2026-11-21"
    if not archive.is_dir():
        pytest.skip("Ignored local T01 archive is not available")
    store = Store(tmp_path)
    originals = {p: p.read_bytes() for p in archive.rglob("*.json")}
    for directory, review_file, report_file, status in (
        ("01-provider-error", None, "report.json", "not_applicable"),
        ("02-completed", "human-bound.json", "report-reviewed.json", "reviewed"),
        ("03-postfix", None, "report.json", "pending"),
    ):
        run = archive / directory
        evidence = evaluate.read_json(run / "evidence.json")
        review = evaluate.read_json(run / review_file) if review_file else None
        packet = store.append(directory, task, evidence, review=review)
        assert packet["report"] == evaluate.read_json(run / report_file)
        assert packet["review_status"] == status
        assert (
            store.regrade(directory, task, packet["evidence_version"])["report"] == packet["report"]
        )
    assert {p: p.read_bytes() for p in archive.rglob("*.json")} == originals
