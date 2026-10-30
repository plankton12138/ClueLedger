"""0.9 U1：模型容量的来源与边界。

容量决定两件事：界面上的占比，以及这一轮到底允许多少输入。两者必须来自同一次
解析，否则会出现「显示还能写」但实际请求被拒的情况。

这里只校验解析与预算，不涉及分词与装配。
"""

from __future__ import annotations

import pytest

from hastings.server.model_limits import (
    DEFAULT_PROJECT_INPUT_TOKENS,
    WORKER_OUTPUT_TOKENS,
    ModelCapacity,
    ModelLimitOverride,
    resolve_model_limits,
)
from hastings.server.provider_config import (
    ContextPolicy,
    ProviderBook,
    ProviderProfile,
    ServerConfig,
    load_server_config,
    save_server_config,
)


def profile(
    *,
    catalog_id: str = "",
    metadata: dict[str, ModelCapacity] | None = None,
    **limits: ModelLimitOverride,
) -> ProviderProfile:
    return ProviderProfile(
        name="deepseek",
        base_url="https://api.deepseek.com",
        api="openai-completions",
        default_model="deepseek-chat",
        catalog_id=catalog_id,
        model_limits=dict(limits),
        model_metadata=dict(metadata or {}),
    )


def test_unconfigured_model_reports_unknown_window_without_guessing() -> None:
    """容量未知不填默认窗口：猜一个窗口会让占比看起来可信。

    但预算不能因此停摆——有效上限退到项目策略，界面与请求检查都还能工作。
    """
    limits = resolve_model_limits(profile(), "deepseek-chat")

    assert limits.context_window_tokens is None
    assert limits.max_input_tokens is None
    assert limits.model_input_limit_tokens is None
    assert limits.input_limit() == DEFAULT_PROJECT_INPUT_TOKENS
    assert limits.input_limit_source == "project"
    assert limits.source == "unknown"
    # 回复预留与运行时实际请求上限一致，未知窗口时也不假装成 8K。
    assert limits.output_reserve_tokens == WORKER_OUTPUT_TOKENS
    # 项目预算绝不写进模型窗口。
    assert limits.project_input_limit_tokens == DEFAULT_PROJECT_INPUT_TOKENS


def test_reported_capacity_supplies_window_and_reserve() -> None:
    limits = resolve_model_limits(
        profile(
            metadata={
                "deepseek-chat": ModelCapacity(
                    context_window_tokens=131_072,
                    max_output_tokens=8_192,
                )
            }
        ),
        "deepseek-chat",
    )

    assert limits.source == "metadata"
    assert limits.context_window_tokens == 131_072
    # 未显式配置回复预留时沿用运行时上限，而不是模型的最大输出。
    assert limits.output_reserve_tokens == WORKER_OUTPUT_TOKENS
    assert limits.input_limit() == 131_072 - WORKER_OUTPUT_TOKENS - limits.safety_tokens


def test_smaller_explicit_input_limit_wins() -> None:
    """有些平台限制的是输入长度而不是窗口，两者取更严的那个。"""
    limits = resolve_model_limits(
        profile(
            metadata={
                "deepseek-chat": ModelCapacity(
                    context_window_tokens=131_072,
                    max_input_tokens=32_000,
                )
            }
        ),
        "deepseek-chat",
    )

    assert limits.input_limit() == 32_000


def test_legacy_manual_budget_overrides_are_ignored() -> None:
    limits = resolve_model_limits(
        profile(
            catalog_id="deepseek",
            **{
                "deepseek-chat": ModelLimitOverride(
                    context_window_tokens=64_000,
                    max_output_tokens=8_192,
                    output_reserve_tokens=8_192,
                    safety_tokens=1_000,
                )
            },
        ),
        "deepseek-chat",
    )
    assert limits.source == "catalog"
    assert limits.context_window_tokens == 131_072
    assert limits.output_reserve_tokens == WORKER_OUTPUT_TOKENS
    assert limits.safety_tokens != 1_000


def test_limits_are_per_exact_model_not_per_prefix() -> None:
    """名称前缀相同不代表容量相同；未配置的模型就是未知。"""
    provider = profile(metadata={"deepseek-chat": ModelCapacity(context_window_tokens=131_072)})

    configured = resolve_model_limits(provider, "deepseek-chat")
    sibling = resolve_model_limits(provider, "deepseek-chat-v2")

    assert configured.source == "metadata"
    assert sibling.source == "unknown"
    assert sibling.context_window_tokens is None


