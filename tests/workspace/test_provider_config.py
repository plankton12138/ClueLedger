"""服务级模型档案（0.4）。

契约：模型来源只有 ``config.yaml`` 一个入口——一个档案就是端点 + key + 默认模型。
应用不读进程环境里的 key / 端点 / 模型名；缺文件等于「还没配置」，服务照常启动，
发送消息的那一轮明确回报未配置。旧版的 ``api_key_env`` 字段被拒绝并给出迁移说明。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hastings.server.provider_config import (
    ProviderConfigError,
    ProviderProfile,
    load_server_config,
    save_server_config,
)


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_missing_file_means_no_providers(tmp_path: Path) -> None:
    config = load_server_config(tmp_path / "absent.yaml")

    assert config.providers == []


def test_empty_file_means_no_providers(tmp_path: Path) -> None:
    config = load_server_config(_write(tmp_path / "config.yaml", ""))

    assert config.providers == []


def test_environment_variables_never_fill_a_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """档案没写 key 就是没 key——设了同名环境变量也补不上。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-environment")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("HASTINGS_MODEL", "env-model")
    path = _write(
        tmp_path / "config.yaml",
        "providers:\n"
        "- name: deepseek\n"
        "  base_url: https://api.deepseek.com/v1\n"
        "  api_key: ''\n"
        "  default_model: deepseek-flash\n",
    )

    config = load_server_config(path)

    profile = config.providers[0]
    assert profile.default_model == "deepseek-flash"
    assert profile.resolved_api_key() == ""
    assert profile.available is False


def test_legacy_api_key_env_field_is_rejected_with_migration_hint(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "config.yaml",
        "providers:\n"
        "- name: deepseek\n"
        "  base_url: https://api.deepseek.com/v1\n"
        "  api_key_env: OPENAI_API_KEY\n",
    )

    with pytest.raises(ProviderConfigError) as raised:
        load_server_config(path)

    message = str(raised.value)
    assert "api_key_env" in message
    assert "['deepseek']" in message
    assert "建议" in message
    assert "api_key" in message


def test_direct_key_and_credential_ref_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    config = load_server_config(
        _write(
            path,
            "providers:\n"
            "- name: deepseek\n"
            "  base_url: https://api.deepseek.com/v1\n"
            "  api_key: sk-direct\n"
            "  default_model: deepseek-flash\n",
        )
    )

    save_server_config(config, path)

    text = path.read_text(encoding="utf-8")
    assert "api_key_env" not in text
    reloaded = load_server_config(path)
    assert reloaded.providers[0].resolved_api_key() == "sk-direct"
    assert reloaded.providers[0].available is True


def test_profile_with_only_credential_ref_is_not_available_until_materialized() -> None:
    profile = ProviderProfile(
        name="deepseek",
        base_url="https://api.deepseek.com/v1",
        credential_ref="hastings:provider:deepseek",
        default_model="deepseek-flash",
    )

    assert profile.resolved_api_key() == ""
    assert profile.available is False
