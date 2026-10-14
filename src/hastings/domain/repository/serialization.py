"""规范 JSON 序列化与稳定内容哈希（Phase 12 T12.1）。

同一逻辑内容必须产生同一哈希——这是幂等检测与 no-op 判定的根基。
规则（明确到每个易漂移的类型）：

- dict：键必须是 str，按码点排序输出；重复语义键不存在（Python dict 本身无重复）；
- list：顺序即语义，保持原序（集合语义请上游先排序成 list）；
- datetime：必须带时区，统一换算 UTC 后 ISO-8601 微秒精度；naive datetime 拒绝；
- float：使用 repr 的最短往返表示；NaN/Inf 无法稳定序列化，直接拒绝；
- bool/int/str/None：原样；其他类型（set、自定义对象）拒绝，不给隐式降级。

哈希为 SHA-256，输入是规范 JSON 的 UTF-8 编码。
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any

__all__ = ["canonical_json", "content_hash"]


def _canon(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(
                f"浮点值 {value!r} 无法规范序列化（NaN/Inf 不允许；"
                "请在上游改用可稳定表示的值）"
            )
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(
                f"datetime {value!r} 不带时区（为什么错：naive 时间在不同环境下语义漂移；"
                "怎样算对：一律使用带时区 UTC 的 datetime）"
            )
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        return {str(k): _canon(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_canon(item) for item in value]
    raise TypeError(
        f"类型 {type(value).__name__} 不支持规范序列化（为什么错：没有明确的全局规则；"
        "怎样算对：先转换为 JSON 兼容结构，集合先排序为 list）"
    )


def canonical_json(value: object) -> str:
    """把 JSON 兼容值序列化为规范形式（键序、时区、浮点均已确定）。"""
    return json.dumps(_canon(value), ensure_ascii=False, separators=(",", ":"), sort_keys=False)


def content_hash(value: object) -> str:
    """稳定内容哈希：sha256(canonical_json(value).encode("utf-8")).hexdigest()。"""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
