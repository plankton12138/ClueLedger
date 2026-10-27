from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from eval.__main__ import CallBudget, main
from eval.harness.grading import (
    evaluate_rule,
    grade_attempt,
    grade_step,
    metrics,
    read_spans,
    status,
)
from eval.harness.reporting import aggregate, build_report
from eval.harness.runtime import intervention, run_attempt
from eval.harness.schema import ROOT, Case, Check, Step, fixture_files, load_cases
from pydantic import ValidationError

from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace import agent
from hastings.workspace.context import build_assembled_context
from hastings.workspace.files import Workspace
from tau_agent.messages import AssistantMessage, TextContent, ToolCall, Usage
from tau_agent.provider_events import AssistantDoneEvent, AssistantStartEvent

FIRST = "正文/0001 雨夜.md"
THIRD = "正文/0003 书房.md"


def evidence() -> dict[str, Any]:
    return {
        "turn_id": "t1",
        "before": {FIRST: {"text": "头部关键证据尾部", "digest": "current"}},
        "after": {FIRST: {"text": "头部关键证据尾部", "digest": "current"}},
        "events": [
            {"type": "assistant_message", "text": "已完成。"},
            {"type": "run_finished", "status": "completed"},
        ],
        "details": {},
        "index_status": {},
        "coverage": [],
        "assemblies": [{}],
        "faults": [],
    }


def deliver(item: dict[str, Any], tool: str, payload: dict[str, Any], key: str) -> None:
    item["events"].append({"type": "model_request_started", "input_detail": key})
    item["details"][key] = {
        "system": "",
        "tools": [],
        "messages": [
            {
                "role": "toolResult",
                "toolName": tool,
                "isError": False,
                "content": [{"type": "text", "text": json.dumps(payload)}],
            }
        ],
    }


def test_all_cases_and_suites_are_loadable() -> None:
    cases, digest = load_cases()
    # 0.9 增加了压缩语义冒烟，总数跟着变；数量断言用来挡住误删。
    assert len(cases) == 30 and len(digest) == 64
    assert any(case.id == "module_only_first_chapter" for case in cases)
    assert not any(case.id.startswith("overview_") for case in cases)
    for case in cases:
        assert fixture_files(case) or case.modules
    for suite in (ROOT / "suites").glob("*.json"):
        data = json.loads(suite.read_text(encoding="utf-8"))
        assert set(data["cases"]) <= {c.id for c in cases}


def test_expand_step_requires_exactly_one_idea_and_no_other_material() -> None:
    """只读展开的受理条件与产品一致：一条灵感，且不夹带章节或索引目标。"""

    with pytest.raises(ValidationError):
        Step(text="展开", purpose="expand_idea")
    with pytest.raises(ValidationError):
        Step(
            text="展开",
            purpose="expand_idea",
            idea_references=["老板娘的旧识", "灯塔"],
        )
    with pytest.raises(ValidationError):
        Step(
            text="展开",
            purpose="expand_idea",
            idea_references=["老板娘的旧识"],
            selected=FIRST,
        )
    step = Step(text="展开", purpose="expand_idea", idea_references=["老板娘的旧识"])
    assert step.purpose == "expand_idea"


def test_tool_not_called_reads_started_tools() -> None:
    rule = Check(id="no_tools", kind="tool_not_called", value="search_ideas,save_idea")
    item = evidence()
    assert evaluate_rule(rule, item, None)["status"] == "pass"
    item["events"].append({"type": "tool_started", "tool": "save_idea"})
    row = evaluate_rule(rule, item, None)
    assert row["status"] == "fail"
    assert row["detail"]["called"] == ["save_idea"]


def test_case_rejects_paths_outside_workspace_and_unknown_fields() -> None:
    for path in ("../secret.json", "C:/outside.md", "/absolute.md", "a\\b.md"):
        with pytest.raises(ValidationError):
            Case(id="bad", title="bad", family="bad", files={path: ""}, steps=[Step(text="x")])
    with pytest.raises(ValidationError):
        Step.model_validate({"text": "x", "allowed_change": [THIRD]})
    with pytest.raises(ValidationError):
        Step(text="x", required_changes=[THIRD])


