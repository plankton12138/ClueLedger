"""背景接口（V03-4.2）：只读装配、空作品合法、临时原文不写回。

HTTP 层测试：真 API、真仓库（内存后端），不调用模型。
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from hastings.server.v2 import V2Runtime, build_v2_app


def _client() -> tuple[TestClient, str, V2Runtime]:
    runtime = V2Runtime.memory()
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "背景测试"}).json()["project_id"]
    return client, pid, runtime


def _chapter(client: TestClient, pid: str, *, key: str, title: str, text: str) -> str:
    response = client.post(
        f"/api/v2/projects/{pid}/chapters/import",
        json={"idempotency_key": key, "title": title, "text": text},
    )
    assert response.status_code == 200, response.text
    for chapter in client.get(f"/api/v2/projects/{pid}/manuscript").json()["chapters"]:
        if chapter["title"] == title:
            return chapter["chapter_id"]
    raise AssertionError("导入后找不到该章")


def _agreement(client: TestClient, pid: str, **payload: object) -> dict:
    body = {"statement": "全书第一人称", "topic": "narrative_person", "request_id": "req-1"}
    body.update(payload)
    response = client.post(f"/api/v2/projects/{pid}/agreements", json=body)
    assert response.status_code == 201, response.text
    return response.json()["agreement"]


def _character(client: TestClient, pid: str, name: str = "姐姐") -> dict:
    response = client.post(
        f"/api/v2/projects/{pid}/ledger/entries",
        json={"kind": "story.character", "fields": {"name": name}, "request_id": f"req-{name}"},
    )
    assert response.status_code == 201, response.text
    return response.json()["entry"]


def test_empty_project_is_a_legal_empty_background() -> None:
    client, pid, _ = _client()
    payload = client.get(f"/api/v2/projects/{pid}/context/background").json()
    assert payload["commit_id"] is None
    assert payload["items"] == []
    assert payload["coverage"] == []
    assert payload["content_hash"]


def test_background_is_composed_by_tiers_and_never_writes() -> None:
    client, pid, runtime = _client()
    chapter_id = _chapter(client, pid, key="imp1", title="第一章 雪夜", text="侦探抵达旅馆。")
    _agreement(client, pid)
    _character(client, pid)
    head_before = client.get(f"/api/v2/projects/{pid}/manuscript").json()["commit_id"]

    response = client.get(
        f"/api/v2/projects/{pid}/context/background",
        params={"scope": "manuscript", "target": chapter_id},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    tiers = [item["tier"] for item in payload["items"]]
    assert tiers[0] == "agreements"
    assert "core" not in tiers  # 没写故事前提，核心层为空是如实结果
    assert "index" in tiers
    assert payload["versions"]["commit_id"] == head_before
    assert [a["agreement_id"] for a in payload["versions"]["agreements"]]

    # 缺摘要且没要求读原文：目标章如实记为未获得，而不是造一段假的背景。
    coverage = {c["chapter_id"]: c for c in payload["coverage"]}[chapter_id]
    assert coverage["mode"] == "omitted"
    assert "missing" in coverage["source"]

    # 只读：head 不变，也没有任务被创建。
    assert client.get(f"/api/v2/projects/{pid}/manuscript").json()["commit_id"] == head_before
    assert asyncio.run(runtime.jobs.list_jobs(pid)) == []


def test_temporary_text_fills_missing_summary_without_pretending_it_is_one() -> None:
    client, pid, _ = _client()
    chapter_id = _chapter(client, pid, key="imp1", title="第一章 雪夜", text="侦探抵达旅馆。")
    payload = client.get(
        f"/api/v2/projects/{pid}/context/background",
        params={"scope": "manuscript", "target": chapter_id, "include_temporary_text": "true"},
    ).json()
    target = [item for item in payload["items"] if item["tier"] == "target"][0]
    assert target["temporary"] is True
    assert "侦探抵达旅馆" in target["text"]
    coverage = {c["chapter_id"]: c for c in payload["coverage"]}[chapter_id]
    assert coverage["mode"] == "temporary_text"


def test_scope_filters_manuscript_only_agreements_in_discussion() -> None:
    client, pid, _ = _client()
    _agreement(client, pid, statement="全书第一人称")
    _agreement(
        client,
        pid,
        statement="正文避免抒情",
        topic="tone",
        scope="manuscript",
        request_id="req-2",
    )
    discussion = client.get(
        f"/api/v2/projects/{pid}/context/background", params={"scope": "discussion"}
    ).json()
    assert [item["text"] for item in discussion["items"] if item["tier"] == "agreements"] == [
        "全书第一人称"
    ]
    manuscript = client.get(
        f"/api/v2/projects/{pid}/context/background", params={"scope": "manuscript"}
    ).json()
    assert len([item for item in manuscript["items"] if item["tier"] == "agreements"]) == 2


def test_invalid_scope_and_budget_are_explicit_errors() -> None:
    client, pid, _ = _client()
    bad_scope = client.get(
        f"/api/v2/projects/{pid}/context/background", params={"scope": "everything"}
    )
    assert bad_scope.status_code == 400
    assert bad_scope.json()["error"]["code"] == "BACKGROUND_SCOPE_INVALID"

    too_big = client.get(
        f"/api/v2/projects/{pid}/context/background", params={"budget_tokens": 100_000}
    )
    assert too_big.status_code == 400
    assert too_big.json()["error"]["code"] == "BACKGROUND_BUDGET_INVALID"


def test_budget_that_cannot_hold_agreements_is_a_named_error() -> None:
    client, pid, _ = _client()
    _agreement(client, pid, statement="全书第一人称，不写叙述者视角外的心声" * 4)
    response = client.get(
        f"/api/v2/projects/{pid}/context/background", params={"budget_tokens": 200}
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "BACKGROUND_BUDGET_TOO_SMALL"


def test_capabilities_publish_background_contract() -> None:
    client, _, _ = _client()
    features = client.get("/api/v2/capabilities").json()["features"]
    assert features["background"]["status"] == "draft"
    assert features["background"]["default_tokens"] == 12_000
    assert features["background"]["tiers"][0] == "agreements"
