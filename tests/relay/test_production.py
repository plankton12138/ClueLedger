"""v1.4 P5：支付服务生产配置校验。"""

from __future__ import annotations

from pathlib import Path

import pytest

from hastings.relay.config import RelayConfigError, load_settings


def _production_env(tmp_path: Path) -> dict[str, str]:
    return {
        "ALIPAY_APP_ID": "2026000000000000",
        "ALIPAY_APP_PRIVATE_KEY": "private",
        "ALIPAY_PUBLIC_KEY": "public",
        "ALIPAY_SELLER_ID": "2088000000000000",
        "ALIPAY_GATEWAY": "https://openapi.alipay.com/gateway.do",
        "RELAY_DB_PATH": str(tmp_path / "shared.sqlite3"),
        "RELAY_PUBLIC_BASE_URL": "https://clue.example.com",
        "RELAY_SITE_URL": "https://clue.example.com",
        "RELAY_ADMIN_TOKEN": "admin",
        "RELAY_INTERNAL_SECRET": "internal",
        "PLATFORM_MODEL_API_KEY": "platform",
    }


def test_production_refuses_sandbox_fallback(tmp_path: Path) -> None:
    with pytest.raises(RelayConfigError, match="生产模式"):
        load_settings(root=tmp_path, environ={}, production=True)


def test_production_requires_explicit_database_and_secrets(tmp_path: Path) -> None:
    env = _production_env(tmp_path)
    env.pop("RELAY_DB_PATH")
    with pytest.raises(RelayConfigError, match="RELAY_DB_PATH"):
        load_settings(root=tmp_path, environ=env, production=True)
    env = _production_env(tmp_path)
    env.pop("RELAY_ADMIN_TOKEN")
    with pytest.raises(RelayConfigError, match="RELAY_ADMIN_TOKEN"):
        load_settings(root=tmp_path, environ=env, production=True)
    env = _production_env(tmp_path)
    env.pop("RELAY_INTERNAL_SECRET")
    with pytest.raises(RelayConfigError, match="RELAY_INTERNAL_SECRET"):
        load_settings(root=tmp_path, environ=env, production=True)


def test_production_accepts_https_and_secret_files(tmp_path: Path) -> None:
    env = _production_env(tmp_path)
    admin_file = tmp_path / "admin.token"
    admin_file.write_text("admin-from-file", encoding="utf-8")
    env.pop("RELAY_ADMIN_TOKEN")
    env["RELAY_ADMIN_TOKEN_FILE"] = str(admin_file)
    settings = load_settings(root=tmp_path, environ=env, production=True)
    assert settings.production is True
    assert settings.sandbox is False
    assert settings.admin_token == "admin-from-file"
    assert settings.platform_enabled is True
    assert settings.database_path == tmp_path / "shared.sqlite3"
