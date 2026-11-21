"""Run the frozen T01 r1 through the isolated product workspace chat API."""

import hashlib
import hmac
import json
import math
import re
import stat
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from eval.benchmark.evaluate import JsonObject, evidence_sha256
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import URL, InvalidURL, Response

from hastings.application.ports.credentials import CredentialStore
from hastings.infrastructure.credentials import build_credential_store
from hastings.library.modules import ModuleLibrary
from hastings.library.storage import LibraryStorage
from hastings.server.__main__ import load_provider_book
from hastings.server.file_app import (
    configured_limits_factory,
    configured_provider_factory,
    create_workspace_app,
)
from hastings.server.model_connections import ModelConnectionService
from hastings.server.provider_config import DEFAULT_CONFIG_PATH
from hastings.workspace.files import STRUCTURE_PATH
from hastings.workspace.module_assembly import ASSEMBLY_PATH, AssemblyStore
from hastings.workspace.state import RecentWorkspaces

# Pin content as well as identity so editing the task in-place cannot enable a new workflow.
_T01_R1 = "06712bbb9cc85777ff05989f1a122d4c25fe9656a3ee22473336197b9bd7e96e"
_UNSUPPORTED = "Runner supports only the unchanged T01 r1 task definition."
_WORKSPACES = "/api/file-workspaces"
_BOOK = "雪山温泉旅馆"
_BAD_PATH = re.compile(r'[\\:<>"|?*\x00-\x1f]')
_PROVIDER_ERRORS = ("MODEL_CALL_FAILED", "MODEL_NOT_CONFIGURED")
_TERMINAL = ("completed", "failed", "interrupted", "stopped", "partial", "needs_input")
_TRACE_LABELS = {
    "loop": "模型回合",
    "model": "模型请求",
    "tool_call": "工具调用",
    "gate": "规则检查",
    "retrieval": "装配写作上下文",
    "write": "文件变更",
    "reply": "助手回复",
}
_TRACE_TOOLS = ("set_task_scope", "read_structure", "write_structure", "ask_author")
_TRACE_STATUSES = _TERMINAL + (
    "running",
    "recovered",
    "completed_with_errors",
    "passed",
    "blocked",
    "truncated",
)
_TRACE_ERRORS = _PROVIDER_ERRORS + (
    "STRUCTURE_MARKER_REQUIRED",
    "STRUCTURE_BUDGET_CONFLICT",
    "TASK_SCOPE_REQUIRED_CONFLICT",
)


class _Unavailable(RuntimeError):
    """Only static, archive-safe runner messages may be put in this exception."""


class _ReadOnlyCredentials:
    """Allow configured keyring references without product plaintext-key migration."""

    def __init__(self, store: CredentialStore) -> None:
        self.store = store

    def load(self, ref: str) -> str | None:
        return self.store.load(ref)

    def save(self, ref: str, secret: str) -> None:
        raise PermissionError("Benchmark credentials are read-only.")

    def delete(self, ref: str) -> None:
        raise PermissionError("Benchmark credentials are read-only.")


def _supported(task: JsonObject) -> bool:
    try:
        return evidence_sha256(task) == _T01_R1
    except (TypeError, ValueError):
        return False


def _invalid(reason: str) -> JsonObject:
    return {"valid": False, "reason": reason}


