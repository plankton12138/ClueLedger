"""1.0.9 P6：工作台投影、建章即建正文与正文保存接口。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.state import RecentWorkspaces


class _NoModel:
    def stream_response(self, **_: Any) -> Any:
        raise AssertionError("写作工作台接口不应调用模型")


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    book = RecentWorkspaces(tmp_path / "state")
    app = create_workspace_app(
        workspaces=book,
        provider_factory=lambda: ModelSnapshot(
            connection_id="test", display_name="测试连接", model="fake", provider=_NoModel()
        ),
    )
    with TestClient(app) as opened:
        yield opened


def open_workspace(client: TestClient, tmp_path: Path) -> tuple[str, Path]:
    root = tmp_path / "book"
    response = client.post(
        "/api/file-workspaces", json={"path": str(root), "create": not root.exists()}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return body["id"], Path(body["path"])


def test_chapter_cannot_be_created_before_book_intent(
    client: TestClient, tmp_path: Path
) -> None:
    workspace_id, _ = open_workspace(client, tmp_path)
    response = client.post(
        f"/api/file-workspaces/{workspace_id}/story/chapters",
        json={
            "title": None,
            "after_chapter_id": None,
            "expected_plan_revision": "empty",
            "request_key": "chapter:before-intent",
        },
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "BOOK_INTENT_REQUIRED"


def test_writing_flow_through_http(client: TestClient, tmp_path: Path) -> None:
    workspace_id, root = open_workspace(client, tmp_path)
    base = f"/api/file-workspaces/{workspace_id}/story"

    book = client.get(f"{base}/workbench")
    assert book.status_code == 200, book.text
    assert book.json()["plan_revision"] is None
    assert book.json()["book"]["chapters"] == []

    intent = client.put(
        f"{base}/book-intent",
        json={
            "text": "暴雪封山的旅馆发生命案，林砚从湿手套开始怀疑管家。",
            "expected_plan_revision": "empty",
            "request_key": "book:1",
        },
    )
    assert intent.status_code == 200, intent.text
    plan_revision = intent.json()["plan_revision"]

    created = client.post(
        f"{base}/chapters",
        json={
            "title": "第一章",
            "after_chapter_id": None,
            "expected_plan_revision": plan_revision,
            "request_key": "chapter:1",
        },
    )
    assert created.status_code == 201, created.text
    created_body = created.json()
    chapter_id = created_body["chapter_id"]
    assert (root / created_body["path"]).exists()

    chapter = client.get(f"{base}/workbench", params={"chapter_id": chapter_id}).json()
    assert chapter["chapter"]["plan"]["moves"] == []
    assert "can_start" not in chapter["chapter"]
    assert chapter["chapter"]["prose"]["editable"] is True
    assert chapter["chapter"]["prose"]["blocked_reason"] is None
    assert chapter["chapter"]["index"]["items"] == []
    assert chapter["chapter"]["index"]["analysis"]["state"] == "unavailable"

    saved = client.put(
        f"{base}/chapters/{chapter_id}/prose",
        json={
            "text": "# 第一章\n\n林砚走进旅馆，柜台上有一双仍在滴水的手套。\n",
            "expected_digest": created_body["prose_digest"],
            "draft_revision": "local_1",
            "candidate_id": None,
            "request_key": "save:1",
        },
    )
    assert saved.status_code == 200, saved.text
    saved_body = saved.json()
    assert saved_body["index_revision"].startswith("idx_")
    assert "chapter_revision" not in saved_body
    assert (root / saved_body["path"]).exists()

    conflict = client.put(
        f"{base}/chapters/{chapter_id}/prose",
        json={
            "text": "# 第一章\n\n另一版正文。\n",
            "expected_digest": created_body["prose_digest"],
            "draft_revision": "local_2",
            "candidate_id": None,
            "request_key": "save:2",
        },
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "PROSE_DIGEST_MISMATCH"

    started = client.post(
        f"{base}/chapters/{chapter_id}/start",
        json={"request_key": "start:gone"},
    )
    assert started.status_code in (404, 405), started.text
    plan_route = client.put(
        f"{base}/chapters/{chapter_id}/plan",
        json={"request_key": "plan:gone"},
    )
    assert plan_route.status_code in (404, 405), plan_route.text


def test_unplanned_prose_is_readonly_in_workbench(client: TestClient, tmp_path: Path) -> None:
    workspace_id, root = open_workspace(client, tmp_path)
    (root / "正文").mkdir(exist_ok=True)
    (root / "正文" / "0001 手稿.md").write_text("# 手稿\n\n原有正文。\n", encoding="utf-8")

    book = client.get(f"/api/file-workspaces/{workspace_id}/story/workbench").json()
    assert [row["path"] for row in book["book"]["unplanned_prose"]] == ["正文/0001 手稿.md"]

    chapter = client.get(
        f"/api/file-workspaces/{workspace_id}/story/workbench",
        params={"prose_path": "正文/0001 手稿.md"},
    )
    assert chapter.status_code == 200, chapter.text
    body = chapter.json()
    assert body["chapter"]["plan"] is None
    assert body["chapter"]["prose"]["editable"] is False
    assert body["chapter"]["prose"]["blocked_reason"] == "book_intent_required"
    assert body["chapter"]["index"] is None
