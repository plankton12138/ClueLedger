"""规范 JSON 序列化、规范值树与稳定内容哈希（Phase 12 T12.1，收口修订）。

同一逻辑内容必须产生同一哈希——这是幂等检测与 no-op 判定的根基。
规则（明确到每个易漂移的类型）：

- dict：键必须是 str（非 str 键直接拒绝，不做隐式 str() 转换——转换会造成
  {1: "first", "1": "second"} 与 {"1": "second"} 哈希碰撞且内容丢失），
  按码点排序输出；
- list/tuple：顺序即语义，保持原序（集合语义请上游先排序成 list）；tuple
  统一规范为 list，保证内存与 SQLite 后端保存同一种领域值；
- datetime：必须带时区，统一换算 UTC 后 ISO-8601 微秒精度；naive datetime 拒绝；
- float：使用 repr 的最短往返表示；NaN/Inf 无法稳定序列化，直接拒绝；
- bool/int/str/None：原样；其他类型（set、自定义对象）拒绝，不给隐式降级。

`normalize_json` 返回全新规范值树：与调用者传入对象零共享引用，Revision、
Relation metadata、Event payload 一律保存该树的拷贝——Revision 创建后不可
被调用者后续修改（不变量 1）。

哈希为 SHA-256，输入是规范 JSON 的 UTF-8 编码。
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from typing import Any

__all__ = ["canonical_json", "content_hash", "normalize_json"]


def _norm(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(
                f"浮点值 {value!r} 无法规范序列化（NaN/Inf 不允许；请在上游改用可稳定表示的值）"
            )
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(
                f"datetime {value!r} 不带时区（为什么错：naive 时间在不同环境下语义漂移；"
                "怎样算对：一律使用带时区 UTC 的 datetime）"
            )
        return value.astimezone(UTC).isoformat()
    if isinstance(value, dict):
        bad = next((k for k in value if not isinstance(k, str)), None)
        if bad is not None:
            raise ValueError(
                f"字典键 {bad!r} 不是 str（为什么错：隐式 str() 转换会让 "
                "{1: 'first', '1': 'second'} 与 {'1': 'second'} 产生同一哈希且丢失内容；"
                "怎样算对：上游先把键统一转成 str 再提交）"
            )
        return {k: _norm(v) for k, v in sorted(value.items(), key=lambda kv: kv[0])}
    if isinstance(value, (list, tuple)):
        return [_norm(item) for item in value]
    raise TypeError(
        f"类型 {type(value).__name__} 不支持规范序列化（为什么错：没有明确的全局规则；"
        "怎样算对：先转换为 JSON 兼容结构，集合先排序为 list）"
    )


def normalize_json(value: object) -> Any:
    """返回与调用者对象零共享引用的规范值树（键排序、tuple→list、UTC 归一）。

    无法稳定规范化的输入（非 str 键、naive datetime、set、NaN/Inf 等）抛
    TypeError/ValueError，不做隐式降级。
    """
    return _norm(value)


def canonical_json(value: object) -> str:
    """把 JSON 兼容值序列化为规范形式（键序、时区、浮点均已确定）。"""
    return json.dumps(_norm(value), ensure_ascii=False, separators=(",", ":"), sort_keys=False)


def content_hash(value: object) -> str:
    """稳定内容哈希：sha256(canonical_json(value).encode("utf-8")).hexdigest()。"""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