def _app(config: Path | None, root: Path, approval_key: bytes | None) -> tuple[FastAPI, str | None]:
    config_path = (config if config is not None else DEFAULT_CONFIG_PATH).expanduser()
    try:
        credentials = _ReadOnlyCredentials(build_credential_store())
        book, _, _ = load_provider_book(config_path, None, credentials)
    except Exception:
        raise _Unavailable("Cannot load product model configuration or credentials.") from None
    chosen = book.selection()
    profile = book.profile(chosen.connection_id) if chosen.connection_id else None
    if profile is not None and profile.auth_method == "subscription":
        # Refresh can rotate a shared refresh token on the provider, even without local writes.
        raise _Unavailable("Select an API-key connection; subscription auth is not isolated.")
    if profile is not None and chosen.available:
        try:
            endpoint = URL(profile.base_url)
        except InvalidURL:
            endpoint = URL("")
        if not endpoint.is_absolute_url or endpoint.scheme not in ("http", "https"):
            raise _Unavailable("Product model endpoint is not a valid HTTP(S) URL.")
    token = None
    if approval_key is not None and profile is not None and chosen.available:
        # Only the process-keyed MAC leaves this function, never raw route
        # settings or an unkeyed digest of the materialized credentials.
        payload = json.dumps(
            [
                profile.model_dump(mode="json", exclude={"api_key"}),
                profile.resolved_api_key(),
                chosen.model,
                chosen.thinking_level,
                book.project_input_limit,
            ],
            sort_keys=True,
        )
        token = hmac.digest(approval_key, payload.encode(), "sha256").hex()
    state = root / "state"
    library = root / "modules-library"
    library.mkdir()
    app = create_workspace_app(
        workspaces=RecentWorkspaces(state),
        provider_factory=configured_provider_factory(book),
        limits_factory=configured_limits_factory(book),
        model_service=ModelConnectionService(
            book=book, config_path=config_path, credentials=credentials
        ),
        library=ModuleLibrary(library, cache_file=state / "library-catalog.json"),
    )
    return app, token


def _json(response: Response) -> JsonObject:
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError("Expected a product response object.")
    return body


def _raise_walk_error(error: OSError) -> None:
    raise error


def _snapshot(book: Path) -> tuple[dict[str, str], bytes | None]:
    # The navigation catalog is not a complete inventory (e.g. it omits STYLE.md).
    # Ignore Git internals and the assembly manifest/lock (captured separately).
    if not book.is_dir() or book.is_symlink() or book.is_junction():
        raise OSError("Invalid workspace root.")
    digests: dict[str, str] = {}
    structure = None
    for directory, dirs, files in book.walk(on_error=_raise_walk_error):
        if directory == book and ".git" in dirs:
            dirs.remove(".git")
        if any((directory / name).is_junction() for name in dirs):
            raise OSError("Unexpected workspace junction.")
        for name in files:
            path = directory / name
            relative = path.relative_to(book).as_posix()
            if relative in (ASSEMBLY_PATH, ".clueledger/.assembly.lock"):
                continue
            if _BAD_PATH.search(relative) or not stat.S_ISREG(path.lstat().st_mode):
                raise OSError("Cannot snapshot non-regular or unsupported workspace path.")
            data = path.read_bytes()
            digests[relative] = hashlib.sha256(data).hexdigest()
            if relative == STRUCTURE_PATH:
                structure = data
    return dict(sorted(digests.items())), structure


def _library_ids(library: Path) -> list[str]:
    if not stat.S_ISDIR(library.lstat().st_mode) or library.is_junction():
        raise OSError("Invalid workspace library.")
    base = library / "modules"
    try:
        mode = base.lstat().st_mode
    except FileNotFoundError:
        return []  # The product creates the store lazily.
    if not stat.S_ISDIR(mode) or base.is_junction():
        raise OSError("Invalid workspace library.")
    return list(LibraryStorage(library).module_ids())


def _binding_ids(book: Path) -> list[str]:
    manifest = book / ASSEMBLY_PATH
    for path in (manifest.parent, manifest):
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            return []  # No assembly is a valid empty state.
        expected = stat.S_ISREG if path == manifest else stat.S_ISDIR
        if not expected(mode) or path.is_junction():
            raise OSError("Invalid workspace assembly.")
    return [binding.id for binding in AssemblyStore(book).read().bindings]


