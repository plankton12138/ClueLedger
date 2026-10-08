"""HastingsSession：WritingSession 的标准实现（P3 T2）。

绑定一个 StoryWorkspace 与一个 AgentHarness，负责：
- busy 防护：同一会话同时只允许一个 prompt 流（AgentBusyError 三要素）；
- steer / follow_up / cancel 命令直通 harness；
- 事件订阅直通 harness（每连接一个 listener，多标签页天然 fan-out）。

本模块不 import fastapi——协议无关，纯 asyncio + FakeProvider 可测。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING

from hastings.bible.store import StoryWorkspace
from hastings.server.session import AgentBusyError, WritingSession
from tau_agent.events import AgentEvent
from tau_agent.harness import AgentHarness, AgentHarnessConfig, EventListener
from tau_agent.messages import AgentMessage
from tau_agent.provider import ModelProvider

BUSY_MESSAGE = (
    "prompt 被拒绝：当前有写作流正在进行。"
    "为什么错：同一个故事会话同时只能运行一个写作流。"
    "怎样算对：用 steer 在当前流中插话、用 follow_up 排队下一轮，或先 abort 中止当前流。"
)


class HastingsSession:
    """WritingSession 的标准实现：绑定一个 StoryWorkspace 与一个 AgentHarness。"""

    def __init__(self, harness: AgentHarness, workspace: StoryWorkspace) -> None:
        self._harness = harness
        self._workspace = workspace

    @classmethod
    def from_workspace(
        cls, workspace: StoryWorkspace, provider: ModelProvider, model: str
    ) -> HastingsSession:
        """按 CLI 同款方式装配工具集、system prompt 与 harness。"""
        from hastings.cli import create_writing_tools
        from hastings.prompts import build_system_prompt

        tools = create_writing_tools(workspace)
        harness = AgentHarness(
            AgentHarnessConfig(
                provider=provider,
                model=model,
                system=build_system_prompt(tools),
                tools=tools,
            )
        )
        return cls(harness=harness, workspace=workspace)

    @property
    def workspace(self) -> StoryWorkspace:
        return self._workspace

    @property
    def harness(self) -> AgentHarness:
        return self._harness

    @property
    def slug(self) -> str:
        return self._workspace.slug

    @property
    def model(self) -> str:
        return self._harness.config.model

    @property
    def is_running(self) -> bool:
        return self._harness.is_running

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(tool.name for tool in self._harness.config.tools)

    @property
    def messages(self) -> tuple[AgentMessage, ...]:
        return self._harness.messages

    def prompt(self, content: str) -> AsyncIterator[AgentEvent]:
        if self._harness.is_running:
            raise AgentBusyError(BUSY_MESSAGE)
        return self._harness.prompt(content)

    def steer(self, content: str) -> None:
        self._harness.steer(content)

    def follow_up(self, content: str) -> None:
        self._harness.follow_up(content)

    def cancel(self) -> None:
        self._harness.cancel()

    def subscribe(self, listener: EventListener) -> Callable[[], None]:
        return self._harness.subscribe(listener)


# 静态结构自检：实现与 WritingSession 协议漂移时 mypy strict 即报错
if TYPE_CHECKING:

    def _conforms(session: HastingsSession) -> WritingSession:
        return session


__all__ = ["BUSY_MESSAGE", "HastingsSession"]
