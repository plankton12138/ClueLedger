"""灵感本接口（0.8 C）。

契约：作者经作品前缀读写灵感；冲突返回 409，格式问题返回 400，未登记作品返回 404；
接口不触发模型，也不改任何正文文件。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.workspace.files import Workspace
from hastings.workspace.state import RecentWorkspaces


def _unconfigured_model() -> object:
    raise AssertionError("这些用例只走灵感接口，不应该调用模型")


def _client(tmp_path: Path) -> tuple[TestClient, str]:
    book = RecentWorkspaces(tmp_path / "state")
    client = TestClient(
        create_workspace_app(workspaces=book, provider_factory=_unconfigured_model)  # type: ignore[arg-type]
    )
    root = tmp_path / "雪山旅馆"
    response = client.post("/api/file-workspaces", json={"path": str(root), "create": True})
    assert response.status_code == 200, response.text
    return client, str(response.json()["id"])


def _create(client: TestClient, workspace_id: str, *, key: str = "idea-http-1") -> dict:
    response = client.post(
        f"/api/file-workspaces/{workspace_id}/ideas",
        json={
            "title": "老板娘",
            "body": "老板娘认识死者。",
            "tags": ["人物"],
            "request_key": key,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_create_edit_and_history(tmp_path: Path) -> None:
    client, workspace_id = _client(tmp_path)
    created = _create(client, workspace_id)

    listing = client.get(f"/api/file-workspaces/{workspace_id}/ideas")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["title"] == "老板娘"

    edited = client.patch(
        f"/api/file-workspaces/{workspace_id}/ideas/{created['idea_id']}",
        json={
            "expected_digest": created["digest"],
            "expected_revision": created["revision_id"],
            "body": "老板娘一直在保护死者的女儿。",
            "request_key": "idea-http-2",
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["body"] == "老板娘一直在保护死者的女儿。"

    revisions = client.get(
        f"/api/file-workspaces/{workspace_id}/ideas/{created['idea_id']}/revisions"
    )
    assert [item["operation"] for item in revisions.json()["items"]] == ["edit", "create"]

    first = client.get(
        f"/api/file-workspaces/{workspace_id}/ideas/{created['idea_id']}"
        f"/revisions/{created['revision_id']}"
    )
    assert first.json()["body"] == "老板娘认识死者。"


def test_stale_edit_maps_to_409(tmp_path: Path) -> None:
    client, workspace_id = _client(tmp_path)
    created = _create(client, workspace_id)

    response = client.patch(
        f"/api/file-workspaces/{workspace_id}/ideas/{created['idea_id']}",
        json={
            "expected_digest": "过期指纹",
            "expected_revision": created["revision_id"],
            "body": "旧页面修改。",
            "request_key": "idea-http-2",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEA_STALE_CONFLICT"


def test_trash_and_restore_round_trip(tmp_path: Path) -> None:
    client, workspace_id = _client(tmp_path)
    created = _create(client, workspace_id)
    base = f"/api/file-workspaces/{workspace_id}/ideas"

    trashed = client.post(
        f"{base}/{created['idea_id']}/trash",
        json={
            "expected_digest": created["digest"],
            "expected_revision": created["revision_id"],
            "request_key": "idea-http-trash",
        },
    )
    assert trashed.status_code == 200
    assert trashed.json()["deleted"] is True
    assert client.get(base).json()["total"] == 0
    assert client.get(base, params={"deleted": True}).json()["total"] == 1

    restored = client.post(
        f"{base}/{created['idea_id']}/restore",
        json={
            "target_revision": created["revision_id"],
            "expected_digest": trashed.json()["digest"],
            "request_key": "idea-http-restore",
        },
    )
    assert restored.status_code == 200
    assert restored.json()["deleted"] is False
    assert restored.json()["body"] == "老板娘认识死者。"


def test_missing_idea_maps_to_404(tmp_path: Path) -> None:
    client, workspace_id = _client(tmp_path)
    response = client.get(f"/api/file-workspaces/{workspace_id}/ideas/idea_0000000000000000")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "IDEA_NOT_FOUND"


def test_too_long_body_is_rejected_without_writing(tmp_path: Path) -> None:
    client, workspace_id = _client(tmp_path)
    response = client.post(
        f"/api/file-workspaces/{workspace_id}/ideas",
        json={"body": "字" * 20_001, "request_key": "idea-http-long"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IDEA_CONTENT_TOO_LARGE"
    assert client.get(f"/api/file-workspaces/{workspace_id}/ideas").json()["total"] == 0


def test_ideas_do_not_touch_chapter_files(tmp_path: Path) -> None:
    client, workspace_id = _client(tmp_path)
    root = tmp_path / "雪山旅馆"
    chapter = root / "正文/0001 雨夜.md"
    Workspace.open(root).write("正文/0001 雨夜.md", "第一章\n\n雪。", expected_digest=None)
    before = chapter.read_bytes()

    created = _create(client, workspace_id)
    client.patch(
        f"/api/file-workspaces/{workspace_id}/ideas/{created['idea_id']}",
        json={
            "expected_digest": created["digest"],
            "expected_revision": created["revision_id"],
            "body": "只改灵感。",
            "request_key": "idea-http-2",
        },
    )

    assert chapter.read_bytes() == before


def test_unknown_workspace_maps_to_404(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    response = client.get("/api/file-workspaces/does-not-exist/ideas")
    assert response.status_code == 404


def test_list_exposes_created_order_and_length_fields(tmp_path: Path) -> None:
    client, workspace_id = _client(tmp_path)
    first = _create(client, workspace_id, key="idea-http-order-1")
    second = _create(client, workspace_id, key="idea-http-order-2")

    page = client.get(f"/api/file-workspaces/{workspace_id}/ideas?order=created&limit=1")
    assert page.status_code == 200, page.text
    body = page.json()
    item = body["items"][0]
    assert set(item) >= {"created_at", "body_chars", "excerpt_truncated"}
    assert item["body_chars"] == len("老板娘认识死者。")
    assert item["excerpt_truncated"] is False
    assert body["next_cursor"]

    # 排序方式进入游标身份：换排序不能复用旧游标。
    mismatched = client.get(
        f"/api/file-workspaces/{workspace_id}/ideas?order=updated&limit=1"
        f"&cursor={body['next_cursor']}"
    )
    assert mismatched.status_code == 409
    assert mismatched.json()["error"]["code"] == "QUERY_CURSOR_STALE_CONFLICT"

    # 修改较早的一条后，默认（updated）排序把它排到最前，created 排序不变。
    created_order = [
        row["idea_id"]
        for row in client.get(f"/api/file-workspaces/{workspace_id}/ideas?order=created").json()[
            "items"
        ]
    ]
    edited = client.patch(
        f"/api/file-workspaces/{workspace_id}/ideas/{first['idea_id']}",
        json={
            "expected_digest": first["digest"],
            "expected_revision": first["revision_id"],
            "body": "老板娘一直在保护死者的女儿。",
            "request_key": "idea-http-order-3",
        },
    )
    assert edited.status_code == 200, edited.text
    assert [
        row["idea_id"]
        for row in client.get(f"/api/file-workspaces/{workspace_id}/ideas?order=created").json()[
            "items"
        ]
    ] == created_order
    assert second["idea_id"] in created_order


def test_update_keeps_legacy_title_and_tags_when_only_body_is_sent(tmp_path: Path) -> None:
    client, workspace_id = _client(tmp_path)
    created = _create(client, workspace_id, key="idea-http-legacy-1")
    edited = client.patch(
        f"/api/file-workspaces/{workspace_id}/ideas/{created['idea_id']}",
        json={
            "expected_digest": created["digest"],
            "expected_revision": created["revision_id"],
            "body": "只改正文。",
            "request_key": "idea-http-legacy-2",
        },
    )
    assert edited.status_code == 200, edited.text
    view = edited.json()
    assert view["body"] == "只改正文。"
    assert view["title"] == "老板娘"
    assert view["tags"] == ["人物"]


def test_origin_keeps_the_selection_locator(tmp_path: Path) -> None:
    """对话选段记成灵感时，来源定位要按提交的原样保存。"""
    client, workspace_id = _client(tmp_path)
    response = client.post(
        f"/api/file-workspaces/{workspace_id}/ideas",
        json={
            "body": "选段原文",
            "request_key": "idea-origin-1",
            "origin": {
                "role": "assistant",
                "conversation_id": "c1",
                "message_seq": 2,
                "quote": "选段原文",
                "turn_id": "t1",
            },
        },
    )
    assert response.status_code == 200, response.text
    origin = response.json()["origin"]
    assert origin["role"] == "assistant"
    assert origin["message_seq"] == 2
    assert origin["turn_id"] == "t1"
    assert origin["quote"] == "选段原文"
