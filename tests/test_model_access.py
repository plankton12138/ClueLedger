"""厂商连接与模型选择分开，V2 工作者必须使用作者明确选择的来源。"""

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from hastings.server.app import create_app
from hastings.server.provider_config import (
    ProviderBook,
    ProviderProfile,
    ServerConfig,
    load_server_config,
)
from hastings.server.v2 import V2Runtime
from test_server_app import stub_registry


@pytest.fixture
def access(tmp_path: Path):
    book = ProviderBook(
        ServerConfig(
            providers=[ProviderProfile(name="default", base_url="https://example.com/v1")]
        ),
        boot_name="default",
        boot_model="",
        config_path=tmp_path / "providers.yaml",
    )
    runtime = V2Runtime.memory()
    client = TestClient(create_app(stub_registry(tmp_path), book=book, v2_runtime=runtime))
    return client, book, runtime


def test_connect_preset_needs_no_service_address_and_never_returns_key(access) -> None:
    client, book, _ = access
    response = client.post(
        "/api/providers/connect",
        json={
            "catalog_id": "deepseek",
            "api_key": "test-secret-12345678",
        },
    )
    assert response.status_code == 200
    assert "test-secret" not in response.text
    assert book.profile("deepseek").base_url == "https://api.deepseek.com/v1"
    assert book.profile("deepseek").resolved_api_key() == "test-secret-12345678"
    assert client.get("/api/model-selection").json()["provider"] is None


def test_selection_persists_and_controls_real_v2_adapter(access) -> None:
    client, book, runtime = access
    client.post("/api/providers/connect", json={"catalog_id": "deepseek", "api_key": "key"})
    client.post("/api/providers/connect", json={"catalog_id": "ollama"})
    chosen = client.put("/api/model-selection", json={"provider": "ollama", "model": "qwen3:4b"})
    assert chosen.status_code == 200
    assert chosen.json()["provider"] == "ollama"
    loaded = load_server_config(book.config_path)
    assert loaded.active_provider == "ollama" and loaded.active_model == "qwen3:4b"
    adapter = runtime.worker.runner.adapter()
    assert adapter.model == "qwen3:4b"
    provider = adapter.factory()
    assert provider._config.base_url == "http://localhost:11434/v1"
    assert provider._config.omit_authorization_header is True
    assert provider._config.infer_api_from_model is False
    assert provider._config.timeout_seconds == 180
    assert adapter.timeout == 600


def test_unavailable_selection_does_not_fall_back_to_another_vendor(access) -> None:
    client, book, runtime = access
    response = client.put("/api/model-selection", json={"provider": "missing", "model": "x"})
    assert response.status_code == 404
    assert not book.config_path.exists()
    client.post("/api/providers/connect", json={"catalog_id": "ollama"})
    client.put("/api/model-selection", json={"provider": "ollama", "model": "gpt-oss:20b"})
    assert runtime.worker.runner.adapter().model == "gpt-oss:20b"
    empty = client.put("/api/model-selection", json={"provider": "ollama", "model": "  "})
    assert empty.status_code == 422
    assert client.get("/api/model-selection").json()["model"] == "gpt-oss:20b"
    assert client.delete("/api/providers/ollama").status_code == 409


def test_reconnect_preserves_secret_only_for_same_preset(access) -> None:
    client, book, _ = access
    client.post("/api/providers/connect", json={"catalog_id": "deepseek", "api_key": "secret"})
    old = book.provider("deepseek")
    assert client.post("/api/providers/connect", json={"catalog_id": "deepseek"}).status_code == 200
    assert book.profile("deepseek").resolved_api_key() == "secret"
    client.post("/api/providers/connect", json={"catalog_id": "deepseek", "api_key": "new-secret"})
    assert book.provider("deepseek") is not old
    assert (
        client.post("/api/providers/connect", json={"catalog_id": "zhipu-coding"}).status_code
        == 422
    )
    assert book.profile("zhipu-coding") is None


