"""运行时编排：假客户端 + 本地文件，不调模型、不启真实服务。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from evaluation.harness.client import AcceptedTurn, EvalClientError
from evaluation.harness.evidence import extract_commit_receipts, snapshot_files
from evaluation.harness.runtime import AttemptRunner, CallBudget, prepare_work_copy
from evaluation.harness.schema import load_case


class FakeClient:
    def __init__(self, work_mutator: Any = None) -> None:
        self.opened: list[str] = []
        self.capture: list[bool] = []
        self.turns: list[dict[str, Any]] = []
        self._work_mutator = work_mutator
        self._workspace = "ws-1"

    def open_workspace(self, path: str) -> str:
        self.opened.append(path)
        self._path = path
        return self._workspace

    def set_capture_inputs(self, workspace_id: str, enabled: bool) -> None:
        assert workspace_id == self._workspace
        self.capture.append(enabled)

    def start_turn(
        self,
        workspace_id: str,
        *,
        text: str,
        selected_path: str | None,
        conversation_id: str | None = None,
    ) -> AcceptedTurn:
        self.turns.append(
            {
                "text": text,
                "selected_path": selected_path,
                "conversation_id": conversation_id,
            }
        )
        if self._work_mutator:
            self._work_mutator(Path(self._path))
        return AcceptedTurn(
            workspace_id=workspace_id,
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
                "type": "model_request_finished",
                "turn_id": "turn-1",
            },
            {
                "seq": 2,
                "type": "story_candidate_committed",
                "path": "正文/0003.md",
                "turn_id": "turn-1",
            },
            {
                "seq": 3,
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
        return {"turn": {"id": turn_id, "status": "completed"}, "events": []}

    def read_attachment(self, workspace_id: str, key: str) -> Any:
        del workspace_id, key
        return {"system": "", "messages": []}

    def close(self) -> None:
        return None


def _seed_case(tmp: Path) -> Path:
    work = tmp / "works" / "base" / "inn"
    chapter = work / "正文" / "0003.md"
    chapter.parent.mkdir(parents=True)
    chapter.write_text(
        "前文保留。\n林岚推开客栈的门，雨还在下。\n「你昨晚去了哪里？」\n",
        encoding="utf-8",
    )
    (work / "写作约定.md").write_text("约定。\n", encoding="utf-8")
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
                    {"id": "preserved_verbatim", "level": "must"},
                    {"id": "committed_via_candidate", "level": "must"},
                    {"id": "snow_present", "level": "human", "question": "是否雪夜？"},
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return case_path


def test_snapshot_and_copy(tmp_path: Path) -> None:
    case_path = _seed_case(tmp_path)
    case = load_case(case_path, works_root=tmp_path / "works")
    dest = tmp_path / "attempt"
    prepare_work_copy(case.work_dir, dest)
    files = snapshot_files(dest)
    assert "正文/0003.md" in files
    assert "写作约定.md" in files


def test_extract_commit_receipts() -> None:
    events = [
        {"type": "story_candidate_committed", "path": "正文/0003.md"},
        {"type": "run_finished", "recording": "no_change", "changed_paths": []},
    ]
    assert extract_commit_receipts(events) == [
        {"path": "正文/0003.md", "via": "candidate", "raw_type": "story_candidate_committed"}
    ]


def test_runner_grades_after_fake_turn(tmp_path: Path) -> None:
    case_path = _seed_case(tmp_path)
    case = load_case(case_path, works_root=tmp_path / "works")

    def mutate(work: Path) -> None:
        target = work / "正文" / "0003.md"
        target.write_text(
            "前文保留。\n林岚推开客栈的门，雪落在石阶上。\n「你昨晚去了哪里？」\n",
            encoding="utf-8",
        )

    client = FakeClient(work_mutator=mutate)
    runner = AttemptRunner(
        client=client,  # type: ignore[arg-type]
        attempts_root=tmp_path / "attempts",
        budget=CallBudget(max_requests=5),
        sleep=lambda _: None,
    )
    result = runner.run(case, attempt=1)
    assert result.status == "completed"
    assert result.grade is not None
    assert result.grade.outcome == "pending_review"
    assert client.capture == [True]
    assert client.turns[0]["text"] == "改成雪夜"
    by_id = {item.check_id: item.verdict for item in result.grade.checks}
    assert by_id["only_region_changed"] == "pass"
    assert by_id["preserved_verbatim"] == "pass"
    assert by_id["committed_via_candidate"] == "pass"
    assert by_id["snow_present"] == "pending_review"


def test_runner_not_run_when_budget_exhausted(tmp_path: Path) -> None:
    case_path = _seed_case(tmp_path)
    case = load_case(case_path, works_root=tmp_path / "works")
    runner = AttemptRunner(
        client=FakeClient(),  # type: ignore[arg-type]
        attempts_root=tmp_path / "attempts",
        budget=CallBudget(max_requests=0),
        sleep=lambda _: None,
    )
    result = runner.run(case, attempt=1)
    assert result.status == "not_run"


def test_runner_infrastructure_error(tmp_path: Path) -> None:
    case_path = _seed_case(tmp_path)
    case = load_case(case_path, works_root=tmp_path / "works")

    class Boom(FakeClient):
        def open_workspace(self, path: str) -> str:
            raise EvalClientError("服务未启动")

    runner = AttemptRunner(
        client=Boom(),  # type: ignore[arg-type]
        attempts_root=tmp_path / "attempts",
        budget=CallBudget(max_requests=5),
        sleep=lambda _: None,
    )
    result = runner.run(case, attempt=1)
    assert result.status == "infrastructure_error"
    assert "服务未启动" in result.error
