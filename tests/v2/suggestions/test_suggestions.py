"""账本更新建议（V03-4.4）：显式启动、带来源暂存、采用前核对来源。

建议复用候选组的确认机制：部分采用一次提交、假设拦截、未选项保留。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from hastings.server.v2 import V2Runtime, build_v2_app

ORIGINAL = "侦探在雪夜抵达旅馆，前台站着一个女人，她说自己是姐姐。"
CHANGED = "侦探在雪夜抵达旅馆，前台没有人，只留了一张字条。"


def _proposals(*rows: dict[str, Any]) -> str:
    return json.dumps({"suggestions": list(rows)}, ensure_ascii=False)


TWO = _proposals(
    {
        "kind": "story.character",
        "fields": {"name": "姐姐", "identity": "旅馆经营者"},
        "quote": "前台站着一个女人，她说自己是姐姐",
    },
    {
        "kind": "story.truth_fact",
        "fields": {"statement": "旅馆当晚只有一位客人"},
        "quote": "侦探在雪夜抵达旅馆",
    },
)


def _runtime(reply: str = TWO) -> tuple[V2Runtime, list[str]]:
    runtime = V2Runtime.memory()
    calls: list[str] = []

    async def proposer(*, system: str, prompt: str) -> tuple[str, str]:
        calls.append(prompt)
        return reply, "test-model"

    runtime.suggestions._proposer = proposer  # type: ignore[assignment]
    return runtime, calls


def _client(runtime: V2Runtime) -> tuple[TestClient, str, str]:
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "建议测试"}).json()["project_id"]
    client.post(
        f"/api/v2/projects/{pid}/chapters/import",
        json={"idempotency_key": "imp1", "title": "第一章 雪夜", "text": ORIGINAL},
    )
    chapter_id = client.get(f"/api/v2/projects/{pid}/manuscript").json()["chapters"][0][
        "chapter_id"
    ]
    return client, pid, chapter_id


def _start(client: TestClient, pid: str, chapter_id: str, key: str = "sug-1") -> dict:
    response = client.post(
        f"/api/v2/projects/{pid}/chapters/{chapter_id}/suggestions",
        json={"request_id": key},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_explicit_start_stages_items_with_source_and_writes_no_ledger() -> None:
    runtime, calls = _runtime()
    client, pid, chapter_id = _client(runtime)

    body = _start(client, pid, chapter_id)

    assert body["status"] == "staged"
    assert body["staged"] == 2
    assert body["source"]["chapter_base"]
    assert len(calls) == 1
    assert ORIGINAL in calls[0]

    group = client.get(f"/api/v2/projects/{pid}/suggestions").json()
    assert group["pending_count"] == 2
    assert group["origin"]["chapter_id"] == chapter_id
    assert group["origin"]["model"] == "test-model"
    first = group["items"][0]
    assert first["source"]["quote"] == "前台站着一个女人，她说自己是姐姐"
    assert first["source"]["chapter_base"] == body["source"]["chapter_base"]

    # 建议只是候选：正式账本仍然是空的。
    assert client.get(f"/api/v2/projects/{pid}/ledger").json()["entries"] == []


def test_zero_suggestions_is_a_legal_empty_result() -> None:
    runtime, _ = _runtime(_proposals())
    client, pid, chapter_id = _client(runtime)

    body = _start(client, pid, chapter_id)

    assert body["status"] == "empty"
    assert body["staged"] == 0
    group = client.get(f"/api/v2/projects/{pid}/suggestions").json()
    assert group["items"] == []
    assert group["pending_count"] == 0


def test_malformed_output_is_rejected_without_staging() -> None:
    runtime, _ = _runtime("我觉得可以加一个姐姐")
    client, pid, chapter_id = _client(runtime)

    response = client.post(
        f"/api/v2/projects/{pid}/chapters/{chapter_id}/suggestions",
        json={"request_id": "sug-bad"},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "STORY_INVALID_COMMAND"
    assert client.get(f"/api/v2/projects/{pid}/suggestions").json()["items"] == []


def test_proposal_without_quote_is_rejected() -> None:
    runtime, _ = _runtime(
        _proposals({"kind": "story.character", "fields": {"name": "姐姐"}, "quote": "  "})
    )
    client, pid, chapter_id = _client(runtime)

    response = client.post(
        f"/api/v2/projects/{pid}/chapters/{chapter_id}/suggestions",
        json={"request_id": "sug-noquote"},
    )
    assert response.status_code == 400, response.text
    assert "来源原句" in response.json()["error"]["why"]
    assert client.get(f"/api/v2/projects/{pid}/suggestions").json()["items"] == []


def test_partial_accept_creates_one_entry_and_keeps_the_rest_pending() -> None:
    runtime, _ = _runtime()
    client, pid, chapter_id = _client(runtime)
    _start(client, pid, chapter_id)
    group = client.get(f"/api/v2/projects/{pid}/suggestions").json()

    accepted = client.post(
        f"/api/v2/projects/{pid}/ledger/candidates/accept",
        json={
            "item_ids": [group["items"][0]["item_id"]],
            "expected_revision": group["revision"],
            "request_id": "acc-1",
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "committed"

    entries = client.get(f"/api/v2/projects/{pid}/ledger").json()["entries"]
    assert [entry["title"] for entry in entries] == ["姐姐"]
    after = client.get(f"/api/v2/projects/{pid}/suggestions").json()
    assert after["pending_count"] == 1
    assert after["items"][0]["status"] == "accepted"
    assert after["items"][1]["status"] == "pending"


def test_accept_is_blocked_when_the_source_chapter_changed() -> None:
    runtime, _ = _runtime()
    client, pid, chapter_id = _client(runtime)
    _start(client, pid, chapter_id)
    group = client.get(f"/api/v2/projects/{pid}/suggestions").json()

    saved = client.post(
        f"/api/v2/projects/{pid}/chapters/{chapter_id}/save",
        json={"idempotency_key": "save-1", "full_text": CHANGED},
    )
    assert saved.status_code == 200, saved.text

    blocked = client.post(
        f"/api/v2/projects/{pid}/ledger/candidates/accept",
        json={
            "item_ids": [group["items"][0]["item_id"]],
            "expected_revision": group["revision"],
            "request_id": "acc-1",
        },
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"]["code"] == "SOURCE_NEEDS_RECHECK"
    assert client.get(f"/api/v2/projects/{pid}/ledger").json()["entries"] == []


def test_hypothesis_suggestion_cannot_enter_the_ledger() -> None:
    runtime, _ = _runtime(
        _proposals(
            {
                "kind": "story.character",
                "fields": {"name": "戴白手套的女人"},
                "quote": "前台站着一个女人",
                "hypothesis": True,
            }
        )
    )
    client, pid, chapter_id = _client(runtime)
    _start(client, pid, chapter_id)
    group = client.get(f"/api/v2/projects/{pid}/suggestions").json()
    assert group["items"][0]["hypothesis"] is True

    rejected = client.post(
        f"/api/v2/projects/{pid}/ledger/candidates/accept",
        json={
            "item_ids": [group["items"][0]["item_id"]],
            "expected_revision": group["revision"],
            "request_id": "acc-1",
        },
    )
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()["error"]["code"] == "LEDGER_HYPOTHESIS_NOT_ACCEPTABLE"


def test_saving_never_starts_suggestions_by_itself() -> None:
    runtime, calls = _runtime()
    client, pid, chapter_id = _client(runtime)
    client.post(
        f"/api/v2/projects/{pid}/chapters/{chapter_id}/save",
        json={"idempotency_key": "save-1", "full_text": CHANGED},
    )
    assert calls == []


def test_same_request_id_does_not_duplicate_items() -> None:
    runtime, calls = _runtime()
    client, pid, chapter_id = _client(runtime)
    _start(client, pid, chapter_id, "sug-same")
    _start(client, pid, chapter_id, "sug-same")

    group = client.get(f"/api/v2/projects/{pid}/suggestions").json()
    assert len(group["items"]) == 2
    assert len(calls) == 2  # 第二次仍调用了模型，但没有重复暂存
