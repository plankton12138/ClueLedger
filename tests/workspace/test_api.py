"""文件工作区接口（0.4 V04-1）。

契约：浏览器经真实 HTTP 读目录、读原文、搜索、导入；接口**不提供**写正文的端点，
修改只来自对话里的 Agent 或应用之外的编辑器。错误统一为
``{"error": {code, where, why, fix, details}}``，状态码按错误码尾缀映射。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hastings.server.file_app import create_workspace_app
from hastings.workspace.directory_dialog import DirectoryPickerError
from hastings.workspace.files import Workspace
from hastings.workspace.state import ConversationStore, RecentWorkspaces, conversation_root


def _unconfigured_model() -> tuple[object, str]:
    raise AssertionError("这些用例只走文件接口，不应该调用模型")


def _client(tmp_path: Path) -> TestClient:
    book = RecentWorkspaces(tmp_path / "state")
    return TestClient(
        create_workspace_app(workspaces=book, provider_factory=_unconfigured_model)  # type: ignore[arg-type]
    )


def _created(client: TestClient, root: Path) -> str:
    response = client.post("/api/file-workspaces", json={"path": str(root), "create": True})
    assert response.status_code == 200
    workspace_id: str = response.json()["id"]
    return workspace_id


def test_create_workspace_makes_only_body_directory(tmp_path: Path) -> None:
    client = _client(tmp_path)
    root = tmp_path / "雪山旅馆"

    response = client.post("/api/file-workspaces", json={"path": str(root), "create": True})

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "雪山旅馆"
    assert body["exists"] is True
    assert body["path"] == str(root.resolve())
    assert (root / "正文").is_dir()
    assert not (root / "设定集").exists()
    # 新作品不预建线索与真相文件；作者按需创建或改用模块库。
    assert not (root / "设定集/真相.md").exists()
    assert not (root / "设定集/线索.md").exists()
    # 新作品自带版本记录：.git 与首个基线提交。
    assert (root / ".git").is_dir()
    assert client.get("/api/file-workspaces").json() == [body]


def test_prune_stale_workspaces_removes_missing_binding(tmp_path: Path) -> None:
    client = _client(tmp_path)
    kept_root = tmp_path / "还在"
    kept_root.mkdir()
    gone_root = tmp_path / "已删除"
    (gone_root / "正文").mkdir(parents=True)
    kept = client.post("/api/file-workspaces", json={"path": str(kept_root)}).json()
    gone = client.post("/api/file-workspaces", json={"path": str(gone_root)}).json()
    shutil.rmtree(gone_root)

    response = client.delete("/api/file-workspaces/stale")

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["removed"]] == [gone["id"]]
    assert [item["id"] for item in body["workspaces"]] == [kept["id"]]
    assert client.get("/api/file-workspaces").json() == [kept]


def test_remove_workspace_deletes_files_and_clears_app_state(tmp_path: Path) -> None:
    book = RecentWorkspaces(tmp_path / "state")
    client = TestClient(
        create_workspace_app(workspaces=book, provider_factory=_unconfigured_model)  # type: ignore[arg-type]
    )
    root = tmp_path / "雪山旅馆"
    created = client.post("/api/file-workspaces", json={"path": str(root), "create": True}).json()
    state_root = conversation_root(book.root, created["id"])
    ConversationStore(state_root).create_with_first_turn(
        text="写第一章", request_key="k1", turn_id="t1"
    )

    response = client.request(
        "DELETE", f"/api/file-workspaces/{created['id']}", json={"confirmed_path": str(root)}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["removed"]["id"] == created["id"]
    assert body["workspaces"] == []
    assert client.get("/api/file-workspaces").json() == []
    # 应用内的会话与活动记录随登记一起清理。
    assert not state_root.exists()
    assert not root.exists()
    assert body["removed"]["exists"] is False


def test_remove_workspace_rejects_running_turn(tmp_path: Path) -> None:
    client = _client(tmp_path)
    root = tmp_path / "雪山旅馆"
    created = client.post("/api/file-workspaces", json={"path": str(root), "create": True}).json()
    client.app.state.run_registry.mark(root, "t1")  # type: ignore[attr-defined]

    response = client.request(
        "DELETE", f"/api/file-workspaces/{created['id']}", json={"confirmed_path": str(root)}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "WORKSPACE_BUSY_CONFLICT"
    assert [item["id"] for item in client.get("/api/file-workspaces").json()] == [created["id"]]


def test_remove_unknown_workspace_returns_not_found(tmp_path: Path) -> None:
    response = _client(tmp_path).request(
        "DELETE", "/api/file-workspaces/not-registered", json={"confirmed_path": str(tmp_path)}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "WORKSPACE_NOT_FOUND"


def test_rename_workspace_renames_directory_and_migrates_state(tmp_path: Path) -> None:
    book = RecentWorkspaces(tmp_path / "state")
    client = TestClient(
        create_workspace_app(workspaces=book, provider_factory=_unconfigured_model)  # type: ignore[arg-type]
    )
    parent = tmp_path / "书架"
    parent.mkdir()
    old_root = parent / "旧名"
    created = client.post(
        "/api/file-workspaces", json={"path": str(old_root), "create": True}
    ).json()
    old_state = conversation_root(book.root, created["id"])
    ConversationStore(old_state).create_with_first_turn(
        text="写第一章", request_key="k1", turn_id="t1"
    )

    response = client.patch(f"/api/file-workspaces/{created['id']}", json={"name": "新名"})

    assert response.status_code == 200
    body = response.json()
    renamed = body["workspace"]
    new_root = parent / "新名"
    assert (renamed["name"], renamed["path"]) == ("新名", str(new_root))
    assert renamed["id"] != created["id"]
    assert body["workspaces"] == [renamed]
    assert client.get("/api/file-workspaces").json() == [renamed]
    # 目录连同正文与版本记录一起改名，旧路径不再存在。
    assert not old_root.exists()
    assert (new_root / "正文").is_dir()
    assert (new_root / ".git").is_dir()
    # 会话与活动记录迁到新标识下，仍能按新标识读取作品。
    new_state = conversation_root(book.root, renamed["id"])
    assert not old_state.exists()
    assert ConversationStore(new_state).conversations()[0].title == "写第一章"
    assert client.get(f"/api/file-workspaces/{renamed['id']}/files").status_code == 200


def test_rename_workspace_rejects_existing_directory(tmp_path: Path) -> None:
    client = _client(tmp_path)
    root = tmp_path / "旧名"
    created = client.post("/api/file-workspaces", json={"path": str(root), "create": True}).json()
    (tmp_path / "已有目录").mkdir()

    response = client.patch(f"/api/file-workspaces/{created['id']}", json={"name": "已有目录"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "WORKSPACE_NAME_CONFLICT"
    assert root.is_dir()
    assert client.get("/api/file-workspaces").json()[0]["id"] == created["id"]


def test_rename_workspace_rejects_invalid_name(tmp_path: Path) -> None:
    client = _client(tmp_path)
    root = tmp_path / "旧名"
    created = client.post("/api/file-workspaces", json={"path": str(root), "create": True}).json()

    response = client.patch(f"/api/file-workspaces/{created['id']}", json={"name": "一层/二层"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "WORKSPACE_NAME_INVALID"
    assert root.is_dir()


def test_rename_workspace_rejects_running_turn(tmp_path: Path) -> None:
    client = _client(tmp_path)
    root = tmp_path / "旧名"
    created = client.post("/api/file-workspaces", json={"path": str(root), "create": True}).json()
    client.app.state.run_registry.mark(root, "t1")  # type: ignore[attr-defined]

    response = client.patch(f"/api/file-workspaces/{created['id']}", json={"name": "新名"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "WORKSPACE_BUSY_CONFLICT"
    assert root.is_dir()


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


def test_files_lists_chapters_characters_and_fixed_settings(tmp_path: Path) -> None:
    client = _client(tmp_path)
    root = tmp_path / "作品"
    workspace_id = _created(client, root)

    empty = client.get(f"/api/file-workspaces/{workspace_id}/files").json()
    assert empty["chapters"] == []
    assert empty["characters"] == []
    assert [item["path"] for item in empty["settings"]] == [
        "设定集/线索.md",
        "设定集/真相.md",
    ]
    assert not any(item["exists"] for item in empty["settings"])

    client.post(
        f"/api/file-workspaces/{workspace_id}/imports",
        files={"file": ("雨夜.md", "# 雨夜\n\n侦探抵达旅馆。\n".encode(), "text/markdown")},
    )
    listed = client.get(f"/api/file-workspaces/{workspace_id}/files").json()
    assert [(item["path"], item["title"], item["order"]) for item in listed["chapters"]] == [
        ("正文/0001 雨夜.md", "雨夜", "0001")
    ]


def test_files_and_file_share_display_metadata(tmp_path: Path) -> None:
    client = _client(tmp_path)
    root = tmp_path / "雪山旅馆"
    workspace_id = _created(client, root)
    workspace = Workspace.open(root)
    workspace.write("正文/0001 暴雪来客.md", "# 雪山旅馆\n\n正文。\n", expected_digest=None)
    workspace.write("正文/0003 书房里的脚印.md", "## 第三章 书房里的脚印\n", expected_digest=None)
    workspace.write(
        "设定集/角色小传/许衡.md",
        "---\nroles: [suspect, culprit]\n---\n# 许衡\n\n身份。\n",
        expected_digest=None,
    )

    listed = client.get(f"/api/file-workspaces/{workspace_id}/files").json()
    chapters = {item["path"]: item for item in listed["chapters"]}
    first = chapters["正文/0001 暴雪来客.md"]
    assert first["display_title"] == "第一章 暴雪来客"
    assert first["chapter_number"] == 1
    assert first["chapter_type"] == "regular"
    assert first["metadata_warnings"]
    assert chapters["正文/0003 书房里的脚印.md"]["display_title"] == "第三章 书房里的脚印"
    character = listed["characters"][0]
    assert character["display_title"] == "许衡"
    assert character["roles"] == ["suspect", "culprit"]

    detail = client.get(
        f"/api/file-workspaces/{workspace_id}/file",
        params={"path": "设定集/角色小传/许衡.md"},
    ).json()
    assert detail["display_title"] == "许衡"
    assert detail["roles"] == ["suspect", "culprit"]
    # 原文保留元信息；稿纸读 display_text，不把 --- 围栏当正文。
    assert detail["text"].startswith("---")
    assert detail["display_text"].startswith("# 许衡")
    assert detail["digest"] == workspace.read("设定集/角色小传/许衡.md").digest


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

    (root / "设定集").mkdir()
    (root / "设定集/真相.md").write_text("", encoding="utf-8")
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


# -- Git 状态与撤销 -------------------------------------------------------


def test_git_status_reports_uninitialized_then_initialized(tmp_path: Path) -> None:
    client = _client(tmp_path)
    plain = tmp_path / "已有目录"
    plain.mkdir()
    workspace_id = client.post("/api/file-workspaces", json={"path": str(plain)}).json()["id"]

    before = client.get(f"/api/file-workspaces/{workspace_id}/git/status")
    assert before.status_code == 200
    assert before.json()["initialized"] is False

    after = client.post(f"/api/file-workspaces/{workspace_id}/git/init")

    assert after.status_code == 200
    assert after.json()["initialized"] is True
    assert after.json()["clean"] is True
    assert (plain / ".git").is_dir()
    log = client.get(f"/api/file-workspaces/{workspace_id}/git/log").json()
    assert [commit["subject"] for commit in log["commits"]] == ["初始化作品目录"]


def test_git_diff_record_and_commit_diff(tmp_path: Path) -> None:
    client = _client(tmp_path)
    root = tmp_path / "作品"
    workspace_id = _created(client, root)
    (root / "正文/0001 雨夜.md").write_text("# 雨夜\n\n侦探抵达旅馆。\n", encoding="utf-8")

    status = client.get(f"/api/file-workspaces/{workspace_id}/git/status").json()
    assert [item["code"] for item in status["changes"]] == ["??"]

    # 新文件不在工作区差异里：这是 Git 的行为，界面另行显示「未跟踪」。
    assert client.get(f"/api/file-workspaces/{workspace_id}/git/diff").json()["diff"] == ""

    recorded = client.post(f"/api/file-workspaces/{workspace_id}/git/record", json={})
    assert recorded.status_code == 200
    commit = recorded.json()["commit"]
    assert commit

    commit_diff = client.get(f"/api/file-workspaces/{workspace_id}/git/commits/{commit}/diff")
    assert commit_diff.status_code == 200
    assert "+侦探抵达旅馆。" in commit_diff.json()["diff"]
    assert commit_diff.json()["paths"] == ["正文/0001 雨夜.md"]

    again = client.post(f"/api/file-workspaces/{workspace_id}/git/record", json={})
    assert again.json()["commit"] is None


def test_git_revert_requires_current_head(tmp_path: Path) -> None:
    client = _client(tmp_path)
    root = tmp_path / "作品"
    workspace_id = _created(client, root)
    (root / "正文/0001 雨夜.md").write_text("# 雨夜\n", encoding="utf-8")
    commit = client.post(f"/api/file-workspaces/{workspace_id}/git/record", json={}).json()[
        "commit"
    ]

    reverted = client.post(
        f"/api/file-workspaces/{workspace_id}/git/revert", json={"commit": commit}
    )
    assert reverted.status_code == 200
    assert not (root / "正文/0001 雨夜.md").exists()

    stale = client.post(f"/api/file-workspaces/{workspace_id}/git/revert", json={"commit": commit})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "GIT_HEAD_MOVED_CONFLICT"


def test_git_commit_diff_rejects_unknown_commit(tmp_path: Path) -> None:
    client = _client(tmp_path)
    workspace_id = _created(client, tmp_path / "作品")

    response = client.get(f"/api/file-workspaces/{workspace_id}/git/commits/deadbeef/diff")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "GIT_COMMIT_NOT_FOUND"


def test_default_location_reports_the_documents_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """新建作品的默认父目录由服务端解析：浏览器不知道本机的文档目录在哪。"""
    documents = tmp_path / "Documents"
    documents.mkdir()
    monkeypatch.setattr("hastings.workspace.api.default_workspace_root", lambda: documents)

    client = _client(tmp_path)

    response = client.get("/api/file-workspaces/default-location")

    assert response.status_code == 200
    assert response.json() == {"root": str(documents)}


def test_pick_directory_returns_the_chosen_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """界面不再手输路径：位置只能从系统对话框来，接口原样回传作者选的目录。"""
    chosen = tmp_path / "我的小说"
    chosen.mkdir()
    seen: dict[str, object] = {}

    def fake_pick(*, initial: Path | None = None) -> Path:
        seen["initial"] = initial
        return chosen

    monkeypatch.setattr("hastings.workspace.api.pick_directory", fake_pick)
    client = _client(tmp_path)

    response = client.post("/api/file-workspaces/pick-directory", json={"initial": str(tmp_path)})

    assert response.status_code == 200
    assert response.json() == {"path": str(chosen)}
    assert seen["initial"] == tmp_path


def test_pick_directory_cancel_returns_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("hastings.workspace.api.pick_directory", lambda *, initial=None: None)
    client = _client(tmp_path)

    response = client.post("/api/file-workspaces/pick-directory", json={})

    assert response.status_code == 200
    assert response.json() == {"path": None}


def test_pick_directory_busy_is_conflict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """已经有一个系统窗口开着时，第二个请求要明确拒绝，而不是再弹一个。"""

    def busy(*, initial: Path | None = None) -> Path | None:
        raise DirectoryPickerError(
            code="DIRECTORY_PICKER_BUSY_CONFLICT",
            why="已经有一个选择窗口开着",
            fix="先在系统窗口里选好或取消",
        )

    monkeypatch.setattr("hastings.workspace.api.pick_directory", busy)
    client = _client(tmp_path)

    response = client.post("/api/file-workspaces/pick-directory", json={})

    assert response.status_code == 409
    body = response.json()["error"]
    assert body["code"] == "DIRECTORY_PICKER_BUSY_CONFLICT"
    assert "取消" in body["fix"]


def test_conversation_context_rejects_foreign_paths(tmp_path: Path) -> None:
    """材料只接受作品受管路径：不能借接口把任意磁盘位置带进受理。"""
    client = _client(tmp_path)
    workspace_id = _created(client, tmp_path / "雪山旅馆")
    url = f"/api/file-workspaces/{workspace_id}/conversations"

    outside = client.post(
        url,
        json={
            "text": "参考这个文件",
            "request_key": "k1",
            "context": {"reference_paths": ["../秘密.md"]},
        },
    )
    assert outside.status_code == 422

    missing = client.post(
        url,
        json={
            "text": "参考这个文件",
            "request_key": "k2",
            "context": {"reference_paths": ["正文/0009 没有.md"]},
        },
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "CONTEXT_SOURCE_NOT_FOUND"


def test_conversation_ui_keeps_draft_references(tmp_path: Path) -> None:
    """草稿的参考与文字一起保存；文字清空后引用不残留。"""
    client = _client(tmp_path)
    root = tmp_path / "雪山旅馆"
    workspace_id = _created(client, root)
    (root / "正文/0001 雨夜.md").write_text("# 雨夜\n", encoding="utf-8")
    url = f"/api/file-workspaces/{workspace_id}/conversation-ui"

    first = client.get(url).json()
    assert first["draft_contexts"] == {}

    saved = client.patch(
        url,
        json={
            "set_draft": True,
            "draft_target": "new",
            "draft_text": "参考第一章改第三章",
            "set_draft_context": True,
            "draft_selected_path": "正文/0001 雨夜.md",
            "draft_reference_paths": ["正文/0001 雨夜.md"],
        },
        headers={"If-Match": first["revision"]},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["draft_contexts"] == {
        "new": {"selected_path": "正文/0001 雨夜.md", "reference_paths": ["正文/0001 雨夜.md"]}
    }

    cleared = client.patch(
        url,
        json={"set_draft": True, "draft_target": "new", "draft_text": ""},
        headers={"If-Match": saved.json()["revision"]},
    )
    assert cleared.json()["drafts"] == {}
    assert cleared.json()["draft_contexts"] == {}


def test_draft_context_rejects_unmanaged_reference(tmp_path: Path) -> None:
    client = _client(tmp_path)
    workspace_id = _created(client, tmp_path / "雪山旅馆")
    url = f"/api/file-workspaces/{workspace_id}/conversation-ui"

    response = client.patch(
        url,
        json={
            "set_draft_context": True,
            "draft_target": "new",
            "draft_reference_paths": ["C:/随便一个文件.md"],
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "CONVERSATION_UI_TARGET_INVALID"


def test_state_endpoint_lists_declared_identity(tmp_path: Path) -> None:
    """身份状态按当前章节返回：已披露、未到章与计划披露分开，冲突单列。"""
    client = _client(tmp_path)
    root = tmp_path / "雪山旅馆"
    workspace_id = _created(client, root)
    (root / "正文/0001 雨夜.md").write_text("第一章 雨夜\n\n雪。\n", encoding="utf-8")
    (root / "设定集/角色小传").mkdir(parents=True)
    (root / "设定集/角色小传/林静.md").write_text(
        "---\n"
        "roles: [suspect]\n"
        "surface: 被害人的主治医师\n"
        "aliases: [林医生]\n"
        "revealed: [1:主治医师, 6:与死者有旧怨]\n"
        "planned: [9:凶手]\n"
        "---\n\n# 林静\n\n手术灯下站了整夜。\n",
        encoding="utf-8",
    )

    body = client.get(f"/api/file-workspaces/{workspace_id}/state").json()

    assert body["domain"] == "disclosure"
    assert body["current_chapter"] == 1
    entity = body["entities"][0]
    assert entity["name"] == "林静"
    assert entity["surface"] == "被害人的主治医师"
    assert entity["aliases"] == ["林医生"]
    assert entity["revealed"] == [{"fact": "主治医师", "chapter": 1, "source": "declared"}]
    assert entity["record_status"] is None
    assert entity["verified"] == 0
    assert entity["upcoming"] == [{"fact": "与死者有旧怨", "chapter": 6, "source": "declared"}]
    assert entity["planned"] == [{"fact": "凶手", "chapter": 9, "source": "declared"}]
    assert entity["overdue"] is False
    assert body["conflicts"] == []


def test_state_endpoint_switches_domains(tmp_path: Path) -> None:
    """同一个入口按 domain 返回不同状态域，各有自己的形状。"""
    client = _client(tmp_path)
    root = tmp_path / "雪山旅馆"
    workspace_id = _created(client, root)
    (root / "正文/0001 雨夜.md").write_text("第一章 雨夜\n\n雪。\n", encoding="utf-8")
    (root / "设定集/角色小传").mkdir(parents=True)
    (root / "设定集/角色小传/林静.md").write_text(
        "---\nroles: [suspect]\naliases: [林医生]\n---\n\n# 林静\n", encoding="utf-8"
    )

    roster = client.get(f"/api/file-workspaces/{workspace_id}/state?domain=roster").json()
    timeline = client.get(f"/api/file-workspaces/{workspace_id}/state?domain=timeline").json()
    glossary = client.get(f"/api/file-workspaces/{workspace_id}/state?domain=glossary").json()

    assert roster["domain"] == "roster"
    assert roster["characters"][0]["name"] == "林静"
    # 还没有核对记录：位置未知、章节为空，但姓名与存活默认值可用。
    assert roster["characters"][0]["chapter"] is None
    assert roster["characters"][0]["alive"] is True

    assert timeline["domain"] == "timeline"
    assert timeline["entries"] == []
    assert timeline["missing"] == [1]

    assert glossary["domain"] == "glossary"
    assert glossary["terms"][0]["term"] == "林静"
    assert glossary["terms"][0]["source"] == "declared"


def test_state_endpoint_lists_clue_ledger(tmp_path: Path) -> None:
    """线索按状态分列，超期项单独给一份内容清单；没有记录时为空。"""
    client = _client(tmp_path)
    root = tmp_path / "雪山旅馆"
    workspace_id = _created(client, root)
    (root / "正文/0001 雨夜.md").write_text("第一章 雨夜\n\n雪。\n", encoding="utf-8")

    empty = client.get(f"/api/file-workspaces/{workspace_id}/state?domain=clues").json()

    assert empty["domain"] == "clues"
    assert empty["entries"] == []
    assert empty["overdue"] == []
