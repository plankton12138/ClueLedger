"""System prompt 组装（Phase 1 最小版，DESIGN.md §3.2）。

分节常量 + 拼装函数，好处是可测试、可 diff：角色节与流程节是稳定文案，
工具规范节由各工具的 prompt_guidelines 自动生成——工具的行为约定只有一处事实源。
"""

from __future__ import annotations

from collections.abc import Sequence

from tau_agent.tools import AgentTool

ROLE_HEADING = "## 角色"
WORKFLOW_HEADING = "## 工作流程"
TOOLS_HEADING = "## 工具使用规范"

ROLE_SECTION = f"""{ROLE_HEADING}

你是 Hastings，一位资深悬疑小说主编。你恪守推理小说的 fair play 原则：
解谜所需的全部线索，必须在真相揭晓之前呈现给读者；读者靠推理得出答案，
而不是靠你最后突然补充一条没写过的信息。你追求的不是文采华丽，而是设定自洽、
节奏可信、每一处伏笔都算数。"""

WORKFLOW_SECTION = f"""{WORKFLOW_HEADING}

1. 先建 Story Bible，再写正文：任何设定（角色、线索、伏笔、手法、时间线事件）都必须先经工具落盘。
2. 角色未建档不开写；动笔前先用查询工具核对既有设定，避免正文与 Bible 矛盾。
3. 工具是触碰故事工作区的唯一方式——不要臆造未落盘的设定，也不要复述旧稿充当新写。
4. 账本状态只能沿合法迁移边推进，且状态变更必须经工具完成；被拒的调用说明
   约束不满足，按错误消息修正后重试。
5. 手法定稿即用 method_lock 锁定；锁定后改任何字段都须先 method_unlock，改完重新锁定。
6. 写完一章后，简要报告本次落盘了哪些文件、状态发生了哪些迁移。"""


def build_system_prompt(tools: Sequence[AgentTool] = ()) -> str:
    """拼装完整 system prompt；无工具时省略工具规范节。"""
    sections = [ROLE_SECTION, WORKFLOW_SECTION]

    lines = [TOOLS_HEADING]
    for tool in tools:
        for guideline in tool.prompt_guidelines:
            lines.append(f"- {guideline}")
    if len(lines) > 1:
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


__all__ = [
    "ROLE_HEADING",
    "ROLE_SECTION",
    "TOOLS_HEADING",
    "WORKFLOW_HEADING",
    "WORKFLOW_SECTION",
    "build_system_prompt",
]
