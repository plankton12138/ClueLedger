"""Read projection for a single author turn."""

from __future__ import annotations

from bisect import bisect_right
from datetime import datetime
from typing import Any

TRACE_TYPES = (
    "turn_started",
    "run_finished",
    "attempt_started",
    "loop_started",
    "loop_finished",
    "gate_decided",
    "model_request_started",
    "model_request_finished",
    "model_request_retried",
    "tool_started",
    "tool_finished",
    "context_prepared",
    "file_changed",
    "assistant_started",
    "assistant_finished",
)

BOUNDARIES = {
    "loop_started": ("loop", "loop_id", True),
    "loop_finished": ("loop", "loop_id", False),
    "model_request_started": ("model", "operation_id", True),
    "model_request_finished": ("model", "operation_id", False),
    "tool_started": ("tool_call", "tool_call_id", True),
    "tool_finished": ("tool_call", "tool_call_id", False),
    "assistant_started": ("reply", "message_id", True),
    "assistant_finished": ("reply", "message_id", False),
}

POINTS = {
    "gate_decided": ("gate", "规则检查"),
    "context_prepared": ("retrieval", "装配写作上下文"),
    "file_changed": ("write", "文件变更"),
}


def elapsed(start: str, end: str) -> int | None:
    try:
        delta = datetime.fromisoformat(end) - datetime.fromisoformat(start)
        return round(delta.total_seconds() * 1000) if delta.total_seconds() >= 0 else None
    except (ValueError, TypeError):
        return None


def tool_span(call_id: str, operation_id: str | None) -> str:
    return f"tool_call:{operation_id}:{call_id}" if operation_id else f"tool_call:{call_id}"


def turn_summary(start: dict[str, Any], end: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "turn_id": start["turn_id"],
        "seq": start["seq"],
        "text": start.get("text", ""),
        "started_at": start["at"],
        "finished_at": end["at"] if end else None,
        "duration_ms": (
            elapsed(start["at"], end["at"]) if end and end.get("status") != "interrupted" else None
        ),
        "status": end.get("status", "interrupted") if end else "running",
        "trace_available": start.get("trace_version") == 1,
    }


