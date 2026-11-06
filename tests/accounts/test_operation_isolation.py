"""v1.4 P2b：操作记录按账号归属，猜 ID 不能读取或控制他人任务。"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from hastings.accounts.config import AccountsConfig
from hastings.accounts.email import read_outbox
from hastings.accounts.runtime import AuthRuntime, build_auth_runtime
from hastings.accounts.workspaces import UserWorkspaces
from hastings.server.file_app import create_workspace_app
from hastings.workspace.operations import Operation, OperationStore


def _unconfigured_model() -> tuple[object, str]:
    raise AssertionError("操作归属用例不应调用模型")


def _setup(tmp_path: Path) -> tuple[TestClient, AuthRuntime, Path]:
    runtime = build_auth_runtime(
        AccountsConfig(
            database_path=tmp_path / "clueledger.sqlite3",
            public_base_url="http://testserver",
            mail_outbox=tmp_path / "outbox",
        )
    )
    workspaces = UserWorkspaces(
        tmp_path / "state", runtime.database, tmp_path / "server-workspaces"
    )
    app = create_workspace_app(
        workspaces=workspaces,
        provider_factory=_unconfigured_model,  # type: ignore[arg-type]
        auth=runtime,
    )
    return TestClient(app), runtime, tmp_path / "state"


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
    message = [item for item in read_outbox(runtime.config.mail_outbox) if item.to == email][-1]
    assert (
        client.post("/api/auth/verify-email", json={"token": _token(message.body)}).status_code
        == 200
    )
    login = client.post("/api/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["user"]["user_id"], login.json()["csrf_token"]


def _workspace(client: TestClient, csrf: str) -> str:
    response = client.post(
        "/api/file-workspaces",
        json={"name": "雪山旅馆", "create": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["id"])


def test_operation_ownership_is_checked_on_every_read_and_control(tmp_path: Path) -> None:
    first, runtime, state = _setup(tmp_path)
    user_a, csrf_a = _account(first, runtime, "a@example.com")
    workspace_id = _workspace(first, csrf_a)

    store = OperationStore(state)
    store.create(
        Operation(
            id="op_isolation_chat",
            owner="workspace",
            workspace_id=workspace_id,
            conversation_id="conv_1",
            turn_id="turn_1",
            purpose="chat",
            owner_user_id=user_a,
            status="paused",
        )
    )
    store.create(
        Operation(
            id="op_isolation_probe",
            owner="models",
            workspace_id="",
            conversation_id="",
            turn_id="",
            purpose="probe",
            owner_user_id=user_a,
            status="paused",
        )
    )

    assert first.get("/api/operations/op_isolation_chat").status_code == 200
    assert first.get("/api/operations/op_isolation_probe").status_code == 200
    assert len(first.get("/api/operations?conversation_id=conv_1").json()) == 1

    second = TestClient(first.app)
    _, csrf_b = _account(second, runtime, "b@example.com")

    assert second.get("/api/operations/op_isolation_chat").status_code == 404
    assert second.get("/api/operations/op_isolation_probe").status_code == 404
    assert second.get("/api/operations?conversation_id=conv_1").json() == []
    assert (
        second.post(
            "/api/operations/op_isolation_chat/pause",
            json={"request_key": "pause-1"},
            headers={"X-CSRF-Token": csrf_b},
        ).status_code
        == 404
    )
    assert second.get("/api/operations/op_isolation_chat/events").status_code == 404
