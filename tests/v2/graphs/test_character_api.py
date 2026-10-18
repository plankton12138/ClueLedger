"""人物图（V03-6.2）HTTP 用例：视角标签、叙述位置解析、未记录不合成、局部聚焦。

都走真实服务：节点/边只来自同 Commit 的正式账本，读人物图不产生任何写入。
叙述结构（章/场景/节拍）与按节拍归位的认知在真实账本里没有写路径，用例用
直接提交预置——这与「人物图只读」不冲突，只是准备事实的来源不同。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hastings.domain.repository.ids import Actor, ArtifactId, ProjectId
from hastings.domain.repository.requests import ArtifactChange, CommitRequest
from hastings.server.v2 import V2Runtime, build_v2_app


def _client() -> tuple[TestClient, str, V2Runtime]:
    runtime = V2Runtime.memory()
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "人物图测试"}).json()["project_id"]
    return client, pid, runtime


def _create(client: TestClient, pid: str, kind: str, fields: dict, request_id: str) -> dict:
    response = client.post(
        f"/api/v2/projects/{pid}/ledger/entries",
        json={"kind": kind, "fields": fields, "request_id": request_id},
    )
    assert response.status_code == 201, response.text
    return response.json()["entry"]


def _characters(client: TestClient, pid: str, **params: str) -> dict:
    response = client.get(f"/api/v2/projects/{pid}/graphs/characters", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _commit(runtime: V2Runtime, pid: str, changes: list[ArtifactChange], key: str) -> None:
    branch = runtime.service.get_branch(ProjectId(pid), "main")
    assert branch is not None
    runtime.service.commit(
        CommitRequest(
            project_id=ProjectId(pid),
            branch_id=branch.id,
            expected_head=branch.head_commit_id,
            actor=Actor(kind="human", id="author"),
            idempotency_key=key,
            summary="用例预置结构与认知",
            changes=tuple(changes),
        )
    )


def _story_structure(character_id: str, fact1: str, fact2: str) -> list[ArtifactChange]:
    return [
        ArtifactChange(
            artifact_id=ArtifactId("art_ch"),
            kind="narrative.chapter",
            content={"node_id": "art_ch", "kind": "narrative.chapter", "title": "第一章"},
        ),
        ArtifactChange(
            artifact_id=ArtifactId("art_sc"),
            kind="narrative.scene",
            content={"node_id": "art_sc", "kind": "narrative.scene", "parent_id": "art_ch"},
        ),
        ArtifactChange(
            artifact_id=ArtifactId("art_b1"),
            kind="narrative.beat",
            content={"beat_id": "art_b1", "parent_id": "art_sc", "title": "第一拍"},
        ),
        ArtifactChange(
            artifact_id=ArtifactId("art_b2"),
            kind="narrative.beat",
            content={"beat_id": "art_b2", "parent_id": "art_sc", "title": "第二拍"},
        ),
        ArtifactChange(
            artifact_id=ArtifactId("art_k1"),
            kind="story.knowledge",
            content={
                "character_id": character_id,
                "fact_id": fact1,
                "mode": "knows",
                "at_beat_id": "art_b1",
            },
        ),
        ArtifactChange(
            artifact_id=ArtifactId("art_k2"),
            kind="story.knowledge",
            content={
                "character_id": character_id,
                "fact_id": fact2,
                "mode": "suspects",
                "at_beat_id": "art_b2",
            },
        ),
    ]


def test_author_and_character_perspectives_expose_different_truth() -> None:
    client, pid, _ = _client()
    sister = _create(client, pid, "story.character", {"name": "姐姐"}, "r-a")
    guest = _create(client, pid, "story.character", {"name": "妹妹"}, "r-b")
    _create(
        client,
        pid,
        "story.relationship",
        {
            "source_id": sister["id"],
            "target_id": guest["id"],
            "relation_type": "姐妹",
            "public_state": "表面和睦",
            "true_state": "暗中猜忌",
        },
        "r-rel",
    )
    fact = _create(client, pid, "story.truth_fact", {"statement": "九点到达"}, "r-f")
    _create(
        client,
        pid,
        "story.knowledge",
        {"character_id": sister["id"], "fact_id": fact["id"], "mode": "suspects"},
        "r-k",
    )

    author = _characters(client, pid)
    assert author["perspective"] == {"mode": "author", "character_id": None, "label": "作者全知"}
    assert author["position"] is None
    assert author["focus"] is None
    edges = {(edge["source"], edge["target"]): edge for edge in author["edges"]}
    relation = edges[(sister["id"], guest["id"])]
    assert (relation["semantic_layer"], relation["label"]) == ("truth", "姐妹")
    assert (relation["public_state"], relation["true_state"]) == ("表面和睦", "暗中猜忌")
    assert relation["true_state_hidden"] is False
    knowledge = edges[(sister["id"], fact["id"])]
    assert (knowledge["semantic_layer"], knowledge["label"]) == ("knowledge", "怀疑")

    guest_view = _characters(client, pid, perspective=guest["id"])
    assert guest_view["perspective"] == {
        "mode": "character",
        "character_id": guest["id"],
        "label": "妹妹视角",
    }
    guest_edges = {(edge["source"], edge["target"]): edge for edge in guest_view["edges"]}
    # 妹妹看不见姐姐的认知，也看不见关系的真实状态。
    assert set(guest_edges) == {(sister["id"], guest["id"])}
    assert guest_edges[(sister["id"], guest["id"])]["true_state"] == ""
    assert guest_edges[(sister["id"], guest["id"])]["true_state_hidden"] is True


def test_position_by_beat_or_chapter_filters_knowledge() -> None:
    client, pid, runtime = _client()
    sister = _create(client, pid, "story.character", {"name": "姐姐"}, "r-a")
    fact1 = _create(client, pid, "story.truth_fact", {"statement": "九点到达"}, "r-f1")
    fact2 = _create(client, pid, "story.truth_fact", {"statement": "十点离开"}, "r-f2")
    _commit(runtime, pid, _story_structure(sister["id"], fact1["id"], fact2["id"]), "req-structure")

    early = _characters(client, pid, at_beat_id="art_b1")
    assert early["position"] == {
        "kind": "beat",
        "at_chapter_id": None,
        "at_beat_id": "art_b1",
        "label": "第一拍",
    }
    assert {(e["source"], e["target"]) for e in early["edges"]} == {(sister["id"], fact1["id"])}

    # 「截至第一章」解析为该章末节拍，与直接指定末节拍结果一致。
    by_chapter = _characters(client, pid, at_chapter_id="art_ch")
    assert by_chapter["position"] == {
        "kind": "chapter",
        "at_chapter_id": "art_ch",
        "at_beat_id": "art_b2",
        "label": "第一章",
    }
    by_last_beat = _characters(client, pid, at_beat_id="art_b2")
    assert by_chapter["edges"] == by_last_beat["edges"]
    assert {(e["source"], e["target"]) for e in by_chapter["edges"]} == {
        (sister["id"], fact1["id"]),
        (sister["id"], fact2["id"]),
    }


def test_position_conditions_are_mutually_exclusive_and_unknown_is_rejected() -> None:
    client, pid, runtime = _client()
    sister = _create(client, pid, "story.character", {"name": "姐姐"}, "r-a")
    fact1 = _create(client, pid, "story.truth_fact", {"statement": "九点到达"}, "r-f1")
    fact2 = _create(client, pid, "story.truth_fact", {"statement": "十点离开"}, "r-f2")
    _commit(runtime, pid, _story_structure(sister["id"], fact1["id"], fact2["id"]), "req-structure")

    both = client.get(
        f"/api/v2/projects/{pid}/graphs/characters",
        params={"at_beat_id": "art_b1", "at_chapter_id": "art_ch"},
    )
    assert both.status_code == 400, both.text
    assert both.json()["error"]["code"] == "STORY_INVALID_COMMAND"

    unknown_chapter = client.get(
        f"/api/v2/projects/{pid}/graphs/characters", params={"at_chapter_id": "art_missing"}
    )
    assert unknown_chapter.status_code == 404, unknown_chapter.text
    assert unknown_chapter.json()["error"]["code"] == "STORY_UNKNOWN_ARTIFACT"

    unknown_perspective = client.get(
        f"/api/v2/projects/{pid}/graphs/characters", params={"perspective": "art_missing"}
    )
    assert unknown_perspective.status_code == 404, unknown_perspective.text


def test_unrecorded_relation_is_a_gap_not_an_unaware_edge() -> None:
    client, pid, _ = _client()
    sister = _create(client, pid, "story.character", {"name": "姐姐"}, "r-a")
    fact = _create(client, pid, "story.truth_fact", {"statement": "九点到达"}, "r-f")

    author = _characters(client, pid)
    assert not any(
        edge["source"] == sister["id"] and edge["target"] == fact["id"] for edge in author["edges"]
    )
    assert not any(edge["semantic_layer"] == "knowledge" for edge in author["edges"])

    # 角色视角里，没有认知记录的事实甚至不会成为节点——缺口如实留白。
    character = _characters(client, pid, perspective=sister["id"])
    assert character["edges"] == []
    assert {node["id"] for node in character["nodes"]} == {sister["id"]}


def test_focus_returns_local_neighbours_within_the_view() -> None:
    client, pid, _ = _client()
    sister = _create(client, pid, "story.character", {"name": "姐姐"}, "r-a")
    guest = _create(client, pid, "story.character", {"name": "妹妹"}, "r-b")
    _create(
        client,
        pid,
        "story.relationship",
        {"source_id": sister["id"], "target_id": guest["id"], "relation_type": "姐妹"},
        "r-rel",
    )
    fact = _create(client, pid, "story.truth_fact", {"statement": "九点到达"}, "r-f")
    clue = _create(
        client, pid, "story.clue", {"statement": "怀表停在九点", "points_to": fact["id"]}, "r-clue"
    )
    _create(
        client,
        pid,
        "story.knowledge",
        {"character_id": sister["id"], "fact_id": fact["id"], "mode": "suspects"},
        "r-k",
    )

    # 作者全知：焦点事实的邻居含声明认知的人物与指向它的线索。
    author = _characters(client, pid, focus=fact["id"])
    assert author["focus"]["node_id"] == fact["id"]
    assert set(author["focus"]["neighbor_ids"]) == {sister["id"], clue["id"]}

    # 角色视角：线索「指向」不在人物图内，焦点事实的邻居只剩该角色。
    character = _characters(client, pid, perspective=sister["id"], focus=fact["id"])
    assert set(character["focus"]["neighbor_ids"]) == {sister["id"]}

    missing = client.get(
        f"/api/v2/projects/{pid}/graphs/characters", params={"focus": "art_missing"}
    )
    assert missing.status_code == 404, missing.text


def test_reading_the_character_graph_writes_nothing() -> None:
    client, pid, _ = _client()
    _create(client, pid, "story.character", {"name": "姐姐"}, "r-a")
    _create(client, pid, "story.character", {"name": "妹妹"}, "r-b")
    before_head = client.get(f"/api/v2/projects/{pid}/desk-state").json()["commit_id"]
    before_entries = client.get(f"/api/v2/projects/{pid}/ledger").json()["entries"]

    first = _characters(client, pid)
    second = _characters(client, pid, perspective=first["nodes"][0]["id"])
    assert first == _characters(client, pid)
    assert second == _characters(client, pid, perspective=first["nodes"][0]["id"])
    assert client.get(f"/api/v2/projects/{pid}/desk-state").json()["commit_id"] == before_head
    assert client.get(f"/api/v2/projects/{pid}/ledger").json()["entries"] == before_entries


def test_empty_project_returns_empty_character_graph() -> None:
    client, pid, _ = _client()
    assert _characters(client, pid) == {
        "project_id": pid,
        "commit_id": None,
        "perspective": {"mode": "author", "character_id": None, "label": "作者全知"},
        "position": None,
        "nodes": [],
        "edges": [],
        "focus": None,
    }
