"""T12.1 规范 JSON 序列化与稳定内容哈希测试。

同一逻辑内容必须产生同一哈希（键序无关、时区归一、集合规则明确）；
NaN/Inf 等无法稳定序列化的值必须被拒绝而不是悄悄产出不稳定哈希。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from hastings.domain.repository.serialization import canonical_json, content_hash


def test_key_order_does_not_change_hash() -> None:
    a = content_hash({"title": "雾夜", "acts": ["一", "二"]})
    b = content_hash({"acts": ["一", "二"], "title": "雾夜"})
    assert a == b


def test_same_content_same_hash_across_calls() -> None:
    value = {"chapter": 1, "lines": ["第一行", "第二行"], "meta": {"ok": True, "n": 3}}
    assert content_hash(value) == content_hash(value)
    assert len(content_hash(value)) == 64


def test_different_content_different_hash() -> None:
    assert content_hash({"a": 1}) != content_hash({"a": 2})


def test_datetime_normalized_to_utc() -> None:
    tokyo = timezone(timedelta(hours=9))
    dt_tokyo = datetime(2026, 10, 14, 17, 0, 0, tzinfo=tokyo)
    dt_utc = datetime(2026, 10, 14, 8, 0, 0, tzinfo=UTC)
    assert canonical_json(dt_tokyo) == canonical_json(dt_utc)


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError):
        canonical_json(datetime(2026, 10, 14, 8, 0, 0))


def test_float_rule_is_explicit() -> None:
    # 浮点用 repr 的最短往返表示：同一值稳定，跨值可区分
    assert canonical_json(0.1) == "0.1"
    assert content_hash({"x": 0.5}) != content_hash({"x": 0.25})


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_floats_rejected(bad: float) -> None:
    with pytest.raises(ValueError):
        canonical_json(bad)


def test_list_order_is_significant() -> None:
    assert content_hash(["一", "二"]) != content_hash(["二", "一"])


def test_unsupported_type_rejected() -> None:
    with pytest.raises(TypeError):
        canonical_json({1, 2})
