"""0.9 U1：模型容量的来源与边界。

容量决定两件事：界面上的占比，以及这一轮到底允许多少输入。两者必须来自同一次
解析，否则会出现「显示还能写」但实际请求被拒的情况。

这里只校验解析与预算，不涉及分词与装配。
"""

from __future__ import annotations

import pytest

from hastings.server.model_limits import (
    WORKER_OUTPUT_TOKENS,
    ModelLimitOverride,
    resolve_model_limits,
)
from hastings.server.provider_config import (
    ProviderBook,
    ProviderProfile,
    ServerConfig,
    load_server_config,
)


def profile(**limits: ModelLimitOverride) -> ProviderProfile:
    return ProviderProfile(
        name="deepseek",
        base_url="https://api.deepseek.com",
        api="openai-completions",
        default_model="deepseek-chat",
        model_limits=dict(limits),
    )


def test_unconfigured_model_reports_unknown_window_without_guessing() -> None:
    """没有容量配置时不填默认 128K：猜一个窗口会让占比看起来可信。"""
    limits = resolve_model_limits(profile(), "deepseek-chat")

    assert limits.context_window_tokens is None
    assert limits.max_input_tokens is None
    assert limits.input_limit() is None
    assert limits.source == "unknown"
    # 回复预留与运行时实际请求上限一致，未知窗口时也不假装成 8K。
    assert limits.output_reserve_tokens == WORKER_OUTPUT_TOKENS


def test_override_supplies_window_and_reserve() -> None:
    limits = resolve_model_limits(
        profile(
            **{
                "deepseek-chat": ModelLimitOverride(
                    context_window_tokens=131_072,
                    max_output_tokens=8_192,
                )
            }
        ),
        "deepseek-chat",
    )

    assert limits.source == "override"
    assert limits.context_window_tokens == 131_072
    # 未显式配置回复预留时沿用运行时上限，而不是模型的最大输出。
    assert limits.output_reserve_tokens == WORKER_OUTPUT_TOKENS
    assert limits.input_limit() == 131_072 - WORKER_OUTPUT_TOKENS - limits.safety_tokens


def test_smaller_explicit_input_limit_wins() -> None:
    """有些平台限制的是输入长度而不是窗口，两者取更严的那个。"""
    limits = resolve_model_limits(
        profile(
            **{
                "deepseek-chat": ModelLimitOverride(
                    context_window_tokens=131_072,
                    max_input_tokens=32_000,
                )
            }
        ),
        "deepseek-chat",
    )

    assert limits.input_limit() == 32_000


def test_explicit_reserve_overrides_runtime_default() -> None:
    limits = resolve_model_limits(
        profile(
            **{
                "deepseek-chat": ModelLimitOverride(
                    context_window_tokens=64_000,
                    max_output_tokens=8_192,
                    output_reserve_tokens=8_192,
                    safety_tokens=1_000,
                )
            }
        ),
        "deepseek-chat",
    )

    assert limits.output_reserve_tokens == 8_192
    assert limits.safety_tokens == 1_000
    assert limits.input_limit() == 64_000 - 8_192 - 1_000


def test_limits_are_per_exact_model_not_per_prefix() -> None:
    """名称前缀相同不代表容量相同；未配置的模型就是未知。"""
    provider = profile(
        **{"deepseek-chat": ModelLimitOverride(context_window_tokens=131_072)}
    )

    configured = resolve_model_limits(provider, "deepseek-chat")
    sibling = resolve_model_limits(provider, "deepseek-chat-v2")

    assert configured.source == "override"
    assert sibling.source == "unknown"
    assert sibling.context_window_tokens is None


def test_revision_changes_when_capacity_changes() -> None:
    """指纹要跟着容量变，否则改完配置仍会复用旧估算。"""
    small = resolve_model_limits(
        profile(**{"deepseek-chat": ModelLimitOverride(context_window_tokens=64_000)}),
        "deepseek-chat",
    )
    large = resolve_model_limits(
        profile(**{"deepseek-chat": ModelLimitOverride(context_window_tokens=131_072)}),
        "deepseek-chat",
    )

    assert small.revision != large.revision


def test_revision_distinguishes_models_on_the_same_connection() -> None:
    provider = profile(
        **{
            "deepseek-chat": ModelLimitOverride(context_window_tokens=131_072),
            "deepseek-reasoner": ModelLimitOverride(context_window_tokens=131_072),
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
    """对外视图用 null 表示未知，前端据此不显示百分比。"""
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
        "source": "unknown",
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


def test_config_reads_limits_for_exact_model(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(WITH_LIMITS, encoding="utf-8")

    loaded = load_server_config(path).providers[0]
    limits = resolve_model_limits(loaded, "deepseek-chat")

    assert limits.source == "override"
    assert limits.context_window_tokens == 131_072
    assert limits.max_output_tokens == 8_192


def test_snapshot_carries_the_limits_of_its_own_model() -> None:
    """快照冻结容量：受理后改配置不影响这一轮已定的预算。"""
    book = ProviderBook(
        ServerConfig(
            providers=[
                profile(**{"deepseek-chat": ModelLimitOverride(context_window_tokens=64_000)}),
                ProviderProfile(
                    name="other",
                    base_url="https://example.com/v1",
                    api_key="sk-other",
                    default_model="m2",
                    model_limits={"m2": ModelLimitOverride(context_window_tokens=32_000)},
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
