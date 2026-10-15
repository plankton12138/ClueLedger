"""Manuscript Schema 公共助手：schema 版本、必填校验、legacy 字段保留策略。

与 domain/story/schema.py 同构（分域各持本套约定，避免跨域耦合）：
未知旧字段进入显式 `legacy_metadata`，绝不静默丢失。
"""

from __future__ import annotations

from typing import Any

from hastings.domain.manuscript import error_codes as ec
from hastings.domain.manuscript.errors import violation

__all__ = [
    "SCHEMA_VERSION",
    "require_str",
    "split_legacy",
    "tuple_of_str",
]

SCHEMA_VERSION = 1


def require_str(
    value: Any, *, field: str, where: str, code: str = ec.MANUSCRIPT_MISSING_REQUIRED_FIELD
) -> str:
    """必填字符串校验；缺失/空串给出三要素错误。"""
    if not isinstance(value, str) or not value.strip():
        raise violation(
            code,
            where,
            f"必填字段 {field!r} 缺失或为空（当前值：{value!r}）",
            f"提供非空的 {field}；显示名可变，但身份/引用字段必须完整",
            field=field,
        )
    return value


def tuple_of_str(value: Any) -> tuple[str, ...]:
    """把任意可迭代值规范为非空字符串元组；None/缺失返回空元组。"""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item).strip())
    return (str(value),)


def split_legacy(content: dict[str, Any], known_keys: frozenset[str]) -> dict[str, Any]:
    """把 content 中不在 known_keys 内的键收进 legacy_metadata。"""
    return {k: v for k, v in content.items() if k not in known_keys}
