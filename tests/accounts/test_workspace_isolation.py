"""v1.4 P2a：server 模式作品按账号隔离，路径由服务器分配。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hastings.accounts.config import AccountsConfig
from hastings.accounts.email import read_outbox
from hastings.accounts.runtime import AuthRuntime, build_auth_runtime
from hastings.accounts.workspaces import UserWorkspaces
from hastings.identity import bind_user_id, reset_user_id
from hastings.server.file_app import create_workspace_app
from hastings.workspace.errors import WorkspaceError


def _unconfigured_model() -> tuple[object, str]:
    raise AssertionError("作品隔离用例不应调用模型")


def _setup(tmp_path: Path) -> tuple[TestClient, AuthRuntime, Path]:
    runtime = build_auth_runtime(
        AccountsConfig(
            database_path=tmp_path / "clueledger.sqlite3",
            public_base_url="http://testserver",
            mail_outbox=tmp_path / "outbox",
        )
    )
    storage_root = tmp_path / "server-workspaces"
    workspaces = UserWorkspaces(tmp_path / "state", runtime.database, storage_root)
    app = create_workspace_app(
        workspaces=workspaces,
        provider_factory=_unconfigured_model,  # type: ignore[arg-type]
        auth=runtime,
    )
    return TestClient(app), runtime, storage_root


def _token(body: str) -> str:
    match = re.search(r"#/verify-email\?token=([A-Za-z0-9_-]+)", body)
    assert match is not None
    return match.group(1)


def _account(client: TestClient, runtime: AuthRuntime, email: str) -> tuple[str, str]:
    password = "correct horse battery"
    assert (
        client.post("/api/auth/register", json={"email": email, "password": password}).status_code
        == 202
    )
    messages = [item for item in read_outbox(runtime.config.mail_outbox) if item.to == email]
    assert (
        client.post("/api/auth/verify-email", json={"token": _token(messages[-1].body)}).status_code
        == 200
    )
    login = client.post("/api/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["user"]["user_id"], login.json()["csrf_token"]


def _create(client: TestClient, csrf: str, name: str) -> dict[str, object]:
    response = client.post(
        "/api/file-workspaces",
        json={"name": name, "create": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_two_accounts_cannot_see_or_open_each_others_workspaces(tmp_path: Path) -> None:
    first, runtime, storage = _setup(tmp_path)
    user_a, csrf_a = _account(first, runtime, "a@example.com")
    created = _create(first, csrf_a, "雪山旅馆")
    workspace_id = str(created["id"])

    assert created["path"] == ""
    assert created["exists"] is True
    assert workspace_id.startswith("ws_")
    assert (storage / user_a / workspace_id).is_dir()
    assert first.get(f"/api/file-workspaces/{workspace_id}/files").status_code == 200

    second = TestClient(first.app)
    user_b, csrf_b = _account(second, runtime, "b@example.com")
    assert user_b != user_a
    assert second.get("/api/file-workspaces").json() == []

    assert second.get(f"/api/file-workspaces/{workspace_id}/files").status_code == 404
    assert second.get(f"/api/file-workspaces/{workspace_id}/activity").status_code == 404
    assert (
        second.get(
            f"/api/file-workspaces/{workspace_id}/events",
            params={"conversation_id": "conv_1", "follow": "false"},
        ).status_code
        == 404
    )
    assert (
        second.patch(
            f"/api/file-workspaces/{workspace_id}",
            json={"name": "偷来的名字"},
            headers={"X-CSRF-Token": csrf_b},
        ).status_code
        == 404
    )
    assert (
        second.request(
            "DELETE",
            f"/api/file-workspaces/{workspace_id}",
            json={"confirmed_path": ""},
            headers={"X-CSRF-Token": csrf_b},
        ).status_code
        == 404
    )

    # 客户端传入的绝对路径在 server 模式被忽略，不会在服务器任意位置建目录。
    outside = tmp_path / "outside"
    own = second.post(
        "/api/file-workspaces",
        json={"path": str(outside), "name": "B 的作品", "create": True},
        headers={"X-CSRF-Token": csrf_b},
    )
    assert own.status_code == 200
    assert not outside.exists()
    assert (storage / user_b / str(own.json()["id"])).is_dir()

    names_a = [item["name"] for item in first.get("/api/file-workspaces").json()]
    names_b = [item["name"] for item in second.get("/api/file-workspaces").json()]
    assert names_a == ["雪山旅馆"]
    assert names_b == ["B 的作品"]


def test_server_mode_disables_local_directory_entry_points(tmp_path: Path) -> None:
    client, runtime, _ = _setup(tmp_path)
    _, csrf = _account(client, runtime, "a@example.com")

    assert client.get("/api/file-workspaces/default-location").status_code == 403
    assert (
        client.post(
            "/api/file-workspaces/pick-directory",
            json={"initial": ""},
            headers={"X-CSRF-Token": csrf},
        ).status_code
        == 403
    )

    no_name = client.post(
        "/api/file-workspaces",
        json={"path": str(tmp_path / "arbitrary"), "create": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert no_name.status_code == 400
    assert no_name.json()["error"]["code"] == "WORKSPACE_NAME_INVALID"
    assert not (tmp_path / "arbitrary").exists()


def test_model_tool_file_api_cannot_reach_another_account(tmp_path: Path) -> None:
    first, runtime, storage = _setup(tmp_path)
    user_a, csrf_a = _account(first, runtime, "a@example.com")
    workspace_a = str(_create(first, csrf_a, "A 的作品")["id"])
    a_root = storage / user_a / workspace_a
    secret = a_root / "正文" / "秘密.md"
    secret.write_text("# 秘密\n\n这条内容不属于 B。\n", encoding="utf-8")

    second = TestClient(first.app)
    user_b, csrf_b = _account(second, runtime, "b@example.com")
    workspace_b = str(_create(second, csrf_b, "B 的作品")["id"])

    registry = first.app.state.workspaces
    token = bind_user_id(user_b)
    try:
        b_workspace = registry.workspace(workspace_b)
        with pytest.raises(WorkspaceError):
            b_workspace.read("../../" + f"{user_a}/{workspace_a}/正文/秘密.md")
        with pytest.raises(WorkspaceError):
            b_workspace.read(str(secret))
    finally:
        reset_user_id(token)


def test_rename_and_delete_stay_inside_the_owner(tmp_path: Path) -> None:
    client, runtime, storage = _setup(tmp_path)
    user, csrf = _account(client, runtime, "a@example.com")
    created = _create(client, csrf, "旧名字")
    workspace_id = str(created["id"])
    disk = storage / user / workspace_id

    renamed = client.patch(
        f"/api/file-workspaces/{workspace_id}",
        json={"name": "新名字"},
        headers={"X-CSRF-Token": csrf},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["workspace"]["id"] == workspace_id
    assert renamed.json()["workspace"]["name"] == "新名字"
    assert disk.is_dir()

    duplicate = client.post(
        "/api/file-workspaces",
        json={"name": "新名字", "create": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "WORKSPACE_NAME_CONFLICT"

    removed = client.request(
        "DELETE",
        f"/api/file-workspaces/{workspace_id}",
        json={"confirmed_path": ""},
        headers={"X-CSRF-Token": csrf},
    )
    assert removed.status_code == 200, removed.text
    assert not disk.exists()
    assert client.get("/api/file-workspaces").json() == []
