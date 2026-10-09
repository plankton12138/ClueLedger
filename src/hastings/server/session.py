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
    def is_running(self) -> bool: ...

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
