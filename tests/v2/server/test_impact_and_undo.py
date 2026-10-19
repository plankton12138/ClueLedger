"""影响高亮与整体操作关联撤销（V03-7.7）。

验收口径（计划 M10 §10 / M11、阶段指南 §11.5）：高亮只来自实际版本差异；
撤销整体操作时正文与关联账本一起恢复；后来独立修改必须保留或逐处选择。
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


def _client() -> tuple[TestClient, V2Runtime, str]:
    runtime = V2Runtime.memory()
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "影响与撤销"}).json()["project_id"]
    return client, runtime, pid


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


def _chapters(client: TestClient, pid: str) -> list[dict]:
    return client.get(f"/api/v2/projects/{pid}/manuscript").json()["chapters"]


def _create(client: TestClient, pid: str, kind: str, fields: dict, request_id: str) -> dict:
    response = client.post(
        f"/api/v2/projects/{pid}/ledger/entries",
        json={"kind": kind, "fields": fields, "request_id": request_id},
    )
    assert response.status_code == 201, response.text
    return response.json()["entry"]


def _publish_revision(client: TestClient, pid: str) -> tuple[str, str, list[str]]:
    """改两章正文 + 修订一个人物，走整体调整统一发布；返回 (op_id, 人物ID, 章ID)。"""
    sister = _create(client, pid, "story.character", {"name": "姐姐"}, "req-sis")
    task = client.post(
        f"/api/v2/projects/{pid}/revision-tasks", json={"request_id": "req-task"}
    ).json()
    task_id = task["task_id"]
    chapters = _chapters(client, pid)
    for index, row in enumerate(chapters):
        staged = client.post(
            f"/api/v2/projects/{pid}/revision-tasks/{task_id}/staged-chapters",
            json={
                "chapter_id": row["chapter_id"],
                "text": f"改写后的第 {index + 1} 章正文。",
                "base_revision_id": row["chapter_base"],
            },
        )
        assert staged.status_code == 200, staged.text
    revised = client.post(
        f"/api/v2/projects/{pid}/revision-tasks/{task_id}/staged-ledger",
        json={
            "entry_id": sister["id"],
            "fields": {"name": "姐姐（改）"},
            "expected_revision": sister["revision_id"],
        },
    )
    assert revised.status_code == 200, revised.text
    published = client.post(
        f"/api/v2/projects/{pid}/revision-tasks/{task_id}/publish",
        json={"operation_id": "op_revision"},
    )
    assert published.status_code == 200, published.text
    return "op_revision", sister["id"], [row["chapter_id"] for row in chapters]


def test_impact_reports_only_actual_version_diff() -> None:
    client, _runtime, pid = _client()
    _import(client, pid)
    operation_id, sister_id, chapter_ids = _publish_revision(client, pid)

    impact = client.get(
        f"/api/v2/projects/{pid}/operations/{operation_id}/impact"
    ).json()
    assert impact["basis"] == "version_diff"
    assert "不代表修订完成" in impact["note"]
    assert {row["chapter_id"] for row in impact["chapters"]} == set(chapter_ids)
    assert all(row["hunks"] >= 1 for row in impact["chapters"])
    assert all(row["has_later_changes"] is False for row in impact["chapters"])

    ledger = next(row for row in impact["ledger"] if row["artifact_id"] == sister_id)
    assert ledger["changed_fields"] == ["name"]
    assert ledger["before_revision"] != ledger["after_revision"]
    assert ledger["has_later_changes"] is False

    assert {item["kind"] for item in impact["highlights"]} == {"chapter", "ledger"}
    assert all(item["basis"] == "version_diff" for item in impact["highlights"])
    assert set(impact["continuation"]["chapter_ids"]) == set(chapter_ids)
    assert impact["continuation"]["has_later_changes"] is False
    assert impact["related_acceptances"] == []


def test_impact_marks_later_changes_without_overwriting() -> None:
    client, _runtime, pid = _client()
    _import(client, pid)
    operation_id, _sister_id, chapter_ids = _publish_revision(client, pid)

    chapter = client.get(f"/api/v2/projects/{pid}/chapters/{chapter_ids[0]}").json()
    saved = client.post(
        f"/api/v2/projects/{pid}/chapters/{chapter_ids[0]}/save",
        json={
            "idempotency_key": "later-save",
            "full_text": "作者后来独立改写的正文。",
            "expected_chapter_base": chapter["chapter_base"],
        },
    )
    assert saved.status_code == 200, saved.text

    impact = client.get(
        f"/api/v2/projects/{pid}/operations/{operation_id}/impact"
    ).json()
    first = next(row for row in impact["chapters"] if row["chapter_id"] == chapter_ids[0])
    assert first["has_later_changes"] is True
    assert first["chars_current"] == len("作者后来独立改写的正文。")
    assert impact["continuation"]["has_later_changes"] is True


def test_undo_restores_chapters_and_related_ledger_together() -> None:
    client, _runtime, pid = _client()
    _import(client, pid)
    before_text = _chapters(client, pid)[0]
    original_text = client.get(
        f"/api/v2/projects/{pid}/chapters/{before_text['chapter_id']}"
    ).json()["text"]
    operation_id, sister_id, _chapter_ids = _publish_revision(client, pid)

    preview = client.get(
        f"/api/v2/projects/{pid}/operations/{operation_id}/undo"
    ).json()
    assert preview["can_undo"] is True, preview
    ledger = next(row for row in preview["ledger"] if row["artifact_id"] == sister_id)
    assert ledger["status"] == "ok"
    assert ledger["before_content"]["name"] == "姐姐"

    applied = client.post(
        f"/api/v2/projects/{pid}/operations/{operation_id}/undo",
        json={"preview_token": preview["preview_token"]},
    )
    assert applied.status_code == 200, applied.text
    result = applied.json()
    assert sister_id in result["restored_ledger_ids"]

    restored_text = client.get(
        f"/api/v2/projects/{pid}/chapters/{before_text['chapter_id']}"
    ).json()["text"]
    assert restored_text == original_text
    entry = client.get(
        f"/api/v2/projects/{pid}/ledger/entries/{sister_id}"
    ).json()
    assert entry["content"]["name"] == "姐姐"


def test_undo_protects_later_independent_ledger_edit() -> None:
    client, _runtime, pid = _client()
    _import(client, pid)
    operation_id, sister_id, _chapter_ids = _publish_revision(client, pid)

    current = client.get(f"/api/v2/projects/{pid}/ledger/entries/{sister_id}").json()
    patched = client.patch(
        f"/api/v2/projects/{pid}/ledger/entries/{sister_id}",
        json={
            "fields": {"name": "姐姐（后来改）"},
            "expected_revision": current["revision_id"],
            "request_id": "later-edit",
        },
    )
    assert patched.status_code == 200, patched.text

    preview = client.get(
        f"/api/v2/projects/{pid}/operations/{operation_id}/undo"
    ).json()
    ledger = next(row for row in preview["ledger"] if row["artifact_id"] == sister_id)
    assert ledger["status"] == "conflict"
    decision = next(item for item in preview["decisions"] if item["target_id"] == sister_id)
    assert decision["choices"] == ["restore", "keep"]

    applied = client.post(
        f"/api/v2/projects/{pid}/operations/{operation_id}/undo",
        json={
            "preview_token": preview["preview_token"],
            "choices": {decision["id"]: "keep"},
        },
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["kept"][0]["target_id"] == sister_id

    entry = client.get(
        f"/api/v2/projects/{pid}/ledger/entries/{sister_id}"
    ).json()
    assert entry["content"]["name"] == "姐姐（后来改）"  # 后来的独立修改保留


def test_related_acceptance_is_reported_and_creation_is_not_deleted() -> None:
    client, _runtime, pid = _client()
    _import(client, pid)
    operation_id, _sister_id, _chapter_ids = _publish_revision(client, pid)

    staged = client.post(
        f"/api/v2/projects/{pid}/ledger/candidates/items",
        json={"kind": "story.place", "fields": {"name": "阁楼"}, "request_id": "stage-1"},
    ).json()
    group = staged
    item_id = group["items"][0]["item_id"]
    accepted = client.post(
        f"/api/v2/projects/{pid}/ledger/candidates/accept",
        json={
            "item_ids": [item_id],
            "expected_revision": group["revision"],
            "request_id": "accept-1",
            "source_operation_id": operation_id,
        },
    )
    assert accepted.status_code == 200, accepted.text
    accept_commit = accepted.json()["commit_id"]

    impact = client.get(
        f"/api/v2/projects/{pid}/operations/{operation_id}/impact"
    ).json()
    assert len(impact["related_acceptances"]) == 1
    related = impact["related_acceptances"][0]
    assert related["commit_id"] == accept_commit
    assert related["request_id"] == "accept-1"
    assert related["item_ids"] == [item_id]

    preview = client.get(
        f"/api/v2/projects/{pid}/operations/{operation_id}/undo"
    ).json()
    report = preview["related_acceptances"][0]
    assert report["commit_id"] == accept_commit
    assert report["skipped_created"] == 1  # 新建条目不能在撤销里删除
    assert all(row["artifact_id"] != item_id for row in preview["ledger"])

    applied = client.post(
        f"/api/v2/projects/{pid}/operations/{operation_id}/undo",
        json={"preview_token": preview["preview_token"]},
    )
    assert applied.status_code == 200, applied.text
    # 关联采用的新条目仍在正式账本里（本版不删除新建条目）。
    ledger = client.get(f"/api/v2/projects/{pid}/ledger").json()["entries"]
    assert any(entry["title"] == "阁楼" for entry in ledger)


def test_unknown_source_operation_is_rejected() -> None:
    client, _runtime, pid = _client()
    _import(client, pid)
    staged = client.post(
        f"/api/v2/projects/{pid}/ledger/candidates/items",
        json={"kind": "story.place", "fields": {"name": "阁楼"}, "request_id": "stage-1"},
    ).json()
    group = staged
    item_id = group["items"][0]["item_id"]
    response = client.post(
        f"/api/v2/projects/{pid}/ledger/candidates/accept",
        json={
            "item_ids": [item_id],
            "expected_revision": group["revision"],
            "request_id": "accept-1",
            "source_operation_id": "op_missing",
        },
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "REVISION_OPERATION_NOT_FOUND"
