"""统一图谱投影（V03-6.1 / M12）HTTP 用例：与账本一致、固定 Commit、只读。

四类图谱（V03-6.2/6.3）都会消费这同一份投影，所以这里盯的是共性契约：
节点/边只来自同 Commit 的正式账本，读图谱不产生任何写入。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hastings.domain.repository.ids import Actor, ArtifactId, ProjectId
from hastings.domain.repository.requests import ArtifactChange, CommitRequest
from hastings.domain.story.graph import SEMANTIC_LAYERS
from hastings.server.v2 import V2Runtime, build_v2_app


def _client() -> tuple[TestClient, str, V2Runtime]:
    runtime = V2Runtime.memory()
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "图谱测试"}).json()["project_id"]
    return client, pid, runtime


def _create(client: TestClient, pid: str, kind: str, fields: dict, request_id: str) -> dict:
    response = client.post(
        f"/api/v2/projects/{pid}/ledger/entries",
        json={"kind": kind, "fields": fields, "request_id": request_id},
    )
    assert response.status_code == 201, response.text
    return response.json()["entry"]


def _graph(client: TestClient, pid: str, **params: str) -> dict:
    response = client.get(f"/api/v2/projects/{pid}/graphs", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _head(client: TestClient, pid: str) -> str | None:
    return client.get(f"/api/v2/projects/{pid}/desk-state").json()["commit_id"]


def _seed(client: TestClient, pid: str) -> dict:
    sister = _create(client, pid, "story.character", {"name": "姐姐"}, "req-a")
    guest = _create(client, pid, "story.character", {"name": "客人"}, "req-b")
    _create(
        client,
        pid,
        "story.relationship",
        {"source_id": sister["id"], "target_id": guest["id"], "relation_type": "主客"},
        "req-rel",
    )
    fact = _create(client, pid, "story.truth_fact", {"statement": "九点到达"}, "req-f")
    clue = _create(
        client, pid, "story.clue", {"statement": "怀表", "points_to": fact["id"]}, "req-clue"
    )
    _create(
        client,
        pid,
        "story.knowledge",
        {"character_id": guest["id"], "fact_id": fact["id"], "mode": "suspects"},
        "req-k",
    )
    return {"sister": sister, "guest": guest, "fact": fact, "clue": clue}


def test_graph_matches_ledger_at_the_same_commit() -> None:
    client, pid, _ = _client()
    ids = _seed(client, pid)
    payload = _graph(client, pid)
    assert payload["commit_id"] == _head(client, pid)

    ledger_ids = {
        item["id"] for item in client.get(f"/api/v2/projects/{pid}/ledger").json()["entries"]
    }
    node_ids = {node["id"] for node in payload["nodes"]}
    assert node_ids == ledger_ids  # 与同 Commit 账本目录对象一致
    assert {node["semantic_layer"] for node in payload["nodes"]} <= set(SEMANTIC_LAYERS)

    edges = {(edge["source"], edge["target"]): edge for edge in payload["edges"]}
    relationship = edges[(ids["sister"]["id"], ids["guest"]["id"])]
    assert relationship["label"] == "主客"
    assert relationship["declared_by"]
    knowledge = edges[(ids["guest"]["id"], ids["fact"]["id"])]
    assert knowledge["semantic_layer"] == "knowledge"
    assert knowledge["label"] == "怀疑"
    clue = edges[(ids["clue"]["id"], ids["fact"]["id"])]
    assert clue["label"] == "指向"


def test_reading_the_graph_writes_nothing() -> None:
    client, pid, _ = _client()
    _seed(client, pid)
    before_head = _head(client, pid)
    before_entries = client.get(f"/api/v2/projects/{pid}/ledger").json()["entries"]

    first = _graph(client, pid)
    second = _graph(client, pid)
    assert first == second
    assert _head(client, pid) == before_head
    assert client.get(f"/api/v2/projects/{pid}/ledger").json()["entries"] == before_entries


def test_graph_honours_at_commit_id_and_rejects_unknown_commit() -> None:
    client, pid, _ = _client()
    created = _create(client, pid, "story.character", {"name": "姐姐"}, "req-c")
    first_commit = _head(client, pid)
    updated = client.patch(
        f"/api/v2/projects/{pid}/ledger/entries/{created['id']}",
        json={
            "fields": {"goal": "守住旅馆"},
            "expected_revision": created["revision_id"],
            "request_id": "req-up",
        },
    )
    assert updated.status_code == 200, updated.text
    assert _head(client, pid) != first_commit

    current = {node["id"]: node for node in _graph(client, pid)["nodes"]}[created["id"]]
    pinned = {node["id"]: node for node in _graph(client, pid, at_commit_id=first_commit)["nodes"]}
    assert current["revision"] != created["revision_id"]
    assert pinned[created["id"]]["revision"] == created["revision_id"]
    assert pinned[created["id"]]["title"] == "姐姐"

    unknown = client.get(f"/api/v2/projects/{pid}/graphs", params={"at_commit_id": "cmt_missing"})
    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["error"]["code"] == "REPO_COMMIT_NOT_FOUND"


def test_empty_project_returns_empty_projection() -> None:
    client, pid, _ = _client()
    payload = _graph(client, pid)
    assert payload == {"project_id": pid, "commit_id": None, "nodes": [], "edges": []}


def test_evidence_needs_check_reaches_the_anchored_node() -> None:
    client, pid, runtime = _client()
    character = _create(client, pid, "story.character", {"name": "姐姐"}, "req-c")
    branch = runtime.service.get_branch(ProjectId(pid), "main")
    assert branch is not None
    runtime.service.commit(
        CommitRequest(
            project_id=ProjectId(pid),
            branch_id=branch.id,
            expected_head=branch.head_commit_id,
            actor=Actor(kind="human", id="author"),
            idempotency_key="evd-seed-1",
            summary="写入需核对证据",
            changes=(
                ArtifactChange(
                    artifact_id=ArtifactId("art_evd_1"),
                    kind="evidence.link",
                    content={
                        "evidence_id": "evd_1",
                        "anchor_kind": "setting",
                        "target_id": character["id"],
                        "chapter_id": "art_ch",
                        "quote": "姐姐站在门口",
                        "source_hash": "hash-1",
                        "status": "needs_check",
                    },
                ),
            ),
        )
    )
    nodes = {node["id"]: node for node in _graph(client, pid)["nodes"]}
    assert nodes[character["id"]]["evidence_status"] == "needs_check"
