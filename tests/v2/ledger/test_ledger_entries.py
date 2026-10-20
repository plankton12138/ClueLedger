"""账本直接保存（V03-3.2）：类型化创建/修订、引用校验、真实版本回读与幂等。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hastings.application.queries.ledger_forms import LEDGER_FORMS
from hastings.domain.story.canon.entries import LEDGER_FIELDS, LEDGER_KINDS
from hastings.server.v2 import V2Runtime, build_v2_app


def _client() -> tuple[TestClient, str]:
    runtime = V2Runtime.memory()
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "账本测试"}).json()["project_id"]
    return client, pid


def _create(client: TestClient, pid: str, kind: str, fields: dict, request_id: str) -> dict:
    response = client.post(
        f"/api/v2/projects/{pid}/ledger/entries",
        json={"kind": kind, "fields": fields, "request_id": request_id},
    )
    return response


def test_schema_covers_every_writable_field() -> None:
    client, pid = _client()
    payload = client.get(f"/api/v2/projects/{pid}/ledger/schema").json()
    kinds = {item["kind"]: item for item in payload["kinds"]}
    assert set(kinds) == set(LEDGER_KINDS)
    for kind, form in LEDGER_FORMS.items():
        written = {field["key"] for field in form["fields"]}
        assert written == set(LEDGER_FIELDS[kind]), kind
        assert all(field.get("label") for field in form["fields"])
        for field in form["fields"]:
            if field["type"] == "select":
                assert field["default"] in {option["value"] for option in field["options"]}
    # 假设不是可保存种类：schema 不得提供，后端也必须拒绝。
    assert "story.hypothesis" not in kinds


def test_create_character_returns_real_version_and_reads_back() -> None:
    client, pid = _client()
    response = _create(
        client,
        pid,
        "story.character",
        {"name": "姐姐", "identity": "旅馆经营者", "role": "support", "traits": ["寡言"]},
        "req-1",
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "committed"
    assert body["commit_id"]
    entry = body["entry"]
    assert entry["kind"] == "story.character"
    assert entry["title"] == "姐姐"
    assert entry["revision_id"]
    assert entry["content"]["traits"] == ["寡言"]

    detail = client.get(f"/api/v2/projects/{pid}/ledger/entries/{entry['id']}").json()
    assert detail["revision_id"] == entry["revision_id"]

    ledger = client.get(f"/api/v2/projects/{pid}/ledger").json()["entries"]
    assert [item["title"] for item in ledger] == ["姐姐"]
    assert ledger[0]["kind_label"] == "人物"


def test_relationship_requires_existing_characters() -> None:
    client, pid = _client()
    sister = _create(client, pid, "story.character", {"name": "姐姐"}, "req-s").json()["entry"]
    sister_id = sister["id"]

    bad = _create(
        client,
        pid,
        "story.relationship",
        {"source_id": sister_id, "target_id": "art_missing"},
        "req-bad",
    )
    assert bad.status_code == 404, bad.text
    assert bad.json()["error"]["code"] == "STORY_UNKNOWN_ARTIFACT"

    place = _create(client, pid, "story.place", {"name": "旅馆"}, "req-p").json()["entry"]
    mistyped = _create(
        client,
        pid,
        "story.relationship",
        {"source_id": sister_id, "target_id": place["id"]},
        "req-mistyped",
    )
    assert mistyped.status_code == 400
    assert mistyped.json()["error"]["code"] == "STORY_INVALID_COMMAND"

    ok = _create(
        client,
        pid,
        "story.relationship",
        {"source_id": sister_id, "target_id": sister_id, "relation_type": "姐妹"},
        "req-ok",
    )
    assert ok.status_code == 201, ok.text


def test_testimony_keeps_knowledge_type_and_is_visible() -> None:
    client, pid = _client()
    character = _create(client, pid, "story.character", {"name": "妹妹"}, "req-c").json()["entry"]
    fact = _create(client, pid, "story.truth_fact", {"statement": "姐姐九点到达"}, "req-f").json()[
        "entry"
    ]
    knowledge = _create(
        client,
        pid,
        "story.knowledge",
        {
            "character_id": character["id"],
            "fact_id": fact["id"],
            "mode": "suspects",
            "note": "妹妹的证词",
        },
        "req-k",
    )
    assert knowledge.status_code == 201, knowledge.text
    entry = knowledge.json()["entry"]
    assert entry["kind"] == "story.knowledge"
    assert entry["content"]["mode"] == "suspects"
    ledger = client.get(f"/api/v2/projects/{pid}/ledger").json()["entries"]
    assert any(
        item["kind"] == "story.knowledge" and item["summary"].startswith("怀疑") for item in ledger
    )


def test_hypothesis_and_unknown_fields_do_not_enter_ledger() -> None:
    client, pid = _client()
    hypothesis = _create(client, pid, "story.hypothesis", {"statement": "凶手是管家"}, "req-h")
    assert hypothesis.status_code == 400
    assert "假设" in hypothesis.json()["error"]["why"]
    assert client.get(f"/api/v2/projects/{pid}/ledger").json()["entries"] == []

    unknown = _create(
        client, pid, "story.character", {"name": "姐姐", "schema_version": 99}, "req-u"
    )
    assert unknown.status_code == 400
    assert unknown.json()["error"]["code"] == "STORY_INVALID_COMMAND"


def test_update_uses_revision_cas_and_no_change_is_legal() -> None:
    client, pid = _client()
    entry = _create(client, pid, "story.character", {"name": "姐姐"}, "req-c").json()["entry"]
    url = f"/api/v2/projects/{pid}/ledger/entries/{entry['id']}"

    updated = client.patch(
        url,
        json={
            "fields": {"goal": "守住旅馆的秘密"},
            "expected_revision": entry["revision_id"],
            "request_id": "req-u1",
        },
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["status"] == "committed"
    assert body["entry"]["revision_id"] != entry["revision_id"]
    assert body["entry"]["content"]["name"] == "姐姐"
    assert body["entry"]["content"]["goal"] == "守住旅馆的秘密"

    stale = client.patch(
        url,
        json={
            "fields": {"goal": "别的目标"},
            "expected_revision": entry["revision_id"],
            "request_id": "req-u2",
        },
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "LEDGER_ENTRY_CONFLICT"

    same = client.patch(
        url,
        json={
            "fields": {"goal": "守住旅馆的秘密"},
            "expected_revision": body["entry"]["revision_id"],
            "request_id": "req-u3",
        },
    )
    assert same.status_code == 200
    assert same.json()["status"] == "no_change"
    assert same.json()["commit_id"] is None


def test_same_request_id_replays_without_duplicate_entry() -> None:
    client, pid = _client()
    first = _create(client, pid, "story.place", {"name": "旅馆"}, "req-same")
    second = _create(client, pid, "story.place", {"name": "旅馆"}, "req-same")
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["commit_id"] == second.json()["commit_id"]
    assert second.json()["replayed"] is True
    ledger = client.get(f"/api/v2/projects/{pid}/ledger").json()["entries"]
    assert len([item for item in ledger if item["title"] == "旅馆"]) == 1


def test_empty_project_is_valid_first_save() -> None:
    client, pid = _client()
    response = _create(client, pid, "story.premise", {"premise": "姐妹在旅馆重逢"}, "req-first")
    assert response.status_code == 201, response.text
    assert response.json()["entry"]["kind"] == "story.premise"
    assert response.json()["entry"]["title"] == "姐妹在旅馆重逢"
