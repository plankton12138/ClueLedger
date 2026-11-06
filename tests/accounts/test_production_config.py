"""v1.4 P5：生产配置、加密凭据、健康检查与访问日志。"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hastings.infrastructure.credentials import (
    CredentialStoreError,
    EncryptedFileCredentialStore,
    build_credential_store,
    load_master_key,
)
from hastings.server.__main__ import StartupError, assemble_server
from hastings.server.file_app import create_workspace_app
from hastings.server.production import production_config_errors
from hastings.workspace.state import RecentWorkspaces


def _unconfigured_model() -> object:
    raise AssertionError("生产配置用例不应调用模型")


def _complete() -> dict[str, object]:
    return {
        "mode": "server",
        "public_base_url": "https://clue.example.com",
        "database": Path("/data/clueledger.sqlite3"),
        "workspaces_root": Path("/data/workspaces"),
        "libraries_root": Path("/data/libraries"),
        "credential_key_file": Path("/etc/clueledger/credential.key"),
        "smtp_configured": True,
        "billing_url": "http://127.0.0.1:8300",
        "billing_secret": "internal",
        "trust_proxy": True,
    }


def test_production_errors_are_reported_together() -> None:
    errors = production_config_errors(
        mode="local",
        public_base_url="http://clue.example.com",
        database=None,
        workspaces_root=None,
        libraries_root=None,
        credential_key_file=None,
        smtp_configured=False,
        billing_url=None,
        billing_secret=None,
        trust_proxy=False,
    )
    assert len(errors) == 9
    assert production_config_errors(**_complete()) == []


def test_production_startup_refuses_missing_configuration() -> None:
    with pytest.raises(StartupError, match="生产配置不完整"):
        assemble_server(mode="server", production=True, database=Path("x.sqlite3"))
    with pytest.raises(StartupError, match="HTTPS"):
        assemble_server(
            mode="server",
            production=True,
            database=Path("x.sqlite3"),
            public_base_url="http://clue.example.com",
        )


def test_encrypted_credential_store_round_trip(tmp_path: Path) -> None:
    key = os.urandom(32)
    key_file = tmp_path / "credential.key"
    key_file.write_bytes(base64.b64encode(key) + b"\n")
    assert load_master_key(key_file) == key

    store = build_credential_store(
        key_file=key_file, credentials_file=tmp_path / "credentials.vault"
    )
    assert isinstance(store, EncryptedFileCredentialStore)
    store.save("hastings:provider:demo", "secret-value")
    assert store.load("hastings:provider:demo") == "secret-value"
    assert "secret-value" not in (tmp_path / "credentials.vault").read_text(encoding="utf-8")
    assert store.refs() == {"hastings:provider:demo"}

    wrong = EncryptedFileCredentialStore(tmp_path / "credentials.vault", os.urandom(32))
    with pytest.raises(CredentialStoreError):
        wrong.load("hastings:provider:demo")
    store.delete("hastings:provider:demo")
    assert store.load("hastings:provider:demo") is None


def test_access_log_correlates_without_query_or_secret(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    app = create_workspace_app(
        workspaces=RecentWorkspaces(tmp_path / "state"),
        provider_factory=_unconfigured_model,  # type: ignore[arg-type]
    )
    client = TestClient(app)
    with caplog.at_level(logging.INFO, logger="clue.access"):
        response = client.get(
            "/api/file-workspaces?token=should-not-appear",
            headers={"X-Request-ID": "req-upstream-123"},
        )
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-upstream-123"
    records = [item for item in caplog.records if item.name == "clue.access"]
    assert records
    record = records[-1]
    assert record.path == "/api/file-workspaces"  # type: ignore[attr-defined]
    assert record.request_id == "req-upstream-123"  # type: ignore[attr-defined]
    assert "should-not-appear" not in record.getMessage()


def test_health_endpoint_checks_database(tmp_path: Path) -> None:
    from hastings.accounts.config import AccountsConfig
    from hastings.accounts.runtime import build_auth_runtime

    runtime = build_auth_runtime(
        AccountsConfig(
            database_path=tmp_path / "db.sqlite3",
            public_base_url="http://testserver",
            mail_outbox=tmp_path / "outbox",
        )
    )
    app = create_workspace_app(
        workspaces=RecentWorkspaces(tmp_path / "state"),
        provider_factory=_unconfigured_model,  # type: ignore[arg-type]
        auth=runtime,
    )
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
