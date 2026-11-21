"""Grade captured T01 evidence without running the product or a model."""

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, TypeGuard

TASK_PATH = Path(__file__).with_name("tasks") / "t01.json"
STRUCTURE_PATH = "STRUCTURE.md"
MODULE_LISTS = ("library_module_ids", "binding_ids")

type JsonObject = dict[str, Any]
type Result = tuple[str, str, list[str]]


def unique_keys(pairs: list[tuple[str, Any]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path: Path) -> JsonObject:
    data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain an object.")
    return data


def validate_evidence(task: JsonObject, evidence: JsonObject) -> None:
    for key, expected in (("task_id", "id"), ("task_revision", "revision")):
        value = task["scenario"][expected]
        actual = evidence.get(key)
        if type(actual) is not type(value) or actual != value:
            raise ValueError(f"{key} must match the current task.")
    preflight = evidence.get("preflight")
    if not isinstance(preflight, dict) or type(preflight.get("valid")) is not bool:
        raise ValueError("preflight.valid must be a boolean.")
    if "reason" in preflight and not isinstance(preflight["reason"], str):
        raise ValueError("preflight.reason must be text.")
    for key in ("workspace_before", "workspace_after"):
        if key not in evidence:
            continue
        snapshot = evidence[key]
        if not isinstance(snapshot, dict):
            raise ValueError(f"{key} must be an object.")
        for path, digest in snapshot.items():
            if (
                any(part in ("", ".", "..") for part in path.split("/"))
                or re.search(r'[\\:<>"|?*\x00-\x1f]', path)
                or not isinstance(digest, str)
                or not re.fullmatch(r"[A-Fa-f0-9]{64}", digest)
            ):
                raise ValueError(f"{key} must map relative paths to SHA-256 digests.")
    if "modules_after" in evidence:
        modules = evidence["modules_after"]
        if not isinstance(modules, dict) or modules.keys() - set(MODULE_LISTS):
            raise ValueError("modules_after must be an object of module or binding ID lists.")
        for key, ids in modules.items():
            if not isinstance(ids, list) or any(
                not isinstance(i, str) or not i.strip() for i in ids
            ):
                raise ValueError(f"modules_after/{key} must be a list of nonempty IDs.")
    for key in ("structure_text", "assistant_reply", "execution_error"):
        if key in evidence and not isinstance(evidence[key], str):
            raise ValueError(f"{key} must be text.")


def valid_annotation(value: Any) -> TypeGuard[JsonObject]:
    return (
        isinstance(value, dict)
        and value.get("verdict") in ("pass", "fail", "unknown")
        and isinstance(value.get("reason"), str)
        and bool(value["reason"].strip())
    )


def invalid_run(evidence: JsonObject) -> tuple[str, list[str]] | None:
    if not evidence["preflight"]["valid"]:
        return evidence["preflight"].get("reason") or "Preflight failed.", ["preflight"]
    if error := evidence.get("execution_error"):
        return error, ["execution_error"]
    return None


def workspace_diff(rule: JsonObject, evidence: JsonObject) -> Result:
    snapshots = ("workspace_before", "workspace_after")
    refs = [key for key in snapshots if key in evidence]
    if missing := [key for key in snapshots if key not in evidence]:
        return "unknown", f"Missing {', '.join(missing)}.", refs

    before, after = (evidence[key] for key in refs)
    changes = {}
    for path in sorted(before.keys() | after.keys()):
        if path not in before:
            changes[path] = "created"
        elif path not in after:
            changes[path] = "deleted"
        elif before[path].lower() != after[path].lower():
            changes[path] = "modified"
    required = rule["required"]
    passed = all(changes.get(path) == kind for path, kind in required.items()) and (
        rule["allow_other_changes"] or changes == required
    )
    summary = ", ".join(f"{kind}: {path}" for path, kind in changes.items()) or "none"
    return "pass" if passed else "fail", f"Observed workspace changes: {summary}.", refs


def modules_empty(evidence: JsonObject) -> Result:
    modules = evidence.get("modules_after", {})
    refs = [f"modules_after/{key}" for key in MODULE_LISTS if key in modules]
    nonempty = [key for key in MODULE_LISTS if modules.get(key)]
    if nonempty:
        return "fail", "Nonempty module lists: " + ", ".join(nonempty) + ".", refs
    missing = [key for key in MODULE_LISTS if key not in modules]
    if missing:
        return "unknown", "Missing module lists: " + ", ".join(missing) + ".", refs
    return "pass", "Both module lists are empty.", refs


def semantic_evidence(check: JsonObject, evidence: JsonObject) -> tuple[JsonObject, list[str], str]:
    content = {}
    refs = []
    missing = []
    for key in check["evidence"]:
        if key == "workspace_after":
            after = evidence.get(key)
            if after is None:
                missing.append(key)
            elif STRUCTURE_PATH not in after:
                refs.append(key)
                missing.append(f"{STRUCTURE_PATH} (absent from workspace_after)")
            else:
                refs.append(f"workspace_after/{STRUCTURE_PATH}")
                if "structure_text" not in evidence:
                    missing.append("structure_text")
                else:
                    refs.append("structure_text")
                    text = evidence["structure_text"]
                    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                    if digest == after[STRUCTURE_PATH].lower():
                        content["structure_text"] = text
                    else:
                        missing.append("structure_text (does not match snapshot digest)")
        elif key in evidence:
            refs.append(key)
            content[key] = evidence[key]
        else:
            missing.append(key)
    reason = f"Missing semantic evidence: {', '.join(missing)}." if missing else ""
    return content, refs, reason


def evidence_sha256(evidence: JsonObject) -> str:
    # Stable across JSON formatting, but tied to the complete capture, including the turn ID.
    payload = json.dumps(evidence, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_bound_checks(path: Path, evidence: JsonObject) -> JsonObject:
    document = read_json(path)
    if document.get("evidence_sha256") != evidence_sha256(evidence):
        raise ValueError(f"{path.name}: evidence_sha256 does not match the evidence.")
    checks = document.get("checks")
    if not isinstance(checks, dict):
        raise ValueError(f"{path.name}: checks must be an object.")
    return checks


def task_identity(task: JsonObject) -> JsonObject:
    scenario = task["scenario"]
    return {"task_id": scenario["id"], "task_revision": scenario["revision"]}


def judge_inputs(task: JsonObject, evidence: JsonObject) -> JsonObject:
    packets = {}
    unavailable = {}
    scenario = task["scenario"]
    invalid = invalid_run(evidence)
    for check_id, check in task["llm_graders"].items():
        content, refs, missing = semantic_evidence(check, evidence)
        if invalid or missing:
            unavailable[check_id] = invalid[0] if invalid else missing
            continue
        checkpoint = check["checkpoint"]
        steps = scenario["cases"][checkpoint["case"]]["steps"]
        step = next(step for step in steps if step["id"] == checkpoint["step"])
        packets[check_id] = {
            "author_message": scenario["messages"][step["message"]],
            "rule": check["rule"],
            "evidence": content,
            "evidence_refs": refs,
        }
    return {
        **task_identity(task),
        "evidence_sha256": evidence_sha256(evidence),
        "inputs": packets,
        "missing": unavailable,
    }


def evaluate(
    task: JsonObject,
    evidence: JsonObject,
    human: JsonObject,
    judge: JsonObject | None,
) -> JsonObject:
    rows = []
    required = set()
    invalid = invalid_run(evidence)
    for block in ("code_graders", "llm_graders"):
        for check_id, check in task[block].items():
            comparison = {}
            if invalid:
                reason, refs = invalid
                verdict = "not_run"
            elif block == "llm_graders":
                _, refs, missing = semantic_evidence(check, evidence)
                annotation = human.get(check_id)
                if missing:
                    verdict, reason = "unknown", missing
                elif annotation is None:
                    verdict, reason = "unknown", "Missing human verdict."
                else:
                    verdict, reason = annotation["verdict"], annotation["reason"]
                if judge is not None:
                    proposed = judge.get(check_id)
                    if missing or not valid_annotation(proposed):
                        proposed = {
                            "verdict": "unknown",
                            "reason": missing or "Missing or invalid Judge verdict.",
                        }
                    comparison = {
                        "judge": proposed,
                        "judge_disagrees": (
                            proposed["verdict"] != verdict
                            if verdict in ("pass", "fail")
                            and proposed["verdict"] in ("pass", "fail")
                            else None
                        ),
                    }
            else:
                rule = check["rule"]
                match rule["type"]:
                    case "workspace_diff":
                        verdict, reason, refs = workspace_diff(rule, evidence)
                    case "modules_empty":
                        verdict, reason, refs = modules_empty(evidence)
                    case _:
                        raise ValueError(f"Unsupported Code Grader for {check_id}: {rule['type']}")
            rows.append(
                {
                    "case_id": check["checkpoint"]["case"],
                    "step_id": check["checkpoint"]["step"],
                    "check_id": check_id,
                    "verdict": verdict,
                    "reason": reason,
                    "evidence_refs": refs,
                    **comparison,
                }
            )
            if check["role"] == "required":
                required.add(verdict)
    aggregate = "inconclusive"
    if invalid:
        aggregate = "invalid"
    elif "fail" in required:
        aggregate = "fail"
    elif required == {"pass"}:
        aggregate = "pass"
    return {**task_identity(task), "verdict": aggregate, "checks": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--human", type=Path, help="Read authoritative human semantic judgments.")
    parser.add_argument("--judge", type=Path, help="Compare optional offline Judge judgments.")
    parser.add_argument("--judge-inputs", action="store_true", help="Export per-check evidence.")
    args = parser.parse_args()
    if args.judge_inputs and (args.human or args.judge):
        parser.error("--judge-inputs cannot be used with --human or --judge.")
    try:
        task = read_json(TASK_PATH)
        evidence = read_json(args.evidence)
        validate_evidence(task, evidence)
        if args.judge_inputs:
            output = judge_inputs(task, evidence)
        else:
            human = read_bound_checks(args.human, evidence) if args.human else {}
            for check_id, annotation in human.items():
                if check_id not in task["llm_graders"] or not valid_annotation(annotation):
                    raise ValueError(f"Invalid human verdict for {check_id}.")
            judge = None
            if args.judge:
                try:
                    judge = read_bound_checks(args.judge, evidence)
                except (OSError, ValueError):
                    judge = {}
            output = evaluate(task, evidence, human, judge)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
