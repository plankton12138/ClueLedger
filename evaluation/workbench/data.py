"""工作台读运行目录：报告、证据包、案例期望、复核文件。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evaluation.harness.evidence import attempt_paths
from evaluation.harness.schema import CaseLoadError, load_cases

CASES_ROOT = Path(__file__).resolve().parents[1] / "cases"
WORKS_ROOT = Path(__file__).resolve().parents[1] / "works"

STAGE_LABELS = {
    "scope": "范围登记",
    "read": "读取原文",
    "candidate": "提交候选",
    "verify": "应用核对",
    "repair": "修补",
    "commit": "提交",
}

STAGE_TO_ATTRIBUTION = {
    "scope": "context",
    "read": "context",
    "candidate": "tool",
    "verify": "verify",
    "repair": "tool",
    "commit": "commit",
}

ATTRIBUTION_OPTIONS = [
    ("context", "上下文装配", "产品问题 · 修复后跑门禁"),
    ("model", "模型理解", "产品问题 · 修复后跑门禁"),
    ("tool", "工具或写前检查", "产品问题 · 修复后跑门禁"),
    ("verify", "候选核对", "产品问题 · 修复后跑门禁"),
    ("commit", "提交", "产品问题 · 修复后跑门禁"),
    ("eval", "评测判错", "改检查项 · 用证据包重评"),
    ("not_issue", "不是问题", "关闭"),
]


def read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load_reviews_file(path: Path) -> dict[str, Any]:
    raw = read_json(path, {"reviews": [], "attributions": []})
    if isinstance(raw, list):
        return {"reviews": raw, "attributions": []}
    if not isinstance(raw, dict):
        return {"reviews": [], "attributions": []}
    reviews = raw.get("reviews")
    attributions = raw.get("attributions")
    return {
        "reviews": reviews if isinstance(reviews, list) else [],
        "attributions": attributions if isinstance(attributions, list) else [],
    }


def write_reviews_file(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    reviews = payload.get("reviews")
    attributions = payload.get("attributions")
    if not isinstance(reviews, list):
        raise ValueError("请求体需要 reviews 数组")
    if attributions is None:
        attributions = []
    if not isinstance(attributions, list):
        raise ValueError("请求体 attributions 须为数组")
    normalized = {"reviews": reviews, "attributions": attributions}
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized


def validate_reviews(reviews: list[Any]) -> str | None:
    for item in reviews:
        if not isinstance(item, dict):
            continue
        if item.get("verdict") in {"pass", "fail"} and not item.get("evidence_ref"):
            return "通过/失败必须填写证据位置"
    return None


def attempt_dir(run_dir: Path, case_id: str, attempt: int) -> Path:
    return run_dir / "attempts" / f"{case_id}--{attempt}"


def find_attempt_row(report: dict[str, Any], case_id: str, attempt: int) -> dict[str, Any] | None:
    for row in report.get("attempts") or []:
        if row.get("case_id") == case_id and int(row.get("attempt") or 0) == attempt:
            return row
    return None


def load_case_expect(case_id: str) -> dict[str, Any] | None:
    try:
        cases = {item.id: item for item in load_cases(CASES_ROOT, works_root=WORKS_ROOT)}
    except CaseLoadError:
        return None
    case = cases.get(case_id)
    if case is None:
        return None
    return {
        "case_id": case.id,
        "region": {
            "path": case.expect.region.path,
            "from": case.expect.region.from_anchor,
            "to": case.expect.region.to_anchor,
        },
        "preserve": list(case.expect.preserve),
        "steps": [{"author": step.author, "focus": step.focus} for step in case.steps],
    }


def build_attempt_payload(run_dir: Path, report: dict[str, Any], case_id: str, attempt: int) -> dict[str, Any] | None:
    row = find_attempt_row(report, case_id, attempt)
    if row is None:
        return None
    root = attempt_dir(run_dir, case_id, attempt)
    paths = attempt_paths(root)
    meta = read_json(paths.meta, {})
    turns: list[dict[str, Any]] = []
    if paths.traces.is_dir():
        for path in sorted(paths.traces.glob("*.json")):
            turns.append({"turn_id": path.stem, "has_trace": True})
    elif isinstance(meta.get("turns"), list):
        for item in meta["turns"]:
            if isinstance(item, dict) and item.get("turn_id"):
                turns.append({"turn_id": str(item["turn_id"]), "has_trace": False})
    return {
        "case_id": case_id,
        "attempt": attempt,
        "row": row,
        "meta": meta,
        "turns": turns,
        "expect": load_case_expect(case_id),
        "has_evidence": paths.before.is_file(),
        "stage_labels": STAGE_LABELS,
        "default_attribution": STAGE_TO_ATTRIBUTION.get(str(row.get("first_failed_stage") or ""), "tool"),
        "attribution_options": [
            {"id": key, "label": label, "hint": hint} for key, label, hint in ATTRIBUTION_OPTIONS
        ],
    }


def load_trace(run_dir: Path, case_id: str, attempt: int, turn_id: str) -> dict[str, Any] | None:
    path = attempt_paths(attempt_dir(run_dir, case_id, attempt)).traces / f"{turn_id}.json"
    if not path.is_file():
        return None
    data = read_json(path, None)
    return data if isinstance(data, dict) else None


def load_event(run_dir: Path, case_id: str, attempt: int, event_id: str) -> dict[str, Any] | None:
    events_path = attempt_paths(attempt_dir(run_dir, case_id, attempt)).events
    if not events_path.is_file():
        return None
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and str(item.get("event_id") or "") == event_id:
            return item
    return None


def load_input(run_dir: Path, case_id: str, attempt: int, operation_id: str) -> Any | None:
    path = attempt_paths(attempt_dir(run_dir, case_id, attempt)).inputs / f"{operation_id}.json"
    if not path.is_file():
        return None
    return read_json(path, None)


def load_diff_view(run_dir: Path, case_id: str, attempt: int) -> dict[str, Any] | None:
    root = attempt_dir(run_dir, case_id, attempt)
    paths = attempt_paths(root)
    if not paths.before.is_file():
        return None
    before = read_json(paths.before, {})
    diff = read_json(paths.diff, {})
    expect = load_case_expect(case_id) or {}
    region = expect.get("region") or {}
    path = str(region.get("path") or "")
    before_text = before.get(path, "") if isinstance(before, dict) else ""
    after_path = paths.work / path if path else None
    after_text = ""
    if after_path and after_path.is_file():
        after_text = after_path.read_text(encoding="utf-8")
    elif isinstance(before, dict) and path:
        after_text = str(before.get(path) or "")
    return {
        "diff": diff,
        "path": path,
        "before": before_text,
        "after": after_text,
        "region": region,
        "preserve": expect.get("preserve") or [],
        "unchanged": path in (diff.get("unchanged") or []) if isinstance(diff, dict) else False,
    }


def capture_inputs_enabled(run_dir: Path, case_id: str, attempt: int) -> bool:
    inputs = attempt_paths(attempt_dir(run_dir, case_id, attempt)).inputs
    return inputs.is_dir() and any(inputs.glob("*.json"))


def resolve_run_dir(runs_root: Path, run_id: str) -> Path | None:
    """在 runs_root 下解析运行目录；拒绝路径穿越。"""
    if not run_id or run_id in {".", ".."} or "/" in run_id or "\\" in run_id:
        return None
    root = runs_root.resolve()
    candidate = (root / run_id).resolve()
    if not str(candidate).startswith(str(root)):
        return None
    if not (candidate / "report.json").is_file():
        return None
    return candidate


def list_runs(runs_root: Path) -> list[dict[str, Any]]:
    """扫描已落盘运行（含 report.json 的子目录），按修改时间新到旧。"""
    if not runs_root.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in runs_root.iterdir():
        if not path.is_dir():
            continue
        report_path = path / "report.json"
        if not report_path.is_file():
            continue
        report = read_json(report_path, {})
        gate = report.get("gate") if isinstance(report, dict) else None
        summary = report.get("summary") if isinstance(report, dict) else None
        identity = report.get("identity") if isinstance(report, dict) else None
        mtime = report_path.stat().st_mtime
        items.append(
            {
                "id": path.name,
                "mode": report.get("mode") if isinstance(report, dict) else None,
                "gate_passed": bool(gate.get("passed")) if isinstance(gate, dict) else None,
                "summary": summary if isinstance(summary, dict) else {},
                "identity": identity if isinstance(identity, dict) else {},
                "mtime": mtime,
            }
        )
    items.sort(key=lambda item: float(item.get("mtime") or 0), reverse=True)
    return items
