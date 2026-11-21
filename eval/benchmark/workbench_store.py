"""Local, append-only evidence and report storage for the benchmark workbench."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import NotRequired, TypedDict, cast

from eval.benchmark import evaluate
from eval.benchmark.evaluate import JsonObject

# Shared by Store instances in this process, including instances for the same root.
_LOCK = RLock()
_RUN_ID = re.compile(r"[a-zA-Z0-9_-]{1,120}")
_EVIDENCE_VERSION = re.compile(r"e-[0-9]{12}")
_REPORT_VERSION = re.compile(r"r-[0-9]{12}")


class StaleEvidenceError(ValueError):
    """A review was submitted for evidence other than the current capture."""


class _Generation(TypedDict):
    packet: JsonObject
    checks: JsonObject
    evidence: NotRequired[JsonObject]
    terminal_summary: NotRequired[JsonObject]


def _check_path(path: Path, *, directory: bool) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode) or (
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise ValueError(f"Store path must not be a symlink or reparse point: {path.name}")
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode):
        raise ValueError(f"Invalid store path type: {path.name}")
    return True


def _check_version(version: str, pattern: re.Pattern[str], name: str) -> None:
    if not isinstance(version, str) or not pattern.fullmatch(version):
        raise ValueError(f"Invalid {name}.")


def can_advance(task: JsonObject, case_id: str, step_id: str, report: JsonObject) -> bool:
    """Gate the next step on every required check at the just-completed checkpoint."""
    if report.get("verdict") == "invalid" or any(
        report.get(key) != value for key, value in evaluate.task_identity(task).items()
    ):
        return False
    checkpoint = {"case": case_id, "step": step_id}
    required = {
        key
        for group in (task["code_graders"], task["llm_graders"])
        for key, check in group.items()
        if check["role"] == "required" and check["checkpoint"] == checkpoint
    }
    rows = report.get("checks")
    if not required or not isinstance(rows, list):
        return False
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            return False
        key = row.get("check_id")
        if (
            row.get("case_id") != case_id
            or row.get("step_id") != step_id
            or not isinstance(key, str)
            or key not in required
        ):
            continue
        if key in seen or row.get("verdict") != "pass":
            return False
        seen.add(key)
    return seen == required


def _validate_checks(inputs: JsonObject, checks: object) -> JsonObject:
    if not isinstance(checks, dict) or not checks:
        raise ValueError("checks must be a nonempty object.")
    for key, annotation in checks.items():
        if key not in inputs["inputs"]:
            raise ValueError(f"No review inputs are available for {key!r}.")
        if not evaluate.valid_annotation(annotation):
            raise ValueError(f"Invalid human verdict for {key!r}.")
    return checks


def bound_checks(inputs: JsonObject, review: JsonObject) -> JsonObject:
    if review.get("evidence_sha256") != inputs["evidence_sha256"]:
        raise ValueError("Review evidence_sha256 does not match the evidence.")
    return _validate_checks(inputs, review.get("checks"))


def grade_capture(
    task: JsonObject, evidence: JsonObject, inputs: JsonObject, checks: JsonObject
) -> JsonObject:
    if evaluate.invalid_run(evidence) or not task["llm_graders"]:
        status = "not_applicable"
    elif not checks:
        status = "pending"
    elif checks.keys() == task["llm_graders"].keys():
        status = "reviewed"
    else:
        status = "partial"
    return {
        "evidence_sha256": inputs["evidence_sha256"],
        "report": evaluate.evaluate(task, evidence, checks, None),
        "review_status": status,
        "review_inputs": inputs,
    }


class Store:
    """Atomically publish generations in a private root owned by one process.

    Version IDs are opaque and scoped to the run. Do not edit the storage files
    directly or have another process write them concurrently.
    """

    def __init__(self, root: Path) -> None:
        self._root = root.absolute()

    @contextmanager
    def initializing(self, run_id: str) -> Iterator[Store]:
        """Publish an initial group of generations as one directory, or none on failure."""
        destination = self._run(run_id)
        with _LOCK:
            if self._records(destination):
                yield self
                return
            self._root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=self._root, prefix=".initializing-") as temp:
                staging = Store(Path(temp))
                yield staging
                source = staging._run(run_id)
                if source.is_dir():
                    if _check_path(destination, directory=True):
                        destination.rmdir()  # A previous single-file failure may leave it empty.
                    source.rename(destination)

    def _run(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise ValueError("Invalid run_id; expected 1–120 ASCII letters, digits, '_' or '-'.")
        # The on-disk name is case-safe even on case-insensitive filesystems.
        return self._root / f"run-{run_id.encode('ascii').hex()}"

    @staticmethod
    def _records(path: Path) -> list[_Generation]:
        for parent in reversed(path.parents):
            if not _check_path(parent, directory=True):
                return []
        if not _check_path(path, directory=True):
            return []
        records = []
        for file in sorted(path.iterdir()):
            if file.suffix == ".json" and _REPORT_VERSION.fullmatch(file.stem):
                _check_path(file, directory=False)
                record = evaluate.read_json(file)
                packet = record.get("packet")
                if (
                    not isinstance(packet, dict)
                    or not isinstance(record.get("checks"), dict)
                    or ("evidence" in record and not isinstance(record["evidence"], dict))
                    or (
                        "terminal_summary" in record
                        and not isinstance(record["terminal_summary"], dict)
                    )
                ):
                    raise ValueError(f"Invalid stored generation: {file.name}")
                if packet.get("report_version") != file.stem:
                    raise ValueError("Stored report version does not match its filename.")
                records.append(cast(_Generation, record))
        return records

    @staticmethod
    def _publish(path: Path, packet: JsonObject, record: JsonObject) -> JsonObject:
        generation = {"packet": packet, **record}
        payload = json.dumps(generation, ensure_ascii=True, allow_nan=False)
        path.mkdir(parents=True, exist_ok=True)
        target = path / f"{packet['report_version']}.json"
        if _check_path(target, directory=False):
            raise FileExistsError(target)
        staged: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=path, prefix=".staging-", delete=False) as output:
                staged = Path(output.name)
                output.write(payload.encode("ascii"))
                output.flush()
                os.fsync(output.fileno())
            staged.replace(target)
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)
        return packet

    @staticmethod
    def _packet(
        task: JsonObject,
        evidence: JsonObject,
        evidence_version: str,
        report_number: int,
        checks: JsonObject,
    ) -> JsonObject:
        inputs = evaluate.judge_inputs(task, evidence)
        return {
            **grade_capture(task, evidence, inputs, checks),
            "evidence_version": evidence_version,
            "report_version": f"r-{report_number:012d}",
        }

    @staticmethod
    def _capture(records: list[_Generation], version: str | None = None) -> _Generation:
        for record in reversed(records):
            if "evidence" not in record:
                continue
            packet = record["packet"]
            if version is not None and packet["evidence_version"] != version:
                continue
            if evaluate.evidence_sha256(record["evidence"]) != packet["evidence_sha256"]:
                raise ValueError("Stored evidence does not match its evidence_sha256.")
            return record
        raise KeyError(version)

    @staticmethod
    def _latest_report(records: list[_Generation], capture: _Generation) -> _Generation:
        binding = capture["packet"]
        for record in reversed(records):
            packet = record["packet"]
            if packet["evidence_version"] == binding["evidence_version"]:
                if packet["evidence_sha256"] != binding["evidence_sha256"]:
                    raise ValueError("Stored report does not match its evidence_sha256.")
                return record
        raise KeyError(binding["evidence_version"])

    def append(
        self,
        run_id: str,
        task: JsonObject,
        evidence: JsonObject,
        *,
        review: JsonObject | None = None,
        terminal_summary: JsonObject | None = None,
    ) -> JsonObject:
        """Append a capture; an identical current capture is a retry, not a new version."""
        evaluate.validate_evidence(task, evidence)
        digest = evaluate.evidence_sha256(evidence)
        checks: JsonObject = {}
        if review is not None:
            if not isinstance(review, dict):
                raise ValueError("Review evidence_sha256 does not match the evidence.")
            checks = bound_checks(evaluate.judge_inputs(task, evidence), review)
        path = self._run(run_id)
        with _LOCK:
            records = self._records(path)
            if records:
                if terminal_summary is not None:
                    raise ValueError("Only the initial capture can record the execution summary.")
                capture = self._capture(records)
                evaluate.validate_evidence(task, capture["evidence"])
                if capture["packet"]["evidence_sha256"] == digest:
                    if checks:
                        return self.review(run_id, task, digest, checks)
                    return self._latest_report(records, capture)["packet"]
            version = f"e-{sum('evidence' in record for record in records) + 1:012d}"
            packet = self._packet(task, evidence, version, len(records) + 1, checks)
            record = {"evidence": evidence, "checks": checks}
            if terminal_summary is not None:
                record["terminal_summary"] = terminal_summary
            return self._publish(path, packet, record)

    def review(
        self, run_id: str, task: JsonObject, evidence_sha256: str, checks: JsonObject
    ) -> JsonObject:
        """Merge submitted checks for the current digest without deleting unspecified checks."""
        path = self._run(run_id)
        with _LOCK:
            records = self._records(path)
            capture = self._capture(records)
            evidence, binding = capture["evidence"], capture["packet"]
            if evidence_sha256 != binding["evidence_sha256"]:
                raise StaleEvidenceError("Review evidence_sha256 is stale; refresh the run.")
            evaluate.validate_evidence(task, evidence)
            previous = self._latest_report(records, capture)
            validated = _validate_checks(evaluate.judge_inputs(task, evidence), checks)
            merged = previous["checks"] | validated
            if merged == previous["checks"]:
                return previous["packet"]
            packet = self._packet(
                task, evidence, binding["evidence_version"], len(records) + 1, merged
            )
            return self._publish(path, packet, {"checks": merged})

    def regrade(self, run_id: str, task: JsonObject, evidence_version: str) -> JsonObject:
        """Always publish a report for this capture, without changing the current capture."""
        _check_version(evidence_version, _EVIDENCE_VERSION, "evidence_version")
        path = self._run(run_id)
        with _LOCK:
            records = self._records(path)
            capture = self._capture(records, evidence_version)
            evidence = capture["evidence"]
            evaluate.validate_evidence(task, evidence)
            checks = self._latest_report(records, capture)["checks"]
            packet = self._packet(task, evidence, evidence_version, len(records) + 1, checks)
            return self._publish(path, packet, {"checks": checks})

    def terminal_summary(self, run_id: str) -> JsonObject | None:
        """Recover execution metadata only from the digest-verified initial capture."""
        with _LOCK:
            initial = self._records(self._run(run_id))[:1]
            if not initial or "terminal_summary" not in initial[0]:
                return None
            return self._capture(initial)["terminal_summary"]

    def current(self, run_id: str) -> JsonObject | None:
        with _LOCK:
            records = self._records(self._run(run_id))
            return (
                self._latest_report(records, self._capture(records))["packet"] if records else None
            )

    def evidence(self, run_id: str, version: str | None = None) -> JsonObject:
        if version is not None:
            _check_version(version, _EVIDENCE_VERSION, "evidence_version")
        with _LOCK:
            return self._capture(self._records(self._run(run_id)), version)["evidence"]

    def report(self, run_id: str, report_version: str) -> JsonObject:
        """Read a specific immutable report packet, including its evidence binding."""
        _check_version(report_version, _REPORT_VERSION, "report_version")
        with _LOCK:
            records = self._records(self._run(run_id))
            for record in records:
                packet = record["packet"]
                if packet["report_version"] == report_version:
                    capture = self._capture(records, packet["evidence_version"])
                    if packet["evidence_sha256"] != capture["packet"]["evidence_sha256"]:
                        raise ValueError("Stored report does not match its evidence_sha256.")
                    return packet
        raise KeyError(report_version)

    def versions(self, run_id: str) -> dict[str, list[str]]:
        with _LOCK:
            records = self._records(self._run(run_id))
            return {
                "evidence_versions": [
                    r["packet"]["evidence_version"] for r in records if "evidence" in r
                ],
                "report_versions": [r["packet"]["report_version"] for r in records],
            }
