import asyncio
import hashlib
import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from eval.benchmark import workbench_api, workbench_runner
from eval.benchmark.evaluate import TASK_PATH, evaluate, read_json, validate_evidence
from fastapi.testclient import TestClient

from hastings.infrastructure.credentials import InMemoryCredentialStore
from hastings.server.provider_runtime import ModelSnapshot
from tau_agent.messages import AssistantMessage, TextContent, ThinkingContent, ToolCall
from tau_agent.provider_events import AssistantDoneEvent, AssistantErrorEvent, AssistantStartEvent

_MANUSCRIPT = "## 铁律\n第三人称限制视角。\n\n## 路线\n1. 暴雪封山。\n2. 老板失踪。\n"
_REPLY = "第一版全书结构已保存，真相和结局待定。"
_INTENT = {"X-Workbench-Intent": "local"}
_SELECTION = {"library_id": "writing", "task_id": "T01", "revision": 1}


@pytest.fixture
def model_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    credentials = InMemoryCredentialStore()
    credentials.save("hastings:provider:fixture", "unrelated-current-key")
    monkeypatch.setattr(
        workbench_runner, "build_credential_store", lambda: credentials, raising=False
    )
    config = tmp_path / "models.yaml"
    config.write_text(
        "providers:\n"
        "  - name: fixture\n"
        "    base_url: https://example.invalid/private-endpoint\n"
        "    api_key: config-test-secret\n"
        "    default_model: fixture-model\n",
        encoding="utf-8",
    )
    return config


