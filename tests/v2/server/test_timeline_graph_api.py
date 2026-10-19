"""时间线 REST（V03-7.6）：真实事件与证词分轨、时间原样、聚焦不改变事实。"""

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
    pid = client.post("/api/v2/projects", json={"name": "时间线"}).json()["project_id"]
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


def _timeline(client: TestClient, pid: str, **params: str) -> dict:
    query = "&".join(f"{key}={value}" for key, value in params.items())
    suffix = f"?{query}" if query else ""
    response = client.get(f"/api/v2/projects/{pid}/graphs/timeline{suffix}")
    assert response.status_code == 200, response.text
    return response.json()


def _scene(client: TestClient, pid: str) -> dict[str, str]:
    sister = _create(client, pid, "story.character", {"name": "姐姐"}, "req-sis")
    detective = _create(client, pid, "story.character", {"name": "侦探"}, "req-det")
    room = _create(client, pid, "story.place", {"name": "书房"}, "req-room")
    corridor = _create(client, pid, "story.place", {"name": "走廊"}, "req-corridor")
    early = _create(
        client,
        pid,
        "story.timeline_event",
        {"time": "八点到九点之间", "place_id": room["id"], "participants": [sister["id"]]},
        "req-e1",
    )
    late = _create(
        client,
        pid,
        "story.timeline_event",
        {"time": "晚上十点", "place_id": corridor["id"], "participants": [detective["id"]]},
        "req-e2",
    )
    fact = _create(
        client,
        pid,
        "story.truth_fact",
        {"statement": "姐姐九点到达", "event_id": early["id"]},
        "req-fact",
    )
    _create(
        client,
        pid,
        "story.knowledge",
        {
            "character_id": detective["id"],
            "fact_id": fact["id"],
            "mode": "misbelieves",
            "note": "他相信管家说的是十点",
        },
        "req-know",
    )
    return {
        "sister": sister["id"],
        "detective": detective["id"],
        "room": room["id"],
        "corridor": corridor["id"],
        "early": early["id"],
        "late": late["id"],
    }


def test_empty_project_reports_no_events_and_ordering_note() -> None:
    client, pid = _client()
    payload = _timeline(client, pid)
    assert payload["commit_id"] is None
    assert payload["events"] == []
    assert [gap["code"] for gap in payload["gaps"]] == ["TIMELINE_NO_EVENTS"]
    assert payload["ordering"]["story_time"] == "as_recorded"
    assert "不用章节先后" in payload["ordering"]["note"]


def test_events_split_by_character_and_place() -> None:
    client, pid = _client()
    _import(client, pid)
    ids = _scene(client, pid)

    by_character = _timeline(client, pid)
    assert by_character["group_by"] == "character"
    assert {track["id"] for track in by_character["tracks"]} == {ids["sister"], ids["detective"]}
    sister_track = next(track for track in by_character["tracks"] if track["id"] == ids["sister"])
    assert sister_track["event_ids"] == [ids["early"]]
    early = next(event for event in by_character["events"] if event["event_id"] == ids["early"])
    assert early["time_text"] == "八点到九点之间"  # 区间原样保留
    assert early["time_status"] == "recorded"

    by_place = _timeline(client, pid, group_by="place")
    assert {track["id"] for track in by_place["tracks"]} == {ids["room"], ids["corridor"]}


def test_testimony_track_keeps_mode_and_true_event_time_is_separate() -> None:
    client, pid = _client()
    _import(client, pid)
    ids = _scene(client, pid)
    payload = _timeline(client, pid)
    track = payload["testimonies"][0]
    assert track["character_id"] == ids["detective"]
    item = track["items"][0]
    assert item["mode"] == "misbelieves"
    assert item["mode_label"] == "误信"
    assert item["note"] == "他相信管家说的是十点"
    assert item["claimed_event_id"] == ids["early"]
    assert item["claimed_event_time"] == "八点到九点之间"
    assert item["position"] is None  # 获知节拍未记录：不按章节顺序推算
    assert "不作为证词时间" in payload["testimony_note"]


def test_focus_narrows_tracks_and_unknown_focus_is_404() -> None:
    client, pid = _client()
    _import(client, pid)
    ids = _scene(client, pid)

    focused = _timeline(client, pid, focus=ids["sister"])
    assert [track["id"] for track in focused["tracks"]] == [ids["sister"]]
    assert [event["event_id"] for event in focused["events"]] == [ids["early"]]
    assert focused["focus"]["node_id"] == ids["sister"]

    by_event = _timeline(client, pid, focus=ids["late"])
    assert [event["event_id"] for event in by_event["events"]] == [ids["late"]]

    missing = client.get(f"/api/v2/projects/{pid}/graphs/timeline?focus=missing")
    assert missing.status_code == 404, missing.text
    assert missing.json()["error"]["code"] == "STORY_UNKNOWN_ARTIFACT"


def test_invalid_group_by_is_rejected() -> None:
    client, pid = _client()
    _import(client, pid)
    response = client.get(f"/api/v2/projects/{pid}/graphs/timeline?group_by=chapter")
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "STORY_INVALID_COMMAND"
