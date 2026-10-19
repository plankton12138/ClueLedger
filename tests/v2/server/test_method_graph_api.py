"""手法图 REST（V03-7.5）：真实来源、缺口与完整度。

验收口径（计划 M12 / A21）：四类图都有真实数据与空态；手法图资料不完整时仍忠实
展示缺口，不新增结构对象、不调用模型。
"""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from hastings.server.v2 import V2Runtime, build_v2_app

MD = """# 雪山旅馆

## 第一章 雪夜

侦探在雪夜抵达旅馆。
"""


def _client() -> tuple[TestClient, str]:
    runtime = V2Runtime.memory()
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "手法图"}).json()["project_id"]
    return client, pid


def _import(client: TestClient, pid: str) -> None:
    preview = client.post(
        f"/api/v2/projects/{pid}/imports/previews",
        json={
            "filename": "雪山旅馆.md",
            "content_base64": base64.b64encode(MD.encode("utf-8")).decode("ascii"),
        },
    ).json()
    response = client.post(
        f"/api/v2/projects/{pid}/imports/previews/{preview['preview_id']}/confirm",
        json={"preview_hash": preview["preview_hash"]},
    )
    assert response.status_code == 200, response.text


def _create(client: TestClient, pid: str, kind: str, fields: dict, request_id: str) -> dict:
    response = client.post(
        f"/api/v2/projects/{pid}/ledger/entries",
        json={"kind": kind, "fields": fields, "request_id": request_id},
    )
    assert response.status_code == 201, response.text
    return response.json()["entry"]


def _method(client: TestClient, pid: str, **params: str) -> dict:
    query = "&".join(f"{key}={value}" for key, value in params.items())
    suffix = f"?{query}" if query else ""
    response = client.get(f"/api/v2/projects/{pid}/graphs/method{suffix}")
    assert response.status_code == 200, response.text
    return response.json()


def _full_chain(client: TestClient, pid: str) -> dict[str, str]:
    culprit = _create(client, pid, "story.character", {"name": "姐姐"}, "req-char")
    place = _create(client, pid, "story.place", {"name": "书房"}, "req-place")
    event = _create(
        client,
        pid,
        "story.timeline_event",
        {"time": "晚上九点", "place_id": place["id"], "participants": [culprit["id"]]},
        "req-event",
    )
    truth = _create(
        client,
        pid,
        "story.mystery_truth",
        {
            "culprit_id": culprit["id"],
            "method": "把安眠药换成心脏药",
            "motive": "独吞遗产",
            "cover_up": "伪装成意外",
            "true_timeline": [event["id"]],
        },
        "req-truth",
    )
    fact = _create(
        client,
        pid,
        "story.truth_fact",
        {"statement": "药瓶被换过", "event_id": event["id"]},
        "req-fact",
    )
    clue = _create(
        client,
        pid,
        "story.clue",
        {"statement": "床头有两只药瓶", "points_to": [fact["id"]]},
        "req-clue",
    )
    return {
        "culprit": culprit["id"],
        "place": place["id"],
        "event": event["id"],
        "truth": truth["id"],
        "fact": fact["id"],
        "clue": clue["id"],
    }


def test_empty_project_reports_no_truth_recorded() -> None:
    client, pid = _client()
    payload = _method(client, pid)
    assert payload["commit_id"] is None
    assert payload["truths"] == []
    assert payload["nodes"] == []
    assert [gap["code"] for gap in payload["gaps"]] == ["METHOD_TRUTH_NOT_RECORDED"]
    assert payload["gaps"][0]["fix"]


def test_manuscript_without_truth_still_reports_gap() -> None:
    client, pid = _client()
    _import(client, pid)
    payload = _method(client, pid)
    assert payload["commit_id"] is not None
    assert [gap["code"] for gap in payload["gaps"]] == ["METHOD_TRUTH_NOT_RECORDED"]


def test_partial_truth_shows_text_gaps_and_culprit_edge() -> None:
    client, pid = _client()
    culprit = _create(client, pid, "story.character", {"name": "姐姐"}, "req-char")
    _create(
        client,
        pid,
        "story.mystery_truth",
        {"culprit_id": culprit["id"], "method": "换药"},
        "req-truth",
    )
    payload = _method(client, pid)
    truth = payload["truths"][0]
    assert truth["method"] == "换药"
    assert truth["missing_fields"] == ["motive", "cover_up"]
    assert payload["completeness"] == {
        "recorded": 1,
        "total": 3,
        "note": "只统计手法/动机/掩盖三段说明是否已写，不代表推理链成立或手法可行",
    }
    pairs = {(edge["source"], edge["target"], edge["label"]) for edge in payload["edges"]}
    assert (truth["truth_id"], culprit["id"], "凶手") in pairs
    assert any(gap["code"] == "METHOD_CLUES_NOT_RECORDED" for gap in payload["gaps"])


def test_full_chain_projects_text_relations_and_no_gaps() -> None:
    client, pid = _client()
    ids = _full_chain(client, pid)
    payload = _method(client, pid)

    truth = payload["truths"][0]
    assert truth["method"] == "把安眠药换成心脏药"
    assert truth["motive"] == "独吞遗产"
    assert truth["cover_up"] == "伪装成意外"
    assert truth["true_timeline"] == [ids["event"]]
    assert payload["gaps"] == []
    assert payload["completeness"]["recorded"] == 3

    assert {node["id"] for node in payload["nodes"]} == {
        ids["culprit"],
        ids["place"],
        ids["event"],
        ids["truth"],
        ids["fact"],
        ids["clue"],
    }
    pairs = {(edge["source"], edge["target"], edge["label"]) for edge in payload["edges"]}
    assert (ids["truth"], ids["culprit"], "凶手") in pairs
    assert (ids["truth"], ids["event"], "真实时间线") in pairs
    assert (ids["event"], ids["place"], "地点") in pairs
    assert (ids["clue"], ids["fact"], "指向") in pairs
    assert all(edge["semantic_layer"] == "truth" for edge in payload["edges"])


def test_focus_limits_to_neighbors_and_unknown_focus_is_404() -> None:
    client, pid = _client()
    ids = _full_chain(client, pid)
    focused = _method(client, pid, focus=ids["event"])
    assert {node["id"] for node in focused["nodes"]} == {
        ids["event"],
        ids["place"],
        ids["culprit"],
        ids["truth"],
        ids["fact"],
    }
    assert focused["focus"]["node_id"] == ids["event"]
    assert set(focused["focus"]["neighbor_ids"]) == {
        ids["place"],
        ids["culprit"],
        ids["truth"],
        ids["fact"],
    }

    missing = client.get(f"/api/v2/projects/{pid}/graphs/method?focus=missing")
    assert missing.status_code == 404, missing.text
    assert missing.json()["error"]["code"] == "STORY_UNKNOWN_ARTIFACT"
