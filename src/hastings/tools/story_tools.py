"""story_stage 工具（P8 T2，计划 D2）：工作区生命周期的唯一写入口。

推进是协议：模型在作者明确确认后才调用本工具；门是硬约束：write_chapter
侧的阶段预检（T4）不依赖本工具的自觉。advance/rollback 均须 reason，
迁移留痕进 story.yaml 的 stage_history（D1）。REST 不暴露 story.yaml 写口。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast

from hastings.bible.models import Method, OutlineEntry
from hastings.bible.store import (
    StoryWorkspace,
    list_characters,
    load_ledger,
    load_story_doc,
    save_story_doc,
)
from hastings.bible.story_state import (
    ADVANCE_EDGES,
    ROLLBACK_EDGES,
    StagePrereqs,
    StageTransition,
    StoryStage,
    validate_transition,
)
from hastings.tools.architecture_gate import is_v2, refuse_tool, v2_write_rejection
from hastings.tools.errors import ToolInputError
from tau_agent.messages import TextContent
from tau_agent.tools import AgentTool, AgentToolResult, ToolCancellationToken, ToolUpdateCallback
from tau_agent.types import JSONValue

_VALID_ACTIONS = ("advance", "rollback")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _require_str(arguments: Mapping[str, JSONValue], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolInputError(
            f"参数错误：{key} 必须是非空字符串。"
            f"为什么错：当前 arguments 缺少或传空了 {key}。"
            "怎样算对：补上该参数后重试（枚举取值见工具 schema）。"
        )
    return value


def _derive_prereqs(workspace: StoryWorkspace) -> StagePrereqs:
    """从工作区实况派生确认门计数（纯读，不落盘）。"""
    methods = load_ledger(workspace.method_path, Method)
    outlines = load_ledger(workspace.outline_path, OutlineEntry)
    return StagePrereqs(
        characters=len(list_characters(workspace)),
        methods=len(methods),
        methods_locked=sum(1 for m in methods if m.status == "locked"),
        outlines=len(outlines),
    )


def _resolve_target(action: str, stage: str, target: str | None) -> str:
    """解析并校验目标阶段：advance 缺省取正向边第一条（writing 态默认去
    revising，完结须显式 target="complete"）；rollback 必须显式指定。"""
    edges = ADVANCE_EDGES if action == "advance" else ROLLBACK_EDGES
    allowed = edges.get(stage, [])
    if target is None:
        if action == "rollback" or not allowed:
            raise ToolInputError(
                f"参数错误：target 缺省但 {stage} 没有缺省去向。"
                "为什么错：回退必须显式指定目标阶段（只允许相邻回退边）。"
                f"怎样算对：从合法边中选：{'、'.join(allowed) or '（无）'}。"
            )
        return allowed[0]
    if target not in allowed:
        # 单值指令（H2）：弱模型对「从合法边中选」的枚举式纠偏不执行
        # （C1-qwen3-32k，2026-10-12：被拒后不照抄、不重试）。改为直接给出
        # 本次该用的 target，让纠偏从「枚举里挑」变成「照抄这一步」。
        hint = (
            f"怎样算对：阶段必须一步一格推进，本次请把 target 改为 {allowed[0]!r} 后重试"
            f"（{stage} 的合法去向：{'、'.join(allowed)}）。"
            if allowed
            else f"怎样算对：{stage} 没有 {action} 去向，请先核对当前阶段。"
        )
        raise ToolInputError(
            f"参数错误：action={action} 时 target={target!r} 不是 {stage} 的合法去向。"
            "为什么错：迁移表没有登记这条边（或方向与 action 不符）。"
            f"{hint}"
        )
    return target


def _render_story_stage(arguments: Mapping[str, JSONValue]) -> str | None:
    action = arguments.get("action")
    target = arguments.get("target")
    verb = "推进" if action == "advance" else "回退"
    return f"{verb}阶段 → {target}" if isinstance(target, str) else f"{verb}阶段"


def create_story_tools(
    workspace: StoryWorkspace, *, architecture_version: str = "v1"
) -> list[AgentTool]:
    """创建绑定 workspace 的 story_stage 工具。

    ``architecture_version="v2"`` 时被替换为拒绝桩（T17.6）：V2 不再用全局阶段
    作为写入授权；项目里程碑只用于导航，各类对象各有自己的状态。
    """

    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        action = _require_str(arguments, "action")
        reason = _require_str(arguments, "reason")
        if action not in _VALID_ACTIONS:
            raise ToolInputError(
                f"参数错误：action={action!r}。"
                "为什么错：只接受 advance（推进）或 rollback（回退）。"
                "怎样算对：作者确认后推进传 advance；作者要求推翻设定/返工传 rollback。"
            )
        target_arg = arguments.get("target")
        if target_arg is not None and not isinstance(target_arg, str):
            raise ToolInputError(
                f"参数错误：target 须为字符串，收到 {type(target_arg).__name__}。"
                "为什么错：目标阶段是阶段名（如 bible_confirmed）。"
                "怎样算对：从工具 schema 的六阶段枚举中取值。"
            )

        doc = load_story_doc(workspace)
        stage: StoryStage = doc.stage
        target = _resolve_target(action, stage, target_arg)
        try:
            validate_transition(stage, target, prereqs=_derive_prereqs(workspace))
        except ValueError as exc:
            raise ToolInputError(str(exc)) from exc

        doc.stage = cast(StoryStage, target)
        doc.stage_history.append(
            StageTransition.model_validate(
                {"from": stage, "to": target, "reason": reason.strip(), "at": _now_iso()}
            )
        )
        save_story_doc(workspace, doc)
        verb = "推进" if action == "advance" else "回退"
        return AgentToolResult(
            content=[
                TextContent(
                    text=(f"阶段已{verb}：{stage} → {target}。理由已留痕：{reason.strip()}")
                )
            ],
            details={
                "action": action,
                "from": stage,
                "to": target,
                "reason": reason.strip(),
                "history_count": len(doc.stage_history),
            },
        )

    tool = AgentTool(
        name="story_stage",
        label="推进/回退创作阶段",
        description=(
            "推进或回退工作区的创作阶段（story.yaml 生命周期：intake → "
            "bible_drafting → bible_confirmed → writing → revising → complete）。"
            "用法：作者明确确认 Bible 后 advance 到 bible_confirmed（确认门要求"
            "角色≥2、手法全部锁定、章纲≥1，缺口见错误消息）；作者要求推翻设定"
            "或终稿返工时 rollback。advance 缺省去下一阶段，writing 态完结须"
            '显式 target="complete"。reason 必填并留痕。'
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_VALID_ACTIONS),
                    "description": "advance=推进，rollback=回退",
                },
                "reason": {
                    "type": "string",
                    "description": "迁移理由（作者决策依据），必填，进 stage_history 留痕",
                },
                "target": {
                    "type": "string",
                    "description": (
                        "目标阶段（六选一：intake/bible_drafting/bible_confirmed/"
                        "writing/revising/complete）；advance 缺省为下一阶段，rollback 必填"
                    ),
                },
            },
            "required": ["action", "reason"],
            "additionalProperties": False,
        },
        execute_fn=execute,
        prompt_guidelines=(
            "story_stage 是阶段硬门的钥匙：未经作者明确确认不要 advance 到 "
            "bible_confirmed；被确认门拒绝时按错误消息补齐账本缺口后重试。",
        ),
        render_call=_render_story_stage,
    )
    if is_v2(architecture_version):
        return [
            refuse_tool(
                tool,
                rejection=v2_write_rejection(
                    "story_stage",
                    alternative="V2 项目不用全局阶段做写入授权；项目里程碑仅供导航，"
                    "写入由各领域命令在自己对象的状态上完成。",
                ),
            )
        ]
    return [tool]


__all__ = ["create_story_tools"]
