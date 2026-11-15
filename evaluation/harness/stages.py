"""改稿过程环节：从事件与证据计算 earliest failure。不导入产品业务模块。"""

from __future__ import annotations

from typing import Any

from evaluation.harness.checks import AttemptEvidence
from evaluation.harness.schema import Case

SCOPE_TOOLS = frozenset({"declare_scope", "register_scope", "declare_edit_scope"})
CANDIDATE_TOOLS = frozenset({"submit_candidate", "propose_candidate", "commit_candidate"})
VERIFY_TOOLS = frozenset({"verify_candidate", "check_candidate", "review_candidate"})
REPAIR_TOOLS = frozenset({"repair_candidate", "revise_candidate"})


def _norm(path: str) -> str:
    return path.replace("\\", "/")


def tool_span_id(event: dict[str, Any]) -> str | None:
    call = event.get("tool_call_id")
    if not isinstance(call, str) or not call:
        return None
    op = event.get("parent_operation_id") or event.get("operation_id")
    if isinstance(op, str) and op:
        return f"tool_call:{op}:{call}"
    return f"tool_call:{call}"


def _turn_of(events: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> str | None:
    for event in reversed(events):
        turn = event.get("turn_id")
        if isinstance(turn, str) and turn:
            return turn
    return None


def _first_tool(
    events: tuple[dict[str, Any], ...] | list[dict[str, Any]], names: frozenset[str]
) -> dict[str, Any] | None:
    for event in events:
        if event.get("type") != "tool_started":
            continue
        if str(event.get("tool") or "") in names:
            return event
    return None


def _stage(
    stage_id: str,
    verdict: str,
    *,
    span_id: str | None = None,
    turn_id: str | None = None,
    reason: str = "",
) -> dict[str, Any]:
    item: dict[str, Any] = {"id": stage_id, "verdict": verdict}
    if span_id:
        item["span_id"] = span_id
    if turn_id:
        item["turn_id"] = turn_id
    if reason:
        item["reason"] = reason
    return item


def compute_stages(
    case: Case, evidence: AttemptEvidence
) -> tuple[list[dict[str, Any]], str | None]:
    events = evidence.events
    turn_id = _turn_of(events)
    focus = _norm(case.steps[0].focus) if case.steps and case.steps[0].focus else None
    path = _norm(case.expect.region.path)

    # 1 范围登记
    scope_event = _first_tool(events, SCOPE_TOOLS)
    if evidence.scope_registrations:
        ok = False
        if focus:
            for item in evidence.scope_registrations:
                paths = item.get("paths") or item.get("path") or []
                if isinstance(paths, str):
                    paths = [paths]
                if any(_norm(str(p)) == focus for p in paths):
                    ok = True
                    break
        else:
            ok = True
        stages_scope = _stage(
            "scope",
            "pass" if ok else "fail",
            span_id=tool_span_id(scope_event) if scope_event else None,
            turn_id=(scope_event or {}).get("turn_id") or turn_id,
            reason="" if ok else "范围登记未覆盖作者 focus",
        )
    elif scope_event:
        stages_scope = _stage(
            "scope",
            "pass",
            span_id=tool_span_id(scope_event),
            turn_id=str(scope_event.get("turn_id") or turn_id or ""),
        )
    else:
        stages_scope = _stage("scope", "skipped", turn_id=turn_id, reason="未观察到范围登记")

    # 2 读取原文
    if evidence.model_inputs_before_candidate is None:
        stages_read = _stage("read", "skipped", turn_id=turn_id, reason="无模型输入快照")
    else:
        try:
            chapter = next(v for k, v in evidence.before_files.items() if _norm(k) == path)
        except StopIteration:
            stages_read = _stage("read", "fail", turn_id=turn_id, reason=f"缺少目标文件：{path}")
        else:
            joined = "\n".join(evidence.model_inputs_before_candidate)
            read_tool = None
            for event in events:
                if event.get("type") != "tool_started" or event.get("tool") != "read_file":
                    continue
                args = event.get("args") or {}
                if _norm(str(args.get("path") or "")) == path:
                    read_tool = event
                    break
            if chapter in joined:
                stages_read = _stage(
                    "read",
                    "pass",
                    span_id=tool_span_id(read_tool) if read_tool else None,
                    turn_id=(read_tool or {}).get("turn_id") or turn_id,
                )
            else:
                stages_read = _stage(
                    "read",
                    "fail",
                    span_id=tool_span_id(read_tool) if read_tool else None,
                    turn_id=(read_tool or {}).get("turn_id") or turn_id,
                    reason="首次提交候选前输入未含目标章节全文",
                )

    # 3 提交候选
    candidate_event = _first_tool(events, CANDIDATE_TOOLS)
    has_commit_event = any(e.get("type") == "story_candidate_committed" for e in events)
    if candidate_event or has_commit_event or evidence.commit_receipts:
        stages_candidate = _stage(
            "candidate",
            "pass",
            span_id=tool_span_id(candidate_event) if candidate_event else None,
            turn_id=(candidate_event or {}).get("turn_id") or turn_id,
        )
    else:
        ask = _first_tool(events, frozenset({"ask_author"}))
        stages_candidate = _stage(
            "candidate",
            "fail",
            span_id=tool_span_id(ask) if ask else None,
            turn_id=(ask or {}).get("turn_id") or turn_id,
            reason="未提交候选",
        )

    # 4 应用核对 / 5 修补：候选未发生则未到达
    if stages_candidate["verdict"] != "pass":
        stages_verify = _stage("verify", "not_reached", turn_id=turn_id)
        stages_repair = _stage("repair", "not_reached", turn_id=turn_id)
    else:
        verify_event = _first_tool(events, VERIFY_TOOLS)
        if verify_event is None:
            # 有提交回执则视为核对已通过（产品路径把核对绑在提交上）
            if evidence.commit_receipts:
                stages_verify = _stage("verify", "pass", turn_id=turn_id)
            else:
                stages_verify = _stage(
                    "verify", "skipped", turn_id=turn_id, reason="未观察到独立核对步骤"
                )
        else:
            finished = next(
                (
                    e
                    for e in events
                    if e.get("type") == "tool_finished"
                    and e.get("tool_call_id") == verify_event.get("tool_call_id")
                ),
                None,
            )
            ok = finished is None or finished.get("ok") is not False
            stages_verify = _stage(
                "verify",
                "pass" if ok else "fail",
                span_id=tool_span_id(verify_event),
                turn_id=str(verify_event.get("turn_id") or turn_id or ""),
            )
        repair_event = _first_tool(events, REPAIR_TOOLS)
        if repair_event is None:
            stages_repair = _stage("repair", "not_reached", turn_id=turn_id)
        else:
            stages_repair = _stage(
                "repair",
                "pass",
                span_id=tool_span_id(repair_event),
                turn_id=str(repair_event.get("turn_id") or turn_id or ""),
            )

    # 6 提交
    if evidence.commit_receipts:
        stages_commit = _stage("commit", "pass", turn_id=turn_id)
    elif evidence.rejected_commits:
        stages_commit = _stage(
            "commit",
            "pass",
            turn_id=turn_id,
            reason="旧版本候选被拒绝",
        )
    else:
        stages_commit = _stage(
            "commit",
            "fail",
            turn_id=turn_id,
            reason="未找到经候选核对的提交回执",
        )

    stages = [
        stages_scope,
        stages_read,
        stages_candidate,
        stages_verify,
        stages_repair,
        stages_commit,
    ]
    first = next((s["id"] for s in stages if s["verdict"] == "fail"), None)
    return stages, first
