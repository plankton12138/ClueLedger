"""供应商预设、连接配置兼容与选择规则（V04-9A）。

契约：
- 预设只描述「怎么接」；连接保存完整地址与协议，运行时不再按供应商名推断。
- 旧档案（只有 default_model、没有 models/display_name）照常读取，读时兼容、写时落新字段。
- 空连接目录是合法状态：删掉最后一个连接后仍能启动，选择保持「未选」。
- 选择优先级：界面选择 > CLI ``--model`` > 配置 explicit/none > auto 启动默认。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hastings.server.provider_catalog import PRESETS, preset
from hastings.server.provider_config import (
    ProviderBook,
    ProviderProfile,
    ServerConfig,
    build_provider,
    config_revision,
    load_server_config,
    save_server_config,
)
from hastings.server.provider_runtime import (
    MANAGED_APIS,
    ModelSnapshot,
    is_loopback_url,
    snapshot,
)
from tau_ai.anthropic import AnthropicProvider
from tau_ai.google import GoogleGenerativeAIProvider
from tau_ai.openai_compatible import OpenAICompatibleProvider

LEGACY = """providers:
- name: deepseek
  base_url: https://api.deepseek.com/v1
  api_key: sk-legacy
  default_model: deepseek-chat
"""


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _book(
    config: ServerConfig, *, boot: str = "", boot_model: str = "", cli: str = ""
) -> ProviderBook:
    return ProviderBook(config, boot_name=boot, boot_model=boot_model, cli_model=cli)


def _profile(name: str, **overrides: object) -> ProviderProfile:
    fields: dict[str, object] = {
        "name": name,
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "sk-" + name,
        "default_model": "m1",
    }
    fields.update(overrides)
    return ProviderProfile(**fields)  # type: ignore[arg-type]


# -- 供应商目录 -------------------------------------------------------------


def test_catalog_covers_planned_presets_and_only_implemented_apis() -> None:
    ids = {item.id for item in PRESETS}

    assert {
        "openai",
        "deepseek",
        "qwen",
        "moonshot",
        "zhipu",
        "openrouter",
        "anthropic",
        "google",
    } <= ids
    assert "custom" in ids
    for item in PRESETS:
        assert set(item.apis) <= set(MANAGED_APIS)
        assert item.api in item.apis
        if not item.custom:
            # 预设地址必须显式给出，不靠运行时拼接域名补齐。
            assert item.base_url.startswith("https://")


def test_region_specific_provider_exposes_region_options() -> None:
    qwen = preset("qwen")

    assert qwen is not None
    assert [region.id for region in qwen.regions] == ["beijing", "singapore"]
    assert qwen.base_url == qwen.regions[0].base_url
    assert all(region.base_url.startswith("https://") for region in qwen.regions)


def test_unknown_catalog_id_is_read_as_custom(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "config.yaml",
        "providers:\n- name: mine\n  catalog_id: 某个已下线的预设\n"
        "  base_url: https://example.invalid/v1\n  api_key: sk-x\n  default_model: m\n",
    )

    config = load_server_config(path)

    assert config.providers[0].catalog_id == "某个已下线的预设"
    assert preset(config.providers[0].catalog_id) is None


# -- 旧配置与空配置 ---------------------------------------------------------


def test_legacy_profile_reads_models_from_default_model(tmp_path: Path) -> None:
    config = load_server_config(_write(tmp_path / "config.yaml", LEGACY))

    profile = config.providers[0]
    assert profile.models == []
    assert profile.models_for() == ["deepseek-chat"]
    assert profile.title == "deepseek"
    assert config.selection_mode == "auto"


def test_empty_provider_list_is_a_valid_saved_state(tmp_path: Path) -> None:
    """删掉最后一个连接后磁盘是空列表；重启仍然是「没有连接」，不是损坏。"""
    path = _write(tmp_path / "config.yaml", "providers: []\nselection_mode: none\n")

    config = load_server_config(path)
    save_server_config(config, path)

    assert config.providers == []
    assert load_server_config(path).selection_mode == "none"


def test_legacy_active_provider_is_read_as_explicit(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "config.yaml",
        LEGACY + "active_provider: deepseek\nactive_model: deepseek-chat\n",
    )

    config = load_server_config(path)

    assert config.selection_mode == "explicit"


def test_new_fields_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    config = ServerConfig(
        providers=[
            _profile(
                "conn_a",
                display_name="我的 DeepSeek",
                catalog_id="deepseek",
                api="openai-responses",
                models=["m1", "m2"],
                default_model="m2",
            )
        ],
        active_provider="conn_a",
        active_model="m1",
        selection_mode="explicit",
    )

    save_server_config(config, path)
    reloaded = load_server_config(path)

    profile = reloaded.providers[0]
    assert profile.display_name == "我的 DeepSeek"
    assert profile.models == ["m1", "m2"]
    assert profile.api == "openai-responses"
    assert (reloaded.active_provider, reloaded.active_model) == ("conn_a", "m1")


def test_cleared_selection_survives_save_and_reload(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    config = ServerConfig(providers=[_profile("a")], selection_mode="none")

    save_server_config(config, path)

    assert "active_provider" not in path.read_text(encoding="utf-8")
    assert load_server_config(path).selection_mode == "none"


# -- 选择规则 ---------------------------------------------------------------


def test_explicit_selection_wins_over_startup_default() -> None:
    config = ServerConfig(
        providers=[_profile("a"), _profile("b")],
        active_provider="b",
        active_model="m1",
        selection_mode="explicit",
    )
    book = _book(config, boot="a", boot_model="m1")

    selection = book.selection()

    assert (selection.connection_id, selection.source) == ("b", "saved")


def test_auto_mode_falls_back_to_first_available_connection() -> None:
    config = ServerConfig(providers=[_profile("a")])

    selection = _book(config).selection()

    assert (selection.connection_id, selection.model, selection.source) == ("a", "m1", "automatic")
    assert selection.available is True


def test_cli_model_overrides_saved_model_but_keeps_saved_connection() -> None:
    config = ServerConfig(
        providers=[_profile("a"), _profile("b")],
        active_provider="b",
        active_model="m1",
        selection_mode="explicit",
    )
    book = _book(config, boot="a", boot_model="cli-model", cli="cli-model")

    selection = book.selection()

    assert (selection.connection_id, selection.model, selection.source) == (
        "b",
        "cli-model",
        "startup",
    )


def test_ui_selection_replaces_cli_override() -> None:
    config = ServerConfig(providers=[_profile("a"), _profile("b")])
    book = _book(config, boot="a", boot_model="cli-model", cli="cli-model")

    book.apply_selection("b", "m1")

    selection = book.selection()
    assert (selection.connection_id, selection.model, selection.source) == ("b", "m1", "saved")


def test_cleared_selection_stays_cleared_instead_of_auto_picking() -> None:
    config = ServerConfig(providers=[_profile("a")], selection_mode="none")

    selection = _book(config).selection()

    assert selection.source == "none"
    assert selection.connection_id is None
    assert selection.available is False
    assert selection.reason


def test_explicit_selection_without_key_stays_selected_but_unavailable() -> None:
    """失效凭据只提示修复，不静默换到别的连接。"""
    profile = _profile("a", api_key="")
    config = ServerConfig(
        providers=[profile, _profile("b")],
        active_provider="a",
        active_model="m1",
        selection_mode="explicit",
    )

    selection = _book(config).selection()

    assert (selection.connection_id, selection.available) == ("a", False)
    assert "凭据" in selection.reason


def test_auto_mode_skips_connection_without_key() -> None:
    config = ServerConfig(providers=[_profile("a", api_key=""), _profile("b")])

    selection = _book(config).selection()

    assert (selection.connection_id, selection.source) == ("b", "automatic")


# -- 配置版本 ---------------------------------------------------------------


def test_revision_tracks_content_not_timestamp(tmp_path: Path) -> None:
    path = _write(tmp_path / "config.yaml", LEGACY)
    first = config_revision(path)

    path.touch()
    assert config_revision(path) == first

    _write(path, LEGACY + "# 改了一行\n")
    assert config_revision(path) != first


def test_revision_of_missing_file_is_stable(tmp_path: Path) -> None:
    assert config_revision(tmp_path / "absent.yaml") == config_revision(tmp_path / "absent.yaml")


# -- 协议构造 ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("api", "expected"),
    [
        ("openai-completions", OpenAICompatibleProvider),
        ("openai-responses", OpenAICompatibleProvider),
        ("anthropic-messages", AnthropicProvider),
        ("google-generative-ai", GoogleGenerativeAIProvider),
    ],
)
def test_each_api_builds_its_own_adapter(api: str, expected: type[object]) -> None:
    provider = build_provider(_profile("a", api=api))

    assert isinstance(provider, expected)
    assert callable(provider.aclose)


def test_provider_resolves_from_connection_fields_not_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-environment")
    provider = build_provider(_profile("a", api_key="sk-from-config"))

    assert provider._config.api_key == "sk-from-config"  # type: ignore[attr-defined]


def test_loopback_detection() -> None:
    assert is_loopback_url("http://localhost:11434/v1")
    assert is_loopback_url("http://127.0.0.1:8000")
    assert not is_loopback_url("https://api.deepseek.com/v1")


def test_snapshot_describes_only_public_fields() -> None:
    snap = snapshot(_profile("a", display_name="我的连接"), "m1")

    described = snap.describe()
    assert described == {
        "connection_id": "a",
        "connection_name": "我的连接",
        "model": "m1",
    }
    assert "sk-a" not in str(snap)


@pytest.mark.anyio
async def test_snapshot_closes_its_instance() -> None:
    closed: list[bool] = []

    class _Closable:
        async def aclose(self) -> None:
            closed.append(True)

    snap = ModelSnapshot(
        connection_id="a",
        display_name="a",
        model="m1",
        provider=_Closable(),  # type: ignore[arg-type]
    )

    await snap.aclose()

    assert closed == [True]