def _preflight(client: TestClient, root: Path) -> tuple[JsonObject, str, dict[str, str]]:
    try:
        selected = _json(client.get("/api/model-connections"))["selection"]
        connection_id, model = selected["connection_id"], selected["model"]
    except Exception:
        raise _Unavailable("Cannot read the product's model selection.") from None
    if selected.get("available") is not True or not all(
        isinstance(value, str) and value.strip() for value in (connection_id, model)
    ):
        raise _Unavailable("No available model selected in the product configuration.")

    book = root / _BOOK
    try:
        workspace = _json(client.post(_WORKSPACES, json={"path": str(book), "create": True}))
        workspace_id = workspace["id"]
        base = f"{_WORKSPACES}/{workspace_id}"
        _json(client.get(base + "/files"))
        history = _json(client.get(base + "/conversations"))
        before, _ = _snapshot(book)
        if (
            before.keys() - {".gitignore"}
            or history["items"] != []
            or history["active_turn"] is not None
            or _library_ids(root / "modules-library")
            or _binding_ids(book)
        ):
            raise _Unavailable("Expected a clean work home, conversation, and module library.")
    except _Unavailable:
        raise
    except Exception:
        raise _Unavailable("Cannot create or inspect the isolated work and module state.") from None
    return {"valid": True, "connection_id": connection_id, "model": model}, workspace_id, before


def prepare(
    task: JsonObject, config: Path | None, *, approval_key: bytes | None = None
) -> JsonObject:
    """Check an expendable product fixture without starting a conversation or model probe."""
    if not _supported(task):
        return _invalid(_UNSUPPORTED)
    try:
        with TemporaryDirectory(prefix="clueledger-preflight-") as temporary:
            root = Path(temporary)
            app, token = _app(config, root, approval_key)
            with TestClient(app) as client:
                selection, _, _ = _preflight(client, root)
                if token is not None:
                    selection["approval_token"] = token
                return selection
    except _Unavailable as exc:
        return _invalid(str(exc))
    except Exception:
        return _invalid("Cannot initialize or clean up the isolated product preflight.")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _iso(value: object) -> str | None:
    if not isinstance(value, str) or not re.fullmatch(r"[\dT:Z+.-]{19,40}", value, re.ASCII):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return value if parsed.utcoffset() is not None else None
    except ValueError:
        return None


def _count(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _event_id(value: object) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{32}", value) else None


def _trace(payload: JsonObject, turn_id: str) -> list[JsonObject]:
    if payload["turn"]["turn_id"] != turn_id:
        raise ValueError("Unexpected product trace turn.")
    events = [row for row in payload["events"] if row.get("kind") in _TRACE_LABELS]
    # Product span IDs can contain provider-supplied tool call IDs. Keep linkage, not those IDs.
    ids = {row["span_id"]: f"span-{index}" for index, row in enumerate(events, 1)}
    return [
        {
            "span_id": ids[row["span_id"]],
            "parent_span_id": ids.get(row.get("parent_span_id")),
            "retry_of_span_id": ids.get(row.get("retry_of_span_id")),
            "kind": row["kind"],
            "name": (
                row["name"]
                if row["kind"] == "tool_call" and row.get("name") in _TRACE_TOOLS
                else _TRACE_LABELS[row["kind"]]
            ),
            "status": row["status"] if row.get("status") in _TRACE_STATUSES else "unknown",
            "point": row.get("point") is True,
            "start_offset_ms": _count(row.get("start_offset_ms")),
            "duration_ms": _count(row.get("duration_ms")),
            "started_at": _iso(row.get("started_at")),
            "finished_at": _iso(row.get("finished_at")),
            "start_event_id": _event_id(row.get("start_event_id")),
            "finish_event_id": _event_id(row.get("finish_event_id")),
            "error_code": row["error_code"] if row.get("error_code") in _TRACE_ERRORS else None,
            "summary": "",
            "usage": None,
        }
        for row in events
    ]


def _await_terminal(
    client: TestClient, conversation: str, turn_id: str, deadline: float
) -> tuple[list[JsonObject], JsonObject] | None:
    while True:
        rows = [
            row
            for row in _json(client.get(conversation + "/messages"))["events"]
            if row.get("turn_id") == turn_id
        ]
        terminal = next((row for row in reversed(rows) if row.get("type") == "run_finished"), None)
        if terminal is not None and terminal.get("status") in _TERMINAL:
            return rows, terminal
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(0.1, remaining))


