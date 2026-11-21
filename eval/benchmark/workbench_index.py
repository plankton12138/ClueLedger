"""Read-only indexes for the local benchmark workbench."""

from __future__ import annotations

import argparse
import json
import re
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from jsonschema import Draft202012Validator, ValidationError
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

JsonObject = dict[str, Any]
BENCHMARK = Path(__file__).resolve().parent


def _unique_object(pairs: list[tuple[str, Any]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_object(path: Path) -> JsonObject:
    try:
        result = json.loads(path.read_bytes(), object_pairs_hook=_unique_object)
        if not isinstance(result, dict):
            raise ValueError("expected a JSON object")
    except ValueError as exc:
        raise ValueError(f"{path.name}: {exc}") from exc
    return result


def _check_references(task: JsonObject) -> None:
    scenario = task["scenario"]
    steps: set[tuple[str, str]] = set()
    for case_id, case in scenario["cases"].items():
        for step in case["steps"]:
            key = (case_id, step["id"])
            if key in steps:
                raise ValueError(f"duplicate step: {key}")
            steps.add(key)
            if step["message"] not in scenario["messages"]:
                raise ValueError(f"unknown message {step['message']!r} for {key}")
    required: set[tuple[str, str]] = set()
    seen: set[str] = set()
    for group in (task["code_graders"], task["llm_graders"]):
        for check_id, check in group.items():
            if check_id in seen:
                raise ValueError(f"duplicate check: {check_id}")
            seen.add(check_id)
            checkpoint = check["checkpoint"]
            key = (checkpoint["case"], checkpoint["step"])
            if key not in steps:
                raise ValueError(f"unknown checkpoint for {check_id}: {key}")
            if check["role"] == "required":
                required.add(key)
    if steps - required:
        raise ValueError(f"steps without required checks: {sorted(steps - required)}")


def _page[T](rows: list[T], offset: int, limit: int) -> tuple[T, ...]:
    if offset < 0 or limit < 1:
        raise ValueError("offset must be nonnegative and limit must be positive")
    return tuple(rows[offset : offset + limit])


@dataclass(frozen=True, order=True)
class TaskRef:
    library_id: str
    task_id: str
    revision: int


@dataclass(frozen=True)
class TaskSummary(TaskRef):
    title: str


class Catalog:
    def __init__(self, tasks: dict[TaskRef, JsonObject]) -> None:
        self._tasks = tasks

    @classmethod
    def load(cls, libraries: dict[str, Path]) -> Catalog:
        schema = _read_object(BENCHMARK / "task.schema.json")
        validator = Draft202012Validator(schema)
        tasks: dict[TaskRef, JsonObject] = {}
        for library_id, root in libraries.items():
            if not re.fullmatch(r"[a-z][a-z0-9_-]*", library_id) or not root.is_dir():
                raise ValueError(f"invalid library or missing directory: {library_id}")
            for path in sorted(root.glob("*.json")):
                if path.resolve().parent != root.resolve():
                    raise ValueError(f"task outside library: {path.name}")
                task = _read_object(path)
                try:
                    validator.validate(task)
                    _check_references(task)
                except ValidationError as exc:
                    raise ValueError(f"{library_id}/{path.name}: {exc.message}") from exc
                except ValueError as exc:
                    raise ValueError(f"{library_id}/{path.name}: {exc}") from exc
                scenario = task["scenario"]
                # JSON Schema treats 1.0 as an integer; use a canonical identity in the index.
                ref = TaskRef(library_id, scenario["id"], int(scenario["revision"]))
                if ref in tasks:
                    raise ValueError(f"duplicate task revision: {ref}")
                tasks[ref] = task
        return cls(tasks)

    def list(
        self, *, library_id: str | None = None, query: str = "", offset: int = 0, limit: int = 50
    ) -> tuple[TaskSummary, ...]:
        latest: dict[tuple[str, str], TaskSummary] = {}
        for ref in sorted(self._tasks):
            if library_id is None or ref.library_id == library_id:
                latest[ref.library_id, ref.task_id] = TaskSummary(
                    ref.library_id, ref.task_id, ref.revision, self._tasks[ref]["scenario"]["title"]
                )
        terms = query.casefold().split()
        rows = [
            row
            for row in latest.values()
            if all(term in f"{row.task_id} {row.title}".casefold() for term in terms)
        ]
        return _page(rows, offset, limit)

    def revisions(self, library_id: str, task_id: str) -> tuple[int, ...]:
        return tuple(
            ref.revision
            for ref in sorted(self._tasks, reverse=True)
            if (ref.library_id, ref.task_id) == (library_id, task_id)
        )

    def detail(self, library_id: str, task_id: str, revision: int) -> JsonObject:
        return deepcopy(self._tasks[TaskRef(library_id, task_id, revision)])


RunKind = Literal["task", "regression"]
RunTiming = Literal["started", "not_started", "unknown"]
ExecutionStatus = Literal["not_started", "running", "completed", "failed", "unknown"]
RunVerdict = Literal["pass", "fail", "inconclusive", "invalid"]
LibraryId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]*$")]
TaskId = Annotated[str, Field(pattern=r"^T[0-9]{2,}$")]


class _ArchiveModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _TaskLink(_ArchiveModel):
    library_id: LibraryId
    task_id: TaskId
    revision: Annotated[int, Field(ge=1)]

    def as_ref(self) -> TaskRef:
        return TaskRef(self.library_id, self.task_id, self.revision)


class RunSummary(_ArchiveModel):
    run_id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]+$")]
    kind: RunKind
    execution_status: ExecutionStatus
    task: _TaskLink | None = None
    started_at: Annotated[AwareDatetime, Field(strict=False)] | None = None
    verdict: RunVerdict | None = None
    title: str | None = None
    turn_id: str | None = None
    model: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _require_timestamp_string(cls, value: Any) -> Any:
        if isinstance(value, dict):
            start = value.get("started_at")
            if start is not None and (not isinstance(start, str) or "T" not in start):
                raise ValueError("started_at requires an ISO 8601 timestamp with timezone")
        return value

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if (self.task is not None) != (self.kind == "task"):
            raise ValueError("task runs require a task reference; independent regressions omit it")
        if self.execution_status == "not_started" and self.started_at is not None:
            raise ValueError("an unstarted run cannot have an execution start time")
        return self

    @property
    def timing(self) -> RunTiming:
        if self.started_at is not None:
            return "started"
        return "not_started" if self.execution_status == "not_started" else "unknown"

    def _order_key(self) -> tuple[datetime, str]:
        start = self.started_at
        return (datetime.min.replace(tzinfo=UTC) if start is None else start, self.run_id)