def test_old_version_and_post_write_reads_do_not_count() -> None:
    item = evidence()
    payload = {
        "path": FIRST,
        "digest": "old",
        "start_char": 0,
        "end_char": 8,
        "text": "头部关键证据尾部",
    }
    deliver(item, "read_file", payload, "old")
    item["events"].append({"type": "file_changed", "changed": True})
    deliver(item, "read_file", {**payload, "digest": "current"}, "new")
    assert read_spans(item, FIRST) == []


def test_fragmented_current_reads_cover_evidence_but_not_gaps() -> None:
    item = evidence()
    rule = Check(id="evidence", kind="read_quote", path=FIRST, value="关键证据")
    text = item["before"][FIRST]["text"]
    for start, end in ((0, 4), (4, 8)):
        deliver(
            item,
            "read_file",
            {
                "path": FIRST,
                "digest": "current",
                "start_char": start,
                "end_char": end,
                "text": text[start:end],
            },
            f"read-{start}",
        )
    assert evaluate_rule(rule, item, None)["status"] == "pass"
    deliver(
        item,
        "read_file",
        {
            "path": FIRST,
            "digest": "current",
            "start_char": 5,
            "end_char": 8,
            "text": text[5:8],
        },
        "read-4",
    )
    assert evaluate_rule(rule, item, None)["status"] == "fail"


def test_preread_counts_only_with_matching_frozen_digest() -> None:
    item = evidence()
    item["events"] = [
        {
            "type": "context_prepared",
            "references": [{"path": FIRST, "digest": "current"}],
            "provided": [{"path": FIRST, "start": 0, "end": 8, "length": 8}],
        }
    ]
    item["events"].append({"type": "model_request_started", "input_detail": "snapshot"})
    item["details"]["snapshot"] = {"system": "头部关键证据尾部", "messages": []}
    assert read_spans(item, FIRST) == [(0, 8)]
    item["events"][0]["references"][0]["digest"] = "old"
    assert read_spans(item, FIRST) == []


def test_final_content_does_not_hide_outside_write_then_restore() -> None:
    item = evidence()
    item["events"].insert(0, {"type": "file_changed", "action": "update", "path": FIRST})
    rows = grade_step(Step(text="只讨论"), item, "k/step-1", {})
    assert next(r for r in rows if r["id"] == "allowed_final_changes")["status"] == "pass"
    assert next(r for r in rows if r["id"] == "trajectory_scope")["status"] == "fail"


def test_later_grant_cannot_authorize_earlier_write() -> None:
    item = evidence()
    item["events"][:0] = [
        {"type": "file_changed", "action": "update", "path": FIRST},
        {"type": "task_scope_set", "grants": [{"action": "update", "path": FIRST}]},
    ]
    rows = grade_step(Step(text="修改", allowed_changes=[FIRST]), item, "k/step-1", {})
    assert next(r for r in rows if r["id"] == "scope_before_write")["status"] == "fail"


def test_blocked_unauthorized_attempt_is_separate_from_actual_mutation() -> None:
    item = evidence()
    item["events"][:0] = [
        {"type": "tool_started", "tool": "delete_file", "args": {"path": FIRST}},
        {"type": "tool_finished", "tool": "delete_file", "ok": False},
    ]
    rows = grade_step(Step(text="只讨论"), item, "k/step-1", {})
    assert next(r for r in rows if r["id"] == "decision_scope")["status"] == "fail"
    assert next(r for r in rows if r["id"] == "trajectory_scope")["status"] == "pass"
    assert metrics([item])["blocked_write_calls"] == 1


def test_extra_edit_on_allowed_path_fails_exact_replacement() -> None:
    item = evidence()
    item["after"][FIRST] = {"text": "头部新的证据尾部以及额外改动", "digest": "new"}
    step = Step(
        text="只替换", allowed_changes=[FIRST], exact_replacements={FIRST: {"关键": "新的"}}
    )
    rows = grade_step(step, item, "k/step-1", {})
    assert next(r for r in rows if r["id"].startswith("exact_replace"))["status"] == "fail"