def _start_turn(
    client: TestClient, base: str, author_text: str, run: JsonObject
) -> tuple[str, str, str | None]:
    response = client.post(
        base + "/conversations",
        # Fresh work-home focus: no selected file, references, task text, or grader metadata.
        json={"text": author_text, "request_key": "opening-request", "purpose": "chat"},
    )
    accepted = _json(response)
    if response.status_code != 202:
        raise ValueError("Expected product turn acceptance.")
    conversation_id, turn_id, operation_id = (
        accepted["conversation_id"],
        accepted["turn_id"],
        accepted["operation_id"],
    )
    run.update(conversation_id=conversation_id, turn_id=turn_id, operation_id=operation_id)
    return (
        base + f"/conversations/{conversation_id}",
        operation_id,
        _iso(accepted.get("started_at")),
    )


def _collect(
    root: Path, evidence: JsonObject, rows: list[JsonObject], terminal: JsonObject
) -> list[str]:
    run = evidence["run"]
    run["status"] = terminal["status"]
    if started := next((row for row in rows if row.get("type") == "turn_started"), None):
        for key in ("connection_id", "model"):
            if isinstance(started.get(key), str) and started[key].strip():
                run[key] = started[key]
    if run["status"] == "failed":
        for error in _PROVIDER_ERRORS:
            if any(row.get("type") == "error" and row.get("code") == error for row in rows):
                evidence["execution_error"] = error
                break

    book = root / _BOOK
    missing = []
    try:
        after, structure = _snapshot(book)
        evidence["workspace_after"] = after
    except Exception:
        missing.append("workspace_after")
    else:
        if structure is not None:
            try:
                evidence["structure_text"] = structure.decode("utf-8")
            except UnicodeError:
                missing.append("structure_text")

    modules = {}
    readers: tuple[tuple[str, Callable[[], list[str]]], ...] = (
        ("library_module_ids", lambda: _library_ids(root / "modules-library")),
        ("binding_ids", lambda: _binding_ids(book)),
    )
    for key, read in readers:
        try:
            modules[key] = read()
        except Exception:
            missing.append(key)
    if modules:
        evidence["modules_after"] = modules

    replies = [row.get("text") for row in rows if row.get("type") == "assistant_message"]
    public = [text for text in replies if isinstance(text, str)]
    if public and len(public) == len(replies):
        evidence["assistant_reply"] = "\n\n".join(public)
    else:
        missing.append("assistant_reply")
    return missing


