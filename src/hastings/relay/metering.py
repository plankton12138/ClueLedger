"""订阅模型的点数额度计算。

1 点 = 1,000,000 微点。内部只用整数微点，避免流式结算出现浮点误差。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

__all__ = [
    "DEFAULT_RATES",
    "ModelRate",
    "actual_micro",
    "estimate_input_tokens",
    "extract_usage",
    "rate_for",
    "request_max_output",
    "reservation_micro",
]

MICRO_PER_POINT = 1_000_000


@dataclass(frozen=True, slots=True)
class ModelRate:
    name: str
    multiplier: Decimal
    max_output_tokens: int


DEFAULT_RATES: tuple[ModelRate, ...] = (
    ModelRate("deepseek-v4-pro", Decimal("1.0"), 131_072),
    ModelRate("deepseek-flash", Decimal("0.30"), 131_072),
)

_RATES_BY_NAME = {rate.name: rate for rate in DEFAULT_RATES}


def rate_for(model: str, rates: tuple[ModelRate, ...] = DEFAULT_RATES) -> ModelRate | None:
    for rate in rates:
        if rate.name == model:
            return rate
    return _RATES_BY_NAME.get(model)


def _text_tokens(text: str) -> int:
    cjk = sum(1 for ch in text if ord(ch) >= 0x2E80)
    other = len(text) - cjk
    return math.ceil(cjk + other * 0.5)


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def estimate_input_tokens(body: dict[str, Any]) -> int:
    """对服务端实际请求体做保守估算；宁多留，不依赖浏览器。"""
    total = 0
    system = body.get("system")
    if system is not None:
        total += _text_tokens(_content_text(system))
    for message in body.get("messages", []):
        if not isinstance(message, dict):
            continue
        total += 8
        total += _text_tokens(_content_text(message.get("content", "")))
        total += _text_tokens(_content_text(message.get("name", "")))
        total += _text_tokens(_content_text(message.get("tool_calls", "")))
    tools = body.get("tools")
    if tools is not None:
        total += _text_tokens(_content_text(tools))
    # 20% 余量与固定的消息/工具封装开销；结算仍以真实 usage 为准。
    return max(1, math.ceil(total * 1.2) + 200)


def request_max_output(body: dict[str, Any], *, default_tokens: int, rate: ModelRate) -> int:
    raw = body.get("max_tokens")
    if raw is None:
        raw = body.get("max_completion_tokens")
    if raw is None:
        return min(default_tokens, rate.max_output_tokens)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_tokens 必须是整数") from exc
    if value <= 0:
        raise ValueError("max_tokens 必须大于 0")
    if value > rate.max_output_tokens:
        raise ValueError(f"max_tokens 超过该模型上限 {rate.max_output_tokens}")
    return value


def reservation_micro(rate: ModelRate, *, input_tokens: int, max_output_tokens: int) -> int:
    """按全部输入未命中缓存预估，得到这一轮的上限。"""
    raw = Decimal(333 * input_tokens + 1000 * max_output_tokens) * rate.multiplier
    return max(1, int(raw.to_integral_value(rounding="ROUND_CEILING")))


def actual_micro(
    rate: ModelRate,
    *,
    input_tokens: int,
    cached_tokens: int,
    output_tokens: int,
) -> int:
    cached = max(0, min(cached_tokens, input_tokens))
    miss = max(0, input_tokens - cached)
    raw = Decimal(333 * miss + 11 * cached + 1000 * output_tokens) * rate.multiplier
    return max(0, int(raw.to_integral_value(rounding="ROUND_CEILING")))


def extract_usage(payload: dict[str, Any]) -> tuple[int, int, int] | None:
    """从 OpenAI/DeepSeek usage 中取 (输入, 命中缓存, 输出) tokens。"""
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
    completion = usage.get("completion_tokens", usage.get("output_tokens"))
    if prompt is None or completion is None:
        return None
    cached = usage.get("prompt_cache_hit_tokens")
    if cached is None:
        details = usage.get("prompt_tokens_details")
        if isinstance(details, dict):
            cached = details.get("cached_tokens")
    if cached is None:
        cached = usage.get("cache_read_input_tokens", 0)
    try:
        return int(prompt), int(cached), int(completion)
    except (TypeError, ValueError):
        return None
