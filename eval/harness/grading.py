"""只基于保存的证据判定；缺失证据、未复核语义和未知用量不冒充通过或零。"""

from __future__ import annotations

import json
from typing import Any, cast

from eval.harness.schema import Case, Check, Step
from hastings.workspace.materials import covers, merge_spans

WRITE_TOOLS = {"write_file", "edit_file", "delete_file", "move_file"}
SCORER_VERSION = "2"


def check_row(key: str, ok: bool, detail: Any, *, status: str | None = None) -> dict[str, Any]:
    return {"id": key, "status": status or ("pass" if ok else "fail"), "detail": detail}


def status(rows: list[dict[str, Any]]) -> str:
    states = {row["status"] for row in rows}
    if "fail" in states:
        return "fail"
    if "error" in states:
        return "error"
    if "pending_review" in states:
        return "pending_review"
    return "pass" if rows and states == {"pass"} else "not_run"


def read_spans(evidence: dict[str, Any], path: str) -> list[tuple[int, int]]:
    """只接受当前版本且在首次实际写入前提供的原文，包括显式参考预读。"""
    before = evidence["before"].get(path)
    if before is None:
        return []
    spans = []
    for event in evidence["events"]:
        if event["type"] == "file_changed" and event.get("changed"):
            break
        if event["type"] == "context_prepared":
            refs = {r["path"]: r["digest"] for r in event.get("references", [])}
            if refs.get(path) == before["digest"]:
                for provided in event.get("provided", []):
                    if provided["path"] != path:
                        continue
                    fragment = before["text"][provided["start"] : provided["end"]]
                    if fragment and fragment in initial_input_text(evidence):
                        spans.append((provided["start"], provided["end"]))
    for result in provided_results(evidence, "read_file"):
        if result.get("path") != path or result.get("digest") != before["digest"]:
            continue
        start, end = result.get("start_char"), result.get("end_char")
        if (
            isinstance(start, int)
            and isinstance(end, int)
            and 0 <= start <= end <= len(before["text"])
            and result.get("text") == before["text"][start:end]
        ):
            spans.append((start, end))
    return list(merge_spans(spans))


def provided_results(evidence: dict[str, Any], tool: str) -> list[dict[str, Any]]:
    """工具执行记录不足以证明交付；只采用写入前实际模型输入里的成功工具返回。"""
    results = []
    for event in evidence["events"]:
        if event["type"] == "file_changed" and event.get("changed"):
            break
        if event["type"] != "model_request_started":
            continue
        snapshot = evidence["details"].get(event.get("input_detail"), {})
        for message in snapshot.get("messages", []):
            if (
                message.get("role") != "toolResult"
                or message.get("toolName") != tool
                or message.get("isError")
            ):
                continue
            content = message.get("content", [])
            text = (
                content if isinstance(content, str) else "".join(b.get("text", "") for b in content)
            )
            try:
                result = json.loads(text)
            except (ValueError, TypeError):
                continue
            if isinstance(result, dict):
                results.append(result)
    return results


def partitions(evidence: dict[str, Any]) -> list[dict[str, str]]:
    """实际装配结果与发送快照同时保存；不能用运行后的当前文件重建输入。"""
    return cast(list[dict[str, str]], evidence.get("assemblies", []))


def initial_input_text(evidence: dict[str, Any]) -> str:
    for event in evidence["events"]:
        if event["type"] != "model_request_started":
            continue
        snapshot = evidence["details"].get(event.get("input_detail"), {})
        texts = [str(snapshot.get("system", ""))]
        for message in snapshot.get("messages", []):
            content = message.get("content", "")
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                texts.extend(str(block.get("text", "")) for block in content)
        return "\n".join(texts)
    return ""


