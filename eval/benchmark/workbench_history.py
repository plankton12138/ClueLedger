"""Read the known historical T01 bundle without altering its files."""

from __future__ import annotations

import json
import re
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any

from eval.benchmark.evaluate import read_json

JsonObject = dict[str, Any]
BASELINE_ID = "t_21a1fb49"
RERUN_ID = "t_b325361d"
PROVIDER_ID = "t_cdb19dd4"
REGRESSION_ID = "t_49808196"
LEGACY_DIRS = {
    PROVIDER_ID: "01-provider-error",
    BASELINE_ID: "02-completed",
    RERUN_ID: "03-postfix",
    REGRESSION_ID: "03-postfix",
}
EVIDENCE_KEYS = {
    "task_id",
    "task_revision",
    "preflight",
    "workspace_before",
    "workspace_after",
    "modules_after",
    "structure_text",
    "assistant_reply",
    "execution_error",
    "author_message",
    "prose_text",
}
TRACE_KEYS = {
    "seq",
    "event_id",
    "type",
    "at",
    "status",
    "tool",
    "ok",
    "path",
    "action",
    "code",
    "error_code",
    "size",
    "after_digest",
    "changed",
    "tool_call_id",
}


def evidence_view(data: JsonObject) -> JsonObject:
    return {key: value for key, value in data.items() if key in EVIDENCE_KEYS}


class Legacy:
    def __init__(self, root: Path, run_id: str) -> None:
        self.root = root
        self.run_id = run_id
        self.directory = LEGACY_DIRS.get(run_id)

    def _file(self, name: str, *, root: bool = False) -> Path | None:
        if self.directory is None:
            return None
        path = self.root / name if root else self.root / self.directory / name
        if path.is_file() and path.resolve().is_relative_to(self.root.resolve()):
            return path
        return None

    def _json(self, name: str) -> Any:
        path = self._file(name)
        return json.loads(path.read_bytes()) if path else None

    def evidence(self, version: str | None = None) -> JsonObject | None:
        if version not in (None, "legacy-current", "legacy-original"):
            return None
        if self.run_id == REGRESSION_ID:
            data = self._json("prose-regression.json") if version != "legacy-original" else None
        else:
            data = self._json(
                "evidence-original.json" if version == "legacy-original" else "evidence.json"
            )
        if not isinstance(data, dict):
            return None
        run = data.get("run")
        if isinstance(run, dict) and run.get("turn_id") == self.run_id:
            if self.run_id == REGRESSION_ID:
                path = self._file("prose-chapter.md")
                if path:
                    data["prose_text"] = path.read_text(encoding="utf-8")
            return data
        return None

    def versions(self) -> list[str]:
        return [v for v in ("legacy-original", "legacy-current") if self.evidence(v) is not None]

    def report(self) -> JsonObject | None:
        if self.run_id == REGRESSION_ID or self.evidence() is None:
            return None
        data = self._json("report-reviewed.json" if self.run_id == BASELINE_ID else "report.json")
        return data if isinstance(data, dict) else None

    def review(self) -> JsonObject | None:
        path = self._file("human-bound.json") if self.run_id == BASELINE_ID else None
        return read_json(path) if path else None

    def milestones(self) -> list[JsonObject]:
        if self.run_id != BASELINE_ID or not self._file("trace.html", root=True):
            return []
        return [
            {
                "type": "warning",
                "summary": "首次取证漏读已保存的 STRUCTURE.md；原始快照缺少该文件。",
            },
            {
                "type": "evidence",
                "summary": "从磁盘重读 STRUCTURE.md，补证后在同一轮重判，没有重新执行 Agent。",
            },
            {"type": "review", "summary": "两项代码检查及三项人工语义检查通过。"},
        ]

    def trace(self) -> list[JsonObject]:
        if self.run_id == RERUN_ID:
            data = self._json("trace-events.json")
            return data if isinstance(data, list) else []
        if self.run_id == REGRESSION_ID:
            data = self.evidence()
            return data.get("trace", []) if data else []
        path = self._file("trace.html", root=True)
        if not path or self.run_id not in (PROVIDER_ID, BASELINE_ID):
            return []
        text = path.read_text(encoding="utf-8")
        anchors = (
            ("first-turn", "provider-error")
            if self.run_id == PROVIDER_ID
            else (
                "second-turn",
                "read-scope",
                "write-rejected",
                "file-saved",
                "round-complete",
                "model-spans",
                "model-2",
                "model-3",
                "model-4",
                "model-5",
            )
        )
        events = {}
        for anchor in anchors:
            match = re.search(rf'<details id="{anchor}"[^>]*>(.*?)</details>', text, re.S)
            if not match:
                continue
            for source in re.findall(r"<pre>(.*?)</pre>", match[1], re.S):
                try:
                    rows = json.loads(unescape(source))
                except ValueError:
                    continue
                for row in rows if isinstance(rows, list) else [rows]:
                    if not isinstance(row, dict) or not isinstance(row.get("seq"), int):
                        continue
                    if anchor == "write-rejected" and row.get("ok") is False:
                        row["code"] = "STRUCTURE_MARKER_REQUIRED"
                    events[row["seq"]] = row
        return list(events.values())


