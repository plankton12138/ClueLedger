"""写作守门装配（P10 T3/T4，D2/D3）：一处接线，CLI/eval/Web 三端生效。

``build_harness``（CLI/eval）与 ``HastingsSession.from_workspace``（Web）两个
装配点共用本函数——守门策略只有一处事实源。守卫全部走 tau 现有公开接口
（事件订阅 + ``config.before_tool_call`` + ``harness.steer``），tau 内核 diff 为空：

- ``StageAdvanceGuard``（D3）：单次 prompt 内 ``story_stage advance`` 至多一次；
- ``ProseLeakGuard``（D2）：写作阶段正文直出经 steering 纠偏。
"""

from __future__ import annotations

from hastings.bible.store import StoryWorkspace
from hastings.manuscript.advance_guard import StageAdvanceGuard
from hastings.manuscript.prose_guard import ProseLeakGuard
from tau_agent.harness import AgentHarness


def attach_guards(harness: AgentHarness, workspace: StoryWorkspace) -> StageAdvanceGuard:
    """装配写作守门；返回 advance 守卫供测试断言。"""
    advance_guard = StageAdvanceGuard()
    harness.subscribe(advance_guard.on_event)
    harness.config.before_tool_call = advance_guard.before_tool_call
    harness.subscribe(ProseLeakGuard(workspace, harness).on_event)
    return advance_guard


__all__ = ["attach_guards"]
