"""会话累计用量的统计口径（0.9 U0）。

事件里的 ``usage`` 是适配层归一化后的供应商数据，但四类协议对「输入」的定义不同：
OpenAI 两类的 input 已经扣掉缓存读写，Anthropic 的 input 本身就不含缓存。
界面要显示的是同一个东西——这一次请求让模型看了多少输入、写了多少输出，
所以统计前先换算成一份共同口径，再谈累计。

三条规则固定下来，后续阶段不再各自解释：

- **输入包含缓存。** 完整输入 = 未缓存输入 + 缓存读取 + 缓存写入；缓存只是计费方式，
  它同样占上下文，不能让「命中缓存的请求」看起来消耗很小。
- **推理不重复计入。** 两类协议都把推理算在输出里，输出字段直接使用，不再叠加推理分项。
- **零不等于上报。** 一次真实请求必然有输入。整份用量全为零，说明适配层没解析到字段，
  和「供应商确实报了零」不是同一件事，按未上报处理。

分项仍然可能只报了一部分。此时已知项照常累计，未知项保持 ``None``：
把它填成 0 会让累计值看起来精确，实际上是猜的。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["ReportedUsage", "UsageTotals", "reported_usage"]


@dataclass(frozen=True, slots=True)
class ReportedUsage:
    """一次请求的用量，按统计口径换算后的形状。

    ``input_tokens`` 是含缓存的完整输入，``cache_read_tokens`` 与
    ``cache_write_tokens`` 是它的子项而不是额外消耗。任何字段为 ``None``
    都表示这一项没有得到可靠数值，不表示零。
    """

    total_tokens: int | None
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None


def _count(value: Any) -> int | None:
    """只接受非负整数；布尔与字符串不做静默转换。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _sum_known(values: list[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def reported_usage(raw: Any) -> ReportedUsage | None:
    """把事件里的一条 usage 换算成统计口径；无法确认时返回 ``None``。"""
    if not isinstance(raw, Mapping):
        return None
    fresh = _count(raw.get("input"))
    cache_read = _count(raw.get("cache_read"))
    cache_write = _count(raw.get("cache_write"))
    output = _count(raw.get("output"))
    input_tokens = _sum_known([fresh, cache_read, cache_write])

    # 整份为零说明这次请求没有可用用量：真实请求不可能没有任何输入。
    components = [input_tokens, output, cache_read, cache_write]
    if all(value is None or value == 0 for value in components):
        return None

    # 适配层只填了 input/output 时 total 仍是默认零；用已知部分重算，不留假总量。
    total = _count(raw.get("total_tokens"))
    if not total:
        total = _sum_known([input_tokens, output])

    return ReportedUsage(
        total_tokens=total,
        input_tokens=input_tokens,
        output_tokens=output,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
    )


@dataclass(frozen=True, slots=True)
class UsageTotals:
    """一个会话的累计用量对外视图。

    数值与计数分开：``finished_requests`` 是已结束的真实请求数，
    ``requests_without_usage`` 说明其中多少条没有可靠用量。界面据此决定显示
    具体数值、零，还是「部分请求未统计」。请求计数不用成功状态过滤——失败和
    停止的请求只要消耗了用量，就已经发生。

    ``requests_in_progress`` 单独计数：运行中的请求已经发出，但还没有终态用量。
    """

    total_tokens: int | None
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    finished_requests: int
    requests_without_usage: int
    requests_in_progress: int
    model_duration_ms: int | None

    def as_dict(self) -> dict[str, int | None]:
        """接口形状；字段名与前端 ``UsageTotals`` 一一对应。"""
        return {
            "total_tokens": self.total_tokens,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "finished_requests": self.finished_requests,
            "requests_without_usage": self.requests_without_usage,
            "requests_in_progress": self.requests_in_progress,
            "model_duration_ms": self.model_duration_ms,
        }

