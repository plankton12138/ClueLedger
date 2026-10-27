"""作品真实删除：确认范围、失败恢复与共享内容保护，不调用模型。"""

from __future__ import annotations

import shutil
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hastings.library import ModuleLibrary
from hastings.server.file_app import create_workspace_app
from hastings.workspace.deletion import delete_workspace_files
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.state import ConversationStore, RecentWorkspaces, conversation_root


def _unconfigured_model() -> object:
    raise AssertionError("作品删除不应调用模型")


def _client(tmp_path: Path) -> tuple[TestClient, RecentWorkspaces, Path]:
    book = RecentWorkspaces(tmp_path / "state")
    library = tmp_path / "ModuleLibrary"
    return (
        TestClient(
            create_workspace_app(
                workspaces=book,
                provider_factory=_unconfigured_model,  # type: ignore[arg-type]
                library=ModuleLibrary(library),
            )
        ),
        book,
        library,
    )


def _create(client: TestClient, root: Path) -> str:
    response = client.post("/api/file-workspaces", json={"path": str(root), "create": True})
    assert response.status_code == 200, response.text
    return str(response.json()["id"])


def _delete(client: TestClient, workspace_id: str, root: Path):
    return client.request(
        "DELETE", f"/api/file-workspaces/{workspace_id}", json={"confirmed_path": str(root)}
    )


def test_delete_requires_current_confirmed_path(tmp_path: Path) -> None:
    client, book, _ = _client(tmp_path)
    root = tmp_path / "作品"
    workspace_id = _create(client, root)
    assert client.delete(f"/api/file-workspaces/{workspace_id}").status_code == 422
    response = _delete(client, workspace_id, tmp_path / "另一部作品")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "WORKSPACE_CONFIRMATION_CONFLICT"
    assert root.is_dir()
    assert book.get(workspace_id).exists


def test_delete_removes_all_owned_files_and_preserves_shared_content(tmp_path: Path) -> None:
    client, book, library = _client(tmp_path)
    root = tmp_path / "作品甲"
    workspace_id = _create(client, root)
    other = tmp_path / "作品乙"
    other_id = _create(client, other)
    library.mkdir()
    shared = library / "共享模块.md"
    shared.write_text("共享内容", encoding="utf-8")
    (root / "灵感本").mkdir()
    (root / "灵感本/灵感.json").write_text("{}", encoding="utf-8")
    (root / ".clueledger").mkdir()
    (root / ".clueledger/assembly.json").write_text("{}", encoding="utf-8")
    readonly = root / "正文/0001 雨夜.md"
    readonly.write_text("雨夜", encoding="utf-8")
    readonly.chmod(stat.S_IREAD)
    state = conversation_root(book.root, workspace_id)
    ConversationStore(state).create_with_first_turn(
        text="写第一章", request_key="delete-test", turn_id="t1"
    )

    response = _delete(client, workspace_id, root)

    assert response.status_code == 200, response.text
    assert not root.exists()
    assert not state.exists()
    assert shared.read_text(encoding="utf-8") == "共享内容"
    assert book.get(other_id).exists


@pytest.mark.parametrize("target", ["library", "state", "parent"])
def test_delete_rejects_protected_roots(tmp_path: Path, target: str) -> None:
    client, book, library = _client(tmp_path)
    root = {"library": library, "state": book.root, "parent": tmp_path / "父作品"}[target]
    # 状态登记创建前先创建正文；打开仅登记，不改写原目录。
    (root / "正文").mkdir(parents=True)
    opened = client.post("/api/file-workspaces", json={"path": str(root)}).json()
    if target == "parent":
        _create(client, root / "子作品")

    response = _delete(client, opened["id"], root)

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "WORKSPACE_DELETE_PROTECTED"
    assert root.is_dir()
    assert book.get(opened["id"]).exists


def test_delete_failure_keeps_registration_and_allows_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, book, _ = _client(tmp_path)
    root = tmp_path / "作品"
    workspace_id = _create(client, root)
    with monkeypatch.context() as patch:

        def fail(*args, **kwargs):
            raise PermissionError("文件被占用")

        patch.setattr("hastings.workspace.deletion.shutil.rmtree", fail)
        response = _delete(client, workspace_id, root)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "WORKSPACE_DELETE_FAILED"
    assert book.get(workspace_id).exists
    assert _delete(client, workspace_id, root).status_code == 200
    assert book.recent() == ()


