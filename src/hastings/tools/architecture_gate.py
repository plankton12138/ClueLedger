"""V2 项目的工具层写门（Phase 17 T17.6：旧写路径不可达证明）。

V2 项目只允许经领域命令与 Commit Service 写入。旧的通用写工具（``bible_upsert``、
``write_chapter``、``method_lock``/``method_unlock``、``story_stage``）会绕过版本检查、
依赖边与领域不变量，或在 V2 里冒充授权——因此对 V2 项目一律替换为**拒绝桩**。

保留同名工具而不省略，是为了让越界调用显式失败并给出修正指引（四透镜①②），
而不是退化成「未知工具」的静默混乱；这与 ``bible_tools`` 里既有的 V2 拒绝桩同构。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from hastings.tools.errors import ToolInputError
from tau_agent.tools import (
    AgentTool,
    AgentToolResult,
    ToolCancellationToken,
    ToolUpdateCallback,
)
from tau_agent.types import JSONValue

__all__ = ["V2", "is_v2", "refuse_tool"]

V2 = "v2"

_GUIDELINE = (
    "本项目 architecture_version=v2：{name} 已停用（调用必被拒绝）。"
    "V2 项目只允许经领域命令与 Commit Service 写入；读取用对应查询工具。"
)


def is_v2(architecture_version: str) -> bool:
    """项目声明的架构版本是否为 V2（唯一判定点，工具层不解析设置文件）。"""
    return architecture_version == V2


def refuse_tool(tool: AgentTool, *, rejection: str) -> AgentTool:
    """返回一个同名的拒绝桩：execute 固定抛 ``ToolInputError``。

    说明与执行函数必须同源（与 ``bible_tools`` 的 V2 桩同教训）：若 description /
    prompt_guidelines 仍指示模型调用，模型会陷入「提示要求调用 / 工具固定拒绝」的
    循环。这里把说明改成明确的不可用宣告，并替换执行函数。
    """

    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, arguments, signal, on_update
        raise ToolInputError(rejection)

    return replace(
        tool,
        description=f"（V2 项目不可用）{tool.description}",
        execute_fn=execute,
        prompt_guidelines=(_GUIDELINE.format(name=tool.name),),
    )


def v2_write_rejection(tool_name: str, *, alternative: str) -> str:
    """统一的三要素拒绝消息：哪里错 / 为什么错 / 怎样算对。"""
    return (
        f"{tool_name} 在 V2 项目中不可用（本项目 architecture_version=v2）。"
        "为什么错：V2 项目只允许经领域命令与 Commit Service 写入，旧通用写工具会绕过"
        "版本检查、依赖边与领域不变量（或把全局阶段误当授权）。"
        f"怎样算对：{alternative}"
    )
