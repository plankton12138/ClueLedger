"""线索图（V03-6.3）HTTP 用例：计划/实现分离、叙述位置筛选、证据与局部聚焦。

都走真实服务：节点/边只来自同 Commit 的正式账本，读线索图不产生任何写入。
叙述结构与按节拍归位的披露在真实账本里没有写路径，用例用直接提交预置——
这与「线索图只读」不冲突，只是准备事实的来源不同。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hastings.domain.repository.ids import Actor, ArtifactId, ProjectId
from hastings.domain.repository.requests import ArtifactChange, CommitRequest
from hastings.server.v2 import V2Runtime, build_v2_app


def _client() -> tuple[TestClient, str, V2Runtime]:
    runtime = V2Runtime.memory()
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "线索图测试"}).json()["project_id"]
    return client, pid, runtime


def _create(client: TestClient, pid: str, kind: str, fields: dict, request_id: str) -> dict:
    response = client.post(
        f"/api/v2/projects/{pid}/ledger/entries",
        json={"kind": kind, "fields": fields, "request_id": request_id},
    )
    assert response.status_code == 201, response.text
    return response.json()["entry"]


def _clues(client: TestClient, pid: str, **params: str) -> dict:
    response = client.get(f"/api/v2/projects/{pid}/graphs/clues", params=params)
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
            summary="用例预置结构与披露",
            changes=tuple(changes),
        )
    )


def _structure(fact_plan: str, fact_seen: str) -> list[ArtifactChange]:
    """三拍结构：b1 计划出现位置、b3 向读者披露 fact_seen（该章末节拍）。"""
    return [
        ArtifactChange(
            artifact_id=ArtifactId("art_ch"),
            kind="narrative.chapter",
            content={"node_id": "art_ch", "order_key": "1", "title": "第一章"},
        ),
        ArtifactChange(
            artifact_id=ArtifactId("art_sc"),
            kind="narrative.scene",
            content={"node_id": "art_sc", "parent_id": "art_ch"},
        ),
        ArtifactChange(
            artifact_id=ArtifactId("art_b1"),
            kind="narrative.beat",
            content={"beat_id": "art_b1", "parent_id": "art_sc", "order_key": "1"},
        ),
        ArtifactChange(
            artifact_id=ArtifactId("art_b2"),
            kind="narrative.beat",
            content={"beat_id": "art_b2", "parent_id": "art_sc", "order_key": "2"},
        ),
        ArtifactChange(
            artifact_id=ArtifactId("art_b3"),
            kind="narrative.beat",
            content={
                "beat_id": "art_b3",
                "parent_id": "art_sc",
                "order_key": "3",
                "reader_disclosures": [
                    {"fact_id": fact_seen, "audience": "reader", "interpretation": "true"}
                ],
            },
        ),
        ArtifactChange(
            artifact_id=ArtifactId("art_clue_plan"),
            kind="story.clue",
            content={
                "statement": "停走的怀表",
                "points_to": [fact_plan],
                "disclosure_plan": ["art_b1"],
            },
        ),
    ]


def test_clue_graph_reports_plan_and_implementation() -> None:
    client, pid, runtime = _client()
    fact_plan = _create(client, pid, "story.truth_fact", {"statement": "怀表已损坏"}, "r-f1")
    fact_seen = _create(client, pid, "story.truth_fact", {"statement": "姐姐九点离开"}, "r-f2")
    _create(
        client,
        pid,
        "story.clue",
        {"statement": "九点的钟声", "points_to": fact_seen["id"]},
        "r-clue-done",
    )
    # 计划位置与「已在正文出现」的披露在真实账本里没有写路径，用直接提交预置。
    _commit(runtime, pid, _structure(fact_plan["id"], fact_seen["id"]), "req-structure")

    payload = _clues(client, pid)
    states = {state["clue_id"]: state for state in payload["clues"]}

    planned = states["art_clue_plan"]
    assert planned["points_to"] == [fact_plan["id"]]
    assert planned["planned_beats"] == ["art_b1"]
    assert planned["disclosed_beats"] == []
    assert planned["status"] == "planned"
    assert planned["first_position"] == 0

    done = next(state for state in payload["clues"] if state["status"] == "implemented")
    assert done["disclosed_beats"] == ["art_b3"]

    edges = {(edge["source"], edge["target"]) for edge in payload["edges"]}
    assert ("art_clue_plan", fact_plan["id"]) in edges
    assert (done["clue_id"], fact_seen["id"]) in edges


def test_position_filters_later_clues_by_beat_or_chapter() -> None:
    client, pid, runtime = _client()
    fact_plan = _create(client, pid, "story.truth_fact", {"statement": "怀表已损坏"}, "r-f1")
    fact_seen = _create(client, pid, "story.truth_fact", {"statement": "姐姐九点离开"}, "r-f2")
    done = _create(
        client,
        pid,
        "story.clue",
        {"statement": "九点的钟声", "points_to": fact_seen["id"]},
        "r-clue-done",
    )
    _commit(runtime, pid, _structure(fact_plan["id"], fact_seen["id"]), "req-structure")

    early = _clues(client, pid, at_beat_id="art_b1")
    assert early["position"] == {
        "kind": "beat",
        "at_chapter_id": None,
        "at_beat_id": "art_b1",
        "label": "art_b1",
    }
    states = {state["clue_id"]: state for state in early["clues"]}
    # b1 位置的线索在「截至 b1」内；b3 才出现的线索不提前进入视图。
    assert states["art_clue_plan"]["within_position"] is True
    assert states[done["id"]]["within_position"] is False

    # 「截至第一章」解析为该章末节拍（art_b3），两条线索都已出现。
    by_chapter = _clues(client, pid, at_chapter_id="art_ch")
    assert by_chapter["position"] == {
        "kind": "chapter",
        "at_chapter_id": "art_ch",
        "at_beat_id": "art_b3",
        "label": "第一章",
    }
    assert all(state["within_position"] is True for state in by_chapter["clues"])


def test_position_conditions_are_mutually_exclusive_and_unknown_is_rejected() -> None:
    client, pid, runtime = _client()
    fact_plan = _create(client, pid, "story.truth_fact", {"statement": "怀表已损坏"}, "r-f1")
    fact_seen = _create(client, pid, "story.truth_fact", {"statement": "姐姐九点离开"}, "r-f2")
    _commit(runtime, pid, _structure(fact_plan["id"], fact_seen["id"]), "req-structure")

    both = client.get(
        f"/api/v2/projects/{pid}/graphs/clues",
        params={"at_beat_id": "art_b1", "at_chapter_id": "art_ch"},
    )
    assert both.status_code == 400, both.text
    assert both.json()["error"]["code"] == "STORY_INVALID_COMMAND"

    unknown_chapter = client.get(
        f"/api/v2/projects/{pid}/graphs/clues", params={"at_chapter_id": "art_missing"}
    )
    assert unknown_chapter.status_code == 404, unknown_chapter.text
    assert unknown_chapter.json()["error"]["code"] == "STORY_UNKNOWN_ARTIFACT"

    unknown_beat = client.get(
        f"/api/v2/projects/{pid}/graphs/clues", params={"at_beat_id": "art_missing"}
    )
    assert unknown_beat.status_code == 404, unknown_beat.text

    unknown_focus = client.get(
        f"/api/v2/projects/{pid}/graphs/clues", params={"focus": "art_missing"}
    )
    assert unknown_focus.status_code == 404, unknown_focus.text


def test_focus_returns_local_neighbours_within_the_view() -> None:
    client, pid, _ = _client()
    fact = _create(client, pid, "story.truth_fact", {"statement": "怀表已损坏"}, "r-f")
    clue = _create(
        client, pid, "story.clue", {"statement": "停走的怀表", "points_to": fact["id"]}, "r-clue"
    )

    focused = _clues(client, pid, focus=clue["id"])
    assert focused["focus"]["node_id"] == clue["id"]
    assert focused["focus"]["neighbor_ids"] == [fact["id"]]
    assert {node["id"] for node in focused["nodes"]} == {clue["id"], fact["id"]}
    assert {state["clue_id"] for state in focused["clues"]} == {clue["id"]}


def test_evidence_needs_check_reaches_the_clue() -> None:
    client, pid, runtime = _client()
    fact = _create(client, pid, "story.truth_fact", {"statement": "怀表已损坏"}, "r-f")
    clue = _create(
        client, pid, "story.clue", {"statement": "停走的怀表", "points_to": fact["id"]}, "r-clue"
    )
    _commit(
        runtime,
        pid,
        [
            ArtifactChange(
                artifact_id=ArtifactId("art_evd_1"),
                kind="evidence.link",
                content={
                    "evidence_id": "evd_1",
                    "anchor_kind": "setting",
                    "target_id": clue["id"],
                    "chapter_id": "art_ch",
                    "quote": "怀表停在九点",
                    "source_hash": "hash-1",
                    "status": "needs_check",
                },
            )
        ],
        "req-evd",
    )
    nodes = {node["id"]: node for node in _clues(client, pid)["nodes"]}
    # 来源正文变化后线索立即显示需核对——复用 M05，不另建失效逻辑。
    assert nodes[clue["id"]]["evidence_status"] == "needs_check"


def test_reading_the_clue_graph_writes_nothing() -> None:
    client, pid, _ = _client()
    fact = _create(client, pid, "story.truth_fact", {"statement": "怀表已损坏"}, "r-f")
    _create(
        client, pid, "story.clue", {"statement": "停走的怀表", "points_to": fact["id"]}, "r-clue"
    )
    before_head = client.get(f"/api/v2/projects/{pid}/desk-state").json()["commit_id"]
    before_entries = client.get(f"/api/v2/projects/{pid}/ledger").json()["entries"]

    assert _clues(client, pid) == _clues(client, pid)
    assert client.get(f"/api/v2/projects/{pid}/desk-state").json()["commit_id"] == before_head
    assert client.get(f"/api/v2/projects/{pid}/ledger").json()["entries"] == before_entries


def test_empty_project_returns_empty_clue_graph() -> None:
    client, pid, _ = _client()
    assert _clues(client, pid) == {
        "project_id": pid,
        "commit_id": None,
        "position": None,
        "focus": None,
        "clues": [],
        "nodes": [],
        "edges": [],
    }
