"""0.9 U1：完整请求的用量估算与预算判断。

估算要回答「这一轮大概占多少」，所以必须与实际请求同源：system、工具定义、消息
都来自装配结果，而不是把作品资料再拼一遍。这里冻结三件事：分词方法怎么算、
指纹跟着什么变、预算状态怎么判。

数字是估算，测试断言的是方法与边界，不是某个模型的真实 token 数。
"""

from __future__ import annotations

import pytest

from hastings.server.model_limits import ModelLimitOverride, resolve_model_limits
from hastings.server.provider_config import ProviderProfile
from hastings.workspace.context_usage import (
    MESSAGE_OVERHEAD_TOKENS,
    TOOL_OVERHEAD_TOKENS,
    budget_state,
    count_tokens,
    estimate_request,
    wire_overhead,
)


def profile(window: int | None = None) -> ProviderProfile:
    limits = {"deepseek-chat": ModelLimitOverride(context_window_tokens=window)} if window else {}
    return ProviderProfile(
        name="deepseek",
        base_url="https://api.deepseek.com",
        api="openai-completions",
        default_model="deepseek-chat",
        model_limits=limits,
    )


# -- 分词 -------------------------------------------------------------------


def test_empty_text_costs_nothing() -> None:
    assert count_tokens("") == 0


def test_chinese_costs_more_than_the_same_length_of_ascii() -> None:
    """按字符数除以四折算中文会严重低估，这正是不能用英文启发式的原因。"""
    assert count_tokens("中文估算") == 4
    assert count_tokens("abcdefgh") == 2


def test_mixed_text_counts_each_script_separately() -> None:
    assert count_tokens("ab中文") == 1 + 2


def test_whitespace_and_punctuation_still_count() -> None:
    """空白与标点也进请求，不能只数汉字和字母。"""
    assert count_tokens("，。！") == 3


# -- 整请求估算 --------------------------------------------------------------


def test_estimate_splits_parts_and_sums_to_total() -> None:
    estimate = estimate_request(
        model_identity="deepseek:deepseek-chat",
        system="系统约定",
        tool_wire='[{"name":"read_file"}]',
        message_wire='[{"role":"user","content":"写一段"}]',
        source_revisions={"assembly": "a1", "materials": "m1"},
        protocol_tokens=wire_overhead(messages=2, tools=3),
        count=count_tokens,
    )

    assert estimate.system_tokens == 4
    assert estimate.tool_tokens == count_tokens('[{"name":"read_file"}]')
    assert estimate.message_tokens == count_tokens('[{"role":"user","content":"写一段"}]')
    assert estimate.protocol_tokens == 2 * MESSAGE_OVERHEAD_TOKENS + 3 * TOOL_OVERHEAD_TOKENS
    assert estimate.total == (
        estimate.system_tokens
        + estimate.tool_tokens
        + estimate.message_tokens
        + estimate.protocol_tokens
    )
    assert estimate.method == "cjk-aware-v1"


def test_same_inputs_produce_the_same_fingerprint() -> None:
    def one() -> str:
        return estimate_request(
            model_identity="deepseek:deepseek-chat",
            system="系统",
            tool_wire="[]",
            message_wire="[]",
            source_revisions={"assembly": "a1"},
            protocol_tokens=0,
            count=count_tokens,
        ).request_fingerprint

    assert one() == one()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model_identity", "openai:gpt-4o"),
        ("system", "换了系统约定"),
        ("tool_wire", '[{"name":"write_file"}]'),
        ("message_wire", '[{"role":"user","content":"换一句"}]'),
        ("source_revisions", {"assembly": "a2"}),
    ],
)
def test_any_change_invalidates_the_fingerprint(field: str, value: object) -> None:
    """换模型、换资料版本都要让旧估算失效，不能复用上一轮的校准。"""
    base: dict[str, object] = {
        "model_identity": "deepseek:deepseek-chat",
        "system": "系统",
        "tool_wire": "[]",
        "message_wire": "[]",
        "source_revisions": {"assembly": "a1"},
        "protocol_tokens": 0,
        "count": count_tokens,
    }
    changed = {**base, field: value}

    assert (
        estimate_request(**base).request_fingerprint  # type: ignore[arg-type]
        != estimate_request(**changed).request_fingerprint  # type: ignore[arg-type]
    )


# -- 预算状态 ---------------------------------------------------------------


def test_budget_is_unknown_without_capacity() -> None:
    """窗口未知时不给百分比，也不能声称还有多少空间。"""
    limits = resolve_model_limits(profile(), "deepseek-chat")

    assert budget_state(total_tokens=50_000, limits=limits) == "unknown"


def test_budget_is_over_when_estimate_exceeds_the_limit() -> None:
    limits = resolve_model_limits(profile(64_000), "deepseek-chat")

    assert budget_state(total_tokens=limits.input_limit() + 1, limits=limits) == "over"


def test_budget_near_limit_warns_before_it_is_full() -> None:
    limits = resolve_model_limits(profile(64_000), "deepseek-chat")
    limit = limits.input_limit()
    assert limit is not None

    assert budget_state(total_tokens=int(limit * 0.95), limits=limits) == "near"
    assert budget_state(total_tokens=int(limit * 0.5), limits=limits) == "ok"