def test_catalog_supplies_capacity_for_known_official_model() -> None:
    """常见官方模型不必手填：目录按预设与精确模型给出窗口与输出上限。"""
    limits = resolve_model_limits(profile(catalog_id="deepseek"), "deepseek-chat")

    assert limits.source == "catalog"
    assert limits.context_window_tokens == 131_072
    assert limits.max_output_tokens == 8_192
    assert limits.input_limit() == 131_072 - WORKER_OUTPUT_TOKENS - limits.safety_tokens


def test_catalog_does_not_apply_to_custom_relay_or_sibling_models() -> None:
    """自定义中转可能改写窗口；目录也不按名称前缀外推。"""
    relay = resolve_model_limits(profile(catalog_id="custom"), "deepseek-chat")
    sibling = resolve_model_limits(profile(catalog_id="deepseek"), "deepseek-chat-v2")

    assert relay.source == "unknown"
    assert sibling.source == "unknown"
    assert relay.context_window_tokens is None


# -- 1.0.2 容量目录与项目预算 ------------------------------------------------


def test_official_deepseek_models_get_capacity_from_catalog() -> None:
    """官方模型没有已存元数据时也能从目录拿到窗口与输出上限（A02）。"""
    for model in ("deepseek-flash", "deepseek-v4-pro"):
        limits = resolve_model_limits(profile(catalog_id="deepseek"), model)

        assert limits.source == "catalog"
        assert limits.context_window_tokens == 1_048_576
        assert limits.max_output_tokens == 393_216


def test_small_window_is_not_enlarged_by_project_budget() -> None:
    """小窗口的供应商不会被项目预算放大（A05）：W=16000、R=4096 → S=800，B=11104。"""
    limits = resolve_model_limits(
        profile(metadata={"m": ModelCapacity(context_window_tokens=16_000)}), "m"
    )

    assert limits.safety_tokens == 800
    assert limits.model_input_limit_tokens == 16_000 - WORKER_OUTPUT_TOKENS - 800
    assert limits.input_limit() == 11_104
    assert limits.input_limit_source == "model"


def test_known_input_limit_without_window_keeps_window_unknown() -> None:
    """只已知输入上限时窗口仍未知，有效上限取两者较小值（A06）。"""
    limits = resolve_model_limits(
        profile(metadata={"m": ModelCapacity(max_input_tokens=64_000)}), "m"
    )

    assert limits.context_window_tokens is None
    assert limits.model_input_limit_tokens == 64_000
    assert limits.input_limit() == 64_000
    assert limits.input_limit_source == "model"


def test_project_budget_binds_when_model_capacity_is_larger() -> None:
    """模型能装一百万，项目只开 128K：有效上限是项目值，窗口仍是模型值。"""
    limits = resolve_model_limits(
        profile(catalog_id="deepseek"), "deepseek-flash"
    )

    assert limits.context_window_tokens == 1_048_576
    assert limits.project_input_limit_tokens == DEFAULT_PROJECT_INPUT_TOKENS
    assert limits.input_limit() == DEFAULT_PROJECT_INPUT_TOKENS
    assert limits.input_limit_source == "project"


def test_equal_project_and_model_limits_report_both() -> None:
    limits = resolve_model_limits(
        profile(metadata={"m": ModelCapacity(context_window_tokens=16_000)}),
        "m",
        project_limit=16_000 - WORKER_OUTPUT_TOKENS - 800,
    )

    assert limits.input_limit_source == "both"


def test_project_limit_changes_revision_and_policy_revision() -> None:
    """改项目预算必须让旧估算失效，策略版本也要跟着变。"""
    base = resolve_model_limits(profile(catalog_id="deepseek"), "deepseek-flash")
    tighter = resolve_model_limits(
        profile(catalog_id="deepseek"), "deepseek-flash", project_limit=64_000
    )

    assert base.revision != tighter.revision
    assert base.policy_revision != tighter.policy_revision


@pytest.mark.parametrize("value", [0, -1, True, "128000", 128_000.5])
def test_project_budget_rejects_non_positive_integers(value: object) -> None:
    """bool、字符串、零与负数都不是可用预算（A07）。"""
    with pytest.raises(ValueError, match="CONTEXT_POLICY_INVALID"):
        ContextPolicy(max_input_tokens=value)  # type: ignore[arg-type]


def test_window_smaller_than_reserve_and_safety_is_a_config_error() -> None:
    """已知窗口放不下回复预留与安全余量时明确失败，不静默退到项目预算（A07）。"""
    with pytest.raises(ValueError, match="CONTEXT_POLICY_INVALID"):
        resolve_model_limits(
            profile(metadata={"m": ModelCapacity(context_window_tokens=4_096)}), "m"
        )