def evaluate_rule(
    rule: Check,
    evidence: dict[str, Any],
    review: dict[str, Any] | None,
) -> dict[str, Any]:
    after = evidence["after"].get(rule.path)
    if rule.kind in {"file_contains", "file_excludes"}:
        ok = after is not None and (
            rule.value in after["text"]
            if rule.kind == "file_contains"
            else rule.value not in after["text"]
        )
        return check_row(rule.id, ok, {"path": rule.path, "expected": rule.value})
    if rule.kind in {"read_quote", "source_quote", "full_read"}:
        source = evidence["before"].get(rule.path)
        spans = read_spans(evidence, rule.path)
        if source is None:
            return check_row(rule.id, False, "当前版本源文件缺失")
        text = source["text"]
        if rule.kind == "full_read":
            ok = covers(spans, 0, len(text))
        else:
            starts = [i for i in range(len(text)) if text.startswith(rule.value, i)]
            ok = any(covers(spans, i, i + len(rule.value)) for i in starts)
            if rule.kind == "source_quote" and not ok:
                for result in provided_results(evidence, "search_text"):
                    for hit in result.get("hits", []):
                        line = hit.get("line", 0)
                        lines = text.splitlines()
                        if (
                            hit.get("path") == rule.path
                            and isinstance(line, int)
                            and 0 < line <= len(lines)
                            and rule.value in lines[line - 1]
                            and rule.value in hit.get("text", "")
                        ):
                            ok = True
        return check_row(rule.id, ok, {"path": rule.path, "spans": spans})
    if rule.kind == "index_status":
        actual = evidence["index_status"].get(rule.path, "missing")
        return check_row(rule.id, actual == rule.value, actual)
    if rule.kind == "coverage":
        records = [
            r for r in evidence["coverage"] if r["domain"] == rule.domain and r["path"] == rule.path
        ]
        ok = any(
            r["outcome"] == rule.value
            and r["freshness"] == "valid"
            and r["task_id"] == evidence["turn_id"]
            for r in records
        )
        return check_row(rule.id, ok, records)
    if rule.kind == "part_excludes":
        assemblies = partitions(evidence)
        ok = bool(assemblies) and all(rule.value not in a.get(rule.part, "") for a in assemblies)
        return check_row(rule.id, ok, {"part": rule.part, "assemblies": len(assemblies)})
    if rule.kind == "module_bound":
        bindings = evidence.get("assembly", {}).get("bindings", [])
        names = {
            str(item.get("name") or item.get("label") or "")
            for item in bindings
            if not item.get("problems")
        }
        return check_row(rule.id, rule.value in names, sorted(names))
    if rule.kind in {"module_contains", "module_excludes"}:
        modules = evidence.get("modules", [])
        haystack = "\n".join(
            [str(module.get("name") or "") for module in modules if not module.get("deleted")]
            + [
                str(entry.get("text") or "")
                for module in modules
                if not module.get("deleted")
                for revision in module.get("revisions", [module])
                for entry in revision.get("entries", [])
            ]
        )
        found = rule.value in haystack
        ok = found if rule.kind == "module_contains" else not found
        return check_row(rule.id, ok, {"value": rule.value, "modules": len(modules)})
    if rule.kind == "idea_contains":
        rows = evidence.get("ideas", [])
        ok = any(
            not row.get("deleted")
            and (rule.value in str(row.get("title", "")) or rule.value in str(row.get("body", "")))
            for row in rows
        )
        return check_row(rule.id, ok, {"value": rule.value, "ideas": len(rows)})
    if rule.kind == "tool_not_called":
        banned = {name.strip() for name in rule.value.split(",") if name.strip()}
        called = sorted(
            {
                str(event.get("tool"))
                for event in evidence["events"]
                if event.get("type") == "tool_started"
            }
        )
        return check_row(
            rule.id, not (banned & set(called)), {"banned": sorted(banned), "called": called}
        )
    if rule.kind == "fault_observed":
        return check_row(rule.id, bool(evidence.get("faults")), evidence.get("faults", []))
    if rule.kind == "compaction_count":
        records = [item for item in evidence.get("compactions", []) if item.get("compaction_id")]
        expected = int(rule.value)
        return check_row(
            rule.id, len(records) >= expected, {"expected": expected, "records": records}
        )
    if rule.kind == "retry_observed":
        rows = [e for e in evidence["events"] if e.get("type") == "model_request_retried"]
        ok = any(rule.value in str(row.get("reason", "")) for row in rows)
        return check_row(rule.id, ok, rows)
    if rule.kind == "manual":
        valid = (
            review is not None
            and review.get("verdict") in {"pass", "fail", "uncertain"}
            and all(
                isinstance(review.get(k), str) and review[k].strip()
                for k in ("reviewer", "reason", "evidence")
            )
        )
        if not valid or review is None or review["verdict"] == "uncertain":
            return check_row(
                rule.id,
                False,
                {"rubric": rule.rubric, "review": review},
                status="pending_review",
            )
        return check_row(rule.id, review["verdict"] == "pass", review)
    raise ValueError(f"未知判定规则：{rule.kind}")