def _instant(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        return None
    return result if result.tzinfo else None


def trace_view(rows: list[JsonObject]) -> JsonObject:
    """Adapt observed events to the product timeline component's input."""
    if rows and all("span_id" in row for row in rows):
        keys = {
            "span_id",
            "parent_span_id",
            "kind",
            "name",
            "start_offset_ms",
            "duration_ms",
            "point",
            "started_at",
            "finished_at",
            "status",
            "summary",
            "error_code",
            "tool_calls",
            "usage",
            "start_event_id",
            "finish_event_id",
            "retry_of_span_id",
        }
        return {
            "events": [{k: v for k, v in row.items() if k in keys} for row in rows],
            "details": {},
        }
    ordered = sorted(
        (row for row in rows if isinstance(row, dict) and _instant(row.get("at"))),
        key=lambda row: (_instant(row["at"]).timestamp(), row.get("seq") or 0),  # type: ignore[union-attr]
    )
    started = next(
        (_instant(row["at"]) for row in ordered if row.get("type") == "turn_started"), None
    )
    opening = {"tool_started": "tool_call", "model_request_started": "model"}
    closing = {"tool_finished": "tool_call", "model_request_finished": "model"}
    points = {
        "file_changed": "write",
        "error": "gate",
        "task_scope_set": "gate",
        "assistant_message": "reply",
        "run_finished": "gate",
        "turn_started": "gate",
    }
    spans: list[JsonObject] = []
    waiting: dict[tuple[str, str], list[JsonObject]] = {}
    details: dict[str, JsonObject] = {}
    previous_failed: dict[str, str] = {}
    for position, raw in enumerate(ordered):
        event_type = raw.get("type")
        if not isinstance(event_type, str):
            continue
        kind = opening.get(event_type) or closing.get(event_type) or points.get(event_type)
        if not kind:
            continue
        event_id = str(raw.get("event_id") or raw.get("seq") or position)
        current_at = _instant(raw["at"])
        delay = ((current_at - started).total_seconds() * 1000) if current_at and started else None
        name = str(
            raw.get("tool") or raw.get("path") or ("模型请求" if kind == "model" else event_type)
        )
        code = raw.get("code") or raw.get("error_code")
        status = (
            "failed"
            if raw.get("ok") is False or event_type == "error"
            else (raw.get("status") or "ok")
        )
        key = (kind, str(raw.get("tool_call_id") or name))
        details[event_id] = {k: v for k, v in raw.items() if k in TRACE_KEYS}
        if event_type in closing and waiting.get(key):
            first = waiting[key].pop(0)
            begin = _instant(first["started_at"])
            first.update(
                finish_event_id=event_id,
                finished_at=raw["at"],
                duration_ms=(current_at - begin).total_seconds() * 1000
                if current_at and begin
                else None,
                status=status,
                error_code=code,
                summary=str(code or ""),
            )
            if status == "failed" and kind == "tool_call":
                previous_failed[name] = first["span_id"]
            continue
        span = dict(
            span_id=event_id,
            parent_span_id=None,
            kind=kind,
            name=name,
            start_offset_ms=delay,
            duration_ms=None,
            point=event_type not in opening,
            started_at=raw["at"],
            finished_at=None,
            status=status,
            summary=str(code or ""),
            error_code=code,
            usage=None,
            start_event_id=event_id,
            finish_event_id=None,
            retry_of_span_id=previous_failed.pop(name, None)
            if event_type == "tool_started"
            else None,
        )
        spans.append(span)
        if event_type in opening:
            span["status"] = "running"
            waiting.setdefault(key, []).append(span)
    return {"events": spans, "details": details}
