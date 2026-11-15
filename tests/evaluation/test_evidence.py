"""证据包落盘与离线重载：不调模型。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from evaluation.harness.checks import grade_attempt
from evaluation.harness.client import AcceptedTurn
from evaluation.harness.evidence import (
    attempt_paths,
    file_diff,
    flatten_model_input,
    load_attempt_evidence,
    write_evidence_pack,
)
from evaluation.harness.runtime import AttemptRunner, CallBudget
from evaluation.harness.schema import load_case


def test_file_diff_marks_changed_and_unchanged() -> None:
    before = {"a.md": "one\n", "b.md": "same\n"}
    after = {"a.md": "two\n", "b.md": "same\n"}
    diff = file_diff(before, after)
    assert diff["changed"] == ["a.md"]
    assert diff["unchanged"] == ["b.md"]
    assert diff["added"] == []
    assert diff["removed"] == []


def test_flatten_model_input_joins_system_and_messages() -> None:
    text = flatten_model_input(
        {
            "system": "系统提示",
            "messages": [
                {"role": "user", "content": "林岚推开客栈的门"},
                {"role": "assistant", "content": [{"type": "text", "text": "好的"}]},
            ],
        }
    )
    assert "系统提示" in text
    assert "林岚推开客栈的门" in text
    assert "好的" in text


def test_write_and_load_evidence_pack(tmp_path: Path) -> None:
    attempt_dir = tmp_path / "revise_snow--1"
    work = attempt_dir / "work"
    work.mkdir(parents=True)
    (work / "正文").mkdir()
    before = {"正文/0003.md": "前文。\n林岚推开客栈的门，雨。\n「你昨晚去了哪里？」\n"}
    after = {"正文/0003.md": "前文。\n林岚推开客栈的门，雪。\n「你昨晚去了哪里？」\n"}
    (work / "正文" / "0003.md").write_text(after["正文/0003.md"], encoding="utf-8")
    events = [
        {"seq": 1, "type": "model_request_started", "operation_id": "op1", "input_detail": "aabb" * 8},
        {"seq": 2, "type": "story_candidate_committed", "path": "正文/0003.md"},
        {"seq": 3, "type": "run_finished", "turn_id": "turn-1", "recording": "committed"},
    ]
    traces = {"turn-1": {"turn": {"id": "turn-1", "status": "completed"}, "events": []}}
    inputs = {"op1": {"system": "sys", "messages": [{"role": "user", "content": before["正文/0003.md"]}]}}
    write_evidence_pack(
        attempt_dir,
        meta={
            "case_id": "revise_snow",
            "attempt": 1,
            "workspace_id": "ws-1",
            "conversation_id": "conv-1",
            "turns": [{"turn_id": "turn-1", "author": "改成雪夜"}],
        },
        before=before,
        events=events,
        traces=traces,
        inputs=inputs,
    )
    paths = attempt_paths(attempt_dir)
    assert paths.before.is_file()
    assert paths.diff.is_file()
    assert paths.events.is_file()
    assert (paths.traces / "turn-1.json").is_file()
    assert (paths.inputs / "op1.json").is_file()
    assert json.loads(paths.diff.read_text(encoding="utf-8"))["changed"] == ["正文/0003.md"]

    evidence = load_attempt_evidence(attempt_dir)
    assert evidence.before_files == before
    assert evidence.after_files["正文/0003.md"].startswith("前文。")
    assert evidence.commit_receipts
    assert evidence.model_inputs_before_candidate is not None
    assert before["正文/0003.md"] in "\n".join(evidence.model_inputs_before_candidate)


def _seed_case(tmp: Path) -> Path:
    work = tmp / "works" / "base" / "inn"
    chapter = work / "正文" / "0003.md"
    chapter.parent.mkdir(parents=True)
    chapter.write_text(
        "前文保留。\n林岚推开客栈的门，雨还在下。\n「你昨晚去了哪里？」\n",
        encoding="utf-8",
    )
    case_path = tmp / "cases" / "snow.yaml"
    case_path.parent.mkdir(parents=True)
    case_path.write_text(
        yaml.safe_dump(
            {
                "id": "revise_snow",
                "version": 1,
                "work": "base/inn",
                "history": "short",
                "steps": [{"author": "改成雪夜", "focus": "正文/0003.md"}],
                "expect": {
                    "region": {
                        "path": "正文/0003.md",
                        "from": "林岚推开客栈的门",
                        "to": "END",
                    },
                    "preserve": ["「你昨晚去了哪里？」"],
                },
                "checks": [
                    {"id": "only_region_changed", "level": "must"},
                    {"id": "region_actually_changed", "level": "must"},
                    {"id": "preserved_verbatim", "level": "must"},
                    {"id": "committed_via_candidate", "level": "must"},
                    {"id": "chapter_in_model_input", "level": "quality"},
                    {"id": "snow_present", "level": "human", "question": "是否雪夜？"},
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return case_path


class PackFakeClient:
    def __init__(self) -> None:
        self._path = ""
        self._workspace = "ws-1"

    def open_workspace(self, path: str) -> str:
        self._path = path
        return self._workspace

    def set_capture_inputs(self, workspace_id: str, enabled: bool) -> None:
        assert workspace_id == self._workspace
        assert enabled is True

    def start_turn(
        self,
        workspace_id: str,
        *,
        text: str,
        selected_path: str | None,
        conversation_id: str | None = None,
    ) -> AcceptedTurn:
        del workspace_id, text, selected_path
        target = Path(self._path) / "正文" / "0003.md"
        target.write_text(
            "前文保留。\n林岚推开客栈的门，雪落在石阶上。\n「你昨晚去了哪里？」\n",
            encoding="utf-8",
        )
        return AcceptedTurn(
            workspace_id=self._workspace,
            conversation_id=conversation_id or "conv-1",
            turn_id="turn-1",
        )

    def poll_events(
        self,
        workspace_id: str,
        conversation_id: str,
        *,
        after_seq: int = 0,
    ) -> list[dict[str, Any]]:
        del workspace_id, conversation_id, after_seq
        return [
            {
                "seq": 1,
                "type": "model_request_started",
                "operation_id": "op1",
                "input_detail": "aabbccddeeff00112233445566778899",
                "turn_id": "turn-1",
            },
            {
                "seq": 2,
                "type": "model_request_finished",
                "operation_id": "op1",
                "turn_id": "turn-1",
            },
            {
                "seq": 3,
                "type": "story_candidate_committed",
                "path": "正文/0003.md",
                "turn_id": "turn-1",
            },
            {
                "seq": 4,
                "type": "run_finished",
                "turn_id": "turn-1",
                "status": "completed",
                "recording": "committed",
                "changed_paths": ["正文/0003.md"],
            },
        ]

    def read_turn_trace(
        self, workspace_id: str, conversation_id: str, turn_id: str
    ) -> dict[str, Any]:
        del workspace_id, conversation_id
        return {"turn": {"id": turn_id, "status": "completed"}, "events": [{"span_id": "model:op1"}]}

    def read_attachment(self, workspace_id: str, key: str) -> Any:
        del workspace_id
        assert key == "aabbccddeeff00112233445566778899"
        return {
            "system": "写作助手",
            "messages": [
                {
                    "role": "user",
                    "content": "前文保留。\n林岚推开客栈的门，雨还在下。\n「你昨晚去了哪里？」\n",
                }
            ],
        }

    def close(self) -> None:
        return None


def test_runner_writes_evidence_pack_under_work_and_evidence(tmp_path: Path) -> None:
    case_path = _seed_case(tmp_path)
    case = load_case(case_path, works_root=tmp_path / "works")
    runner = AttemptRunner(
        client=PackFakeClient(),  # type: ignore[arg-type]
        attempts_root=tmp_path / "attempts",
        budget=CallBudget(max_requests=5),
        sleep=lambda _: None,
    )
    result = runner.run(case, attempt=1)
    assert result.status == "completed"
    assert result.work_dir is not None
    attempt_dir = tmp_path / "attempts" / "revise_snow--1"
    assert result.work_dir == attempt_dir / "work"
    paths = attempt_paths(attempt_dir)
    assert paths.meta.is_file()
    assert paths.before.is_file()
    assert paths.diff.is_file()
    assert paths.events.is_file()
    assert (paths.traces / "turn-1.json").is_file()
    assert (paths.inputs / "op1.json").is_file()
    assert result.grade is not None
    by_id = {item.check_id: item.verdict for item in result.grade.checks}
    assert by_id["region_actually_changed"] == "pass"
    assert by_id["chapter_in_model_input"] == "pass"

    reloaded = load_attempt_evidence(attempt_dir)
    again = grade_attempt(case, reloaded)
    assert {item.check_id: item.verdict for item in again.checks} == by_id


def test_regrade_report_from_evidence_pack(tmp_path: Path) -> None:
    from evaluation.harness.report import build_report, regrade_report

    case_path = _seed_case(tmp_path)
    case = load_case(case_path, works_root=tmp_path / "works")
    runner = AttemptRunner(
        client=PackFakeClient(),  # type: ignore[arg-type]
        attempts_root=tmp_path / "runs" / "attempts",
        budget=CallBudget(max_requests=5),
        sleep=lambda _: None,
    )
    result = runner.run(case, attempt=1)
    run_dir = tmp_path / "runs"
    report = build_report([result], mode="smoke")
    # 故意把程序项改成错误结论，验证离线重评能纠正
    for check in report["attempts"][0]["checks"]:
        if check["level"] == "must":
            check["verdict"] = "fail"
            check["reason"] = "故意写错"
    report["attempts"][0]["outcome"] = "fail"
    updated = regrade_report(report, run_dir, cases={case.id: case})
    by_id = {c["id"]: c["verdict"] for c in updated["attempts"][0]["checks"]}
    assert by_id["region_actually_changed"] == "pass"
    assert by_id["committed_via_candidate"] == "pass"
    assert by_id["snow_present"] == "pending_review"
    assert updated["attempts"][0]["outcome"] == "pending_review"