class _Manifest(_ArchiveModel):
    archive_version: Literal[1]
    runs: list[RunSummary]


class Archive:
    def __init__(self, runs: list[RunSummary]) -> None:
        self._runs: dict[str, RunSummary] = {}
        for run in runs:
            if run.run_id in self._runs:
                raise ValueError(f"duplicate run id: {run.run_id}")
            self._runs[run.run_id] = run

    @classmethod
    def load(cls, path: Path) -> Archive:
        return cls(_Manifest.model_validate(_read_object(path)).runs)

    def list(
        self,
        *,
        task: TaskRef | None = None,
        kind: RunKind | None = None,
        timing: RunTiming = "started",
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[RunSummary, ...]:
        rows = [
            row
            for row in self._runs.values()
            if row.timing == timing
            and (kind is None or row.kind == kind)
            and (task is None or (row.task is not None and row.task.as_ref() == task))
        ]
        return _page(sorted(rows, key=RunSummary._order_key), offset, limit)

    def detail(self, run_id: str) -> RunSummary:
        return self._runs[run_id]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="optional local archive manifest")
    parser.add_argument("--library", action="append", default=[], metavar="ID=DIRECTORY")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    libraries = {"writing": BENCHMARK / "tasks"} if not args.library else {}
    for entry in args.library:
        library_id, sep, root = entry.partition("=")
        if not sep or not root or library_id in libraries:
            parser.error(f"expected unique library ID=DIRECTORY: {entry}")
        libraries[library_id] = Path(root)
    paging = {"offset": args.offset, "limit": args.limit}
    try:
        result: JsonObject = {"tasks": [asdict(r) for r in Catalog.load(libraries).list(**paging)]}
        if args.archive is not None:
            archive = Archive.load(args.archive)
            timings: tuple[RunTiming, ...] = ("started", "not_started", "unknown")
            result["runs"] = {
                timing: [
                    row.model_dump(mode="json") for row in archive.list(timing=timing, **paging)
                ]
                for timing in timings
            }
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
