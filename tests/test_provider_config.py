"""服务级 provider 档案配置测试（P6 T1，D1）。

逐条钉住：
- 缺文件/空文件 → env 单档案回落（name="default"，P1 现状零破坏）；
- 结构性损坏（根节点非映射、缺字段、未知字段、空列表、档案名重复）
  → ProviderConfigError 三要素（哪里错/为什么错/怎样算对）；
- key 解析：直填优先于 env 引用；key 只进不出（mask_key 尾 4 位、
  SecretStr repr 不泄露）。
"""

from pathlib import Path

import pytest
from pydantic import SecretStr

from hastings.server.provider_config import (
    ProviderBook,
    ProviderConfigError,
    ProviderProfile,
    ServerConfig,
    build_provider,
    env_fallback_config,
    load_server_config,
    mask_key,
    save_server_config,
)

CONFIG_YAML = """\
providers:
  - name: deepseek
    base_url: https://api.deepseek.com/v1
    api_key: sk-direct-12345678
    default_model: deepseek-chat
  - name: openrouter
    base_url: https://openrouter.ai/api/v1
    api_key_env: OPENROUTER_API_KEY
    default_model: anthropic/claude-sonnet-4
"""


def _write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# --- 加载：env 回落（现状兼容） ---


def test_missing_file_falls_back_to_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-key-123456")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example.com/v1/")
    monkeypatch.setenv("HASTINGS_MODEL", "env-model")

    config = load_server_config(tmp_path / "no-such.yaml")

    assert len(config.providers) == 1
    profile = config.providers[0]
    assert profile.name == "default"
    assert profile.base_url == "https://api.example.com/v1"  # 尾斜杠剥掉
    assert profile.default_model == "env-model"
    assert profile.available
    assert profile.resolved_api_key() == "sk-env-key-123456"


def test_env_fallback_without_base_url_uses_openai_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    config = env_fallback_config()
    assert config.providers[0].base_url == "https://api.openai.com/v1"


