"""ChatRunner 端口（Phase 19.3 T19.3c）：纯对话的流式模型执行边界。

普通聊天只做文本对话：``tools`` 恒为空，runner 拿不到仓库写端口，也不挂载
任何 Hastings 创作工具（四透镜②：硬约束在工具层，聊天没有写能力）。模型
文本边接收边通过 ``sink`` 推送；失败以 HastingsError 抛出，由应用服务分类
为「授权失败 / 连接失败 / 超时 / 无正文结果」（计划 §19.3）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = ["ChatRun", "ChatRunner", "ChatStreamSink"]


@dataclass(frozen=True)
class ChatRun:
    """一次纯对话请求：模型、系统提示、历史、引用摘录与取消探针。

    ``refs`` 是本轮附加的一次性摘录文本（已带「未保存」等标注），由 runner
    追加到最后一条用户消息；不是持久上下文。
    """

    model: str
    system: str
    history: tuple[tuple[str, str], ...]
    cancelled: Callable[[], bool]
    refs: tuple[str, ...] = ()
    max_output_chars: int = 0


class ChatStreamSink(Protocol):
    """增量文本回调：每收到一段模型文本调用一次（服务端先落盘再发布）。"""

    async def __call__(self, delta: str) -> None: ...


@runtime_checkable
class ChatRunner(Protocol):
    async def stream(self, run: ChatRun, sink: ChatStreamSink) -> str:
        """流式执行并返回最终文本；失败抛出 HastingsError（含稳定 code 与三要素）。"""
