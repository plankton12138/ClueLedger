"""设定候选成组与部分确认（V03-3.2）：暂存不入账、部分确认、依赖闭合、假设拦截。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hastings.server.v2 import V2Runtime, build_v2_app


def _client() -> tuple[TestClient, str]:
    runtime = V2Runtime.memory()
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "候选测试"}).json()["project_id"]
    return client, pid


def _stage(
    client: TestClient,
    pid: str,
    kind: str,
    fields: dict,
    request_id: str,
    *,
    hypothesis: bool = False,
) -> dict:
    response = client.post(
        f"/api/v2/projects/{pid}/ledger/candidates/items",
        json={
            "kind": kind,
            "fields": fields,
            "hypothesis": hypothesis,
            "request_id": request_id,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _last_item(group: dict) -> dict:
    return group["items"][-1]


def _ledger(client: TestClient, pid: str) -> list[dict]:
    return client.get(f"/api/v2/projects/{pid}/ledger").json()["entries"]


def test_stage_does_not_create_formal_entry() -> None:
    client, pid = _client()
    group = _stage(client, pid, "story.character", {"name": "姐姐"}, "s1")
    assert group["pending_count"] == 1
    item = _last_item(group)
    assert item["status"] == "pending"
    assert item["title"] == "姐姐"
    assert _ledger(client, pid) == []


def test_partial_accept_keeps_unselected_items_pending() -> None:
    client, pid = _client()
    _stage(client, pid, "story.character", {"name": "姐姐"}, "s1")
    group = _stage(client, pid, "story.place", {"name": "旅馆"}, "s2")
    sister_id = group["items"][0]["item_id"]
    place_id = group["items"][1]["item_id"]
    assert group["pending_count"] == 2

    accepted = client.post(
        f"/api/v2/projects/{pid}/ledger/candidates/accept",
        json={
            "item_ids": [sister_id],
            "expected_revision": group["revision"],
            "request_id": "a1",
        },
    )
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["status"] == "committed"
    assert body["commit_id"]
    assert [entry["title"] for entry in body["entries"]] == ["姐姐"]
    assert body["group"]["pending_count"] == 1
    assert body["group"]["items"][1]["item_id"] == place_id
    assert [entry["title"] for entry in _ledger(client, pid)] == ["姐姐"]

    # 未选中的地点仍可单独确认，形成第二个正式版本。
    second = client.post(
        f"/api/v2/projects/{pid}/ledger/candidates/accept",
        json={
            "item_ids": [place_id],
            "expected_revision": body["group"]["revision"],
            "request_id": "a2",
        },
    )
    assert second.status_code == 200, second.text
    assert {entry["title"] for entry in _ledger(client, pid)} == {"姐姐", "旅馆"}


def test_grouped_relationship_accepts_with_its_characters_in_one_commit() -> None:
    client, pid = _client()
    group = _stage(client, pid, "story.character", {"name": "姐姐"}, "s1")
    sister_id = group["items"][0]["item_id"]
    group = _stage(client, pid, "story.character", {"name": "妹妹"}, "s2")
    younger_id = group["items"][1]["item_id"]
    group = _stage(
        client,
        pid,
        "story.relationship",
        {
            "source_id": f"@{sister_id}",
            "target_id": f"@{younger_id}",
            "relation_type": "姐妹",
        },
        "s3",
    )
    relation_id = group["items"][2]["item_id"]
    accepted = client.post(
        f"/api/v2/projects/{pid}/ledger/candidates/accept",
        json={
            "item_ids": [relation_id, sister_id, younger_id],
            "expected_revision": group["revision"],
            "request_id": "a1",
        },
    )
    assert accepted.status_code == 200, accepted.text
    entries = accepted.json()["entries"]
    by_title = {entry["title"]: entry for entry in entries if entry["kind"] == "story.character"}
    relation = next(entry for entry in entries if entry["kind"] == "story.relationship")
    assert relation["content"]["source_id"] == by_title["姐姐"]["id"]
    assert relation["content"]["target_id"] == by_title["妹妹"]["id"]
    assert relation["content"]["relation_type"] == "姐妹"


def test_relationship_can_be_confirmed_after_its_characters() -> None:
    client, pid = _client()
    group = _stage(client, pid, "story.character", {"name": "姐姐"}, "s1")
    sister_id = group["items"][0]["item_id"]
    group = _stage(client, pid, "story.character", {"name": "妹妹"}, "s2")
    younger_id = group["items"][1]["item_id"]
    group = _stage(
        client,
        pid,
        "story.relationship",
        {"source_id": f"@{sister_id}", "target_id": f"@{younger_id}"},
        "s3",
    )
    relation_id = group["items"][2]["item_id"]

    first = client.post(
        f"/api/v2/projects/{pid}/ledger/candidates/accept",
        json={
            "item_ids": [sister_id, younger_id],
            "expected_revision": group["revision"],
            "request_id": "a1",
        },
    )
    assert first.status_code == 200, first.text
    characters = {entry["title"]: entry["id"] for entry in first.json()["entries"]}

    second = client.post(
        f"/api/v2/projects/{pid}/ledger/candidates/accept",
        json={
            "item_ids": [relation_id],
            "expected_revision": first.json()["group"]["revision"],
            "request_id": "a2",
        },
    )
    assert second.status_code == 200, second.text
    relation = second.json()["entries"][0]
    assert relation["content"]["source_id"] == characters["姐姐"]
    assert relation["content"]["target_id"] == characters["妹妹"]


def test_unclosed_dependency_is_rejected() -> None:
    client, pid = _client()
    group = _stage(client, pid, "story.character", {"name": "姐姐"}, "s1")
    sister_id = group["items"][0]["item_id"]
    group = _stage(
        client,
        pid,
        "story.relationship",
        {"source_id": f"@{sister_id}", "target_id": f"@{sister_id}"},
        "s2",
    )
    relation_id = group["items"][1]["item_id"]
    response = client.post(
        f"/api/v2/projects/{pid}/ledger/candidates/accept",
        json={
            "item_ids": [relation_id],
            "expected_revision": group["revision"],
            "request_id": "a-bad",
        },
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "LEDGER_DEPENDENCY_UNCLOSED"
    assert _ledger(client, pid) == []


def test_hypothesis_never_enters_ledger() -> None:
    client, pid = _client()
    group = _stage(
        client, pid, "story.character", {"name": "管家"}, "s1", hypothesis=True
    )
    item = _last_item(group)
    assert item["hypothesis"] is True
    assert group["hypothesis_count"] == 1
    response = client.post(
        f"/api/v2/projects/{pid}/ledger/candidates/accept",
        json={
            "item_ids": [item["item_id"]],
            "expected_revision": group["revision"],
            "request_id": "a1",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "LEDGER_HYPOTHESIS_NOT_ACCEPTABLE"
    assert _ledger(client, pid) == []


def test_testimony_keeps_its_type_when_accepted() -> None:
    client, pid = _client()
    character = client.post(
        f"/api/v2/projects/{pid}/ledger/entries",
        json={"kind": "story.character", "fields": {"name": "妹妹"}, "request_id": "c1"},
    ).json()["entry"]
    fact = client.post(
        f"/api/v2/projects/{pid}/ledger/entries",
        json={
            "kind": "story.truth_fact",
            "fields": {"statement": "姐姐九点到达"},
            "request_id": "f1",
        },
    ).json()["entry"]
    group = _stage(
        client,
        pid,
        "story.knowledge",
        {
            "character_id": character["id"],
            "fact_id": fact["id"],
            "mode": "suspects",
            "note": "妹妹声称九点看到姐姐",
        },
        "s1",
    )
    item = _last_item(group)
    response = client.post(
        f"/api/v2/projects/{pid}/ledger/candidates/accept",
        json={
            "item_ids": [item["item_id"]],
            "expected_revision": group["revision"],
            "request_id": "a1",
        },
    )
    assert response.status_code == 200, response.text
    entry = response.json()["entries"][0]
    assert entry["kind"] == "story.knowledge"
    assert entry["content"]["mode"] == "suspects"


def test_accept_is_idempotent_and_revision_guarded() -> None:
    client, pid = _client()
    group = _stage(client, pid, "story.place", {"name": "旅馆"}, "s1")
    item_id = group["items"][0]["item_id"]
    first = client.post(
        f"/api/v2/projects/{pid}/ledger/candidates/accept",
        json={"item_ids": [item_id], "expected_revision": group["revision"], "request_id": "a1"},
    )
    assert first.status_code == 200, first.text
    replay = client.post(
        f"/api/v2/projects/{pid}/ledger/candidates/accept",
        json={"item_ids": [item_id], "expected_revision": group["revision"], "request_id": "a1"},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["commit_id"] == first.json()["commit_id"]
    assert len(_ledger(client, pid)) == 1

    stale = client.post(
        f"/api/v2/projects/{pid}/ledger/candidates/accept",
        json={"item_ids": [item_id], "expected_revision": group["revision"], "request_id": "a2"},
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "LEDGER_CANDIDATE_CONFLICT"


def test_skip_only_changes_group_state() -> None:
    client, pid = _client()
    group = _stage(client, pid, "story.clue", {"statement": "怀表停在九点"}, "s1")
    item_id = group["items"][0]["item_id"]
    response = client.post(
        f"/api/v2/projects/{pid}/ledger/candidates/skip",
        json={"item_ids": [item_id], "expected_revision": group["revision"], "request_id": "k1"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pending_count"] == 0
    assert body["items"][0]["status"] == "skipped"
    assert _ledger(client, pid) == []