def test_needs_input_counts_only_when_the_step_expects_a_pause() -> None:
    item = evidence()
    item["events"] = [
        {
            "type": "author_input_requested",
            "question_id": "q_1",
            "question": "凶手选谁？",
            "options": ["管家", "医生"],
        },
        {"type": "run_finished", "status": "needs_input"},
    ]
    paused = grade_step(Step(text="先问我", expect_needs_input=True), item, "k/step-1", {})
    assert next(r for r in paused if r["id"] == "terminal")["status"] == "pass"
    assert next(r for r in paused if r["id"] == "public_answer")["status"] == "pass"

    unexpected = grade_step(Step(text="先问我"), item, "k/step-1", {})
    assert next(r for r in unexpected if r["id"] == "terminal")["status"] == "fail"


def test_manual_semantics_require_named_evidence_based_review() -> None:
    rule = Check(id="clarify", kind="manual", rubric="必须询问是哪位同名人物")
    item = evidence()
    assert evaluate_rule(rule, item, None)["status"] == "pending_review"
    assert evaluate_rule(rule, item, {"verdict": "pass"})["status"] == "pending_review"
    review = {
        "verdict": "pass",
        "reviewer": "author",
        "reason": "明确问了对象",
        "evidence": "assistant_message:2",
    }
    assert evaluate_rule(rule, item, review)["status"] == "pass"
    review["verdict"] = "uncertain"
    assert evaluate_rule(rule, item, review)["status"] == "pending_review"
    assert status([{"status": "pass"}, {"status": "pending_review"}]) == "pending_review"


def test_missing_stage_and_early_coverage_cannot_pass() -> None:
    item = evidence()
    rule = Check(id="coverage", kind="coverage", domain="timeline", path=FIRST, value="empty")
    item["coverage"] = [
        {
            "domain": "timeline",
            "path": FIRST,
            "outcome": "empty",
            "freshness": "valid",
            "task_id": "old",
        }
    ]
    assert evaluate_rule(rule, item, None)["status"] == "fail"
    case = Case(id="two", title="two", family="two", steps=[Step(text="一"), Step(text="二")])
    assert grade_attempt(case, [item], "two")["status"] == "fail"


def test_missing_usage_stays_unknown_and_failures_count_in_cost_denominator() -> None:
    item = evidence()
    item["events"] += [
        {"type": "model_request_started"},
        {"type": "model_request_finished", "usage": None},
    ]
    measured = metrics([item])
    assert measured["input_tokens"] is None and measured["usage_complete"] is False
    assert metrics([])["input_tokens"] is None
    measured.update(usage_complete=True, input_tokens=100, output_tokens=20)
    results = [{"status": s, "duration_seconds": 1, "metrics": measured} for s in ("pass", "fail")]
    report = aggregate(results, 3)
    assert report["success_rate"] == 1 / 3
    assert report["counts"]["not_run"] == 1
    assert report["tokens_per_success"] == 240


def test_cached_input_is_included_in_total_input() -> None:
    item = evidence()
    item["events"] += [
        {"type": "model_request_started"},
        {
            "type": "model_request_finished",
            "usage": {
                "input": 10,
                "cache_read": 80,
                "cache_write": 10,
                "output": 20,
            },
        },
    ]
    measured = metrics([item])
    assert measured["fresh_input_tokens"] == 10
    assert measured["input_tokens"] == 100


def test_unrun_jobs_are_visible() -> None:
    job = {"key": "x", "variant": "current", "case": "x", "family": "a", "layer": "context"}
    report = build_report({"jobs": [job]}, [])
    assert report["unexecuted"] == ["x"]
    assert report["groups"]["variant"]["current"]["counts"]["not_run"] == 1


