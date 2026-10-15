"""写作守门装配（P10 T3/T4，D2/D3）：一处接线，CLI/eval/Web 三端生效。

``build_harness``（CLI/eval）与 ``HastingsSession.from_workspace``（Web）两个
装配点共用本函数——守门策略只有一处事实源。守卫全部走 tau 现有公开接口
（事件订阅 + ``config.before_tool_call`` + ``harness.steer``），tau 内核 diff 为空：

- ``StageAdvanceGuard``（D3）：单次 prompt 内 ``story_stage advance`` 至多
  ``max_advances`` 次（产品默认 1，一次推进对应一次作者确认）；
- ``ProseLeakGuard``（D2）：写作阶段正文直出经 steering 纠偏。

``max_advances`` 仅评测口径放宽用（如 C 组 4 轮脚本装不下 3 格阶段链时传 3）；
CLI/Web 两端不传即保持产品语义，放宽值随 driver/spec/报告归档保证口径可追溯。
"""

from __future__ import annotations

from hastings.bible.store import StoryWorkspace
from hastings.manuscript.advance_guard import MAX_ADVANCES_PER_PROMPT, StageAdvanceGuard
from hastings.manuscript.prose_guard import ProseLeakGuard
from tau_agent.harness import AgentHarness


def attach_guards(
    harness: AgentHarness,
    workspace: StoryWorkspace,
    *,
    max_advances: int = MAX_ADVANCES_PER_PROMPT,
) -> StageAdvanceGuard:
    """装配写作守门；返回 advance 守卫供测试断言。"""
    advance_guard = StageAdvanceGuard(max_advances=max_advances)
    harness.subscribe(advance_guard.on_event)
    harness.config.before_tool_call = advance_guard.before_tool_call
    harness.subscribe(ProseLeakGuard(workspace, harness).on_event)
    return advance_guard


__all__ = ["attach_guards"]
