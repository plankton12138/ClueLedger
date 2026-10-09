"""内建 provider 目录测试（P7c T15）。

目录是登录式模型接入的数据底座：条目字段完整性直接决定登录表单的预填
质量——逐条钉住 name 唯一、base_url https、default_model 在 models 内、
公开字段非空。目录是纯数据（无密钥）：任何条目都不得携带 api_key 字样。
"""

from hastings.provider_catalog import PROVIDER_CATALOG, catalog_entry


def test_catalog_covers_expected_providers() -> None:
    names = [e.name for e in PROVIDER_CATALOG]
    assert "deepseek" in names
    assert "zhipu" in names
    assert "openai" in names


def test_catalog_names_unique() -> None:
    names = [e.name for e in PROVIDER_CATALOG]
    assert len(names) == len(set(names))


def test_every_entry_has_complete_public_fields() -> None:
    """公开字段非空：缺一项，登录表单就有空预填。"""
    for entry in PROVIDER_CATALOG:
        assert entry.display_name, entry.name
        assert entry.base_url.startswith("https://"), entry.name
        assert entry.models, entry.name
        assert entry.default_model in entry.models, entry.name
        assert entry.api_key_env, entry.name
        assert entry.key_url.startswith("https://"), entry.name


def test_catalog_entry_lookup() -> None:
    assert catalog_entry("deepseek") is not None
    assert catalog_entry("no-such-provider") is None
