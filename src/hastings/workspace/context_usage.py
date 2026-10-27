"""完整请求的用量估算与预算判断（0.9 U1）。

估算只服务两个判断：界面上的「大概占了多少」，以及发请求之前「装得下吗」。
两者都必须是完整请求——系统约定、工具定义、有效历史与当前提示加在一起，
只算正文会让人以为还有很多空间。

**为什么不用「字符数除以四」**：那是英文的折算习惯。中文一个字通常就是一个
token，把中文按四分之一算，会让一个两万字的中文装配看起来只占五千，于是预算
判断永远显示「还早」。这里按字符类型分开折算，宁可略高估。

**为什么带指纹**：估算值只对某一组输入成立。换模型、换资料版本、改系统约定之后，
上一轮的校准就不再有意义。指纹把这些输入压成短标识，供缓存与校准判断复用。

估算不产生副作用：不调用模型、不写会话事件、不登记读取凭据。资料版本由调用方
以 ``source_revisions`` 传入，本模块不去读文件。
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from hastings.server.model_limits import ModelLimits

__all__ = [
    "METHOD",
    "MESSAGE_OVERHEAD_TOKENS",
    "TOOL_OVERHEAD_TOKENS",
    "BudgetState",
    "ContextEstimate",
    "budget_for_limit",
    "budget_state",
    "count_tokens",
    "estimate_request",
    "wire_overhead",
]

#: 估算方法标识；数字随方法变化时调用方可以据此判断旧值是否还可用。
METHOD = "cjk-aware-v1"

#: 每条消息在协议层固定的结构开销（角色、分隔符等）。估算不出来源，按经验固定。
MESSAGE_OVERHEAD_TOKENS = 4
#: 每个工具定义的结构开销：名称、描述、参数结构之外的分隔与类型声明。
TOOL_OVERHEAD_TOKENS = 16

#: 接近预算的比例。到这个程度就不再自动追加资料，避免下一次请求立即溢出。
_NEAR_RATIO = 0.9

BudgetState = Literal["unknown", "ok", "near", "over"]


def _is_wide(char: str) -> bool:
    """CJK 与全角标点：按一个字一个 token 计。"""
    code = ord(char)
    return (
        0x3040 <= code <= 0x30FF  # 日文假名
        or 0x3400 <= code <= 0x4DBF  # 扩展 A
        or 0x4E00 <= code <= 0x9FFF  # 基本汉字
        or 0xF900 <= code <= 0xFAFF  # 兼容汉字
        or 0xFF00 <= code <= 0xFF60  # 全角标点
        or 0x3000 <= code <= 0x303F  # CJK 标点
        or 0xAC00 <= code <= 0xD7AF  # 韩文
    )


def count_tokens(text: str) -> int:
    """中文感知的启发式：宽字符一字一 token，其余四个字符一 token。

    不追求与某家供应商的分词器逐字对齐——那需要下载词表并且对未登录模型无效。
    目标是可复算、方向一致（宁可高估），并且在换模型后仍能给出同一个方法下的对比。
    """
    if not text:
        return 0
    wide = sum(1 for char in text if _is_wide(char))
    narrow = len(text) - wide
    return wide + math.ceil(narrow / 4)


def wire_overhead(*, messages: int, tools: int) -> int:
    """协议结构开销：消息条数与工具个数各自的固定部分。"""
    return messages * MESSAGE_OVERHEAD_TOKENS + tools * TOOL_OVERHEAD_TOKENS


@dataclass(frozen=True, slots=True)
class ContextEstimate:
    """一次完整请求的估算；分项都是估算，不是供应商实测。"""

    system_tokens: int
    tool_tokens: int
    message_tokens: int
    protocol_tokens: int
    method: str
    request_fingerprint: str

    @property
    def total(self) -> int:
        return (
            self.system_tokens
            + self.tool_tokens
            + self.message_tokens
            + self.protocol_tokens
        )


def estimate_request(
    *,
    model_identity: str,
    system: str,
    tool_wire: str,
    message_wire: str,
    source_revisions: Mapping[str, str],
    protocol_tokens: int,
    count: Callable[[str], int] = count_tokens,
    method: str = METHOD,
) -> ContextEstimate:
    """按实际装配结果估算一次请求。

    ``tool_wire`` 与 ``message_wire`` 是即将发出的结构序列化，不是原始作者文本：
    工具参数、角色与协议字段同样占 token，估算必须包含它们。
    """
    identity = json.dumps(
        [model_identity, system, tool_wire, message_wire, source_revisions, method],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return ContextEstimate(
        system_tokens=count(system),
        tool_tokens=count(tool_wire),
        message_tokens=count(message_wire),
        protocol_tokens=protocol_tokens,
        method=method,
        request_fingerprint=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
    )


def budget_for_limit(*, total_tokens: int, input_limit: int | None) -> BudgetState:
    """已知输入上限时的预算判断；没有上限就是 ``unknown``。"""
    if input_limit is None:
        return "unknown"
    if total_tokens > input_limit:
        return "over"
    if total_tokens >= input_limit * _NEAR_RATIO:
        return "near"
    return "ok"


def budget_state(*, total_tokens: int, limits: ModelLimits) -> BudgetState:
    """这一轮装不装得下。

    窗口未知时只能给 ``unknown``：没有输入上限就不能声称「还有空间」，
    也不能据此触发自动整理。
    """
    return budget_for_limit(total_tokens=total_tokens, input_limit=limits.input_limit())
