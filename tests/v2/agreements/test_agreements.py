"""本书约定（V03-4.1）：无模型维护、同主题唯一生效、替换/移除保历史、快照按 Commit 冻结。

覆盖计划 A32 的基础路径：任务要求与本书约定分开（本模块只记作者显式的全书要求），
可直接维护、冲突需要选择、实际快照可追，且改约定不追改正文、不启动模型任务。
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from hastings.server.v2 import V2Runtime, build_v2_app


def _client() -> tuple[TestClient, str, V2Runtime]:
    runtime = V2Runtime.memory()
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "约定测试"}).json()["project_id"]
    return client, pid, runtime


def _create(client: TestClient, pid: str, *, request_id: str, **payload: object) -> dict:
    body = {"statement": "全书第一人称", "topic": "narrative_person", "request_id": request_id}
    body.update(payload)
    return client.post(f"/api/v2/projects/{pid}/agreements", json=body).json()


def _head(client: TestClient, pid: str) -> str | None:
    return client.get(f"/api/v2/projects/{pid}/manuscript").json()["commit_id"]


def test_create_returns_real_version_and_lists_with_labels() -> None:
    client, pid, _ = _client()
    response = client.post(
        f"/api/v2/projects/{pid}/agreements",
        json={
            "statement": "全书第一人称，不出现叙述者视角外的心声",
            "topic": "narrative_person",
            "scope": "manuscript",
            "request_id": "req-1",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "committed"
    assert body["commit_id"]
    agreement = body["agreement"]
    assert agreement["status"] == "active"
    assert agreement["topic_label"] == "叙述人称"
    assert agreement["scope_label"] == "仅正文写作"
    assert agreement["revision_id"]

    listed = client.get(f"/api/v2/projects/{pid}/agreements").json()
    assert [item["id"] for item in listed["agreements"]] == [agreement["id"]]
    assert {row["value"] for row in listed["topics"]} >= {"narrative_person", "tone"}
    assert listed["retired_count"] == 0

    detail = client.get(f"/api/v2/projects/{pid}/agreements/snapshot").json()
    assert [item["agreement_id"] for item in detail["agreements"]] == [agreement["id"]]


def test_unknown_topic_is_rejected_with_allowed_values() -> None:
    client, pid, _ = _client()
    response = client.post(
        f"/api/v2/projects/{pid}/agreements",
        json={"statement": "不要写梦", "topic": "vibe", "request_id": "req-1"},
    )
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "STORY_INVALID_COMMAND"
    assert "narrative_person" in error["details"]["allowed"]


def test_same_topic_conflict_blocks_second_active_and_writes_nothing() -> None:
    client, pid, _ = _client()
    _create(client, pid, request_id="req-1")
    head_before = _head(client, pid)

    response = client.post(
        f"/api/v2/projects/{pid}/agreements",
        json={"statement": "全书第三人称", "topic": "narrative_person", "request_id": "req-2"},
    )
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "AGREEMENT_TOPIC_CONFLICT"
    assert error["details"]["topic"] == "narrative_person"
    assert _head(client, pid) == head_before
    assert len(client.get(f"/api/v2/projects/{pid}/agreements").json()["agreements"]) == 1


def test_pending_choice_coexists_but_is_not_in_force() -> None:
    client, pid, _ = _client()
    active = _create(client, pid, request_id="req-1")["agreement"]
    pending = _create(
        client, pid, request_id="req-2", statement="全书第三人称", status="pending_choice"
    )["agreement"]
    assert pending["status"] == "pending_choice"

    listed = client.get(f"/api/v2/projects/{pid}/agreements").json()["agreements"]
    states = {item["statement"]: item["status"] for item in listed}
    assert states == {"全书第一人称": "active", "全书第三人称": "pending_choice"}

    snapshot = client.get(f"/api/v2/projects/{pid}/agreements/snapshot").json()
    assert [item["statement"] for item in snapshot["agreements"]] == ["全书第一人称"]
    assert pending["id"] not in {item["agreement_id"] for item in snapshot["agreements"]}
    assert active["id"] in {item["agreement_id"] for item in snapshot["agreements"]}


def test_pending_cannot_declare_supersedes() -> None:
    client, pid, _ = _client()
    active = _create(client, pid, request_id="req-1")["agreement"]
    response = client.post(
        f"/api/v2/projects/{pid}/agreements",
        json={
            "statement": "全书第三人称",
            "topic": "narrative_person",
            "status": "pending_choice",
            "supersedes": [active["id"]],
            "request_id": "req-2",
        },
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "STORY_INVALID_COMMAND"


def test_explicit_replacement_retires_old_in_one_commit() -> None:
    client, pid, _ = _client()
    old = _create(client, pid, request_id="req-1")["agreement"]
    pending = _create(
        client, pid, request_id="req-2", statement="全书第三人称", status="pending_choice"
    )["agreement"]

    response = client.patch(
        f"/api/v2/projects/{pid}/agreements/{pending['id']}",
        json={
            "status": "active",
            "supersedes": [old["id"]],
            "expected_revision": pending["revision_id"],
            "request_id": "req-3",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "committed"
    assert body["replaced"] == [old["id"]]

    rows = client.get(f"/api/v2/projects/{pid}/agreements?include_retired=true").json()[
        "agreements"
    ]
    by_id = {row["id"]: row for row in rows}
    assert by_id[old["id"]]["status"] == "retired"
    assert by_id[old["id"]]["retired_reason"] == "superseded"
    assert by_id[old["id"]]["superseded_by"] == pending["id"]
    assert by_id[pending["id"]]["status"] == "active"

    snapshot = client.get(f"/api/v2/projects/{pid}/agreements/snapshot").json()
    assert [item["statement"] for item in snapshot["agreements"]] == ["全书第三人称"]


def test_removal_keeps_history_and_leaves_snapshot() -> None:
    client, pid, _ = _client()
    agreement = _create(client, pid, request_id="req-1")["agreement"]
    response = client.patch(
        f"/api/v2/projects/{pid}/agreements/{agreement['id']}",
        json={
            "status": "retired",
            "retired_reason": "removed",
            "expected_revision": agreement["revision_id"],
            "request_id": "req-2",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["agreement"]["retired_reason"] == "removed"

    assert client.get(f"/api/v2/projects/{pid}/agreements").json()["agreements"] == []
    history = client.get(f"/api/v2/projects/{pid}/agreements?include_retired=true").json()
    assert history["retired_count"] == 1
    assert history["agreements"][0]["id"] == agreement["id"]
    assert client.get(f"/api/v2/projects/{pid}/agreements/snapshot").json()["agreements"] == []


def test_retiring_without_reason_is_rejected() -> None:
    client, pid, _ = _client()
    agreement = _create(client, pid, request_id="req-1")["agreement"]
    response = client.patch(
        f"/api/v2/projects/{pid}/agreements/{agreement['id']}",
        json={
            "status": "retired",
            "expected_revision": agreement["revision_id"],
            "request_id": "req-2",
        },
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "STORY_INVALID_COMMAND"


def test_stale_revision_is_a_conflict_not_a_silent_overwrite() -> None:
    client, pid, _ = _client()
    agreement = _create(client, pid, request_id="req-1")["agreement"]
    first = client.patch(
        f"/api/v2/projects/{pid}/agreements/{agreement['id']}",
        json={
            "statement": "全书第一人称，限定过去时",
            "expected_revision": agreement["revision_id"],
            "request_id": "req-2",
        },
    )
    assert first.status_code == 200, first.text

    stale = client.patch(
        f"/api/v2/projects/{pid}/agreements/{agreement['id']}",
        json={
            "statement": "全书第一人称，现在时",
            "expected_revision": agreement["revision_id"],
            "request_id": "req-3",
        },
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "AGREEMENT_REVISION_CONFLICT"
    current = client.get(f"/api/v2/projects/{pid}/agreements").json()["agreements"][0]
    assert current["statement"] == "全书第一人称，限定过去时"


def test_replay_same_request_returns_first_result_without_new_version() -> None:
    client, pid, _ = _client()
    payload = {
        "statement": "全书第一人称",
        "topic": "narrative_person",
        "request_id": "req-same",
    }
    first = client.post(f"/api/v2/projects/{pid}/agreements", json=payload).json()
    replay = client.post(f"/api/v2/projects/{pid}/agreements", json=payload).json()
    assert replay["replayed"] is True
    assert replay["commit_id"] == first["commit_id"]
    assert replay["agreement"]["id"] == first["agreement"]["id"]
    rows = client.get(f"/api/v2/projects/{pid}/agreements").json()["agreements"]
    assert len(rows) == 1


def test_snapshot_is_pinned_to_commit_and_ignores_unrelated_saves() -> None:
    client, pid, _ = _client()
    agreement = _create(client, pid, request_id="req-1")["agreement"]
    first = client.get(f"/api/v2/projects/{pid}/agreements/snapshot").json()

    # 无关的账本保存不应改变「约定版本」指纹。
    client.post(
        f"/api/v2/projects/{pid}/ledger/entries",
        json={
            "kind": "story.character",
            "fields": {"name": "姐姐"},
            "request_id": "req-char",
        },
    )
    after_save = client.get(f"/api/v2/projects/{pid}/agreements/snapshot").json()
    assert after_save["content_hash"] == first["content_hash"]

    # 改约定后：当前快照变了，旧 Commit 的快照仍是原样（已启动任务不被偷换输入）。
    edited = client.patch(
        f"/api/v2/projects/{pid}/agreements/{agreement['id']}",
        json={
            "statement": "全书第一人称，禁止旁观者补白",
            "expected_revision": agreement["revision_id"],
            "request_id": "req-2",
        },
    ).json()
    assert edited["status"] == "committed"
    current = client.get(f"/api/v2/projects/{pid}/agreements/snapshot").json()
    pinned = client.get(
        f"/api/v2/projects/{pid}/agreements/snapshot?commit_id={first['commit_id']}"
    ).json()
    assert current["content_hash"] != first["content_hash"]
    assert pinned["content_hash"] == first["content_hash"]
    assert pinned["agreements"][0]["statement"] == "全书第一人称"


def test_agreements_stay_out_of_ledger_and_start_no_jobs() -> None:
    client, pid, runtime = _client()
    _create(client, pid, request_id="req-1")
    _create(client, pid, request_id="req-2", statement="不要血腥描写", topic="taboo")

    assert client.get(f"/api/v2/projects/{pid}/ledger").json()["entries"] == []
    jobs = asyncio.run(runtime.jobs.list_jobs(pid))
    assert jobs == []


def test_supersede_must_share_topic() -> None:
    client, pid, _ = _client()
    taboo = _create(client, pid, request_id="req-1", statement="不要血腥描写", topic="taboo")[
        "agreement"
    ]
    pending = _create(
        client, pid, request_id="req-2", statement="全书第三人称", status="pending_choice"
    )["agreement"]
    response = client.patch(
        f"/api/v2/projects/{pid}/agreements/{pending['id']}",
        json={
            "status": "active",
            "supersedes": [taboo["id"]],
            "request_id": "req-3",
        },
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "STORY_INVALID_COMMAND"


def test_message_sourced_agreement_requires_message_id() -> None:
    client, pid, _ = _client()
    response = client.post(
        f"/api/v2/projects/{pid}/agreements",
        json={
            "statement": "全书第一人称",
            "topic": "narrative_person",
            "source_kind": "author_message",
            "request_id": "req-1",
        },
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "STORY_MISSING_REQUIRED_FIELD"

    ok = client.post(
        f"/api/v2/projects/{pid}/agreements",
        json={
            "statement": "全书第一人称",
            "topic": "narrative_person",
            "source_kind": "author_message",
            "source_message_id": "msg_abc",
            "request_id": "req-2",
        },
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["agreement"]["source_message_id"] == "msg_abc"


def test_unknown_agreement_id_is_not_found() -> None:
    client, pid, _ = _client()
    response = client.patch(
        f"/api/v2/projects/{pid}/agreements/art_missing",
        json={"statement": "全书第一人称", "request_id": "req-1"},
    )
    assert response.status_code == 404, response.text


def test_agreements_are_included_in_export() -> None:
    """新增 kind 必须显式登记导出目录：否则带约定的作品导出会直接失败。"""
    import io
    import zipfile

    client, pid, _ = _client()
    _create(client, pid, request_id="req-1")
    response = client.get(f"/api/v2/projects/{pid}/export")
    assert response.status_code == 200, response.text
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert any(name.startswith("bible/agreements/") for name in names), names