def test_provider_reported_capacity_beats_catalog_and_fills_remaining_fields() -> None:
    """服务商返回的窗口优先于目录；目录只补服务商没给的字段。"""
    provider = profile(
        catalog_id="deepseek",
        metadata={"deepseek-chat": ModelCapacity(context_window_tokens=64_000)},
    )

    limits = resolve_model_limits(provider, "deepseek-chat")

    assert limits.source == "metadata"
    assert limits.context_window_tokens == 64_000
    assert limits.max_output_tokens == 8_192


def test_legacy_manual_override_does_not_replace_reported_capacity() -> None:
    """旧手动容量不再影响服务商返回值。"""
    provider = profile(
        catalog_id="deepseek",
        metadata={"deepseek-chat": ModelCapacity(context_window_tokens=64_000)},
        **{"deepseek-chat": ModelLimitOverride(context_window_tokens=32_000)},
    )

    limits = resolve_model_limits(provider, "deepseek-chat")

    assert limits.source == "metadata"
    assert limits.context_window_tokens == 64_000
    assert limits.max_output_tokens == 8_192


def test_legacy_manual_override_does_not_replace_catalog() -> None:
    """旧手动容量不再影响内置目录。"""
    provider = profile(
        catalog_id="deepseek",
        **{"deepseek-chat": ModelLimitOverride(context_window_tokens=100_000)},
    )

    limits = resolve_model_limits(provider, "deepseek-chat")

    assert limits.source == "catalog"
    assert limits.context_window_tokens == 131_072
    assert limits.max_output_tokens == 8_192


def test_revision_changes_when_capacity_changes() -> None:
    """指纹要跟着容量变，否则改完配置仍会复用旧估算。"""
    small = resolve_model_limits(
        profile(metadata={"deepseek-chat": ModelCapacity(context_window_tokens=64_000)}),
        "deepseek-chat",
    )
    large = resolve_model_limits(
        profile(metadata={"deepseek-chat": ModelCapacity(context_window_tokens=131_072)}),
        "deepseek-chat",
    )

    assert small.revision != large.revision


def test_revision_distinguishes_models_on_the_same_connection() -> None:
    provider = profile(
        metadata={
            "deepseek-chat": ModelCapacity(context_window_tokens=131_072),
            "deepseek-reasoner": ModelCapacity(context_window_tokens=131_072),
        }
    )

    assert (
        resolve_model_limits(provider, "deepseek-chat").revision
        != resolve_model_limits(provider, "deepseek-reasoner").revision
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("context_window_tokens", 0),
        ("context_window_tokens", -1),
        ("max_input_tokens", 0),
        ("max_output_tokens", 0),
        ("output_reserve_tokens", 0),
        ("safety_tokens", -1),
    ],
)
def test_non_positive_capacity_is_rejected(field: str, value: int) -> None:
    with pytest.raises(ValueError, match="MODEL_LIMITS_INVALID"):
        ModelLimitOverride(**{field: value})


def test_reserve_larger_than_output_cap_is_rejected() -> None:
    """预留比模型能输出的还多，说明配置本身矛盾。"""
    with pytest.raises(ValueError, match="MODEL_LIMITS_INVALID"):
        ModelLimitOverride(max_output_tokens=4_096, output_reserve_tokens=8_192)


def test_booleans_are_not_accepted_as_capacity() -> None:
    """``True`` 在 Python 里等于 1，当成容量会算出毫无意义的预算。"""
    with pytest.raises(ValueError, match="MODEL_LIMITS_INVALID"):
        ModelLimitOverride(context_window_tokens=True)  # type: ignore[arg-type]


def test_limits_dict_keeps_null_for_unknown_fields() -> None:
    """对外视图用 null 表示未知；项目预算单独一栏，不冒充模型窗口。"""
    limits = resolve_model_limits(profile(), "deepseek-chat")

    assert limits.as_dict() == {
        "connection_id": "deepseek",
        "api": "openai-completions",
        "model": "deepseek-chat",
        "context_window_tokens": None,
        "max_input_tokens": None,
        "max_output_tokens": None,
        "output_reserve_tokens": WORKER_OUTPUT_TOKENS,
        "safety_tokens": 0,
        "project_input_limit_tokens": DEFAULT_PROJECT_INPUT_TOKENS,
        "model_input_limit_tokens": None,
        "input_limit_tokens": DEFAULT_PROJECT_INPUT_TOKENS,
        "input_limit_source": "project",
        "source": "unknown",
        "policy_revision": limits.policy_revision,
        "revision": limits.revision,
    }


