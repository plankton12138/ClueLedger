"""WritingSession：Web 服务层对"打开的工作区 + AgentHarness"的协议抽象（P3 T1）。

协议无关——本模块不 import fastapi，只描述 app/ws/rest 需要的会话读取面与
命令面；具体实现 HastingsSession 在 T2 落地（封装 StoryWorkspace + harness）。
测试用桩实现本协议即可驱动整个 HTTP/WS 层。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from typing import TYPE_CHECKING, Protocol

from hastings.manuscript.diff import DiffHunk
from hastings.manuscript.write_guard import EditMarkRegistry
from tau_agent.events import AgentEvent
from tau_agent.harness import EventListener
from tau_agent.messages import AgentMessage

if TYPE_CHECKING:
    # 运行时不导入——reconcile.py 反向依赖本模块的 AgentBusyError，防循环
    from hastings.context_manager import CompactionResult, TokenUsage
    from hastings.server.reconcile import NoticeListener


class AgentBusyError(RuntimeError):
    """同一会话同时只能有一个运行中的 prompt 流。

    错误消息三要素：哪里错（prompt 被拒）、为什么错（harness 单流）、
    怎样算对（steer / follow_up / cancel）。WS 层把它翻译成 error 帧。
    """


class WritingSession(Protocol):
    """hastings.server 各路由依赖的会话表面（结构化协议）。"""

    @property
    def slug(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def provider_profile(self) -> str:
        """当前会话所用 provider 档案名（P6 T2；跨档案切换的判定依据）。"""
        ...

    @property
    def is_running(self) -> bool: ...

    @property
    def is_compacting(self) -> bool:
        """上下文压缩正在等待摘要或写回会话文件。"""
        ...

    @property
    def tool_names(self) -> tuple[str, ...]: ...

    @property
    def messages(self) -> tuple[AgentMessage, ...]: ...

    @property
    def edit_marks(self) -> EditMarkRegistry:
        """作者草稿锁注册表（P5 D4 人机互斥的共享状态，双侧守门读它）。"""
        ...

    async def request_reconcile(self, chapter_id: str, hunks: Sequence[DiffHunk]) -> None:
        """保存成功后请求对账（P5 D5；空 hunks 为 no-op）。"""
        ...

    async def resync_reconcile(self, chapter_id: str) -> bool:
        """手动重跑最近一次修订的对账（P5 T5 重试/「同步上下文」按钮）。

        返回 False 表示该章没有可重跑的修订（尚无保存记录）——调用方
        据此给出「先保存一次」的三要素指引用。
        """
        ...

    def apply_writing_language(self, language: str) -> None:
        """切换写作语言并立即重建 system prompt（P6 D3；下一轮对话即生效）。"""
        ...

    def apply_model(self, model: str) -> None:
        """同档案热切换模型（P6 D2；下一轮对话即生效，不重建会话）。"""
        ...

    async def auto_compact(self) -> CompactionResult:
        """prompt 前自动两档压缩（P10 T8；超阈值才动，运行中早返回）。

        先刷新 system，再执行会持久化的两档压缩，避免只修改内存导致重启后
        上下文重新膨胀。
        """
        ...

    def context_usage(self) -> TokenUsage:
        """当前上下文占用快照（P10 T12，D11）：get_state 与 token_usage 帧共用。

        走唯一计数源 ``count_context_tokens``——与压缩触发、事件留痕同源。
        """
        ...

    async def compact_context(self) -> CompactionResult:
        """手动两档压缩（P10 T13，D12）：绕过阈值直接调用 D8 同一原语。

        无可剥离内容 → 返回 ``reason="nothing_to_strip"``（合法空结果）；
        运行中 → 返回 ``reason="running"``（REST 侧另有 409 前置守卫）。
        """
        ...

    def subscribe_notice(self, listener: NoticeListener) -> Callable[[], None]:
        """订阅对账进度通知（ws 层翻成 notice 帧），返回退订函数。"""
        ...

    def prompt(self, content: str) -> AsyncIterator[AgentEvent]:
        """开始一轮新的 prompt 流；已有流在跑时抛 AgentBusyError。"""
        ...

    def steer(self, content: str) -> None:
        """向运行中的流插话（下一 turn 前注入）。"""
        ...

    def follow_up(self, content: str) -> None:
        """排队一条流结束后的追加消息。"""
        ...

    def cancel(self) -> None:
        """中止当前 prompt 流（未完成的工具调用由 harness 修复）。"""
        ...

    def subscribe(self, listener: EventListener) -> Callable[[], None]:
        """订阅事件流，返回退订函数。"""
        ...


__all__ = ["AgentBusyError", "WritingSession"]
