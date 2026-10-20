"""上下文预算的朴素估算（供 ContextBuilder 与背景装配共用）。

按 UTF-8 字节数估算 token 上界，偏保守，不依赖某个 Provider 的分词器或浮动的
usage 数值。放在独立模块是为了让 ``contexts.background`` 与 ``contexts.__init__``
互相引用时不形成导入环。
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["estimate_tokens"]


def estimate_tokens(value: Any) -> int:
    # UTF-8 字节上界，偏保守；不依赖某个 Provider 的分词器或浮动 usage。
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