# -- 配置读写 ---------------------------------------------------------------

LEGACY = """providers:
- name: deepseek
  base_url: https://api.deepseek.com/v1
  api_key: sk-legacy
  default_model: deepseek-chat
"""

WITH_LIMITS = """providers:
- name: deepseek
  base_url: https://api.deepseek.com/v1
  api_key: sk-legacy
  default_model: deepseek-chat
  models: [deepseek-chat]
  model_limits:
    deepseek-chat:
      context_window_tokens: 131072
      max_output_tokens: 8192
"""


def test_config_without_limits_still_loads(tmp_path) -> None:
    """旧配置没有容量字段，读取不能因此失败。"""
    path = tmp_path / "config.yaml"
    path.write_text(LEGACY, encoding="utf-8")

    config = load_server_config(path)

    assert config.providers[0].model_limits == {}
    assert config.providers[0].model_metadata == {}
    # 没有 context_policy 时取默认值：null 不等于关闭保护。
    assert config.context_policy.max_input_tokens == DEFAULT_PROJECT_INPUT_TOKENS


def test_config_policy_round_trips_and_survives_connection_edit(tmp_path) -> None:
    """项目预算能加载、能保存，编辑连接不会把它丢掉（A15）。"""
    path = tmp_path / "config.yaml"
    path.write_text(LEGACY + "context_policy:\n  max_input_tokens: 64000\n", encoding="utf-8")

    config = load_server_config(path)
    assert config.context_policy.max_input_tokens == 64_000

    edited = config.model_copy(
        update={
            "providers": [
                profile.model_copy(update={"display_name": "改过的名字"})
                for profile in config.providers
            ]
        }
    )
    save_server_config(edited, path)

    reloaded = load_server_config(path)
    assert reloaded.context_policy.max_input_tokens == 64_000
    assert reloaded.providers[0].display_name == "改过的名字"


def test_default_policy_is_written_explicitly(tmp_path) -> None:
    """默认值也落盘：读者不必从缺省推断当前生效的上限。"""
    path = tmp_path / "config.yaml"
    path.write_text(LEGACY, encoding="utf-8")

    save_server_config(load_server_config(path), path)

    assert "max_input_tokens: 128000" in path.read_text(encoding="utf-8")


WITH_REPORTED = """providers:
- name: openrouter
  catalog_id: openrouter
  base_url: https://openrouter.ai/api/v1
  api: openai-completions
  api_key: sk-legacy
  default_model: deepseek/deepseek-chat
  models: [deepseek/deepseek-chat]
  model_metadata:
    deepseek/deepseek-chat:
      context_window_tokens: 64000
      max_output_tokens: 8192
"""


def test_config_reads_provider_reported_capacity(tmp_path) -> None:
    """服务商返回的容量单独保存；解析时优先于目录。"""
    path = tmp_path / "config.yaml"
    path.write_text(WITH_REPORTED, encoding="utf-8")

    loaded = load_server_config(path).providers[0]
    limits = resolve_model_limits(loaded, "deepseek/deepseek-chat")

    assert limits.source == "metadata"
    assert limits.context_window_tokens == 64_000
    assert limits.max_output_tokens == 8_192


def test_legacy_config_loads_but_manual_limits_are_ignored(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(WITH_LIMITS, encoding="utf-8")

    loaded = load_server_config(path).providers[0]
    limits = resolve_model_limits(loaded, "deepseek-chat")

    assert loaded.model_limits  # 兼容读取旧配置，不让手动值参与解析。
    assert limits.source == "unknown"
    assert limits.context_window_tokens is None
    assert limits.max_output_tokens is None


def test_snapshot_carries_the_limits_of_its_own_model() -> None:
    """快照冻结容量：受理后改配置不影响这一轮已定的预算。"""
    book = ProviderBook(
        ServerConfig(
            providers=[
                profile(metadata={"deepseek-chat": ModelCapacity(context_window_tokens=64_000)}),
                ProviderProfile(
                    name="other",
                    base_url="https://example.com/v1",
                    api_key="sk-other",
                    default_model="m2",
                    model_metadata={"m2": ModelCapacity(context_window_tokens=32_000)},
                ),
            ],
            active_provider="other",
            active_model="m2",
        ),
        boot_name="",
        boot_model="",
    )

    frozen = book.snapshot()

    assert frozen.limits is not None
    assert frozen.limits.model == "m2"
    assert frozen.limits.context_window_tokens == 32_000