def execute(
    task: JsonObject,
    config: Path | None,
    run_dir: Path,
    *,
    timeout: float = 170,
    approval: tuple[bytes, str] | None = None,
) -> JsonObject:
    """Execute one turn and return the capture without writing an evidence archive.

    ``run_dir/isolation`` is exclusive and private to this trial. It retains the
    product's raw conversation logs, attachments, work, and module state, even on
    failure. Keep it out of exports/version control and delete the entire subtree
    after the client closes when raw reinspection is no longer required.
    """
    scenario = task.get("scenario")
    identity = (
        {"task_id": scenario.get("id"), "task_revision": scenario.get("revision")}
        if isinstance(scenario, dict)
        else {}
    )
    evidence: JsonObject = {**identity}
    milestones: list[JsonObject] = []
    result: JsonObject = {
        "evidence": evidence,
        "trace": [],
        "milestones": milestones,
        "started_at": None,
    }

    def mark(kind: str, summary: str, at: str | None = None) -> None:
        milestones.append({"at": at or _now(), "type": kind, "summary": summary})

    mark("preflight_started", "Checking task, model selection, and isolated work fixture.")
    approved = False
    try:
        if not _supported(task):
            raise _Unavailable(_UNSUPPORTED)
        if not math.isfinite(timeout) or timeout <= 0:
            raise _Unavailable("Timeout must be a finite positive number.")
        root = run_dir.expanduser().resolve() / "isolation"
        try:
            root.mkdir(mode=0o700, parents=True, exist_ok=False)
        except OSError:
            raise _Unavailable("Cannot reserve a fresh isolation directory for this run.") from None
        app, token = _app(config, root, approval[0] if approval else None)
        # The same app/book verified here builds the product's per-turn model
        # snapshot. Nothing reloads the configuration after this check.
        if approval is not None and (token is None or not hmac.compare_digest(token, approval[1])):
            raise _Unavailable("Model configuration changed after approval; run preflight again.")
        with TestClient(app) as client:
            selection, workspace_id, before = _preflight(client, root)
            approved = True
            evidence.update(preflight={"valid": True}, workspace_before=before)
            run = evidence["run"] = {
                "workspace_id": workspace_id,
                "model": selection["model"],
                "connection_id": selection["connection_id"],
                "case_id": "main",
                "step_id": "start_work",
            }
            mark("preflight_passed", "Empty work, conversation, and module library confirmed.")
            try:
                author_text = task["scenario"]["messages"]["request"]
                conversation, operation_id, started_at = _start_turn(
                    client,
                    f"{_WORKSPACES}/{workspace_id}",
                    author_text,
                    run,
                )
            except Exception:
                evidence["execution_error"] = "TURN_NOT_ACCEPTED"
                mark("turn_not_accepted", "Product did not confirm acceptance of the opening turn.")
                return result

            evidence["author_message"] = author_text
            result["started_at"] = started_at
            mark("turn_started", "Product accepted the single author message.", started_at)
            turn_id = run["turn_id"]
            try:
                capture = _await_terminal(client, conversation, turn_id, time.monotonic() + timeout)
            except Exception:
                capture = None
                mark("terminal_unavailable", "Cannot read terminal conversation evidence.")
            else:
                if capture is None:
                    mark("timed_out", "Timed out waiting for the product turn terminal event.")

            if capture is None:
                run["status"] = "unknown"
                # Revoke writes and cancel the provider using the product control route.
                with suppress(Exception):
                    _json(
                        client.post(
                            f"/api/operations/{operation_id}/pause",
                            json={
                                "request_key": "benchmark-stop-incomplete",
                            },
                        )
                    )
                    mark(
                        "pause_requested", "Requested product pause after losing terminal evidence."
                    )
                    drained = _await_terminal(client, conversation, turn_id, time.monotonic() + 2)
                    if drained is not None:
                        rows, terminal = drained
                        if terminal["status"] not in ("stopped", "interrupted"):
                            capture = rows, terminal
                        else:
                            run["status"] = terminal["status"]
                            mark("turn_finished", f"Product terminal status: {terminal['status']}.")

            if capture is not None:
                rows, terminal = capture
                started = next((row for row in rows if row.get("type") == "turn_started"), {})
                result["started_at"] = _iso(started.get("at")) or started_at
                mark(
                    "turn_finished",
                    f"Product terminal status: {terminal['status']}.",
                    _iso(terminal.get("at")),
                )
                missing = _collect(root, evidence, rows, terminal)
                mark(
                    "evidence_partial" if missing else "evidence_collected",
                    "Unavailable evidence: " + ", ".join(missing) + "."
                    if missing
                    else "Captured saved work, module state, and public reply.",
                )
            try:
                result["trace"] = _trace(
                    _json(client.get(f"{conversation}/turns/{turn_id}/trace")), turn_id
                )
            except Exception:
                mark("trace_unavailable", "Product Turn Trace could not be captured.")
    except _Unavailable as exc:
        evidence["preflight"] = _invalid(str(exc))
        mark("preflight_failed", str(exc))
    except Exception:
        if not approved:
            reason = "Cannot initialize isolated product execution."
            evidence["preflight"] = _invalid(reason)
            mark("preflight_failed", reason)
        else:
            mark("capture_interrupted", "Product execution or capture ended unexpectedly.")
    return result
