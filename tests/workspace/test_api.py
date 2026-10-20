"""文件工作区接口（0.4 V04-1）。

契约：浏览器经真实 HTTP 读目录、读原文、搜索、导入；接口**不提供**写正文的端点，
修改只来自对话里的 Agent 或应用之外的编辑器。错误统一为
``{"error": {code, where, why, fix, details}}``，状态码按错误码尾缀映射。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.workspace.state import RecentWorkspaces


def _unconfigured_model() -> tuple[object, str]:
    raise AssertionError("这些用例只走文件接口，不应该调用模型")


def _client(tmp_path: Path) -> TestClient:
    book = RecentWorkspaces(tmp_path / "state")
    return TestClient(
        create_workspace_app(book=book, provider_factory=_unconfigured_model)  # type: ignore[arg-type]
    )


def _created(client: TestClient, root: Path) -> str:
    response = client.post("/api/file-workspaces", json={"path": str(root), "create": True})
    assert response.status_code == 200
    workspace_id: str = response.json()["id"]
    return workspace_id


def test_create_workspace_makes_two_blocks(tmp_path: Path) -> None:
    client = _client(tmp_path)
    root = tmp_path / "雪山旅馆"

    response = client.post("/api/file-workspaces", json={"path": str(root), "create": True})

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "雪山旅馆"
    assert body["exists"] is True
    assert body["path"] == str(root.resolve())
    assert (root / "正文").is_dir()
    assert (root / "设定集/真相.md").is_file()
    # 新作品自带版本记录：.git 与首个基线提交。
    assert (root / ".git").is_dir()
    assert client.get("/api/file-workspaces").json() == [body]


def test_open_existing_directory_does_not_create_content(tmp_path: Path) -> None:
    client = _client(tmp_path)
    plain = tmp_path / "随手一个目录"
    plain.mkdir()

    response = client.post("/api/file-workspaces", json={"path": str(plain)})

    assert response.status_code == 200
    # 打开不初始化 Git、不创建任何文件。
    assert list(plain.iterdir()) == []


def test_open_missing_directory_returns_not_found(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post("/api/file-workspaces", json={"path": str(tmp_path / "不存在")})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "WORKSPACE_NOT_FOUND"
    assert set(response.json()["error"]) == {"code", "where", "why", "fix", "details"}


def test_files_lists_chapters_and_three_settings(tmp_path: Path) -> None:
    client = _client(tmp_path)
    root = tmp_path / "作品"
    workspace_id = _created(client, root)

    empty = client.get(f"/api/file-workspaces/{workspace_id}/files").json()
    assert empty["chapters"] == []
    assert [item["path"] for item in empty["settings"]] == [
        "设定集/角色小传.md",
        "设定集/线索.md",
        "设定集/真相.md",
    ]
    assert all(item["exists"] for item in empty["settings"])

    client.post(
        f"/api/file-workspaces/{workspace_id}/imports",
        files={"file": ("雨夜.md", "# 雨夜\n\n侦探抵达旅馆。\n".encode(), "text/markdown")},
    )
    listed = client.get(f"/api/file-workspaces/{workspace_id}/files").json()
    assert [(item["path"], item["title"], item["order"]) for item in listed["chapters"]] == [
        ("正文/0001 雨夜.md", "雨夜", "0001")
    ]


def test_read_file_returns_text_digest_and_kind(tmp_path: Path) -> None:
    client = _client(tmp_path)
    root = tmp_path / "作品"
    workspace_id = _created(client, root)
    client.post(
        f"/api/file-workspaces/{workspace_id}/imports",
        files={"file": ("雨夜.md", "# 雨夜\n\n侦探抵达旅馆。\n".encode(), "text/markdown")},
    )

    chapter = client.get(
        f"/api/file-workspaces/{workspace_id}/file", params={"path": "正文/0001 雨夜.md"}
    )
    assert chapter.status_code == 200
    assert chapter.json()["kind"] == "chapter"
    assert chapter.json()["title"] == "雨夜"
    assert chapter.json()["text"].startswith("# 雨夜")
    assert len(chapter.json()["digest"]) == 64

    setting = client.get(
        f"/api/file-workspaces/{workspace_id}/file", params={"path": "设定集/真相.md"}
    )
    assert setting.status_code == 200
    assert setting.json()["kind"] == "setting"
    assert setting.json()["title"] == "真相"
    assert setting.json()["text"] == ""


@pytest.mark.parametrize("path", ["../x.md", "/etc/passwd", "正文\\0001.md", "设定集/时间线.md"])
def test_read_rejects_paths_outside_managed_set(path: str, tmp_path: Path) -> None:
    client = _client(tmp_path)
    workspace_id = _created(client, tmp_path / "作品")

    response = client.get(f"/api/file-workspaces/{workspace_id}/file", params={"path": path})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "PATH_NOT_MANAGED"


def test_read_missing_file_is_not_found(tmp_path: Path) -> None:
    client = _client(tmp_path)
    workspace_id = _created(client, tmp_path / "作品")

    response = client.get(
        f"/api/file-workspaces/{workspace_id}/file", params={"path": "正文/0009 无.md"}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "FILE_NOT_FOUND"


def test_search_returns_hits_and_requires_query(tmp_path: Path) -> None:
    client = _client(tmp_path)
    workspace_id = _created(client, tmp_path / "作品")
    client.post(
        f"/api/file-workspaces/{workspace_id}/imports",
        files={"file": ("雨夜.md", "# 雨夜\n\n侦探抵达旅馆。\n".encode(), "text/markdown")},
    )

    found = client.get(f"/api/file-workspaces/{workspace_id}/search", params={"q": "旅馆"})
    assert found.status_code == 200
    assert found.json()["hits"] == [
        {"path": "正文/0001 雨夜.md", "line": 3, "text": "侦探抵达旅馆。"}
    ]
    assert found.json()["unreadable"] == []
    assert found.json()["truncated"] is False

    blank = client.get(f"/api/file-workspaces/{workspace_id}/search", params={"q": ""})
    assert blank.status_code == 422


def test_import_rejects_unsupported_file(tmp_path: Path) -> None:
    client = _client(tmp_path)
    workspace_id = _created(client, tmp_path / "作品")

    response = client.post(
        f"/api/file-workspaces/{workspace_id}/imports",
        files={"file": ("稿件.docx", b"whatever", "application/octet-stream")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IMPORT_FORMAT_INVALID"


def test_unknown_workspace_returns_not_found(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/api/file-workspaces/not-a-binding/files")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "WORKSPACE_NOT_FOUND"