def test_model_choices_keep_catalog_on_unsupported_discovery_but_surface_auth_failure(
    tmp_path,
) -> None:
    status = {"code": 404}
    transport = httpx.MockTransport(lambda request: httpx.Response(status["code"]))
    book = ProviderBook(
        ServerConfig(providers=[ProviderProfile(name="default", base_url="https://example.com")]),
        boot_name="default",
        boot_model="",
        config_path=tmp_path / "providers.yaml",
    )
    client = TestClient(create_app(stub_registry(tmp_path), book=book, model_transport=transport))
    client.post("/api/providers/connect", json={"catalog_id": "zhipu-coding", "api_key": "secret"})
    response = client.get("/api/providers/zhipu-coding/model-choices")
    assert response.status_code == 200
    assert response.json()["source"] == "catalog"
    assert response.json()["models"]
    assert response.json()["verified"] is False
    status["code"] = 401
    assert client.get("/api/providers/zhipu-coding/model-choices").status_code == 422


def test_catalog_separates_api_plan_and_local_sources(access) -> None:
    client, _, _ = access
    entries = {p["name"]: p for p in client.get("/api/provider-catalog").json()["catalog"]}
    assert entries["zhipu-coding"]["category"] == "coding_plan"
    assert entries["zhipu"]["base_url"] != entries["zhipu-coding"]["base_url"]
    assert entries["ollama"]["category"] == "local"
    assert entries["ollama"]["no_auth"] is True
    assert entries["lmstudio"]["base_url"] == "http://localhost:1234/v1"


def test_cloud_provider_keeps_existing_timeout_budget(access) -> None:
    client, _, runtime = access
    client.post("/api/providers/connect", json={"catalog_id": "deepseek", "api_key": "key"})
    client.put("/api/model-selection", json={"provider": "deepseek", "model": "deepseek-flash"})
    adapter = runtime.worker.runner.adapter()
    assert adapter.timeout == 120
    assert adapter.factory()._config.timeout_seconds == 45


def test_selected_credentials_expiring_does_not_switch_vendor(access) -> None:
    from hastings.application.errors import EnvironmentFailure

    client, book, runtime = access
    client.post("/api/providers/connect", json={"catalog_id": "deepseek", "api_key": "key"})
    client.post("/api/providers/connect", json={"catalog_id": "ollama"})
    client.put("/api/model-selection", json={"provider": "deepseek", "model": "deepseek-flash"})
    profile = book.profile("deepseek").model_copy(update={"api_key": SecretStr("")})
    book.update_config(
        book.config.model_copy(update={"providers": [profile, book.profile("ollama")]})
    )
    with pytest.raises(EnvironmentFailure, match="不会自动换用"):
        runtime.worker.runner.adapter()
    assert client.get("/api/model-selection").json()["provider"] == "deepseek"
    assert client.get("/api/model-selection").json()["available"] is False


def test_profile_updates_preserve_selection_and_invalid_save_keeps_previous(
    access, monkeypatch
) -> None:
    client, book, _ = access
    client.post("/api/providers/connect", json={"catalog_id": "ollama"})
    client.put("/api/model-selection", json={"provider": "ollama", "model": "qwen3:4b"})
    client.put(
        "/api/providers/custom", json={"base_url": "http://localhost:1234/v1", "no_auth": True}
    )
    assert load_server_config(book.config_path).active_provider == "ollama"
    previous = book.config_path.read_bytes()

    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr("hastings.server.model_access.save_server_config", fail)
    response = client.put("/api/model-selection", json={"provider": "custom", "model": "local"})
    assert response.status_code == 500
    assert book.config.active_provider == "ollama"
    assert book.config_path.read_bytes() == previous


def test_environment_connection_uses_only_explicit_source_variable(access, monkeypatch) -> None:
    client, book, _ = access
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-secret")
    response = client.post(
        "/api/providers/connect",
        json={
            "catalog_id": "deepseek",
            "api_key": "discarded",
            "use_environment": True,
        },
    )
    assert response.status_code == 200
    assert book.profile("deepseek").api_key.get_secret_value() == ""
    assert book.profile("deepseek").resolved_api_key() == "environment-secret"
    assert "environment-secret" not in response.text
