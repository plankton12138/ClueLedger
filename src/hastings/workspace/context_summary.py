"""完整轮次分段生成摘要；整个操作最多四次请求，不截断单轮原话。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from hastings.prompts.tasks import summary_instruction_prompt, summary_merge_prompt
from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.compaction import (
    CompactionPlan,
    CompactionRecord,
    ParsedSummary,
    build_summary_prompt,
    parse_summary,
    summary_text,
)
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.model_activity import request_estimate
from tau_agent.messages import UserMessage

MAX_SUMMARY_REQUESTS = 4


async def summarize_history(
    snapshot: ModelSnapshot,
    events: tuple[dict[str, Any], ...],
    plan: CompactionPlan,
    previous: CompactionRecord | None,
    generate: Callable[[str, str], Awaitable[str]],
    *,
    instruction: str = "",
) -> ParsedSummary:
    """分段摘要；``instruction`` 是作者在这次整理中补充的要求。

    要求进入每一次摘要请求的提示：它决定保留什么、怎么合并，不能只记在操作日志里。
    """
    system, _ = build_summary_prompt(events, plan)
    limit = snapshot.limits.input_limit() if snapshot.limits else None
    # 已成立的摘要继续作为来源；只重读新覆盖的完整轮次，避免反复支付全部旧历史。
    old_end = previous.covered_through_seq if previous else 0
    pending = [
        turn
        for turn in plan.covered_turns
        if any(event.get("turn_id") == turn and event["seq"] > old_end for event in events)
    ]
    carry = summary_text(previous.summary, previous.items) if previous else ""
    parsed: ParsedSummary | None = None
    allowed = set(plan.covered_turns) - set(pending)

    def prompt_for(turns: list[str]) -> str:
        selected = tuple(event for event in events if event.get("turn_id") in turns)
        _, text = build_summary_prompt(selected, plan)
        merged = summary_merge_prompt(carry, text) if carry else text
        if not instruction.strip():
            return merged
        return summary_instruction_prompt(instruction, merged)

    def fits(prompt: str) -> bool:
        estimate = request_estimate(
            identity=f"{snapshot.connection_id}:{snapshot.model}",
            system=system,
            messages=[UserMessage(content=prompt)],
            definitions=[],
            limits=snapshot.limits,
            source_revisions={},
        )
        size = estimate["total_tokens"]
        return limit is None or (isinstance(size, int) and size <= limit)

    for _ in range(MAX_SUMMARY_REQUESTS):
        if not pending:
            break
        group: list[str] = []
        for turn in pending:
            if not fits(prompt_for([*group, turn])):
                break
            group.append(turn)
        if not group:
            raise WorkspaceError(
                code="CONTEXT_REQUIRED_INPUT_TOO_LARGE",
                where="conversation.compaction",
                why="单个完整轮次与必要摘要超过模型输入预算",
                fix="选择容量更大的模型，或缩小后续任务范围；原会话保持完整",
            )
        text = await generate(system, prompt_for(group))
        allowed.update(group)
        parsed = parse_summary(text, allowed_sources=frozenset(allowed))
        if parsed is None:
            raise WorkspaceError(
                code="COMPACTION_FAILED",
                where="conversation.compaction",
                why="摘要格式或来源引用不成立，这次没有整理",
                fix="保留当前上下文，稍后重新整理",
                details={"preview": text[:400]},
            )
        pending = pending[len(group) :]
        carry = summary_text(parsed.summary, parsed.items)
    if pending or parsed is None:
        raise WorkspaceError(
            code="COMPACTION_BUDGET_EXCEEDED",
            where="conversation.compaction",
            why="完整历史超过本次整理的四次摘要调用预算",
            fix="选择容量更大的模型；本次未启用摘要，原上下文保留",
        )
    return parsed
