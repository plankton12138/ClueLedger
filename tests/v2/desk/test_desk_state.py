"""工作台共享状态（V03-4.5）：侧栏只拿计数与权威 ID，不复制列表。

产品设计 §5.1「待确认入口…展开为独立候选区」与 §5.2「右侧只提供数量和定位入口」
要求侧栏引用同一事实源；这些用例盯的就是「不返回正文、不出现第二份列表」。
"""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from hastings.server.v2 import V2Runtime, build_v2_app

MD = """# 雪山旅馆

## 第一章 雪夜

侦探在雪夜抵达旅馆。

## 第二章 空房

走廊尽头有一扇门。
"""


def _client() -> tuple[TestClient, str]:
    runtime = V2Runtime.memory()
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "工作台"}).json()["project_id"]
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


def _states(client: TestClient, pid: str) -> dict:
    response = client.get(f"/api/v2/projects/{pid}/desk-state")
    assert response.status_code == 200, response.text
    return response.json()


def test_empty_project_reports_zeroes_not_empty_cards() -> None:
    client, pid = _client()
    payload = _states(client, pid)
    assert payload["commit_id"] is None
    assert payload["counts"] == {
        "ledger_entries": 0,
        "agreements": 0,
        "summaries": {"fresh": 0, "stale": 0, "missing": 0},
        "pending_candidates": 0,
        "hypothesis_candidates": 0,
        "open_edit_chapters": 0,
    }
    # 候选组 ID 在空作品里也存在：侧栏拿它去读详情，不需要先判断有没有列表。
    assert payload["refs"]["candidate_group_id"].startswith("lcg_")
    assert payload["refs"]["agreements_hash"] == ""


def test_counts_follow_the_actual_commit() -> None:
    client, pid = _client()
    _import(client, pid)
    payload = _states(client, pid)
    assert payload["commit_id"]
    assert payload["counts"]["summaries"]["missing"] == 2
    assert payload["counts"]["summaries"]["fresh"] == 0
    assert payload["counts"]["ledger_entries"] == 0


def test_pending_candidates_are_counted_but_not_listed() -> None:
    """侧栏要数字，不要第二份条目列表。"""
    client, pid = _client()
    staged = client.post(
        f"/api/v2/projects/{pid}/ledger/candidates/items",
        json={"kind": "story.character", "fields": {"name": "林昭"}, "request_id": "req-1"},
    )
    assert staged.status_code in (200, 201), staged.text

    payload = _states(client, pid)
    assert payload["counts"]["pending_candidates"] == 1
    assert "items" not in payload
    # 条目正文只能从候选组接口取，且组 ID 与这里一致。
    group = client.get(f"/api/v2/projects/{pid}/ledger/candidates").json()
    assert group["group_id"] == payload["refs"]["candidate_group_id"]
    assert [item["title"] for item in group["items"]] == ["林昭"]


def test_open_edit_session_is_surfaced_as_reference() -> None:
    client, pid = _client()
    _import(client, pid)
    chapters = client.get(f"/api/v2/projects/{pid}/manuscript").json()["chapters"]
    session = client.post(f"/api/v2/projects/{pid}/edit-sessions").json()["session"]
    saved = client.put(
        f"/api/v2/projects/{pid}/edit-sessions/{session['session_id']}"
        f"/chapters/{chapters[0]['chapter_id']}",
        json={"text": "改过的正文。", "expected_save_seq": 0, "request_id": "s1"},
    )
    assert saved.status_code == 200, saved.text

    payload = _states(client, pid)
    assert payload["counts"]["open_edit_chapters"] == 1
    assert payload["refs"]["edit_session_id"] == session["session_id"]
    assert payload["refs"]["edit_chapter_ids"] == [chapters[0]["chapter_id"]]
    # 只给 ID 与数量：侧栏不复制草稿正文。
    assert "改过的正文。" not in str(payload)


def test_content_hash_is_stable_and_changes_with_content() -> None:
    """指纹让前端判断「要不要重取」，而不是逐字段比对或定时轮询。"""
    client, pid = _client()
    first = _states(client, pid)
    second = _states(client, pid)
    assert first["content_hash"] == second["content_hash"]

    _import(client, pid)
    third = _states(client, pid)
    assert third["content_hash"] != first["content_hash"]


def test_recent_operations_are_reprojected_after_reload() -> None:
    """重启后仍能看到上次结果：列表来自操作记录，不是内存里的实时事件。"""
    client, pid = _client()
    _import(client, pid)
    first = _states(client, pid)
    assert [row["kind"] for row in first["recent_operations"]] == ["chapter.import"]

    # 再读一次（等价于重开页面）：同一条结果、同一个顺序。
    again = _states(client, pid)
    assert again["recent_operations"] == first["recent_operations"]
    row = first["recent_operations"][0]
    assert row["status"] == "committed"
    assert row["changed_chapter_ids"]