def trace_snapshot(
    start: dict[str, Any], rows: list[dict[str, Any]], cursor: str
) -> dict[str, Any]:
    end = None
    terminal_seqs: list[int] = []
    for row in rows:
        if row["type"] == "run_finished":
            end = row
            terminal_seqs.append(row["seq"])
        elif row["type"] == "attempt_started":
            end = None
    turn = turn_summary(start, end)
    result = {
        "schema_version": 1,
        "turn": turn,
        "events": [],
        "range_ms": 0,
        "cursor": cursor,
        "usage": {"input": None, "output": None, "partial": False},
    }
    if not turn["trace_available"]:
        return result

    pairs: dict[str, list[dict[str, Any] | None]] = {}
    retries: dict[str, dict[str, Any]] = {}
    events: list[dict[str, Any]] = []

    def project(
        key: str,
        kind: str,
        name: str,
        opened: dict[str, Any] | None,
        closed: dict[str, Any] | None,
        *,
        point: bool = False,
    ) -> dict[str, Any]:
        source = opened or closed or {}
        finish = closed or {}
        at = source.get("at", "")
        offset = elapsed(start["at"], at)
        duration = finish.get("duration_ms")
        if "duration_ms" not in finish and opened and closed and not point:
            duration = elapsed(at, finish["at"])
        interrupted = bisect_right(terminal_seqs, source.get("seq", 0)) < len(terminal_seqs)
        loop = source.get("loop_id") or finish.get("loop_id")
        return {
            "span_id": key,
            "parent_span_id": f"loop:{loop}" if loop and kind != "loop" else None,
            "kind": kind,
            "name": name,
            "start_offset_ms": offset,
            "duration_ms": duration if opened and not point else None,
            "point": point,
            "started_at": at if opened else None,
            "finished_at": finish.get("at") if not point else None,
            "status": (
                finish.get("decision")
                or finish.get("status")
                or ("failed" if finish.get("ok") is False else "completed")
                if closed
                else "interrupted"
                if interrupted
                else "running"
            ),
            "summary": finish.get("summary") or finish.get("message") or finish.get("path") or "",
            "error_code": finish.get("code"),
            "start_event_id": source.get("event_id") if opened else None,
            "finish_event_id": finish.get("event_id"),
            "usage": finish.get("usage"),
            "retry_of_span_id": (
                tool_span(source["retry_of_tool_call_id"], source.get("retry_of_operation_id"))
                if kind == "tool_call" and source.get("retry_of_tool_call_id")
                else None
            ),
        }

    for row in rows:
        kind = row["type"]
        if kind in BOUNDARIES:
            event_kind, field, is_start = BOUNDARIES[kind]
            if row.get(field):
                key = (
                    tool_span(row[field], row.get("parent_operation_id"))
                    if event_kind == "tool_call"
                    else f"{event_kind}:{row[field]}"
                )
                pairs.setdefault(key, [None, None])[0 if is_start else 1] = row
        elif kind == "model_request_retried" and row.get("operation_id"):
            retries[f"model:{row['operation_id']}"] = row
        elif kind in POINTS:
            event_kind, name = POINTS[kind]
            events.append(
                project(row["event_id"], event_kind, row.get("name", name), row, row, point=True)
            )

    for key, (opened, closed) in pairs.items():
        kind = key.split(":", 1)[0]
        source = opened or closed or {}
        name = (
            source.get("tool")
            or source.get("model")
            or {"loop": "模型回合", "reply": "助手回复"}.get(kind, kind)
        )
        retry = retries.get(key)
        if retry and opened:
            first = {
                **retry,
                "status": "failed",
                "duration_ms": retry.get("attempt_duration_ms"),
                "usage": retry.get("attempt_usage"),
            }
            events.append(project(key, kind, name, opened, first))
            latest = (
                {**closed, "duration_ms": closed.get("attempt_duration_ms")} if closed else None
            )
            retry_start = {**retry, "loop_id": opened.get("loop_id")}
            attempt = project(f"{key}:retry", kind, f"{name} · 重试", retry_start, latest)
            attempt["retry_of_span_id"] = key
            events.append(attempt)
        else:
            item = project(key, kind, name, opened, closed)
            if kind == "reply" and source.get("parent_operation_id"):
                model = pairs.get(f"model:{source['parent_operation_id']}")
                if model and model[0] and model[0].get("loop_id"):
                    item["parent_span_id"] = f"loop:{model[0]['loop_id']}"
            if kind == "reply" and closed and closed.get("completion"):
                item["status"] = closed["completion"]
            events.append(item)

    events.sort(
        key=lambda item: (
            item["start_offset_ms"] is None,
            item["start_offset_ms"] or 0,
            item["kind"] != "loop",
        )
    )
    loop_number = 0
    children: dict[str, list[dict[str, Any]]] = {}
    for item in events:
        if item["parent_span_id"]:
            children.setdefault(item["parent_span_id"], []).append(item)
    for item in events:
        if item["kind"] != "loop":
            continue
        loop_number += 1
        item["name"] = f"Loop {loop_number}"
        group = children.get(item["span_id"], [])
        item["tool_calls"] = sum(child["kind"] == "tool_call" for child in group)
        recovered = {child["retry_of_span_id"] for child in group if child["status"] == "completed"}
        failed = {child["span_id"] for child in group if child["status"] == "failed"}
        if item["status"] == "completed" and failed:
            item["status"] = "recovered" if failed <= recovered else "completed_with_errors"

    models = [item.get("usage") or {} for item in events if item["kind"] == "model"]
    usage: dict[str, int | bool | None] = {"partial": False}
    for key in ("input", "output"):
        values = [entry[key] for entry in models if isinstance(entry.get(key), int)]
        usage[key] = sum(values) if values else None
        usage["partial"] = bool(usage["partial"] or 0 < len(values) < len(models))
    usage["partial"] = bool(
        usage["partial"] or (usage["input"] is None) != (usage["output"] is None)
    )
    last_offset = max((elapsed(start["at"], row["at"]) or 0 for row in rows), default=0)
    last_event = max(
        ((item["start_offset_ms"] or 0) + (item["duration_ms"] or 0) for item in events),
        default=0,
    )
    return {**result, "events": events, "range_ms": max(last_offset, last_event), "usage": usage}