def test_prepare_checks_real_product_fixture_without_sending_or_migrating_credentials(
    model_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = model_config.read_bytes()

    def unexpected_provider_call(*args, **kwargs):
        pytest.fail("preflight must not construct a provider")

    monkeypatch.setattr(
        workbench_runner,
        "configured_provider_factory",
        lambda book: unexpected_provider_call,
        raising=False,
    )
    result = workbench_runner.prepare(read_json(TASK_PATH), model_config)
    assert result == {"valid": True, "connection_id": "fixture", "model": "fixture-model"}
    assert model_config.read_bytes() == before
    assert workbench_runner.build_credential_store().load("hastings:provider:fixture") == (
        "unrelated-current-key"
    )


def test_unsupported_content_fails_preflight_without_touching_run_directory(tmp_path: Path):
    task = deepcopy(read_json(TASK_PATH))
    task["scenario"]["messages"]["request"] += "\n"
    run_dir = tmp_path / "trial"
    result = workbench_runner.execute(task, tmp_path / "missing.yaml", run_dir)

    validate_evidence(task, result["evidence"])
    assert result["evidence"]["preflight"]["valid"] is False
    assert "T01" in result["evidence"]["preflight"]["reason"]
    assert result["started_at"] is None
    assert result["trace"] == []
    assert not run_dir.exists()
    assert workbench_runner.prepare(task, None)["valid"] is False


def install_provider(monkeypatch: pytest.MonkeyPatch, provider: Any) -> None:
    def factory(book):
        selection = book.selection()
        return lambda: ModelSnapshot(selection.connection_id, "Fixture", selection.model, provider)

    monkeypatch.setattr(workbench_runner, "configured_provider_factory", factory)


class WritingProvider:
    """Double only the inference boundary; product owns tools and persistence."""

    def __init__(self, author_text: str) -> None:
        self.author_text = author_text
        self.scope_requests = 0

    async def stream_response(self, *, tools=(), **kwargs):
        yield AssistantStartEvent(partial=AssistantMessage())
        names = {tool.name for tool in tools}
        if "set_task_scope" in names and self.scope_requests == 0:
            self.scope_requests += 1
            assert self.author_text in str(kwargs["messages"])
            chunk = ToolCall(
                id="raw-provider-call-id-private",
                name="set_task_scope",
                arguments={
                    "mode": "edit",
                    "summary": "raw-arguments-private",
                    "authorization": "请把这个想法整理成第一版全书结构并保存。",
                    "grants": [{"action": "create", "path": "STRUCTURE.md"}],
                },
            )
        elif "write_structure" in names and self.scope_requests == 1:
            self.scope_requests += 1
            # Keep this turn observably asynchronous so the runner must await the terminal event.
            await asyncio.sleep(0.1)
            chunk = ToolCall(
                id="raw-provider-write-id-private",
                name="write_structure",
                arguments={"text": _MANUSCRIPT},
            )
        else:
            yield AssistantDoneEvent(
                reason="stop",
                message=AssistantMessage(
                    content=[
                        ThinkingContent(thinking="inner-thinking-private"),
                        TextContent(text=_REPLY),
                    ],
                    stop_reason="stop",
                ),
            )
            return
        yield AssistantDoneEvent(
            reason="toolUse", message=AssistantMessage(content=[chunk], stop_reason="toolUse")
        )


@pytest.mark.parametrize(
    ("original", "changed"),
    [
        ("fixture-model", "other-model"),
        ("private-endpoint", "other-endpoint"),
        ("openai-completions", "openai-responses"),
        ("config-test-secret", "changed-credential"),
    ],
)
def test_launch_requires_approval_for_current_model_route(
    tmp_path, model_config, monkeypatch, original, changed
):
    config = model_config.read_text(encoding="utf-8") + "    api: openai-completions\n"
    model_config.write_text(config, encoding="utf-8")
    monkeypatch.setattr(
        workbench_runner, "configured_provider_factory", lambda book: lambda: pytest.fail("sent")
    )
    with TestClient(
        workbench_api.create_app(tmp_path / "state", config=model_config),
        base_url="http://127.0.0.1",
    ) as client:
        preview = client.post("/api/preflight", json=_SELECTION, headers=_INTENT).json()
        assert preview["valid"]
        assert isinstance(preview.get("approval_token"), str) and preview["approval_token"]
        assert "config-test-secret" not in json.dumps(preview)
        assert "private-endpoint" not in json.dumps(preview)
        assert client.post("/api/runs", json=_SELECTION, headers=_INTENT).status_code == 409
        assert (
            client.post(
                "/api/runs", json={**_SELECTION, "approval_token": "fake"}, headers=_INTENT
            ).status_code
            == 409
        )
        model_config.write_text(config.replace(original, changed), encoding="utf-8")
        assert (
            client.post(
                "/api/runs",
                json={**_SELECTION, "approval_token": preview["approval_token"]},
                headers=_INTENT,
            ).status_code
            == 409
        )
    assert not (tmp_path / "state" / "runs").exists()


def test_worker_rejects_configuration_drift_before_model_send(tmp_path, model_config, monkeypatch):
    execute = workbench_api.Workbench.execute

    def after_acceptance(self, *args, **kwargs):
        model_config.write_text(
            model_config.read_text(encoding="utf-8").replace("fixture-model", "other-model"),
            encoding="utf-8",
        )
        execute(self, *args, **kwargs)

    monkeypatch.setattr(workbench_api.Workbench, "execute", after_acceptance)

    def send():
        pytest.fail("changed model configuration must not send")

    monkeypatch.setattr(workbench_runner, "configured_provider_factory", lambda book: send)
    app = workbench_api.create_app(tmp_path / "state", config=model_config)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        preview = client.post("/api/preflight", json=_SELECTION, headers=_INTENT).json()
        response = client.post(
            "/api/runs",
            json={**_SELECTION, "approval_token": preview.get("approval_token")},
            headers=_INTENT,
        )
        assert response.status_code == 202
    with TestClient(app, base_url="http://127.0.0.1") as client:
        detail = client.get(f"/api/runs/{response.json()['run_id']}").json()
        assert detail["run"]["execution_status"] == "not_started"
        assert not detail["run"].get("model")
        assert not detail["run"].get("turn_id")
        assert detail["report"]["verdict"] == "invalid"


def test_api_runs_product_and_preserves_report_versions(tmp_path, model_config, monkeypatch):
    task = read_json(TASK_PATH)
    install_provider(monkeypatch, WritingProvider(task["scenario"]["messages"]["request"]))
    state = tmp_path / "state"
    headers = {"X-Workbench-Intent": "local"}
    selection = {"library_id": "writing", "task_id": "T01", "revision": 1}
    with TestClient(
        workbench_api.create_app(state, config=model_config), base_url="http://127.0.0.1"
    ) as client:
        preview = client.post("/api/preflight", json=selection, headers=headers).json()
        assert preview["valid"]
        response = client.post(
            "/api/runs",
            json={**selection, "approval_token": preview.get("approval_token")},
            headers=headers,
        )
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        url = f"/api/runs/{run_id}"
        deadline = time.monotonic() + 20
        while True:
            detail = client.get(url).json()
            if detail["run"]["execution_status"] != "running":
                break
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert detail["report"]["verdict"] == "inconclusive", detail["milestones"]
        start = detail["run"]["started_at"]
        assert start
        assert detail["run"]["model"] == "fixture-model"
        summary = read_json(state / "runs" / run_id / "summary.json")
        assert summary["connection_id"] == "fixture"
        assert preview["approval_token"] not in json.dumps(summary)
        capture = client.get(f"{url}/evidence").json()
        assert capture["structure_text"] == _MANUSCRIPT
        assert capture["author_message"] == task["scenario"]["messages"]["request"]
        submitted = {
            "evidence_sha256": detail["evidence_sha256"],
            "checks": {
                key: {"verdict": "pass", "reason": "Controlled API integration fixture."}
                for key in task["llm_graders"]
            },
        }
        assert client.post(f"{url}/review", json=submitted, headers=headers).status_code == 200
        assert client.get(url).json()["report"]["verdict"] == "pass"
        old = client.get(url, params={"report_version": detail["report_version"]}).json()
        assert old["report"]["verdict"] == "inconclusive"
        assert old["evidence_version"] == detail["evidence_version"]
        assert old["report_version"] == detail["report_version"]
        assert (
            client.post(
                f"{url}/regrade",
                json={"evidence_version": detail["evidence_version"]},
                headers=headers,
            ).status_code
            == 200
        )
        assert client.get(url).json()["run"]["started_at"] == start
        assert len(client.get("/api/runs").json()["items"]) == 1
    assert not (state / "runs" / run_id / "isolation").exists()


def test_execute_waits_then_grades_real_saved_artifacts(
    tmp_path: Path, model_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = read_json(TASK_PATH)
    author_text = task["scenario"]["messages"]["request"]
    runs = []
    for run_dir in (tmp_path / "trial-a", tmp_path / "trial-b"):
        install_provider(monkeypatch, WritingProvider(author_text))
        result = workbench_runner.execute(task, model_config, run_dir, timeout=8)
        evidence = result["evidence"]
        validate_evidence(task, evidence)
        report = evaluate(task, evidence, {}, None)
        assert report["verdict"] == "inconclusive", evidence
        assert [check["verdict"] for check in report["checks"]] == ["pass", "pass"] + [
            "unknown"
        ] * 3
        assert evidence["run"]["status"] == "completed"
        assert evidence["run"]["model"] == "fixture-model"
        assert (
            evidence["workspace_after"]["STRUCTURE.md"]
            == hashlib.sha256(_MANUSCRIPT.encode("utf-8")).hexdigest()
        )
        assert evidence["structure_text"] == _MANUSCRIPT
        assert evidence["assistant_reply"] == _REPLY
        assert evidence["author_message"] == author_text
        assert result["started_at"] and all(
            set(stage) == {"at", "type", "summary"} for stage in result["milestones"]
        )
        assert any(event["kind"] == "tool_call" for event in result["trace"])
        trace = json.dumps(result["trace"], ensure_ascii=False)
        for private in (
            "raw-provider-",
            "inner-thinking-private",
            "raw-arguments-private",
            "fixture-model",
            "config-test-secret",
            "private-endpoint",
            _REPLY,
            author_text,
        ):
            assert private not in trace
        assert (run_dir / "isolation" / "雪山温泉旅馆" / "STRUCTURE.md").is_file()
        assert not (run_dir / "evidence.json").exists()
        runs.append(evidence["run"])
    assert all(
        runs[0][key] != runs[1][key] for key in ("workspace_id", "conversation_id", "turn_id")
    )


def test_unavailable_config_prevents_any_product_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_provider():
        pytest.fail("no provider may be constructed after failed preflight")

    monkeypatch.setattr(
        workbench_runner, "configured_provider_factory", lambda book: unexpected_provider
    )
    task = read_json(TASK_PATH)
    run_dir = tmp_path / "trial"
    result = workbench_runner.execute(task, tmp_path / "missing.yaml", run_dir)
    evidence = result["evidence"]
    validate_evidence(task, evidence)
    assert evaluate(task, evidence, {}, None)["verdict"] == "invalid"
    assert "No available model" in evidence["preflight"]["reason"]
    assert result["started_at"] is None
    assert "run" not in evidence


def test_provider_failure_invalidates_without_archiving_raw_error(
    tmp_path: Path, model_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BrokenProvider:
        async def stream_response(self, **kwargs):
            yield AssistantErrorEvent(
                reason="error",
                error=AssistantMessage(
                    stop_reason="error", error_message="raw-provider-error-private"
                ),
            )

    install_provider(monkeypatch, BrokenProvider())
    task = read_json(TASK_PATH)
    result = workbench_runner.execute(task, model_config, tmp_path / "trial", timeout=8)
    evidence = result["evidence"]
    validate_evidence(task, evidence)
    assert evidence["run"]["status"] == "failed"
    assert evidence["execution_error"] == "MODEL_CALL_FAILED"
    assert evaluate(task, evidence, {}, None)["verdict"] == "invalid"
    assert "raw-provider-error-private" not in json.dumps(result)


def test_timeout_pauses_without_grading_a_partial_turn(
    tmp_path: Path, model_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class WaitingProvider:
        cancelled = False

        async def stream_response(self, **kwargs):
            try:
                yield AssistantStartEvent(partial=AssistantMessage())
                await asyncio.sleep(90)
            finally:
                self.cancelled = True

    provider = WaitingProvider()
    install_provider(monkeypatch, provider)
    task = read_json(TASK_PATH)
    result = workbench_runner.execute(task, model_config, tmp_path / "trial", timeout=0.5)
    evidence = result["evidence"]
    validate_evidence(task, evidence)
    assert evaluate(task, evidence, {}, None)["verdict"] == "inconclusive"
    assert {"workspace_after", "modules_after", "assistant_reply"}.isdisjoint(evidence)
    events = [stage["type"] for stage in result["milestones"]]
    assert (
        events.index("turn_started") < events.index("timed_out") < events.index("pause_requested")
    )
    assert provider.cancelled


def test_unreadable_module_state_stays_unknown_without_losing_workspace_evidence(
    tmp_path: Path, model_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = read_json(TASK_PATH)
    run_dir = tmp_path / "trial"

    class CorruptingProvider(WritingProvider):
        async def stream_response(self, **kwargs):
            async for event in super().stream_response(**kwargs):
                if isinstance(event, AssistantDoneEvent) and event.reason == "stop":
                    library = run_dir / "isolation" / "modules-library"
                    assert library.is_dir()
                    (library / "modules").write_text("not a module directory", encoding="utf-8")
                yield event

    install_provider(monkeypatch, CorruptingProvider(task["scenario"]["messages"]["request"]))
    result = workbench_runner.execute(task, model_config, run_dir, timeout=8)
    evidence = result["evidence"]
    validate_evidence(task, evidence)
    assert evidence["modules_after"] == {"binding_ids": []}
    report = evaluate(task, evidence, {}, None)
    assert [check["verdict"] for check in report["checks"][:2]] == ["pass", "unknown"]


def test_needs_input_is_terminal_and_runner_does_not_answer_for_the_author(
    tmp_path: Path, model_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class QuestioningProvider:
        asked = False

        async def stream_response(self, *, tools=(), **kwargs):
            yield AssistantStartEvent(partial=AssistantMessage())
            if not self.asked:
                assert "ask_author" in {tool.name for tool in tools}
                self.asked = True
                yield AssistantDoneEvent(
                    reason="toolUse",
                    message=AssistantMessage(
                        content=[
                            ToolCall(
                                id="private-question-call",
                                name="ask_author",
                                arguments={
                                    "question": "请选择最终结局",
                                    "options": ["开放", "封闭"],
                                },
                            )
                        ],
                        stop_reason="toolUse",
                    ),
                )
            else:
                yield AssistantDoneEvent(
                    reason="stop",
                    message=AssistantMessage(content=[TextContent(text="请先选择结局。")]),
                )

    install_provider(monkeypatch, QuestioningProvider())
    task = read_json(TASK_PATH)
    run_dir = tmp_path / "trial"
    result = workbench_runner.execute(task, model_config, run_dir, timeout=8)
    evidence = result["evidence"]
    validate_evidence(task, evidence)
    assert evidence["run"]["status"] == "needs_input"
    assert evaluate(task, evidence, {}, None)["verdict"] == "fail"
    assert not any(stage["type"] == "timed_out" for stage in result["milestones"])
    conversation = evidence["run"]["conversation_id"]
    (log,) = (run_dir / "isolation" / "state").rglob(f"{conversation}.jsonl")
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [row["turn_id"] for row in rows if row["type"] == "turn_started"] == [
        evidence["run"]["turn_id"]
    ]


def test_malformed_model_endpoint_is_a_preflight_failure(
    tmp_path: Path, model_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_config.write_text(
        model_config.read_text(encoding="utf-8").replace(
            "https://example.invalid/private-endpoint", "bogus-endpoint"
        ),
        encoding="utf-8",
    )

    def unexpected_provider():
        pytest.fail("malformed endpoint must be rejected before product send")

    monkeypatch.setattr(
        workbench_runner, "configured_provider_factory", lambda book: unexpected_provider
    )
    task = read_json(TASK_PATH)
    assert workbench_runner.prepare(task, model_config)["valid"] is False
    result = workbench_runner.execute(task, model_config, tmp_path / "trial")
    assert result["started_at"] is None
    assert "endpoint" in result["evidence"]["preflight"]["reason"]
    assert "bogus-endpoint" not in json.dumps(result)