def test_ablation_changes_actual_assembly_and_restores_builder(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path / "book")
    original = agent.build_turn_assembly
    case = Case(id="ablation", title="x", family="x", steps=[Step(text="x")])
    trace: dict[str, Any] = {"assemblies": [], "faults": []}
    with intervention(case, "no_index", trace):
        result = agent.build_turn_assembly(workspace, index_notes="索引内容")
        assert "index" not in {p.key for p in result.parts}
        assert "索引内容" not in result.text
        assert trace["assemblies"][0] == {p.key: p.text for p in result.parts}
    assert agent.build_turn_assembly is original
    assert "index" in {p.key for p in original(workspace, index_notes="索引内容").parts}
    assert build_assembled_context(workspace).profile == "writer"


class ScriptedProvider:
    def __init__(self, replies: list[AssistantMessage]) -> None:
        self.replies = iter(replies)
        self.inputs: list[Any] = []

    async def stream_response(self, **kwargs: Any) -> Any:
        self.inputs.append(kwargs)
        yield AssistantStartEvent(partial=AssistantMessage())
        reply = next(self.replies)
        assert reply.stop_reason in {"stop", "length", "toolUse"}
        yield AssistantDoneEvent(reason=reply.stop_reason, message=reply)

    async def aclose(self) -> None:
        pass


def test_runner_executes_product_tools_and_saves_self_contained_evidence(tmp_path: Path) -> None:
    cases, _ = load_cases()
    case = next(c for c in cases if c.id == "testimony")
    provider = ScriptedProvider(
        [
            AssistantMessage(
                content=[ToolCall(id="r1", name="read_file", arguments={"path": FIRST})],
                stop_reason="toolUse",
            ),
            AssistantMessage(
                content=[TextContent(text="只是林远的说法，尚不能确定。")],
                usage=Usage(input=12, output=8),
            ),
        ]
    )
    result = asyncio.run(
        run_attempt(
            case,
            "current",
            1,
            factory=lambda: ModelSnapshot("controlled", "controlled", "scripted", provider),
            work=tmp_path / "work",
            out=tmp_path / "out",
            max_turns=8,
            timeout=10,
        )
    )
    assert result["status"] == "pending_review"
    assert result["metrics"]["tool_calls"] == 1
    saved = json.loads(
        (tmp_path / "out" / result["evidence"] / "step-1.json").read_text(encoding="utf-8")
    )
    assert saved["before"][FIRST]["text"]
    assert saved["details"] and saved["assemblies"]
    snapshots = [v for v in saved["details"].values() if isinstance(v, dict) and "system" in v]
    assert len(snapshots) == 2
    assert all("rubric" not in json.dumps(s, ensure_ascii=False) for s in snapshots)


def test_fault_is_injected_once_and_tool_can_recover(tmp_path: Path) -> None:
    case = Case(
        id="fault",
        title="fault",
        family="fault",
        fail_first_read=THIRD,
        steps=[Step(text="只读第三章")],
    )

    def call(name: str) -> AssistantMessage:
        return AssistantMessage(
            content=[ToolCall(id=name, name="read_file", arguments={"path": THIRD})],
            stop_reason="toolUse",
        )

    provider = ScriptedProvider(
        [call("r1"), call("r2"), AssistantMessage(content=[TextContent(text="已读取。")])]
    )
    result = asyncio.run(
        run_attempt(
            case,
            "current",
            1,
            factory=lambda: ModelSnapshot("controlled", "controlled", "scripted", provider),
            work=tmp_path / "work",
            out=tmp_path / "out",
            max_turns=8,
            timeout=10,
        )
    )
    assert result["status"] == "pass"
    assert result["metrics"]["tool_errors"] == 1
    assert result["metrics"]["tool_calls"] == 2


def test_call_budget_never_invokes_provider_after_limit() -> None:
    provider = ScriptedProvider([AssistantMessage(content=[TextContent(text="ok")])])
    budget = CallBudget(1)
    wrapped = budget.wrap(provider)

    async def consume() -> None:
        async for _ in wrapped.stream_response():
            pass
        with pytest.raises(RuntimeError, match="EVAL_REQUEST_BUDGET"):
            async for _ in wrapped.stream_response():
                pass

    asyncio.run(consume())
    assert budget.used == 1 and len(provider.inputs) == 1


