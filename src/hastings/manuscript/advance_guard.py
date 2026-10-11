"""回合规速守卫（P10 T4，D3/L4）：单次 prompt 内 story_stage advance 至多一次。

事件驱动 + 工具调用边界拦截，两层都走 tau 现有公开接口（零内核改动）：

- 订阅 ``AgentStartEvent`` 重置计数——每次 ``prompt()`` 触发一次，计数语义
  精确等于「本回合」（比 stage_history 时间戳阈值在快慢模型下都稳）；
- ``before_tool_call`` 拦截本回合第二次 ``story_stage advance``，返回
  ``(True, 拒绝文案)``——tau 的钩子契约为 ``(是否拦截, 理由)``（见 loop.py
  L299-L303 ``if blocked:`` 与 demo_agent_flow.py 的 ``(False, None)`` 放行），
  loop 生成 ``_error_result`` 作为工具结果喂回模型，拒绝即纠偏。

一次推进应对应一次作者确认，连续推进会让作者失去确认点。rollback 不限次：
作者驱动推翻设定的合法高频操作。
"""

from __future__ import annotations

from collections.abc import Mapping

from tau_agent.events import AgentEvent, AgentStartEvent
from tau_agent.messages import ToolCall

MAX_ADVANCES_PER_PROMPT = 1

REJECTION = (
    "story_stage advance 被拒：本回合已推进过一次。"
    "为什么错：一次推进应对应一次作者确认，连续推进会让作者失去确认点。"
    "怎样算对：bible_confirmed 等确认点应停留等待作者下一条消息；"
    "若作者确已要求连推，等作者回复后再推进。"
)


class StageAdvanceGuard:
    """本回合 advance 计数器：AgentStartEvent 重置 + before_tool_call 限次。"""

    def __init__(self, *, max_advances: int = MAX_ADVANCES_PER_PROMPT) -> None:
        self._max = max_advances
        self._advances = 0

    def on_event(self, event: AgentEvent) -> None:
        if isinstance(event, AgentStartEvent):
            self._advances = 0

    async def before_tool_call(self, call: ToolCall) -> tuple[bool, str | None]:
        if not _is_stage_advance(call):
            return False, None
        if self._advances >= self._max:
            return True, REJECTION
        self._advances += 1
        return False, None


def _is_stage_advance(call: ToolCall) -> bool:
    if call.name != "story_stage":
        return False
    arguments = call.arguments
    return isinstance(arguments, Mapping) and arguments.get("action") == "advance"


__all__ = ["MAX_ADVANCES_PER_PROMPT", "REJECTION", "StageAdvanceGuard"]