def test_delete_already_missing_directory_cleans_registration(tmp_path: Path) -> None:
    client, book, _ = _client(tmp_path)
    root = tmp_path / "作品"
    (root / "正文").mkdir(parents=True)
    workspace_id = client.post("/api/file-workspaces", json={"path": str(root)}).json()["id"]
    (root / "正文").rmdir()
    root.rmdir()
    assert _delete(client, workspace_id, root).status_code == 200
    assert book.recent() == ()


def test_create_conflict_keeps_existing_files_unchanged(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    root = tmp_path / "同名目录"
    root.mkdir()
    existing = root / "原始内容.txt"
    existing.write_text("保留", encoding="utf-8")
    response = client.post("/api/file-workspaces", json={"path": str(root), "create": True})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "WORKSPACE_NAME_CONFLICT"
    assert existing.read_text(encoding="utf-8") == "保留"
    assert sorted(item.name for item in root.iterdir()) == ["原始内容.txt"]


def test_retry_after_state_cleanup_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, book, _ = _client(tmp_path)
    root = tmp_path / "作品"
    workspace_id = _create(client, root)
    state = conversation_root(book.root, workspace_id)
    state.mkdir(parents=True)
    (state / "记录.json").write_text("{}", encoding="utf-8")
    conversations = f"/api/file-workspaces/{workspace_id}/conversations"
    assert client.get(conversations).status_code == 200
    remove = shutil.rmtree
    with monkeypatch.context() as patch:

        def fail_state(path, **kwargs):
            if Path(path) == state:
                raise PermissionError("应用记录被占用")
            return remove(path, **kwargs)

        patch.setattr("hastings.workspace.deletion.shutil.rmtree", fail_state)
        response = _delete(client, workspace_id, root)
    assert response.status_code == 500
    assert not root.exists()
    assert state.exists()
    assert book.get(workspace_id).exists is False
    assert client.get(conversations).status_code == 404
    assert _delete(client, workspace_id, root).status_code == 200
    assert not state.exists()
    assert book.recent() == ()


def test_delete_rejects_replaced_root_link(tmp_path: Path) -> None:
    client, book, _ = _client(tmp_path)
    root = tmp_path / "作品"
    workspace_id = _create(client, root)
    original = tmp_path / "原作品"
    root.rename(original)
    outside = tmp_path / "其他目录"
    (outside / "正文").mkdir(parents=True)
    sentinel = outside / "保留.txt"
    sentinel.write_text("保留", encoding="utf-8")
    try:
        root.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("当前系统不允许创建符号链接")
    response = _delete(client, workspace_id, root)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "WORKSPACE_PATH_CONFLICT"
    assert sentinel.read_text(encoding="utf-8") == "保留"
    assert len(book.recent()) == 1


def test_delete_does_not_follow_nested_directory_links(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    root = tmp_path / "作品"
    workspace_id = _create(client, root)
    outside = tmp_path / "外部资料"
    outside.mkdir()
    sentinel = outside / "保留.txt"
    sentinel.write_text("保留", encoding="utf-8")
    try:
        (root / "外部链接").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("当前系统不允许创建符号链接")
    response = _delete(client, workspace_id, root)
    assert response.status_code == 200, response.text
    assert not root.exists()
    assert sentinel.read_text(encoding="utf-8") == "保留"


def test_state_cleanup_preserves_a_library_inside_state(tmp_path: Path) -> None:
    root = tmp_path / "作品"
    (root / "正文").mkdir(parents=True)
    state = tmp_path / "state" / "workspaces" / "作品id"
    library = state / "ModuleLibrary"
    library.mkdir(parents=True)
    with pytest.raises(WorkspaceError) as error:
        delete_workspace_files(root, state, confirmed_path=str(root), protected_roots=[library])
    assert error.value.code == "WORKSPACE_DELETE_PROTECTED"
    assert root.is_dir()
    assert library.is_dir()