def test_offline_cli_does_not_load_model_config(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.setattr("sys.argv", ["eval", "list", "--suite", "smoke"])
    assert main() == 0
    assert "cross_chapter" in capsys.readouterr().out


def test_claimed_preread_without_actual_input_is_not_credited() -> None:
    item = evidence()
    item["events"].insert(
        0,
        {
            "type": "context_prepared",
            "references": [{"path": FIRST, "digest": "current"}],
            "provided": [{"path": FIRST, "start": 0, "end": 8, "length": 8}],
        },
    )
    assert read_spans(item, FIRST) == []
    rows = grade_step(Step(text="参考"), item, "k/step-1", {})
    assert next(r for r in rows if r["id"] == "preread_delivered")["status"] == "fail"


def test_search_evidence_can_locate_fact_but_cannot_replace_full_read() -> None:
    item = evidence()
    deliver(
        item,
        "search_text",
        {
            "hits": [{"path": FIRST, "line": 1, "text": "头部关键证据尾部"}],
        },
        "search",
    )
    source = Check(id="source", kind="source_quote", path=FIRST, value="关键证据")
    full = Check(id="full", kind="full_read", path=FIRST)
    assert evaluate_rule(source, item, None)["status"] == "pass"
    assert evaluate_rule(full, item, None)["status"] == "fail"
    deliver(
        item,
        "search_text",
        {
            "hits": [{"path": FIRST, "line": 99, "text": "头部关键证据尾部"}],
        },
        "search",
    )
    assert evaluate_rule(source, item, None)["status"] == "fail"


def test_read_record_without_model_delivery_cannot_prove_reading() -> None:
    item = evidence()
    item["events"].insert(
        0,
        {
            "type": "material_read",
            "path": FIRST,
            "digest": "current",
            "start_char": 0,
            "end_char": 8,
        },
    )
    assert read_spans(item, FIRST) == []


def test_cli_run_rescore_and_unknown_review_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ScriptedProvider(
        [
            AssistantMessage(
                content=[ToolCall(id="r1", name="read_file", arguments={"path": FIRST})],
                stop_reason="toolUse",
            ),
            AssistantMessage(
                content=[TextContent(text="客房只是他自己的声称，尚无人证实，不能确定。")]
            ),
        ]
    )
    book = SimpleNamespace(
        selection=lambda: SimpleNamespace(connection_id="controlled", model="scripted"),
        profile=lambda _: SimpleNamespace(
            base_url="https://example.invalid", api="openai-completions"
        ),
    )
    monkeypatch.setattr(
        "hastings.server.__main__.load_provider_book", lambda *_: (book, None, None)
    )
    monkeypatch.setattr(
        "hastings.server.file_app.configured_provider_factory",
        lambda _: lambda: ModelSnapshot("controlled", "controlled", "scripted", provider),
    )
    out = tmp_path / "report"
    monkeypatch.setattr(
        "sys.argv",
        [
            "eval",
            "run",
            "--case",
            "testimony",
            "--config",
            str(tmp_path / "controlled-config.yaml"),
            "--out",
            str(out),
            "--work-root",
            str(tmp_path / "work"),
        ],
    )
    assert main() == 1
    original = (out / "report.json").read_bytes()
    reviews = tmp_path / "reviews.json"
    reviews.write_text(
        json.dumps(
            {
                "testimony--current--1/step-1/claim_not_fact": {
                    "verdict": "pass",
                    "reviewer": "test reviewer",
                    "reason": "仅声称，不能确定",
                    "evidence": "assistant_message",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.argv", ["eval", "score", str(out), "--reviews", str(reviews)])
    assert main() == 0
    assert (out / "report.json").read_bytes() == original
    rescored = json.loads((out / "rescored.json").read_text(encoding="utf-8"))
    assert rescored["summary"]["counts"]["pass"] == 1
    reviews.write_text('{"unknown": {}}', encoding="utf-8")
    assert main() == 2