def test_empty_file_falls_back_to_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """空文件 = 未配置 → env 回落（与 settings.yaml 空文件惯例一致）。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    path = _write_config(tmp_path, "")

    config = load_server_config(path)

    assert [p.name for p in config.providers] == ["default"]


def test_missing_key_makes_profile_unavailable(tmp_path: Path) -> None:
    """api_key 与 api_key_env 皆缺 → 档案保留但 available=False（置灰不阻塞）。"""
    path = _write_config(
        tmp_path,
        "providers:\n"
        "  - name: no-key\n"
        "    base_url: https://api.example.com/v1\n"
        "    default_model: some-model\n",
    )

    config = load_server_config(path)

    assert config.providers[0].available is False
    assert config.providers[0].resolved_api_key() == ""


# --- key 解析与脱敏 ---


def test_direct_api_key_takes_priority_over_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """D1：直填优先；api_key_env 引用的 env 即使存在也不覆盖直填。"""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-from-env-9999")
    path = _write_config(
        tmp_path,
        "providers:\n"
        "  - name: both\n"
        "    base_url: https://api.example.com/v1\n"
        "    api_key: sk-direct-12345678\n"
        "    api_key_env: OPENROUTER_API_KEY\n",
    )

    config = load_server_config(path)

    assert config.providers[0].resolved_api_key() == "sk-direct-12345678"


def test_api_key_env_resolves_from_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-from-env-9999")
    path = _write_config(
        tmp_path,
        "providers:\n"
        "  - name: openrouter\n"
        "    base_url: https://openrouter.ai/api/v1\n"
        "    api_key_env: OPENROUTER_API_KEY\n",
    )

    config = load_server_config(path)

    assert config.providers[0].resolved_api_key() == "sk-from-env-9999"
    assert config.providers[0].available


def test_mask_key_shows_only_last_four() -> None:
    assert mask_key("sk-1234567890abcd") == "…abcd"
    assert mask_key("short") == "…"  # 短 key 连尾 4 位都不给（防整段泄露）
    assert mask_key("") == ""


def test_secret_repr_does_not_leak_key(tmp_path: Path) -> None:
    """key 只进不出：模型 repr / 日志不得出现明文 key。"""
    path = _write_config(tmp_path, CONFIG_YAML)

    config = load_server_config(path)

    assert "sk-direct-12345678" not in repr(config)
    assert "sk-direct-12345678" not in repr(config.providers[0])
    assert config.providers[0].api_key == SecretStr("sk-direct-12345678")


# --- 结构性损坏：三要素报错 ---


def test_non_mapping_root_raises_with_three_element_message(tmp_path: Path) -> None:
    path = _write_config(tmp_path, "- just\n- a\n- list\n")

    with pytest.raises(ProviderConfigError) as exc_info:
        load_server_config(path)

    message = str(exc_info.value)
    assert "根节点" in message
    assert "list" in message
    assert "为什么错" in message
    assert "怎样算对" in message


@pytest.mark.parametrize(
    ("yaml_text", "bad_field"),
    [
        (
            "providers:\n  - name: incomplete\n    default_model: m\n",
            "base_url",
        ),  # 缺 base_url
        (
            "providers:\n  - base_url: https://api.example.com/v1\n",
            "name",
        ),  # 缺 name
        (
            "providers:\n"
            "  - name: typo\n"
            "    base_url: https://api.example.com/v1\n"
            "    api_ky: sk-xxx\n",  # 拼错字段 → extra=forbid 拒绝
            "api_ky",
        ),
    ],
)
def test_invalid_profiles_raise_with_field_location(
    tmp_path: Path, yaml_text: str, bad_field: str
) -> None:
    """字段级定位原样透传：错误消息点名 providers[0].<field>。"""
    path = _write_config(tmp_path, yaml_text)

    with pytest.raises(ProviderConfigError) as exc_info:
        load_server_config(path)

    message = str(exc_info.value)
    assert bad_field in message
    assert "为什么错" in message
    assert "怎样算对" in message


def test_empty_providers_list_raises(tmp_path: Path) -> None:
    path = _write_config(tmp_path, "providers: []\n")

    with pytest.raises(ProviderConfigError, match="列表为空"):
        load_server_config(path)


def test_duplicate_profile_names_raise(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        "providers:\n"
        "  - name: dup\n"
        "    base_url: https://a.example.com/v1\n"
        "  - name: dup\n"
        "    base_url: https://b.example.com/v1\n",
    )

    with pytest.raises(ProviderConfigError, match="重复"):
        load_server_config(path)


def test_unknown_root_field_rejected(tmp_path: Path) -> None:
    """根级未知字段拒绝（拼写错误早失败，不静默忽略）。"""
    path = _write_config(tmp_path, CONFIG_YAML + "unknown_root: 1\n")

    with pytest.raises(ProviderConfigError, match="unknown_root"):
        load_server_config(path)


# --- 写回（P7 T12 档案 UI 化） ---


def test_save_server_config_roundtrip_preserves_profiles(tmp_path: Path) -> None:
    """写回 → 重读：字段逐值一致，key 明文落盘（文件存储边界与手写一致）。"""
    config = ServerConfig(
        providers=[
            ProviderProfile(
                name="deepseek",
                base_url="https://api.deepseek.com/v1",
                api_key="sk-direct-12345678",
                default_model="deepseek-chat",
            ),
            ProviderProfile(
                name="openrouter",
                base_url="https://openrouter.ai/api/v1",
                api_key_env="OPENROUTER_API_KEY",
            ),
        ]
    )
    path = tmp_path / "config.yaml"

    save_server_config(config, path)
    reloaded = load_server_config(path)

    assert [p.name for p in reloaded.providers] == ["deepseek", "openrouter"]
    assert reloaded.providers[0].resolved_api_key() == "sk-direct-12345678"
    assert reloaded.providers[0].default_model == "deepseek-chat"
    assert reloaded.providers[1].api_key_env == "OPENROUTER_API_KEY"
    assert reloaded.providers[1].resolved_api_key() == ""  # env 未设置 → 空


def test_save_server_config_empty_fields_tolerated_on_reload(tmp_path: Path) -> None:
    """空 api_key / 空 default_model 写成空串，重读不炸（与手写文件同一宽容度）。"""
    config = ServerConfig(
        providers=[ProviderProfile(name="bare", base_url="https://api.example.com/v1")]
    )
    path = tmp_path / "config.yaml"

    save_server_config(config, path)

    reloaded = load_server_config(path)
    assert reloaded.providers[0].available is False
    assert reloaded.providers[0].default_model == ""


def test_update_config_prunes_removed_instance_cache() -> None:
    """update_config 换磁盘事实并清理已删档案的实例缓存（仍用档案保留）。"""
    book = ProviderBook(
        ServerConfig(providers=[_profile("keep"), _profile("gone")]),
        boot_name="keep",
        boot_model="",
    )
    book.provider("keep")
    book.provider("gone")

    book.update_config(ServerConfig(providers=[_profile("keep")]))

    assert book.profile("gone") is None
    with pytest.raises(ProviderConfigError, match="不存在"):
        book.provider("gone")
    # 仍用档案的实例缓存保留（同对象复用，httpx client 不重建）
    assert book.provider("keep") is book.provider("keep")


def _profile(name: str) -> ProviderProfile:
    return ProviderProfile(
        name=name, base_url=f"https://{name}.example.com/v1", api_key=f"sk-{name}-12345678"
    )


# --- provider 构造（T2 跨档案切换复用同一入口） ---


def test_build_provider_uses_profile_endpoint_and_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-from-env-9999")
    config = ServerConfig.model_validate(
        {
            "providers": [
                {
                    "name": "openrouter",
                    "base_url": "https://openrouter.ai/api/v1/",
                    "api_key_env": "OPENROUTER_API_KEY",
                }
            ]
        }
    )

    provider = build_provider(config.providers[0])

    assert provider._config.base_url == "https://openrouter.ai/api/v1"  # 尾斜杠剥掉
    assert provider._config.api_key == "sk-from-env-9999"


# --- no_auth 免密端点（本地模型如 Ollama，D11） ---


def test_no_auth_profile_available_without_key() -> None:
    profile = ProviderProfile(name="ollama", base_url="http://localhost:11434/v1", no_auth=True)

    assert profile.available is True
    assert profile.resolved_api_key() == ""


def test_keyless_profile_without_no_auth_unavailable() -> None:
    profile = ProviderProfile(name="broken", base_url="https://api.example.com/v1")

    assert profile.available is False


def test_no_auth_round_trip_through_yaml(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    config = ServerConfig(
        providers=[
            ProviderProfile(
                name="ollama",
                base_url="http://localhost:11434/v1",
                default_model="qwen3:4b",
                no_auth=True,
            )
        ]
    )

    save_server_config(config, path)
    loaded = load_server_config(path)

    assert loaded.providers[0].no_auth is True
    assert loaded.providers[0].available is True


def test_no_auth_false_not_written_to_disk(tmp_path: Path) -> None:
    """no_auth=False 不落盘——旧 config.yaml 格式零扰动。"""
    path = tmp_path / "config.yaml"

    save_server_config(ServerConfig(providers=[_profile("deepseek")]), path)

    assert "no_auth" not in path.read_text(encoding="utf-8")


def test_no_auth_provider_omits_authorization_header() -> None:
    """空 key 的免密档案不构造 Authorization 头——httpx 会以
    Illegal header value b'Bearer ' 拒绝空 Bearer 值的请求。"""
    provider = build_provider(
        ProviderProfile(name="ollama", base_url="http://localhost:11434/v1", no_auth=True)
    )

    assert provider._config.omit_authorization_header is True


def test_keyed_provider_keeps_authorization_header() -> None:
    provider = build_provider(
        ProviderProfile(
            name="deepseek",
            base_url="https://api.deepseek.com/v1",
            api_key="sk-direct-12345678",
        )
    )

    assert provider._config.omit_authorization_header is False
