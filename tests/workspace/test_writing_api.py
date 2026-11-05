"""1.2 P1：格式 5 工作台、建章即建正文与只读正文投影。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.chapter_clues import ClueEntry, anchor_paragraph, write_chapter_clues
from hastings.workspace.state import RecentWorkspaces

from .chapter_seed import seed_chapter


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


def test_author_chapter_create_route_is_removed(client: TestClient, tmp_path: Path) -> None:
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

    assert response.status_code in (404, 405), response.text


def test_writing_flow_through_http(client: TestClient, tmp_path: Path) -> None:
    workspace_id, root = open_workspace(client, tmp_path)
    base = f"/api/file-workspaces/{workspace_id}/story"

    book = client.get(f"{base}/workbench")
    assert book.status_code == 200, book.text
    assert book.json()["plan_revision"] is None
    assert book.json()["book"]["chapters"] == []

    created_body = seed_chapter(
        tmp_path / "state", workspace_id, root, title="第一章", request_key="chapter:1"
    )
    chapter_id = created_body["chapter_id"]
    assert (root / created_body["path"]).exists()

    chapter = client.get(f"{base}/workbench", params={"chapter_id": chapter_id}).json()
    assert chapter["chapter"]["plan"]["moves"] == []
    assert "can_start" not in chapter["chapter"]
    assert chapter["chapter"]["prose"]["editable"] is False
    assert "blocked_reason" not in chapter["chapter"]["prose"]
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


def test_rename_chapter_through_http(client: TestClient, tmp_path: Path) -> None:
    workspace_id, root = open_workspace(client, tmp_path)
    base = f"/api/file-workspaces/{workspace_id}/story"
    created_body = seed_chapter(
        tmp_path / "state",
        workspace_id,
        root,
        title="第一章",
        request_key="chapter:rename-seed",
    )

    renamed = client.put(
        f"{base}/chapters/{created_body['chapter_id']}",
        json={
            "title": "雨夜",
            "expected_plan_revision": created_body["plan_revision"],
            "request_key": "chapter:rename",
        },
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["title"] == "雨夜"

    chapter = client.get(
        f"{base}/workbench", params={"chapter_id": created_body["chapter_id"]}
    ).json()
    assert chapter["chapter"]["plan"]["title"] == "雨夜"
    assert chapter["chapter"]["prose"]["text"].startswith("# 雨夜")
    assert (root / created_body["path"]).read_text(encoding="utf-8").startswith("# 雨夜")


def test_rename_unplanned_prose_through_http(client: TestClient, tmp_path: Path) -> None:
    workspace_id, root = open_workspace(client, tmp_path)
    (root / "正文").mkdir(exist_ok=True)
    (root / "正文" / "0001 旧稿.md").write_text("# 旧稿\n\n原有正文。\n", encoding="utf-8")
    base = f"/api/file-workspaces/{workspace_id}/story"
    entry = client.get(f"{base}/workbench").json()["book"]["unplanned_prose"][0]
    assert entry["name"] == "旧稿"

    renamed = client.put(
        f"{base}/prose-title",
        json={"path": entry["path"], "title": "新稿", "expected_digest": entry["digest"]},
    )
    assert renamed.status_code == 200, renamed.text

    book = client.get(f"{base}/workbench").json()["book"]
    updated = book["unplanned_prose"][0]
    assert updated["name"] == "新稿"
    assert updated["title"].endswith("新稿")
    assert (root / entry["path"]).read_text(encoding="utf-8") == "# 新稿\n\n原有正文。\n"


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
    assert "blocked_reason" not in body["chapter"]["prose"]
    assert body["chapter"]["index"] is None


def test_author_adopt_route_is_removed(client: TestClient, tmp_path: Path) -> None:
    workspace_id, root = open_workspace(client, tmp_path)
    (root / "正文").mkdir(exist_ok=True)
    (root / "正文" / "0001 手稿.md").write_text("# 手稿\n\n原有正文。\n", encoding="utf-8")
    base = f"/api/file-workspaces/{workspace_id}/story"

    response = client.post(
        f"{base}/chapters/adopt",
        json={
            "path": "正文/0001 手稿.md",
            "expected_plan_revision": "empty",
            "request_key": "adopt:removed",
        },
    )

    assert response.status_code in (404, 405), response.text
    book = client.get(f"{base}/workbench").json()["book"]
    assert book["chapters"] == []
    assert [row["path"] for row in book["unplanned_prose"]] == ["正文/0001 手稿.md"]


def test_workbench_exposes_clue_registry(client: TestClient, tmp_path: Path) -> None:
    """v1.3 P4：全书与本章的线索面板数据随工作台一次返回。"""
    workspace_id, root = open_workspace(client, tmp_path)
    base = f"/api/file-workspaces/{workspace_id}/story"
    created = seed_chapter(
        tmp_path / "state", workspace_id, root, title="第一章", request_key="chapter:1"
    )
    prose_text = (root / created["path"]).read_text(encoding="utf-8")
    write_chapter_clues(
        root,
        1,
        "r1",
        [
            ClueEntry(
                id="CL-01",
                kind="伏笔",
                status="planted",
                origin="planned",
                note="箱扣缺漆",
                anchor=anchor_paragraph(prose_text.splitlines()[0], "r1"),
            )
        ],
    )

    book = client.get(f"{base}/workbench").json()["book"]
    assert [entry["id"] for entry in book["clues"]["entries"]] == ["CL-01"]
    assert book["clues"]["entries"][0]["status"] == "planted"
    assert book["clues"]["entries"][0]["planted_in"] == 1

    chapter = client.get(f"{base}/workbench", params={"chapter_id": created["chapter_id"]}).json()[
        "chapter"
    ]
    assert [entry["id"] for entry in chapter["clues"]["entries"]] == ["CL-01"]
    assert chapter["clues"]["findings"] == []


def test_workbench_clue_panel_is_empty_without_registry(client: TestClient, tmp_path: Path) -> None:
    workspace_id, _ = open_workspace(client, tmp_path)
    book = client.get(f"/api/file-workspaces/{workspace_id}/story/workbench").json()["book"]
    assert book["clues"] == {"entries": [], "plans": [], "findings": []}
