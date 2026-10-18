"""章摘要（V03-4.3）：显式启动、来源冻结、迟到结果不覆盖、失效保留内容。

模型用注入的替身，测试只断言路径与落盘结果，不联网。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from hastings.application.services.summary_service import SummaryDraft
from hastings.domain.repository.ids import ProjectId
from hastings.server.v2 import V2Runtime, build_v2_app

ORIGINAL = "侦探在雪夜抵达旅馆，前台没有人。"
CHANGED = "侦探在雪夜抵达旅馆，前台站着一个戴白手套的女人。"
SUMMARY_TEXT = "- 侦探抵达旅馆\n- 前台无人\n- 尚未交代旅馆老板去向"


def _runtime(reply: str = SUMMARY_TEXT, *, before_reply: Any = None) -> tuple[V2Runtime, list[str]]:
    runtime = V2Runtime.memory()
    calls: list[str] = []

    async def summarizer(*, system: str, prompt: str) -> SummaryDraft:
        calls.append(prompt)
        if before_reply is not None:
            await before_reply(runtime)
        return SummaryDraft(text=reply, model="test-model")

    runtime.summaries._summarizer = summarizer  # type: ignore[assignment]
    return runtime, calls


def _client(runtime: V2Runtime) -> tuple[TestClient, str, str]:
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "摘要测试"}).json()["project_id"]
    client.post(
        f"/api/v2/projects/{pid}/chapters/import",
        json={"idempotency_key": "imp1", "title": "第一章 雪夜", "text": ORIGINAL},
    )
    chapters = client.get(f"/api/v2/projects/{pid}/manuscript").json()["chapters"]
    return client, pid, chapters[0]["chapter_id"]


def _start(client: TestClient, pid: str, chapter_id: str, key: str = "sum-1") -> dict:
    return client.post(
        f"/api/v2/projects/{pid}/chapters/{chapter_id}/summary",
        json={"request_id": key},
    ).json()


def test_explicit_start_records_text_sources_and_model() -> None:
    runtime, calls = _runtime()
    client, pid, chapter_id = _client(runtime)

    body = _start(client, pid, chapter_id)

    assert body["status"] == "committed"
    assert body["commit_id"]
    summary = body["summary"]
    assert summary["status"] == "fresh"
    assert summary["text"] == SUMMARY_TEXT
    assert summary["model"] == "test-model"
    assert summary["sources"]["chapter_id"] == chapter_id
    assert summary["sources"]["units"]
    assert summary["source_hash"] == summary["sources"]["chapter_base"]
    assert len(calls) == 1
    assert ORIGINAL in calls[0]

    detail = client.get(f"/api/v2/projects/{pid}/chapters/{chapter_id}/summary").json()
    assert detail["revision_id"] == summary["revision_id"]
    assert detail["status_label"] == "可用"


def test_saving_body_marks_stale_but_keeps_the_text() -> None:
    runtime, _ = _runtime()
    client, pid, chapter_id = _client(runtime)
    _start(client, pid, chapter_id)

    saved = client.post(
        f"/api/v2/projects/{pid}/chapters/{chapter_id}/save",
        json={"idempotency_key": "save-1", "full_text": CHANGED},
    )
    assert saved.status_code == 200, saved.text

    detail = client.get(f"/api/v2/projects/{pid}/chapters/{chapter_id}/summary").json()
    assert detail["status"] == "stale"
    assert "已变化" in detail["reason"]
    # 失效只改状态：内容与来源仍在，作者仍可回看上次摘要。
    assert detail["text"] == SUMMARY_TEXT
    assert detail["sources"]["units"]


def test_late_result_is_superseded_and_writes_nothing() -> None:
    async def save_meanwhile(rt: V2Runtime) -> None:
        # 生成期间作者保存了新稿：来源清单在落地前核对时必然不一致。
        await rt.manuscript_publisher.publish_save(
            project_id=ProjectId(pid_holder["pid"]),
            chapter_id=pid_holder["chapter_id"],
            operations=None,
            full_text=CHANGED,
            expected_chapter_base=None,
            operation_id="op_late",
            summary="保存正文",
        )

    pid_holder: dict[str, str] = {}
    runtime, _ = _runtime(before_reply=save_meanwhile)
    client, pid, chapter_id = _client(runtime)
    pid_holder.update(pid=pid, chapter_id=chapter_id)
    head_before = client.get(f"/api/v2/projects/{pid}/manuscript").json()["commit_id"]

    body = _start(client, pid, chapter_id)

    assert body["status"] == "superseded"
    assert body["committed"] is False
    assert "不写入" in body["reason"]
    assert body["summary"]["status"] == "missing"
    # 只多了那次保存的提交；摘要没有落任何版本。
    assert client.get(f"/api/v2/projects/{pid}/manuscript").json()["commit_id"] != head_before
    listed = client.get(f"/api/v2/projects/{pid}/summaries").json()["summaries"]
    assert listed == []


def test_empty_model_result_is_an_error_and_writes_nothing() -> None:
    runtime, _ = _runtime(reply="   ")
    client, pid, chapter_id = _client(runtime)

    response = client.post(
        f"/api/v2/projects/{pid}/chapters/{chapter_id}/summary",
        json={"request_id": "sum-empty"},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "SUMMARY_EMPTY_RESULT"
    detail = client.get(f"/api/v2/projects/{pid}/chapters/{chapter_id}/summary").json()
    assert detail["status"] == "missing"


def test_without_a_model_the_endpoint_says_so() -> None:
    runtime, _ = _runtime()
    runtime.summaries._summarizer = None
    client, pid, chapter_id = _client(runtime)

    response = client.post(
        f"/api/v2/projects/{pid}/chapters/{chapter_id}/summary",
        json={"request_id": "sum-nomodel"},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "SUMMARY_NOT_AVAILABLE"


def test_unknown_chapter_is_not_found() -> None:
    runtime, _ = _runtime()
    client, pid, _chapter = _client(runtime)
    response = client.post(
        f"/api/v2/projects/{pid}/chapters/art_missing/summary",
        json={"request_id": "sum-missing"},
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "SUMMARY_CHAPTER_NOT_FOUND"


def test_saving_never_starts_a_summary_by_itself() -> None:
    runtime, calls = _runtime()
    client, pid, chapter_id = _client(runtime)
    client.post(
        f"/api/v2/projects/{pid}/chapters/{chapter_id}/save",
        json={"idempotency_key": "save-1", "full_text": CHANGED},
    )
    assert calls == []


def test_replay_same_request_returns_the_first_result() -> None:
    runtime, calls = _runtime()
    client, pid, chapter_id = _client(runtime)
    first = _start(client, pid, chapter_id, "sum-same")
    replay = _start(client, pid, chapter_id, "sum-same")

    assert replay["replayed"] is True
    assert replay["commit_id"] == first["commit_id"]
    assert replay["summary"]["revision_id"] == first["summary"]["revision_id"]
    assert len(calls) == 1
    assert len(client.get(f"/api/v2/projects/{pid}/summaries").json()["summaries"]) == 1


def test_background_prefers_fresh_summary_over_reading_the_chapter() -> None:
    runtime, _ = _runtime()
    client, pid, chapter_id = _client(runtime)
    _start(client, pid, chapter_id)

    payload = client.get(
        f"/api/v2/projects/{pid}/context/background",
        params={"scope": "manuscript", "target": chapter_id},
    ).json()
    target = [item for item in payload["items"] if item["tier"] == "target"]
    assert len(target) == 1
    assert target[0]["kind"] == "chapter_summary"
    assert target[0]["text"] == SUMMARY_TEXT
    coverage = {c["chapter_id"]: c for c in payload["coverage"]}[chapter_id]
    assert coverage["mode"] == "summary"