def grade_step(
    step: Step,
    evidence: dict[str, Any],
    prefix: str,
    reviews: dict[str, Any],
) -> list[dict[str, Any]]:
    events = evidence["events"]
    terminals = [e for e in events if e["type"] == "run_finished"]
    asked = any(e.get("type") == "author_input_requested" for e in events)
    final = terminals[-1]["status"] if terminals else ""
    rows = [
        check_row(
            "terminal",
            bool(terminals)
            and (
                final == "completed"
                or (step.expect_needs_input and final == "needs_input")
                or (step.expect_stopped and final == "stopped")
            ),
            terminals[-1] if terminals else "缺少终态",
        )
    ]
    answer = "\n".join(e.get("text", "") for e in events if e["type"] == "assistant_message")
    # 提问暂停的一轮，公开输出就是问题本身；不要求另有一段助手文字。
    rows.append(
        check_row(
            "public_answer",
            bool(answer.strip()) or (step.expect_needs_input and asked) or step.expect_stopped,
            answer or ("已向作者提问" if asked else ""),
        )
    )
    before, after = evidence["before"], evidence["after"]
    changed = {
        p
        for p in before.keys() | after.keys()
        if before.get(p, {}).get("digest") != after.get(p, {}).get("digest")
    }
    rows.append(
        check_row("allowed_final_changes", changed <= set(step.allowed_changes), sorted(changed))
    )
    rows.append(
        check_row("required_changes", set(step.required_changes) <= changed, step.required_changes)
    )
    violations = []
    attempted = []
    grants: set[tuple[str, str]] = set()
    ungranted = []
    for event in events:
        if event["type"] in {"task_scope_set", "task_scope_extended"}:
            grants = {(g["action"], g["path"]) for g in event.get("grants", [])}
        if event["type"] == "tool_started" and event.get("tool") in WRITE_TOOLS:
            args = event.get("args", {})
            path = args.get("path", args.get("source"))
            if path not in step.allowed_changes or event["tool"] in {"delete_file", "move_file"}:
                attempted.append(event)
        if event["type"] != "file_changed":
            continue
        if (event.get("action"), event.get("path")) not in grants:
            ungranted.append(event)
        if event.get("path") not in step.allowed_changes or event.get("action") != "update":
            violations.append(event)
    rows.append(check_row("trajectory_scope", not violations, violations))
    rows.append(check_row("decision_scope", not attempted, attempted))
    rows.append(check_row("scope_before_write", not ungranted, ungranted))
    missing_preread = []
    for event in events:
        if event["type"] != "context_prepared":
            continue
        for provided in event.get("provided", []):
            source = before.get(provided["path"])
            if source is None or provided["end"] <= provided["start"]:
                continue
            fragment = source["text"][provided["start"] : provided["end"]]
            if fragment not in initial_input_text(evidence):
                missing_preread.append(provided)
    rows.append(check_row("preread_delivered", not missing_preread, missing_preread))
    for path, replacements in step.exact_replacements.items():
        expected = before.get(path, {}).get("text")
        valid = expected is not None
        for old, new in replacements.items():
            if expected is not None:
                valid = valid and expected.count(old) == 1
                expected = expected.replace(old, new)
        rows.append(
            check_row(
                f"exact_replace:{path}",
                valid and after.get(path, {}).get("text") == expected,
                {"path": path, "replacements": replacements},
            )
        )
    for rule in step.checks:
        if rule.kind == "compaction_count" and "--no_compaction--" in prefix:
            rows.append(check_row(rule.id, True, "无压缩对照不要求摘要次数；语义检查仍须通过"))
            continue
        rows.append(evaluate_rule(rule, evidence, reviews.get(f"{prefix}/{rule.id}")))
    if step.purpose == "organize_materials":
        requests = [e for e in events if e["type"] == "model_request_started"]
        allowed = {"list_files", "search_text", "read_file", "publish_material_result"}
        rows.append(
            check_row(
                "material_tools",
                bool(requests) and all(set(e.get("tools", [])) <= allowed for e in requests),
                [e.get("tools") for e in requests],
            )
        )
    return rows


