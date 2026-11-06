"""v1.4 P1：邮箱账号、会话、CSRF 与限速的接口测试。"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hastings.accounts.config import AccountsConfig
from hastings.accounts.email import read_outbox
from hastings.accounts.runtime import AuthRuntime, build_auth_runtime
from hastings.server.file_app import create_workspace_app
from hastings.workspace.state import RecentWorkspaces

_EMAIL = "Author@Example.com"
_PASSWORD = "correct horse battery"


def _unconfigured_model() -> tuple[object, str]:
    raise AssertionError("账号用例不应调用模型")


def _runtime(tmp_path: Path) -> AuthRuntime:
    return build_auth_runtime(
        AccountsConfig(
            database_path=tmp_path / "clueledger.sqlite3",
            public_base_url="http://testserver",
            mail_outbox=tmp_path / "outbox",
        )
    )


def _client(tmp_path: Path, runtime: AuthRuntime | None = None) -> tuple[TestClient, AuthRuntime]:
    resolved = runtime or _runtime(tmp_path)
    app = create_workspace_app(
        workspaces=RecentWorkspaces(tmp_path / "state"),
        provider_factory=_unconfigured_model,  # type: ignore[arg-type]
        auth=resolved,
    )
    return TestClient(app), resolved


def _code_from(message_body: str) -> str:
    match = re.search(r"验证码[：:]\s*(\d{6})", message_body)
    assert match is not None, message_body
    return match.group(1)


def _register_and_verify(
    client: TestClient, runtime: AuthRuntime, *, email: str = _EMAIL, password: str = _PASSWORD
) -> None:
    assert (
        client.post("/api/auth/register", json={"email": email, "password": password}).status_code
        == 202
    )
    message = read_outbox(runtime.config.mail_outbox)[-1]
    code = _code_from(message.body)
    assert (
        client.post("/api/auth/verify-email", json={"email": email, "code": code}).status_code
        == 200
    )


def _login(client: TestClient, *, email: str = _EMAIL, password: str = _PASSWORD) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    body: dict[str, str] = response.json()
    return body


def test_register_verify_login_and_me(tmp_path: Path) -> None:
    client, runtime = _client(tmp_path)

    accepted = client.post("/api/auth/register", json={"email": _EMAIL, "password": _PASSWORD})
    assert accepted.status_code == 202
    assert accepted.json() == {"status": "accepted"}

    messages = read_outbox(runtime.config.mail_outbox)
    assert len(messages) == 1
    assert messages[0].to == _EMAIL
    code = _code_from(messages[0].body)

    blocked = client.post("/api/auth/login", json={"email": _EMAIL, "password": _PASSWORD})
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "AUTH_EMAIL_UNVERIFIED"

    verified = client.post("/api/auth/verify-email", json={"email": _EMAIL, "code": code})
    assert verified.status_code == 200
    assert verified.json() == {"verified": True, "email": _EMAIL}

    repeated = client.post("/api/auth/verify-email", json={"email": _EMAIL, "code": code})
    assert repeated.status_code == 400
    assert repeated.json()["code"] == "AUTH_CODE_INVALID"

    login = client.post("/api/auth/login", json={"email": _EMAIL, "password": _PASSWORD})
    assert login.status_code == 200
    body = login.json()
    assert body["user"]["email"] == _EMAIL
    assert body["user"]["verified"] is True
    assert body["csrf_token"]
    assert client.cookies.get("clue_session")

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["mode"] == "server"
    assert me.json()["authenticated"] is True
    assert me.json()["user"]["user_id"] == body["user"]["user_id"]
    assert me.json()["csrf_token"] == body["csrf_token"]


def test_same_account_can_login_from_second_browser(tmp_path: Path) -> None:
    first, runtime = _client(tmp_path)
    _register_and_verify(first, runtime)
    first_login = _login(first)

    second = TestClient(first.app)
    second_login = _login(second)
    assert second_login["user"]["user_id"] == first_login["user"]["user_id"]

    me = second.get("/api/auth/me")
    assert me.json()["authenticated"] is True
    assert me.json()["user"]["user_id"] == first_login["user"]["user_id"]

    # 两个设备各自持有会话；退出一个不影响另一个。
    assert (
        first.post(
            "/api/auth/logout", headers={"X-CSRF-Token": first_login["csrf_token"]}
        ).status_code
        == 204
    )
    assert second.get("/api/auth/me").json()["authenticated"] is True


def test_public_auth_rejects_cross_site_origin(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)

    response = client.post(
        "/api/auth/register",
        json={"email": _EMAIL, "password": _PASSWORD},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "AUTH_ORIGIN_REJECTED"


def test_api_requires_session_in_server_mode(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)

    response = client.get("/api/file-workspaces")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_SESSION_REQUIRED"
    assert response.headers["X-Request-ID"].startswith("req_")
    assert response.headers["Cache-Control"] == "no-store"

    health = client.get("/api/health")
    assert health.status_code == 200


def test_write_requests_require_csrf_and_same_origin(tmp_path: Path) -> None:
    client, runtime = _client(tmp_path)
    _register_and_verify(client, runtime)
    login = _login(client)

    no_token = client.post(
        "/api/file-workspaces", json={"path": str(tmp_path / "book"), "create": True}
    )
    assert no_token.status_code == 403
    assert no_token.json()["code"] == "AUTH_CSRF_FAILED"

    evil_origin = client.post(
        "/api/file-workspaces",
        json={"path": str(tmp_path / "book"), "create": True},
        headers={"X-CSRF-Token": login["csrf_token"], "Origin": "https://evil.example"},
    )
    assert evil_origin.status_code == 403
    assert evil_origin.json()["code"] == "AUTH_ORIGIN_REJECTED"

    created = client.post(
        "/api/file-workspaces",
        json={"path": str(tmp_path / "book"), "create": True},
        headers={"X-CSRF-Token": login["csrf_token"], "Origin": "http://testserver"},
    )
    assert created.status_code == 200

    # GET 不需要 CSRF。
    assert client.get("/api/file-workspaces").status_code == 200


def test_logout_revokes_current_session(tmp_path: Path) -> None:
    client, runtime = _client(tmp_path)
    _register_and_verify(client, runtime)
    login = _login(client)
    old_cookie = client.cookies.get("clue_session")
    assert old_cookie is not None

    logout = client.post("/api/auth/logout", headers={"X-CSRF-Token": login["csrf_token"]})
    assert logout.status_code == 204
    assert client.get("/api/auth/me").json()["authenticated"] is False

    replay = TestClient(client.app)
    assert (
        replay.get("/api/auth/me", cookies={"clue_session": old_cookie}).json()["authenticated"]
        is False
    )
    assert (
        replay.get("/api/file-workspaces", cookies={"clue_session": old_cookie}).status_code == 401
    )


def test_password_reset_revokes_old_sessions(tmp_path: Path) -> None:
    client, runtime = _client(tmp_path)
    _register_and_verify(client, runtime)
    _login(client)

    assert (
        client.post("/api/auth/forgot-password", json={"email": _EMAIL.lower()}).status_code == 202
    )
    message = read_outbox(runtime.config.mail_outbox)[-1]
    code = _code_from(message.body)

    reset = client.post(
        "/api/auth/reset-password",
        json={"email": _EMAIL.lower(), "code": code, "new_password": "new password phrase"},
    )
    assert reset.status_code == 200
    assert reset.json() == {"reset": True}

    # 重置成功后旧会话不可用，旧密码失效，新密码可用。
    assert client.get("/api/file-workspaces").status_code == 401
    old = client.post("/api/auth/login", json={"email": _EMAIL, "password": _PASSWORD})
    assert old.status_code == 401
    new = client.post("/api/auth/login", json={"email": _EMAIL, "password": "new password phrase"})
    assert new.status_code == 200

    reused = client.post(
        "/api/auth/reset-password",
        json={
            "email": _EMAIL,
            "code": code,
            "new_password": "another password",
        },
    )
    assert reused.status_code == 400
    assert reused.json()["code"] == "AUTH_CODE_INVALID"


def test_expired_verification_code_is_rejected(tmp_path: Path) -> None:
    client, runtime = _client(tmp_path)
    assert (
        client.post("/api/auth/register", json={"email": _EMAIL, "password": _PASSWORD}).status_code
        == 202
    )
    code = _code_from(read_outbox(runtime.config.mail_outbox)[0].body)

    with sqlite3.connect(runtime.config.database_path) as conn:
        conn.execute("UPDATE auth_codes SET expires_at = 0")

    assert (
        client.post("/api/auth/verify-email", json={"email": _EMAIL, "code": code}).status_code
        == 400
    )


def test_verification_code_attempt_limit_and_resend(tmp_path: Path) -> None:
    client, runtime = _client(tmp_path)
    assert (
        client.post("/api/auth/register", json={"email": _EMAIL, "password": _PASSWORD}).status_code
        == 202
    )
    first = _code_from(read_outbox(runtime.config.mail_outbox)[-1].body)

    for _ in range(5):
        wrong = client.post(
            "/api/auth/verify-email",
            json={"email": _EMAIL, "code": "000000" if first != "000000" else "111111"},
        )
        assert wrong.status_code == 400
        assert wrong.json()["code"] == "AUTH_CODE_INVALID"
    blocked = client.post("/api/auth/verify-email", json={"email": _EMAIL, "code": first})
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "AUTH_CODE_ATTEMPTS"

    resent = client.post("/api/auth/resend-verification", json={"email": _EMAIL})
    assert resent.status_code == 202
    second = _code_from(read_outbox(runtime.config.mail_outbox)[-1].body)
    assert second != first
    assert (
        client.post("/api/auth/verify-email", json={"email": _EMAIL, "code": first}).status_code
        == 400
    )
    assert (
        client.post("/api/auth/verify-email", json={"email": _EMAIL, "code": second}).status_code
        == 200
    )


def test_existing_email_response_does_not_reveal_state(tmp_path: Path) -> None:
    client, runtime = _client(tmp_path)
    _register_and_verify(client, runtime)
    before = len(read_outbox(runtime.config.mail_outbox))

    again = client.post(
        "/api/auth/register", json={"email": _EMAIL, "password": "another password"}
    )
    assert again.status_code == 202
    assert again.json() == {"status": "accepted"}
    assert len(read_outbox(runtime.config.mail_outbox)) == before


def test_login_rate_limit(tmp_path: Path) -> None:
    client, runtime = _client(tmp_path)
    _register_and_verify(client, runtime)

    responses = [
        client.post("/api/auth/login", json={"email": _EMAIL, "password": "wrong password"})
        for _ in range(11)
    ]
    assert [response.status_code for response in responses[:10]] == [401] * 10
    assert responses[10].status_code == 429
    assert responses[10].json()["code"] == "AUTH_RATE_LIMITED"


def test_local_mode_keeps_working_without_login(tmp_path: Path) -> None:
    app = create_workspace_app(
        workspaces=RecentWorkspaces(tmp_path / "state"),
        provider_factory=_unconfigured_model,  # type: ignore[arg-type]
    )
    client = TestClient(app)

    assert client.get("/api/file-workspaces").status_code == 200
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json() == {
        "mode": "local",
        "authenticated": True,
        "user": None,
        "csrf_token": None,
    }


def test_server_mode_requires_database(tmp_path: Path) -> None:
    from hastings.server.__main__ import StartupError, assemble_server

    with pytest.raises(StartupError, match="--database"):
        assemble_server(mode="server")
