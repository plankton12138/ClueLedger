"""试验证据包：落盘、差异、离线重载。不导入产品业务模块。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evaluation.harness.checks import AttemptEvidence

_SKIP_NAMES = {".git", "__pycache__", ".DS_Store"}


@dataclass(frozen=True)
class AttemptPaths:
    root: Path
    work: Path
    evidence: Path
    meta: Path
    before: Path
    diff: Path
    events: Path
    traces: Path
    inputs: Path


def snapshot_files(root: Path) -> dict[str, str]:
    """相对作品根的正斜杠路径 → 文本内容（只采 utf-8 文本文件）。"""
    files: dict[str, str] = {}
    if not root.is_dir():
        return files
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in _SKIP_NAMES for part in path.parts):
            continue
        rel = path.relative_to(root).as_posix()
        try:
            files[rel] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
    return files


def extract_commit_receipts(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for event in events:
        kind = str(event.get("type", ""))
        if kind == "story_candidate_committed":
            path = event.get("path") or event.get("target_path")
            receipts.append({"path": path, "via": "candidate", "raw_type": kind})
        elif kind == "run_finished":
            recording = str(event.get("recording", ""))
            paths = event.get("changed_paths") or []
            if recording == "committed" and isinstance(paths, list):
                for path in paths:
                    receipts.append(
                        {"path": path, "via": "candidate", "raw_type": kind}
                    )
    return receipts


def extract_rejected_commits(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rejected: list[dict[str, Any]] = []
    for event in events:
        kind = str(event.get("type", ""))
        code = str(event.get("error_code") or event.get("code") or "")
        if kind in {"error", "tool_finished", "gate_decided"} and code in {
            "VERSION_CONFLICT",
            "STALE_BASELINE",
            "VERSION_MISMATCH",
        }:
            rejected.append(
                {
                    "error_code": code,
                    "path": event.get("path") or event.get("where") or "",
                    "raw_type": kind,
                }
            )
        if kind == "tool_finished" and event.get("ok") is False:
            summary = str(event.get("summary") or event.get("message") or "")
            if "VERSION" in summary.upper() or "版本" in summary:
                rejected.append(
                    {
                        "error_code": code or "VERSION_CONFLICT",
                        "path": event.get("path") or "",
                        "raw_type": kind,
                        "summary": summary,
                    }
                )
    return rejected


def attempt_paths(attempt_dir: Path) -> AttemptPaths:
    evidence = attempt_dir / "evidence"
    return AttemptPaths(
        root=attempt_dir,
        work=attempt_dir / "work",
        evidence=evidence,
        meta=attempt_dir / "meta.json",
        before=evidence / "before.json",
        diff=evidence / "diff.json",
        events=evidence / "events.jsonl",
        traces=evidence / "traces",
        inputs=evidence / "inputs",
    )


def file_diff(before: dict[str, str], after: dict[str, str]) -> dict[str, Any]:
    before_keys = set(before)
    after_keys = set(after)
    changed = sorted(k for k in before_keys & after_keys if before[k] != after[k])
    unchanged = sorted(k for k in before_keys & after_keys if before[k] == after[k])
    return {
        "changed": changed,
        "unchanged": unchanged,
        "added": sorted(after_keys - before_keys),
        "removed": sorted(before_keys - after_keys),
    }


def flatten_model_input(payload: Any) -> str:
    """把模型输入附件压成可检索文本，供 chapter_in_model_input 等检查使用。"""
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, (int, float, bool)):
        return str(payload)
    if isinstance(payload, list):
        return "\n".join(flatten_model_input(item) for item in payload)
    if isinstance(payload, dict):
        parts: list[str] = []
        system = payload.get("system")
        if isinstance(system, str) and system:
            parts.append(system)
        messages = payload.get("messages")
        if isinstance(messages, list):
            for message in messages:
                parts.append(flatten_model_input(message))
        elif "content" in payload:
            parts.append(flatten_model_input(payload.get("content")))
        elif "text" in payload:
            parts.append(flatten_model_input(payload.get("text")))
        else:
            for key, value in payload.items():
                if key in {"tools", "model"}:
                    continue
                parts.append(flatten_model_input(value))
        return "\n".join(part for part in parts if part)
    return str(payload)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_evidence_pack(
    attempt_dir: Path,
    *,
    meta: dict[str, Any],
    before: dict[str, str],
    events: list[dict[str, Any]],
    traces: dict[str, Any],
    inputs: dict[str, Any],
) -> AttemptPaths:
    paths = attempt_paths(attempt_dir)
    paths.evidence.mkdir(parents=True, exist_ok=True)
    paths.traces.mkdir(parents=True, exist_ok=True)
    paths.inputs.mkdir(parents=True, exist_ok=True)
    after = snapshot_files(paths.work) if paths.work.is_dir() else {}
    _write_json(paths.meta, meta)
    _write_json(paths.before, before)
    _write_json(paths.diff, file_diff(before, after))
    paths.events.write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
        encoding="utf-8",
    )
    for turn_id, trace in traces.items():
        safe = str(turn_id).replace("/", "_").replace("\\", "_")
        _write_json(paths.traces / f"{safe}.json", trace)
    for operation_id, payload in inputs.items():
        safe = str(operation_id).replace("/", "_").replace("\\", "_")
        _write_json(paths.inputs / f"{safe}.json", payload)
    return paths


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        item = json.loads(line)
        if isinstance(item, dict):
            events.append(item)
    return events


def _inputs_before_candidate(
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


def load_attempt_evidence(attempt_dir: Path) -> AttemptEvidence:
    paths = attempt_paths(attempt_dir)
    before = _read_json(paths.before) if paths.before.is_file() else {}
    if not isinstance(before, dict):
        before = {}
    before_files = {str(k): str(v) for k, v in before.items()}
    after_files = snapshot_files(paths.work) if paths.work.is_dir() else {}
    events = _read_events(paths.events)
    inputs: dict[str, Any] = {}
    if paths.inputs.is_dir():
        for path in sorted(paths.inputs.glob("*.json")):
            inputs[path.stem] = _read_json(path)
    scope_regs: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") != "tool_started":
            continue
        if str(event.get("tool") or "") not in {
            "declare_scope",
            "register_scope",
            "declare_edit_scope",
        }:
            continue
        args = event.get("args") if isinstance(event.get("args"), dict) else {}
        paths = args.get("paths") or args.get("path") or []
        if isinstance(paths, str):
            paths = [paths]
        scope_regs.append({"paths": list(paths), "raw_type": "tool_started"})

    return AttemptEvidence(
        before_files=before_files,
        after_files=after_files,
        commit_receipts=extract_commit_receipts(events),
        rejected_commits=extract_rejected_commits(events),
        model_inputs_before_candidate=_inputs_before_candidate(events, inputs),
        scope_registrations=scope_regs or None,
        events=tuple(events),
    )
