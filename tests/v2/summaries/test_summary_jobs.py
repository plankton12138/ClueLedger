"""摘要任务化（V03-4.3 收尾）：排队、执行、停止、失败与重试。

任务表是既有的 ``workflow_jobs``，执行器单独一条；测试直接驱动执行器，
不依赖 worker 循环的时序。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi.testclient import TestClient

from hastings.application.services.summary_service import SummaryDraft
from hastings.server.v2 import V2Runtime, build_v2_app

ORIGINAL = "侦探在雪夜抵达旅馆，前台没有人。"
SUMMARY_TEXT = "- 侦探抵达旅馆\n- 前台无人"


def _runtime(replies: list[Any]) -> tuple[V2Runtime, list[str]]:
    runtime = V2Runtime.memory()
    calls: list[str] = []
    queue = list(replies)

    async def summarizer(*, system: str, prompt: str) -> SummaryDraft:
        calls.append(prompt)
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return SummaryDraft(text=str(item), model="test-model")

    runtime.summaries._summarizer = summarizer  # type: ignore[assignment]
    return runtime, calls


def _client(runtime: V2Runtime) -> tuple[TestClient, str, str]:
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "摘要任务"}).json()["project_id"]
    client.post(
        f"/api/v2/projects/{pid}/chapters/import",
        json={"idempotency_key": "imp1", "title": "第一章 雪夜", "text": ORIGINAL},
    )
    chapter_id = client.get(f"/api/v2/projects/{pid}/manuscript").json()["chapters"][0][
        "chapter_id"
    ]
    return client, pid, chapter_id


def test_submit_only_queues_and_does_not_call_the_model() -> None:
    runtime, calls = _runtime([SUMMARY_TEXT])
    client, pid, chapter_id = _client(runtime)

    response = client.post(
        f"/api/v2/projects/{pid}/chapters/{chapter_id}/summary-jobs",
        json={"request_id": "job-1"},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["state"] == "queued"
    assert body["job_id"].startswith("job_")
    assert body["chapter_id"] == chapter_id
    assert calls == []


def test_execute_completes_and_records_the_result() -> None:
    runtime, calls = _runtime([SUMMARY_TEXT])
    client, pid, chapter_id = _client(runtime)
    job_id = client.post(
        f"/api/v2/projects/{pid}/chapters/{chapter_id}/summary-jobs",
        json={"request_id": "job-1"},
    ).json()["job_id"]

    finished = asyncio.run(runtime.summary_jobs.execute(job_id))

    assert finished["state"] == "completed"
    assert finished["result"]["status"] == "committed"
    assert finished["result"]["summary"]["text"] == SUMMARY_TEXT
    assert len(calls) == 1
    detail = client.get(f"/api/v2/projects/{pid}/chapters/{chapter_id}/summary").json()
    assert detail["status"] == "fresh"


def test_stop_before_run_never_calls_the_model() -> None:
    runtime, calls = _runtime([SUMMARY_TEXT])
    client, pid, chapter_id = _client(runtime)
    job_id = client.post(
        f"/api/v2/projects/{pid}/chapters/{chapter_id}/summary-jobs",
        json={"request_id": "job-1"},
    ).json()["job_id"]

    stopped = client.post(f"/api/v2/projects/{pid}/summary-jobs/{job_id}/stop").json()
    assert stopped["state"] == "cancelled"

    finished = asyncio.run(runtime.summary_jobs.execute(job_id))
    assert finished["state"] == "cancelled"
    assert calls == []
    detail = client.get(f"/api/v2/projects/{pid}/chapters/{chapter_id}/summary").json()
    assert detail["status"] == "missing"


def test_stop_during_generation_prevents_the_summary_from_landing() -> None:
    holder: dict[str, str] = {}

    async def summarizer(*, system: str, prompt: str) -> SummaryDraft:
        # 模型还在跑的时候作者点了停止：提交前的那道门必须拦下。
        await holder_runtime["runtime"].summary_jobs.stop(holder["job_id"])
        return SummaryDraft(text=SUMMARY_TEXT, model="test-model")

    holder_runtime: dict[str, V2Runtime] = {}
    runtime = V2Runtime.memory()
    holder_runtime["runtime"] = runtime
    runtime.summaries._summarizer = summarizer  # type: ignore[assignment]
    client, pid, chapter_id = _client(runtime)
    job_id = client.post(
        f"/api/v2/projects/{pid}/chapters/{chapter_id}/summary-jobs",
        json={"request_id": "job-1"},
    ).json()["job_id"]
    holder["job_id"] = job_id

    finished = asyncio.run(runtime.summary_jobs.execute(job_id))

    assert finished["state"] == "cancelled"
    assert client.get(f"/api/v2/projects/{pid}/summaries").json()["summaries"] == []


def test_failure_keeps_no_summary_and_retry_can_succeed() -> None:
    runtime, calls = _runtime(["", SUMMARY_TEXT])
    client, pid, chapter_id = _client(runtime)
    job_id = client.post(
        f"/api/v2/projects/{pid}/chapters/{chapter_id}/summary-jobs",
        json={"request_id": "job-1"},
    ).json()["job_id"]

    failed = asyncio.run(runtime.summary_jobs.execute(job_id))
    assert failed["state"] == "terminal_failed"
    assert failed["failure"]["code"] == "SUMMARY_EMPTY_RESULT"
    assert client.get(f"/api/v2/projects/{pid}/summaries").json()["summaries"] == []

    requeued = client.post(f"/api/v2/projects/{pid}/summary-jobs/{job_id}/retry").json()
    assert requeued["state"] == "retryable_failed"

    done = asyncio.run(runtime.summary_jobs.execute(job_id))
    assert done["state"] == "completed"
    assert done["attempts"] == 2
    assert len(calls) == 2


def test_cancelled_job_cannot_be_retried() -> None:
    runtime, _ = _runtime([SUMMARY_TEXT])
    client, pid, chapter_id = _client(runtime)
    job_id = client.post(
        f"/api/v2/projects/{pid}/chapters/{chapter_id}/summary-jobs",
        json={"request_id": "job-1"},
    ).json()["job_id"]
    client.post(f"/api/v2/projects/{pid}/summary-jobs/{job_id}/stop")

    response = client.post(f"/api/v2/projects/{pid}/summary-jobs/{job_id}/retry")
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "SUMMARY_JOB_CANCELLED"


def test_unknown_job_is_not_found() -> None:
    runtime, _ = _runtime([SUMMARY_TEXT])
    client, pid, _chapter = _client(runtime)
    response = client.get(f"/api/v2/projects/{pid}/summary-jobs/job_missing")
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "SUMMARY_JOB_NOT_FOUND"


def test_sync_endpoint_reports_job_id_and_replays_completed_job() -> None:
    runtime, calls = _runtime([SUMMARY_TEXT])
    client, pid, chapter_id = _client(runtime)
    first = client.post(
        f"/api/v2/projects/{pid}/chapters/{chapter_id}/summary",
        json={"request_id": "sync-1"},
    ).json()
    assert first["job_id"].startswith("job_")
    assert first["replayed"] is False

    replay = client.post(
        f"/api/v2/projects/{pid}/chapters/{chapter_id}/summary",
        json={"request_id": "sync-1"},
    ).json()
    assert replay["replayed"] is True
    assert replay["summary"]["revision_id"] == first["summary"]["revision_id"]
    assert len(calls) == 1


def test_worker_loop_picks_up_queued_summary_jobs() -> None:
    """worker 循环要能派发摘要任务：这是「关掉页面也会继续」的实际保证。"""
    import time

    runtime, calls = _runtime([SUMMARY_TEXT])
    with TestClient(build_v2_app(runtime)) as client:
        pid = client.post("/api/v2/projects", json={"name": "摘要循环"}).json()["project_id"]
        client.post(
            f"/api/v2/projects/{pid}/chapters/import",
            json={"idempotency_key": "imp1", "title": "第一章 雪夜", "text": ORIGINAL},
        )
        chapter_id = client.get(f"/api/v2/projects/{pid}/manuscript").json()["chapters"][0][
            "chapter_id"
        ]
        job_id = client.post(
            f"/api/v2/projects/{pid}/chapters/{chapter_id}/summary-jobs",
            json={"request_id": "job-loop"},
        ).json()["job_id"]

        deadline = time.monotonic() + 6
        job: dict[str, Any] = {}
        while time.monotonic() < deadline:
            job = client.get(f"/api/v2/projects/{pid}/summary-jobs/{job_id}").json()
            if job["state"] in ("completed", "terminal_failed", "cancelled"):
                break
            time.sleep(0.05)
        else:
            raise AssertionError("worker 循环没有派发排队的摘要任务")

        assert job["state"] == "completed"
        assert job["result"]["summary"]["status"] == "fresh"
        assert len(calls) == 1
