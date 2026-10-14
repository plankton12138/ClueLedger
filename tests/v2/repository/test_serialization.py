"""T12.1 规范 JSON 序列化与稳定内容哈希测试。

同一逻辑内容必须产生同一哈希（键序无关、时区归一、集合规则明确）；
NaN/Inf 等无法稳定序列化的值必须被拒绝而不是悄悄产出不稳定哈希。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from hastings.domain.repository.serialization import (
    canonical_json,
    content_hash,
    normalize_json,
)


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


# ----------------------------------------------------------------------
# normalize_json：规范值树（Phase 12 收口）
# ----------------------------------------------------------------------


def test_normalize_returns_new_tree_without_shared_references() -> None:
    inner = {"a": 1}
    value = {"outer": inner, "items": [inner]}
    normalized = normalize_json(value)
    assert normalized == {"outer": {"a": 1}, "items": [{"a": 1}]}
    # 修改原对象不得影响规范值树
    inner["a"] = 999
    assert normalized["outer"] == {"a": 1}
    # 修改规范值树也不得影响原对象
    normalized["items"][0]["a"] = 777
    assert inner["a"] == 999


def test_normalize_datetime_and_tuple() -> None:
    dt = datetime(2026, 10, 14, 17, 0, 0, tzinfo=timezone(timedelta(hours=9)))
    normalized = normalize_json({"when": dt, "pair": ("x", "y")})
    assert normalized["when"] == "2026-10-14T08:00:00+00:00"
    assert normalized["pair"] == ["x", "y"]
    assert isinstance(normalized["pair"], list)


@pytest.mark.parametrize("bad", [{1: "a"}, {"ok": 1, 2: "b"}, {("t",): "v"}])
def test_non_string_dict_keys_rejected(bad: object) -> None:
    with pytest.raises(ValueError):
        normalize_json(bad)


def test_non_string_keys_never_collide_in_hash() -> None:
    # 曾经 {1: "first", "1": "second"} 与 {"1": "second"} 产生同一哈希（内容丢失）
    with pytest.raises(ValueError):
        content_hash({1: "first", "1": "second"})
    assert content_hash({"1": "second"}) == content_hash({"1": "second"})


def test_normalize_key_order_is_sorted() -> None:
    normalized = normalize_json({"b": 1, "a": 2})
    assert list(normalized) == ["a", "b"]
