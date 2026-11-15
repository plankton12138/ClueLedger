"""程序判分：只吃证据快照与案例期望，不调模型、不导入产品业务模块。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from evaluation.harness.schema import Case, Check, Region

Verdict = Literal["pass", "fail", "pending_review", "skipped"]
Outcome = Literal["pass", "fail", "pending_review"]
EvidenceRef = dict[str, Any]


@dataclass(frozen=True)
class AttemptEvidence:
    """一次试验采到的判分证据。路径相对作品根，正斜杠。"""

    before_files: dict[str, str]
    after_files: dict[str, str]
    commit_receipts: list[dict[str, Any]] = field(default_factory=list)
    rejected_commits: list[dict[str, Any]] = field(default_factory=list)
    # 首次提交候选前的模型输入原文（可多段）；缺省表示未采到快照
    model_inputs_before_candidate: list[str] | None = None
    # 范围登记工具调用记录，可选
    scope_registrations: list[dict[str, Any]] | None = None
    # 原始事件（用于 span / missing 定位）
    events: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    level: str
    verdict: Verdict
    reason: str
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True)
class AttemptGrade:
    outcome: Outcome
    checks: tuple[CheckResult, ...]


def _norm(path: str) -> str:
    return path.replace("\\", "/")


def _refs(*items: EvidenceRef | None) -> tuple[EvidenceRef, ...]:
    return tuple(item for item in items if item is not None)


def _file_ref(path: str, *, anchor: str | None = None, fragment: str | None = None) -> EvidenceRef:
    ref: EvidenceRef = {"type": "file", "path": path}
    if anchor:
        ref["anchor"] = anchor
    if fragment:
        ref["fragment"] = fragment
    return ref


def _missing_ref(expected: str, *, turn_id: str | None = None) -> EvidenceRef:
    ref: EvidenceRef = {"type": "missing", "expected": expected}
    if turn_id:
        ref["turn_id"] = turn_id
    return ref


def _input_ref(
    operation_id: str, *, turn_id: str | None = None, fragment: str | None = None
) -> EvidenceRef:
    ref: EvidenceRef = {"type": "input", "operation_id": operation_id}
    if turn_id:
        ref["turn_id"] = turn_id
    if fragment:
        ref["fragment"] = fragment
    return ref


def _span_ref(turn_id: str, span_id: str) -> EvidenceRef:
    return {"type": "span", "turn_id": turn_id, "span_id": span_id}


def _note_ref(text: str) -> EvidenceRef:
    return {"type": "note", "text": text}


def _tool_span_id(event: dict[str, Any]) -> str | None:
    call = event.get("tool_call_id")
    if not isinstance(call, str) or not call:
        return None
    op = event.get("parent_operation_id") or event.get("operation_id")
    if isinstance(op, str) and op:
        return f"tool_call:{op}:{call}"
    return f"tool_call:{call}"


def _last_turn_id(evidence: AttemptEvidence) -> str | None:
    for event in reversed(evidence.events):
        turn = event.get("turn_id")
        if isinstance(turn, str) and turn:
            return turn
    return None


def _related_tool_span(evidence: AttemptEvidence, tools: set[str]) -> EvidenceRef | None:
    for event in evidence.events:
        if event.get("type") != "tool_started":
            continue
        if str(event.get("tool") or "") not in tools:
            continue
        span = _tool_span_id(event)
        turn = event.get("turn_id")
        if span and isinstance(turn, str):
            return _span_ref(turn, span)
    return None


def _region_slices(text: str, region: Region) -> tuple[str, str, str]:
    """返回 (不可改前缀, 可改区域, 不可改后缀)。"""
    start = text.find(region.from_anchor)
    if start < 0:
        raise ValueError(f"初态缺少 from 锚点：{region.from_anchor}")
    if region.to_anchor == "END":
        end = len(text)
    else:
        end = text.find(region.to_anchor, start)
        if end < 0:
            raise ValueError(f"初态缺少 to 锚点：{region.to_anchor}")
        end = end + len(region.to_anchor)
    return text[:start], text[start:end], text[end:]


def _check_only_region_changed(case: Case, evidence: AttemptEvidence) -> CheckResult:
    path = _norm(case.expect.region.path)
    before = evidence.before_files
    after = evidence.after_files
    before_keys = {_norm(k) for k in before}
    after_keys = {_norm(k) for k in after}
    file_ref = _file_ref(path, anchor=case.expect.region.from_anchor)
    if before_keys != after_keys:
        added = sorted(after_keys - before_keys)
        removed = sorted(before_keys - after_keys)
        return CheckResult(
            "only_region_changed",
            "must",
            "fail",
            f"文件集合变化：新增 {added or '无'}，删除 {removed or '无'}",
            evidence=_refs(file_ref),
        )

    def _text(files: dict[str, str], key: str) -> str:
        return next(v for k, v in files.items() if _norm(k) == key)

    for key in sorted(before_keys):
        if key == path:
            continue
        if _text(before, key) != _text(after, key):
            return CheckResult(
                "only_region_changed",
                "must",
                "fail",
                f"非目标文件被修改：{key}",
                evidence=_refs(_file_ref(key)),
            )
    try:
        before_text = _text(before, path)
        after_text = _text(after, path)
    except StopIteration:
        return CheckResult(
            "only_region_changed",
            "must",
            "fail",
            f"缺少目标文件：{path}",
            evidence=_refs(_file_ref(path)),
        )
    try:
        prefix_b, _, suffix_b = _region_slices(before_text, case.expect.region)
        prefix_a, _, suffix_a = _region_slices(after_text, case.expect.region)
    except ValueError as exc:
        return CheckResult(
            "only_region_changed", "must", "fail", str(exc), evidence=_refs(file_ref)
        )
    if prefix_b != prefix_a:
        return CheckResult(
            "only_region_changed",
            "must",
            "fail",
            "目标文件锚点前的正文被修改",
            evidence=_refs(file_ref),
        )
    if suffix_b != suffix_a:
        return CheckResult(
            "only_region_changed",
            "must",
            "fail",
            "目标文件锚点后的正文被修改",
            evidence=_refs(file_ref),
        )
    return CheckResult(
        "only_region_changed",
        "must",
        "pass",
        "仅允许区域发生变化",
        evidence=_refs(file_ref),
    )


def _check_region_actually_changed(case: Case, evidence: AttemptEvidence) -> CheckResult:
    path = _norm(case.expect.region.path)
    file_ref = _file_ref(path, anchor=case.expect.region.from_anchor)

    def _text(files: dict[str, str], key: str) -> str:
        return next(v for k, v in files.items() if _norm(k) == key)

    try:
        before_text = _text(evidence.before_files, path)
        after_text = _text(evidence.after_files, path)
    except StopIteration:
        return CheckResult(
            "region_actually_changed",
            "must",
            "fail",
            f"缺少目标文件：{path}",
            evidence=_refs(_file_ref(path)),
        )
    try:
        _, before_region, _ = _region_slices(before_text, case.expect.region)
        _, after_region, _ = _region_slices(after_text, case.expect.region)
    except ValueError as exc:
        return CheckResult(
            "region_actually_changed", "must", "fail", str(exc), evidence=_refs(file_ref)
        )
    if before_region == after_region:
        return CheckResult(
            "region_actually_changed",
            "must",
            "fail",
            "锚点区域与初态相同",
            evidence=_refs(file_ref),
        )
    return CheckResult(
        "region_actually_changed",
        "must",
        "pass",
        "锚点区域确有改动",
        evidence=_refs(file_ref),
    )


def _check_preserved_verbatim(case: Case, evidence: AttemptEvidence) -> CheckResult:
    path = _norm(case.expect.region.path)
    try:
        after_text = next(v for k, v in evidence.after_files.items() if _norm(k) == path)
    except StopIteration:
        return CheckResult(
            "preserved_verbatim",
            "must",
            "fail",
            f"缺少目标文件：{path}",
            evidence=_refs(_file_ref(path)),
        )
    missing = [frag for frag in case.expect.preserve if frag not in after_text]
    if missing:
        return CheckResult(
            "preserved_verbatim",
            "must",
            "fail",
            f"未逐字保留：{missing}",
            evidence=_refs(
                _file_ref(path, anchor=case.expect.region.from_anchor, fragment=missing[0])
            ),
        )
    frag = case.expect.preserve[0] if case.expect.preserve else None
    return CheckResult(
        "preserved_verbatim",
        "must",
        "pass",
        "保留片段均在",
        evidence=_refs(_file_ref(path, anchor=case.expect.region.from_anchor, fragment=frag)),
    )


def _check_committed_via_candidate(case: Case, evidence: AttemptEvidence) -> CheckResult:
    path = _norm(case.expect.region.path)
    turn_id = _last_turn_id(evidence)
    for receipt in evidence.commit_receipts:
        receipt_path = _norm(str(receipt.get("path", "")))
        via = str(receipt.get("via", ""))
        if receipt_path == path and via == "candidate":
            return CheckResult(
                "committed_via_candidate",
                "must",
                "pass",
                "存在经候选核对的提交回执",
                evidence=_refs(
                    _file_ref(path),
                    _note_ref(str(receipt)),
                ),
            )
    related = _related_tool_span(evidence, {"ask_author", "submit_candidate", "propose_candidate"})
    return CheckResult(
        "committed_via_candidate",
        "must",
        "fail",
        "未找到经候选核对的提交回执",
        evidence=_refs(
            _missing_ref("候选提交回执", turn_id=turn_id),
            related,
        ),
    )


def _check_stale_candidate_rejected(case: Case, evidence: AttemptEvidence) -> CheckResult:
    del case
    for item in evidence.rejected_commits:
        code = str(item.get("error_code", ""))
        if code in {"VERSION_CONFLICT", "STALE_BASELINE", "VERSION_MISMATCH"}:
            return CheckResult(
                "stale_candidate_rejected",
                "must",
                "pass",
                "旧版本候选被拒绝",
                evidence=_refs(_note_ref(str(item))),
            )
    return CheckResult(
        "stale_candidate_rejected",
        "must",
        "fail",
        "未发现版本冲突拒绝记录",
        evidence=_refs(_missing_ref("版本冲突拒绝记录", turn_id=_last_turn_id(evidence))),
    )


def _input_refs_before_candidate(evidence: AttemptEvidence) -> tuple[EvidenceRef, ...]:
    refs: list[EvidenceRef] = []
    for event in evidence.events:
        if event.get("type") == "story_candidate_committed":
            break
        if event.get("type") != "model_request_started":
            continue
        operation = event.get("operation_id")
        turn = event.get("turn_id")
        if isinstance(operation, str) and operation:
            refs.append(
                _input_ref(
                    operation,
                    turn_id=turn if isinstance(turn, str) else None,
                )
            )
    return tuple(refs)


def _check_chapter_in_model_input(case: Case, evidence: AttemptEvidence) -> CheckResult:
    path = _norm(case.expect.region.path)
    input_refs = _input_refs_before_candidate(evidence)
    if evidence.model_inputs_before_candidate is None:
        return CheckResult(
            "chapter_in_model_input",
            "quality",
            "skipped",
            "无模型输入快照",
            evidence=(),
        )
    try:
        chapter = next(v for k, v in evidence.before_files.items() if _norm(k) == path)
    except StopIteration:
        return CheckResult(
            "chapter_in_model_input",
            "quality",
            "fail",
            f"初态缺少目标章节：{path}",
            evidence=_refs(_file_ref(path)),
        )
    joined = "\n".join(evidence.model_inputs_before_candidate)
    if chapter in joined:
        return CheckResult(
            "chapter_in_model_input",
            "quality",
            "pass",
            "首次提交候选前输入含目标章节全文",
            evidence=input_refs or _refs(_file_ref(path)),
        )
    return CheckResult(
        "chapter_in_model_input",
        "quality",
        "fail",
        "首次提交候选前输入未含目标章节全文",
        evidence=input_refs or _refs(_file_ref(path, fragment=case.expect.region.from_anchor)),
    )


def _check_scope_matches_auth(case: Case, evidence: AttemptEvidence) -> CheckResult:
    if evidence.scope_registrations is None:
        return CheckResult(
            "scope_matches_auth",
            "quality",
            "skipped",
            "无范围登记证据",
            evidence=(),
        )
    focus = case.steps[0].focus if case.steps else None
    if not focus:
        return CheckResult(
            "scope_matches_auth",
            "quality",
            "fail",
            "案例未声明 focus，无法对照授权",
            evidence=(),
        )
    focus_norm = _norm(focus)
    related = _related_tool_span(
        evidence, {"declare_scope", "register_scope", "declare_edit_scope"}
    )
    for item in evidence.scope_registrations:
        paths = item.get("paths") or item.get("path") or []
        if isinstance(paths, str):
            paths = [paths]
        if any(_norm(str(p)) == focus_norm for p in paths):
            return CheckResult(
                "scope_matches_auth",
                "quality",
                "pass",
                "范围登记与作者 focus 一致",
                evidence=_refs(related, _note_ref(str(item))),
            )
    return CheckResult(
        "scope_matches_auth",
        "quality",
        "fail",
        "范围登记未覆盖作者 focus",
        evidence=_refs(related, _file_ref(focus_norm)),
    )


_HANDLERS = {
    "only_region_changed": _check_only_region_changed,
    "region_actually_changed": _check_region_actually_changed,
    "preserved_verbatim": _check_preserved_verbatim,
    "committed_via_candidate": _check_committed_via_candidate,
    "stale_candidate_rejected": _check_stale_candidate_rejected,
    "chapter_in_model_input": _check_chapter_in_model_input,
    "scope_matches_auth": _check_scope_matches_auth,
}


def _grade_one(check: Check, case: Case, evidence: AttemptEvidence) -> CheckResult:
    if check.level == "human":
        return CheckResult(
            check.id,
            check.level,
            "pending_review",
            check.question or "待人工复核",
            evidence=_refs(
                _file_ref(
                    _norm(case.expect.region.path),
                    anchor=case.expect.region.from_anchor,
                )
            ),
        )
    handler = _HANDLERS.get(check.id)
    if handler is None:
        return CheckResult(
            check.id,
            check.level,
            "fail",
            f"未知检查项：{check.id}",
            evidence=(),
        )
    return handler(case, evidence)


def _outcome(checks: list[CheckResult]) -> Outcome:
    if any(item.verdict == "fail" for item in checks):
        return "fail"
    if any(item.verdict == "pending_review" for item in checks):
        return "pending_review"
    return "pass"


def grade_attempt(case: Case, evidence: AttemptEvidence) -> AttemptGrade:
    results = [_grade_one(check, case, evidence) for check in case.checks]
    return AttemptGrade(outcome=_outcome(results), checks=tuple(results))
