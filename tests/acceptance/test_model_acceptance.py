"""验收器必须能识别未完成任务；全部使用离线事实，不调用模型。"""

from __future__ import annotations

import runpy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from hastings.workspace.files import Workspace

MODULE = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/model_acceptance.py"))
CASES = {case.key: case for case in MODULE["CASES"]}
FIRST, THIRD = MODULE["FIRST"], MODULE["THIRD"]


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    workspace = Workspace.create(tmp_path / "作品")
    for path, text in MODULE["_FIXTURES"].items():
        MODULE["_seed"](workspace, path, text)
    return workspace


def evaluate(case, workspace, events, *, changed=(), follow=(), **kwargs):
    return MODULE["_checks"](
        case,
        workspace,
        SimpleNamespace(root=workspace.root / "state"),
        list(events),
        list(changed),
        list(follow),
        **kwargs,
    )


def terminal(status="completed"):
    return {"type": "run_finished", "status": status}


def test_edit_request_cannot_pass_without_changes(workspace: Workspace) -> None:
    rows = evaluate(
        CASES["refs_locate"],
        workspace,
        [
            {"type": "material_read", "path": FIRST},
            {"type": "material_read", "path": THIRD},
            terminal(),
        ],
    )
    assert MODULE["result_status"](rows) == "fail"
    assert next(c for c in rows if c["name"] == "要求修改的文件确实改变")["detail"] == [THIRD]


def test_allowed_path_change_is_not_enough_when_requested_text_is_absent(workspace: Workspace):
    rows = evaluate(CASES["refs_locate"], workspace, [terminal()], changed=(THIRD,))
    assert any(c["status"] == "fail" and "落盘内容" in c["name"] for c in rows)


def test_completed_edit_with_current_reads_and_expected_content_passes(workspace: Workspace):
    MODULE["_seed"](workspace, THIRD, "# 书房\n\n桌上那盏茶已经凉透。\n")
    events = [{"type": "material_read", "path": p} for p in (FIRST, THIRD)]
    events.append(terminal())
    rows = evaluate(CASES["refs_locate"], workspace, events, changed=(THIRD,))
    assert MODULE["result_status"](rows) == "pass"


def test_failed_follow_up_cannot_borrow_the_index_turn_success(workspace: Workspace) -> None:
    rows = evaluate(
        CASES["index_recall"],
        workspace,
        [
            {"type": "chapter_index_updated", "status": "valid"},
            terminal(),
        ],
        follow=[terminal("failed")],
        initial_changed=[],
    )
    assert next(c for c in rows if c["name"] == "后续轮完成")["status"] == "fail"
    assert MODULE["result_status"](rows) == "fail"


def test_first_turn_read_does_not_count_as_follow_up_read(workspace: Workspace) -> None:
    rows = evaluate(
        CASES["index_recall"],
        workspace,
        [
            {"type": "material_read", "path": FIRST},
            terminal(),
        ],
        follow=[terminal()],
        initial_changed=[],
    )
    assert next(c for c in rows if c["name"] == "后续轮重新读取第一章原文")["status"] == "fail"


def test_read_of_old_version_does_not_count_as_current_source(workspace: Workspace) -> None:
    rows = evaluate(
        CASES["index_stale"],
        workspace,
        [terminal()],
        follow=[
            {"type": "material_read", "path": FIRST, "digest": "old"},
            terminal(),
        ],
        initial_changed=[],
        index_before_follow="stale",
    )
    assert next(c for c in rows if c["name"] == "后续读取使用当前源版本")["status"] == "fail"


def test_no_write_still_requires_a_public_answer(workspace: Workspace) -> None:
    rows = evaluate(CASES["discuss_only"], workspace, [terminal()])
    assert MODULE["result_status"](rows) == "fail"
    rows = evaluate(
        CASES["discuss_only"],
        workspace,
        [
            {"type": "assistant_message", "text": "可以讨论另一种动机。"},
            terminal(),
        ],
    )
    assert MODULE["result_status"](rows) == "pass"


def test_later_scope_does_not_retroactively_authorize_write() -> None:
    events = [
        {"type": "tool_finished", "tool": "edit_file", "ok": True, "args": {"path": THIRD}},
        {"type": "task_scope_set", "grants": [{"action": "update", "path": THIRD}]},
    ]
    assert MODULE["_scope_errors"](events, {THIRD})
    assert not MODULE["_scope_errors"](events[::-1], {THIRD})


def test_deleted_file_is_not_authorized_by_update_grant() -> None:
    events = [
        {"type": "task_scope_set", "grants": [{"action": "update", "path": THIRD}]},
        {"type": "tool_finished", "tool": "delete_file", "ok": True, "args": {"path": THIRD}},
    ]
    assert MODULE["_scope_errors"](events, {THIRD})


def test_unobserved_extension_is_not_labelled_pass(workspace: Workspace) -> None:
    case = replace(CASES["sync_chapters"], expected_text={}, expected_reads=(), required_changes=())
    rows = evaluate(case, workspace, [terminal()])
    optional = next(c for c in rows if c["name"] == "在线范围追加路径")
    assert optional["status"] == "not_covered"
    assert optional["required"] is False


def test_incomplete_review_is_not_complete(workspace: Workspace) -> None:
    rows = MODULE["_review_checks"](
        [
            {"type": "review_finished", "status": "incomplete", "report_id": None},
        ],
        workspace,
        SimpleNamespace(root=workspace.root / "state"),
    )
    assert rows[0]["status"] == "partial"
    assert MODULE["result_status"](rows) != "pass"


def test_partial_and_uncovered_required_checks_are_not_passes() -> None:
    for state in ("partial", "not_covered"):
        assert (
            MODULE["result_status"]([MODULE["_check"]("检查", False, "", status=state)])
            == "partial"
        )


def test_family_is_not_complete_when_only_one_combined_scenario_ran() -> None:
    rows = MODULE["coverage"]([{"key": "discuss_only", "status": "pass"}])
    family = next(r for r in rows if r["family"] == "任务理解")
    assert family["status"] == "partial"
    assert family["cases"]["continue_unanchored"] == "not_covered"


def test_evidence_keeps_turn_sequence_and_source_version() -> None:
    event = {
        "type": "material_read",
        "seq": 4,
        "turn_id": "t2",
        "path": FIRST,
        "digest": "new",
        "start_char": 0,
        "end_char": 10,
    }
    assert MODULE["_brief"]([event]) == [event]
