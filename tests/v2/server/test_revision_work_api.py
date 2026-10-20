"""整体调整工作版本 REST（V03-7.2）：逐章实际读取与未覆盖都可查，摘要不当全文检查。

验收口径（计划 M10 第 5 条 / 阶段指南 §11.2）：覆盖台账逐章记录五状态 + 读取区间；
只读摘要不能记 ``checked_unchanged``。这些用例从 HTTP 边界把这条约束再钉一遍。
"""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from hastings.server.v2 import V2Runtime, build_v2_app

MD = """# 雪山旅馆

## 第一章 雪夜

侦探在雪夜抵达旅馆，风雪封住了山路。

## 第二章 空房

走廊尽头有一扇门，门后没有声音。
"""


def _client() -> tuple[TestClient, str]:
    runtime = V2Runtime.memory()
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "整体调整"}).json()["project_id"]
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


def _chapters(client: TestClient, pid: str) -> list[str]:
    rows = client.get(f"/api/v2/projects/{pid}/manuscript").json()["chapters"]
    return [row["chapter_id"] for row in rows]


def _open(client: TestClient, pid: str) -> dict:
    response = client.post(f"/api/v2/projects/{pid}/revision-work")
    assert response.status_code == 200, response.text
    return response.json()


def test_open_without_any_commit_is_rejected_with_next_step() -> None:
    client, pid = _client()
    response = client.post(f"/api/v2/projects/{pid}/revision-work")
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "REVISION_WORK_EMPTY_PROJECT"
    assert error["fix"]


def test_get_before_open_reports_not_opened_as_empty_result() -> None:
    """合法空结果不是错误：还没打开就没有工作版本，前端据此提示「还没打开」。"""
    client, pid = _client()
    response = client.get(f"/api/v2/projects/{pid}/revision-work")
    assert response.status_code == 200, response.text
    assert response.json() == {"work_id": None, "opened": False}


def test_open_declares_every_chapter_unread() -> None:
    client, pid = _client()
    _import(client, pid)
    view = _open(client, pid)
    assert view["project_id"] == pid
    assert view["base_commit_id"]
    assert view["total_count"] == 2
    assert view["covered_count"] == 0
    assert view["complete"] is False
    assert [row["status"] for row in view["coverage"]] == ["unread", "unread"]
    # 未覆盖清单逐章给出可读原因——这是「未覆盖可查」的落点。
    assert len(view["uncovered"]) == 2
    assert all(row["reason"] == "尚未读取该章" for row in view["uncovered"])
    # 全局上下文是只读投影：正式基础 + 逐章目录/来源。
    assert view["context"]["base_commit_id"] == view["base_commit_id"]
    assert len(view["context"]["chapters"]) == 2


def test_open_is_idempotent() -> None:
    client, pid = _client()
    _import(client, pid)
    first = _open(client, pid)
    again = _open(client, pid)
    assert again["work_id"] == first["work_id"]
    assert again["base_commit_id"] == first["base_commit_id"]


def test_summary_read_cannot_be_confirmed_unchanged() -> None:
    """只读摘要不能记 checked_unchanged（硬约束，四透镜②）。"""
    client, pid = _client()
    _import(client, pid)
    _open(client, pid)
    chapter = _chapters(client, pid)[0]

    read = client.post(
        f"/api/v2/projects/{pid}/revision-work/reads",
        json={"chapter_id": chapter, "kind": "summary"},
    )
    assert read.status_code == 200, read.text
    assert read.json()["coverage"][0]["status"] == "read"

    confirm = client.post(
        f"/api/v2/projects/{pid}/revision-work/coverage/{chapter}",
        json={"action": "confirm_unchanged"},
    )
    assert confirm.status_code == 400, confirm.text
    error = confirm.json()["error"]
    assert error["code"] == "COVERAGE_NOT_FULLY_READ"
    assert "全文检查" in error["fix"]


def test_full_read_then_confirm_marks_checked_unchanged() -> None:
    client, pid = _client()
    _import(client, pid)
    _open(client, pid)
    chapter = _chapters(client, pid)[0]

    read = client.post(
        f"/api/v2/projects/{pid}/revision-work/reads",
        json={"chapter_id": chapter, "kind": "full"},
    )
    assert read.status_code == 200, read.text
    entry = read.json()["coverage"][0]
    assert entry["read_kind"] == "full"
    assert entry["ranges"] == [{"start": 0, "end": entry["text_length"]}]

    confirm = client.post(
        f"/api/v2/projects/{pid}/revision-work/coverage/{chapter}",
        json={"action": "confirm_unchanged"},
    )
    assert confirm.status_code == 200, confirm.text
    payload = confirm.json()
    assert payload["coverage"][0]["status"] == "checked_unchanged"
    assert payload["covered_count"] == 1
    assert [row["chapter_id"] for row in payload["uncovered"]] == [_chapters(client, pid)[1]]


def test_change_and_blocked_are_recorded() -> None:
    client, pid = _client()
    _import(client, pid)
    _open(client, pid)
    first, second = _chapters(client, pid)

    changed = client.post(
        f"/api/v2/projects/{pid}/revision-work/coverage/{first}",
        json={"action": "changed"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["coverage"][0]["status"] == "changed"

    blocked = client.post(
        f"/api/v2/projects/{pid}/revision-work/coverage/{second}",
        json={"action": "blocked", "reason": "该章有未结束的手工修改副本"},
    )
    assert blocked.status_code == 200, blocked.text
    payload = blocked.json()
    assert payload["coverage"][1]["status"] == "blocked"
    reason = next(r for r in payload["uncovered"] if r["chapter_id"] == second)["reason"]
    assert reason == "该章有未结束的手工修改副本"


def test_blocked_without_reason_is_rejected() -> None:
    client, pid = _client()
    _import(client, pid)
    _open(client, pid)
    chapter = _chapters(client, pid)[0]
    response = client.post(
        f"/api/v2/projects/{pid}/revision-work/coverage/{chapter}",
        json={"action": "blocked", "reason": "  "},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "COVERAGE_INVALID_TRANSITION"


def test_unknown_chapter_and_unknown_action_are_rejected() -> None:
    client, pid = _client()
    _import(client, pid)
    _open(client, pid)

    unknown = client.post(
        f"/api/v2/projects/{pid}/revision-work/reads",
        json={"chapter_id": "ch_missing", "kind": "full"},
    )
    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["error"]["code"] == "COVERAGE_UNKNOWN_CHAPTER"

    chapter = _chapters(client, pid)[0]
    bad_action = client.post(
        f"/api/v2/projects/{pid}/revision-work/coverage/{chapter}",
        json={"action": "approve"},
    )
    assert bad_action.status_code == 400, bad_action.text
    assert bad_action.json()["error"]["code"] == "COVERAGE_INVALID_TRANSITION"


def test_read_without_open_reports_not_found_with_next_step() -> None:
    client, pid = _client()
    _import(client, pid)
    chapter = _chapters(client, pid)[0]
    response = client.post(
        f"/api/v2/projects/{pid}/revision-work/reads",
        json={"chapter_id": chapter, "kind": "full"},
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "REVISION_WORK_NOT_FOUND"