def metrics(evidences: list[dict[str, Any]]) -> dict[str, Any]:
    events = [e for step in evidences for e in step["events"]]
    started = [e for e in events if e["type"] == "model_request_started"]
    finished = [e for e in events if e["type"] == "model_request_finished"]
    tools = [e for e in events if e["type"] == "tool_finished"]
    usage = [e["usage"] for e in finished if e.get("usage") is not None]
    complete = len(usage) == len(started) and bool(started)
    sizes = []
    for step in evidences:
        for e in step["events"]:
            if e["type"] == "model_request_started":
                snapshot = step["details"].get(e.get("input_detail"))
                if isinstance(snapshot, dict) and "system" in snapshot:
                    sizes.append(
                        {
                            "system_chars": len(snapshot["system"]),
                            "messages_json_chars": len(
                                json.dumps(snapshot["messages"], ensure_ascii=False)
                            ),
                            "tools_json_chars": len(
                                json.dumps(snapshot["tools"], ensure_ascii=False)
                            ),
                        }
                    )
    blocked = [e for e in tools if e.get("tool") in WRITE_TOOLS and not e.get("ok")]
    return {
        "model_requests": len(started),
        "model_finished": len(finished),
        "tool_calls": len(tools),
        "tool_errors": sum(not e.get("ok") for e in tools),
        "blocked_write_calls": len(blocked),
        "usage_complete": complete,
        "usage_reported_requests": len(usage),
        "fresh_input_tokens": sum(u.get("input", 0) for u in usage) if complete else None,
        "input_tokens": sum(
            u.get("input", 0) + u.get("cache_read", 0) + u.get("cache_write", 0) for u in usage
        )
        if complete
        else None,
        "output_tokens": sum(u.get("output", 0) for u in usage) if complete else None,
        "cache_read_tokens": sum(u.get("cache_read", 0) for u in usage) if complete else None,
        "cache_write_tokens": sum(u.get("cache_write", 0) for u in usage) if complete else None,
        "request_sizes": sizes,
        "max_request_json_chars": max((sum(s.values()) for s in sizes), default=None),
        "cost": None,
        "summary_requests": sum(e.get("stage") == "compaction" for e in started),
        "summary_duration_ms": sum(
            e.get("duration_ms") or 0 for e in finished if e.get("stage") == "compaction"
        ),
        "summary_input_tokens": sum(
            u.get("input", 0) + u.get("cache_read", 0) + u.get("cache_write", 0)
            for e in finished
            if e.get("stage") == "compaction" and (u := e.get("usage"))
        )
        if complete
        else None,
        "summary_output_tokens": sum(
            u.get("output", 0)
            for e in finished
            if e.get("stage") == "compaction" and (u := e.get("usage"))
        )
        if complete
        else None,
    }


def grade_attempt(
    case: Case,
    evidences: list[dict[str, Any]],
    key: str,
    reviews: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = []
    for i, evidence in enumerate(evidences):
        for row in grade_step(case.steps[i], evidence, f"{key}/step-{i + 1}", reviews or {}):
            rows.append({**row, "id": f"step-{i + 1}/{row['id']}"})
    if len(evidences) != len(case.steps):
        rows.append(check_row("all_steps", False, "有步骤未运行"))
    return {"status": status(rows), "checks": rows, "metrics": metrics(evidences)}
