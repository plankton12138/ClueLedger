"""试验编排：复制作品、HTTP 跑步骤、采证落盘。不导入产品业务模块。"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from evaluation.harness.checks import AttemptEvidence, AttemptGrade, grade_attempt
from evaluation.harness.client import AcceptedTurn, EvalClient, EvalClientError
from evaluation.harness.evidence import (
    attempt_paths,
    extract_commit_receipts,
    extract_rejected_commits,
    flatten_model_input,
    snapshot_files,
    write_evidence_pack,
)
from evaluation.harness.schema import Case
from evaluation.harness.stages import compute_stages

_SKIP_NAMES = {".git", "__pycache__", ".DS_Store"}


@dataclass
class CallBudget:
    max_requests: int
    used: int = 0

    def remaining(self) -> int:
        return max(0, self.max_requests - self.used)

    def note(self, count: int = 1) -> None:
        self.used += count

    def exhausted(self) -> bool:
        return self.used >= self.max_requests


@dataclass(frozen=True)
class AttemptResult:
    case_id: str
    attempt: int
    status: str  # completed | infrastructure_error | not_run
    grade: AttemptGrade | None
    evidence: AttemptEvidence | None
    events: tuple[dict[str, Any], ...] = ()
    work_dir: Path | None = None
    attempt_dir: Path | None = None
    error: str = ""
    model_requests: int = 0
    stages: tuple[dict[str, Any], ...] = ()
    first_failed_stage: str | None = None


def prepare_work_copy(source: Path, dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source,
        dest,
        ignore=shutil.ignore_patterns(*_SKIP_NAMES),
    )
    return dest


def count_model_requests(events: list[dict[str, Any]]) -> int:
    return sum(1 for event in events if event.get("type") == "model_request_finished")


def wait_for_turn(
    client: EvalClient,
    accepted: AcceptedTurn,
    *,
    timeout_s: float = 300.0,
    poll_s: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout_s
    after = 0
    collected: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        batch = client.poll_events(
            accepted.workspace_id,
            accepted.conversation_id,
            after_seq=after,
        )
        if batch:
            collected.extend(batch)
            after = max(int(event.get("seq") or after) for event in batch)
        if any(
            event.get("type") == "run_finished" and event.get("turn_id") == accepted.turn_id
            for event in collected
        ):
            return collected
        if any(event.get("type") == "run_finished" for event in collected):
            return collected
        sleep(poll_s)
    raise EvalClientError(f"等待轮次结束超时（{timeout_s}s），已收 {len(collected)} 条事件")


def _collect_inputs(
    client: EvalClient, workspace_id: str, events: list[dict[str, Any]]
) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    for event in events:
        if event.get("type") != "model_request_started":
            continue
        key = event.get("input_detail")
        operation = event.get("operation_id")
        if not isinstance(key, str) or not key:
            continue
        if not isinstance(operation, str) or not operation:
            operation = key
        if operation in inputs:
            continue
        try:
            inputs[operation] = client.read_attachment(workspace_id, key)
        except EvalClientError:
            continue
    return inputs


def _model_inputs_before_candidate(
    events: list[dict[str, Any]], inputs: dict[str, Any]
) -> list[str] | None:
    if not inputs:
        return None
    texts: list[str] = []
    for event in events:
        if event.get("type") == "story_candidate_committed":
            break
        if event.get("type") != "model_request_started":
            continue
        operation = str(event.get("operation_id") or "")
        if operation and operation in inputs:
            flat = flatten_model_input(inputs[operation])
            if flat:
                texts.append(flat)
    return texts if texts else None


def build_evidence(
    *,
    before: dict[str, str],
    after: dict[str, str],
    events: list[dict[str, Any]],
    model_inputs_before_candidate: list[str] | None = None,
) -> AttemptEvidence:
    return AttemptEvidence(
        before_files=before,
        after_files=after,
        commit_receipts=extract_commit_receipts(events),
        rejected_commits=extract_rejected_commits(events),
        model_inputs_before_candidate=model_inputs_before_candidate,
        events=tuple(events),
    )


def _with_stages(
    case: Case, evidence: AttemptEvidence | None, result: AttemptResult
) -> AttemptResult:
    if evidence is None:
        return result
    stages, first = compute_stages(case, evidence)
    return AttemptResult(
        case_id=result.case_id,
        attempt=result.attempt,
        status=result.status,
        grade=result.grade,
        evidence=result.evidence,
        events=result.events,
        work_dir=result.work_dir,
        attempt_dir=result.attempt_dir,
        error=result.error,
        model_requests=result.model_requests,
        stages=tuple(stages),
        first_failed_stage=first,
    )


@dataclass
class AttemptRunner:
    client: EvalClient
    attempts_root: Path
    budget: CallBudget
    timeout_s: float = 300.0
    poll_s: float = 1.0
    sleep: Callable[[float], None] = field(default=time.sleep, repr=False)

    def run(self, case: Case, *, attempt: int = 1) -> AttemptResult:
        if self.budget.exhausted():
            return AttemptResult(
                case_id=case.id,
                attempt=attempt,
                status="not_run",
                grade=None,
                evidence=None,
                error="模型请求预算已耗尽",
            )
        attempt_dir = self.attempts_root / f"{case.id}--{attempt}"
        paths = attempt_paths(attempt_dir)
        work_dir = paths.work
        before: dict[str, str] = {}
        all_events: list[dict[str, Any]] = []
        traces: dict[str, Any] = {}
        inputs: dict[str, Any] = {}
        turn_meta: list[dict[str, Any]] = []
        workspace_id = ""
        conversation_id: str | None = None
        try:
            prepare_work_copy(case.work_dir, work_dir)
            before = snapshot_files(work_dir)
            workspace_id = self.client.open_workspace(str(work_dir))
            self.client.set_capture_inputs(workspace_id, True)
            for step in case.steps:
                if self.budget.exhausted():
                    self._persist(
                        attempt_dir,
                        case=case,
                        attempt=attempt,
                        workspace_id=workspace_id,
                        conversation_id=conversation_id,
                        turn_meta=turn_meta,
                        before=before,
                        events=all_events,
                        traces=traces,
                        inputs=inputs,
                    )
                    after = snapshot_files(work_dir)
                    evidence = build_evidence(
                        before=before,
                        after=after,
                        events=all_events,
                        model_inputs_before_candidate=_model_inputs_before_candidate(
                            all_events, inputs
                        ),
                    )
                    return _with_stages(
                        case,
                        evidence,
                        AttemptResult(
                            case_id=case.id,
                            attempt=attempt,
                            status="not_run",
                            grade=None,
                            evidence=evidence,
                            events=tuple(all_events),
                            work_dir=work_dir,
                            attempt_dir=attempt_dir,
                            error="步骤未跑完：预算耗尽",
                            model_requests=count_model_requests(all_events),
                        ),
                    )
                accepted = self.client.start_turn(
                    workspace_id,
                    text=step.author,
                    selected_path=step.focus,
                    conversation_id=conversation_id,
                )
                conversation_id = accepted.conversation_id
                events = wait_for_turn(
                    self.client,
                    accepted,
                    timeout_s=self.timeout_s,
                    poll_s=self.poll_s,
                    sleep=self.sleep,
                )
                all_events.extend(events)
                turn_meta.append(
                    {
                        "turn_id": accepted.turn_id,
                        "author": step.author,
                        "focus": step.focus,
                    }
                )
                try:
                    traces[accepted.turn_id] = self.client.read_turn_trace(
                        workspace_id, accepted.conversation_id, accepted.turn_id
                    )
                except EvalClientError:
                    traces[accepted.turn_id] = {
                        "error": "TRACE_UNAVAILABLE",
                        "turn_id": accepted.turn_id,
                    }
                inputs.update(_collect_inputs(self.client, workspace_id, events))
                used = count_model_requests(events)
                if used:
                    self.budget.note(used)
                else:
                    self.budget.note(1)
            after = snapshot_files(work_dir)
            evidence = build_evidence(
                before=before,
                after=after,
                events=all_events,
                model_inputs_before_candidate=_model_inputs_before_candidate(
                    all_events, inputs
                ),
            )
            grade = grade_attempt(case, evidence)
            self._persist(
                attempt_dir,
                case=case,
                attempt=attempt,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                turn_meta=turn_meta,
                before=before,
                events=all_events,
                traces=traces,
                inputs=inputs,
            )
            return _with_stages(
                case,
                evidence,
                AttemptResult(
                    case_id=case.id,
                    attempt=attempt,
                    status="completed",
                    grade=grade,
                    evidence=evidence,
                    events=tuple(all_events),
                    work_dir=work_dir,
                    attempt_dir=attempt_dir,
                    model_requests=count_model_requests(all_events),
                ),
            )
        except EvalClientError as exc:
            after = snapshot_files(work_dir) if work_dir.exists() else {}
            evidence = build_evidence(
                before=before,
                after=after,
                events=all_events,
                model_inputs_before_candidate=_model_inputs_before_candidate(
                    all_events, inputs
                ),
            )
            if work_dir.exists() or before:
                self._persist(
                    attempt_dir,
                    case=case,
                    attempt=attempt,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    turn_meta=turn_meta,
                    before=before,
                    events=all_events,
                    traces=traces,
                    inputs=inputs,
                )
            return _with_stages(
                case,
                evidence,
                AttemptResult(
                    case_id=case.id,
                    attempt=attempt,
                    status="infrastructure_error",
                    grade=None,
                    evidence=evidence,
                    events=tuple(all_events),
                    work_dir=work_dir if work_dir.exists() else None,
                    attempt_dir=attempt_dir if attempt_dir.exists() else None,
                    error=str(exc),
                ),
            )

    def _persist(
        self,
        attempt_dir: Path,
        *,
        case: Case,
        attempt: int,
        workspace_id: str,
        conversation_id: str | None,
        turn_meta: list[dict[str, Any]],
        before: dict[str, str],
        events: list[dict[str, Any]],
        traces: dict[str, Any],
        inputs: dict[str, Any],
    ) -> None:
        write_evidence_pack(
            attempt_dir,
            meta={
                "case_id": case.id,
                "attempt": attempt,
                "workspace_id": workspace_id,
                "conversation_id": conversation_id,
                "turns": turn_meta,
            },
            before=before,
            events=events,
            traces=traces,
            inputs=inputs,
        )
