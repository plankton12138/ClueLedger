"""v1.4 P2c：模型连接、凭据命名空间与私有模块库按账号隔离。"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from hastings.accounts.config import AccountsConfig
from hastings.accounts.email import read_outbox
from hastings.accounts.runtime import AuthRuntime, build_auth_runtime
from hastings.accounts.workspaces import UserWorkspaces
from hastings.infrastructure.credentials import InMemoryCredentialStore
from hastings.server.file_app import create_workspace_app
from hastings.server.user_services import UserResourcesRegistry, build_user_resources

_DRAFT = {
    "display_name": "我的 DeepSeek",
    "catalog_id": "deepseek",
    "base_url": "https://api.deepseek.com/v1",
    "api": "openai-completions",
    "models": ["deepseek-chat"],
    "default_model": "deepseek-chat",
    "api_key": "sk-user-specific-key-4321",
}


def _code(body: str) -> str:
    match = re.search(r"验证码[：:]\s*(\d{6})", body)
    assert match is not None
    return match.group(1)


def _setup(
    tmp_path: Path,
) -> tuple[TestClient, AuthRuntime, UserResourcesRegistry, InMemoryCredentialStore]:
    runtime = build_auth_runtime(
        AccountsConfig(
            database_path=tmp_path / "clueledger.sqlite3",
            public_base_url="http://testserver",
            mail_outbox=tmp_path / "outbox",
        )
    )
    base_credentials = InMemoryCredentialStore()
    workspaces = UserWorkspaces(
        tmp_path / "state", runtime.database, tmp_path / "server-workspaces"
    )
    resources = UserResourcesRegistry(
        builder=lambda user_id, context: build_user_resources(
            user_id,
            context,
            state_root=tmp_path / "state",
            libraries_root=tmp_path / "libraries",
            base_credentials=base_credentials,
        )
    )
    app = create_workspace_app(workspaces=workspaces, auth=runtime, resources=resources)
    return TestClient(app), runtime, resources, base_credentials


def _account(client: TestClient, runtime: AuthRuntime, email: str) -> tuple[str, str]:
    password = "correct horse battery"
    assert (
        client.post("/api/auth/register", json={"email": email, "password": password}).status_code
        == 202
    )
    message = [item for item in read_outbox(runtime.config.mail_outbox) if item.to == email][-1]
    assert (
        client.post(
            "/api/auth/verify-email", json={"email": email, "code": _code(message.body)}
        ).status_code
        == 200
    )
    login = client.post("/api/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["user"]["user_id"], login.json()["csrf_token"]


def _model_config(client: TestClient) -> dict[str, object]:
    response = client.get("/api/model-connections")
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def test_model_connections_credentials_selection_and_library_are_per_account(
    tmp_path: Path,
) -> None:
    first, runtime, resources, base_credentials = _setup(tmp_path)
    user_a, csrf_a = _account(first, runtime, "a@example.com")

    initial = _model_config(first)
    assert initial["connections"] == []
    created = first.post(
        "/api/model-connections",
        json=_DRAFT,
        headers={"If-Match": str(initial["revision"]), "X-CSRF-Token": csrf_a},
    )
    assert created.status_code == 200, created.text
    config = created.json()
    connection_name = config["connections"][0]["name"]

    selected = first.put(
        "/api/model-selection",
        json={"connection_id": connection_name, "model": "deepseek-chat"},
        headers={"If-Match": str(config["revision"]), "X-CSRF-Token": csrf_a},
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["selection"]["connection_id"] == connection_name

    refs_a = base_credentials.refs()
    assert refs_a
    assert all(ref.startswith(f"hastings:user:{user_a}:") for ref in refs_a)
    assert (tmp_path / "state" / "users" / user_a / "model-connections.yaml").is_file()

    second = TestClient(first.app)
    user_b, csrf_b = _account(second, runtime, "b@example.com")
    assert user_b != user_a

    config_b = _model_config(second)
    assert config_b["connections"] == []
    assert config_b["selection"]["connection_id"] is None

    created_b = second.post(
        "/api/model-connections",
        json={**_DRAFT, "display_name": "B 的 DeepSeek"},
        headers={"If-Match": str(config_b["revision"]), "X-CSRF-Token": csrf_b},
    )
    assert created_b.status_code == 200, created_b.text
    refs_b = {ref for ref in base_credentials.refs() if ref.startswith(f"hastings:user:{user_b}:")}
    assert refs_b
    assert not (refs_b & refs_a)

    assert resources.for_user(user_a).library is not resources.for_user(user_b).library
    assert resources.for_user(user_a).auth is not resources.for_user(user_b).auth
    assert resources.for_user(user_a).library.root != resources.for_user(user_b).library.root

    module = {
        "kind": "characters",
        "name": "A 的主角团",
        "request_key": "module-a-1",
        "entries": [
            {
                "entry_id": "lin",
                "title": "林静",
                "body": "法医，与死者相识。",
                "fields": {"roles": ["suspect"]},
            }
        ],
    }
    made = first.post("/api/module-library/modules", json=module, headers={"X-CSRF-Token": csrf_a})
    assert made.status_code == 200, made.text
    assert (
        first.get("/api/module-library/modules", params={"kind": "characters"}).json()["total"] == 1
    )
    assert (
        second.get("/api/module-library/modules", params={"kind": "characters"}).json()["total"]
        == 0
    )
