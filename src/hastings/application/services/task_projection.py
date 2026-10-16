"""ChatTaskProjector（Phase 19.5 T19.5a）：Job 事实 → 对话候选投影。

任务卡只投影实际 Job/Change/Commit，不维护另一份执行真相（计划 §19.5）：
扫描已启动的 draft，把对应 Job 的状态变化落成对话事件——``waiting`` 状态
产生 `candidate.ready` 事件与候选消息（一次且仅一次）；``completed`` /
``terminal_failed`` / ``cancelled`` 只落定 draft，取消的任务永远不产出
可接受候选（迟到输出无效，A16）。

接受不在这里：投影只负责「有候选可审」，接受走显式入口
（``TaskPrepService.accept_candidate`` → 既有工作流审阅规则）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime
from inspect import isawaitable
from typing import Any

from hastings.application.ports.conversations import ConversationStore
from hastings.application.ports.task_drafts import TaskDraftStore
from hastings.domain.conversation import models as m
from hastings.domain.conversation.task_drafts import DRAFT_STARTED, TaskDraft

__all__ = ["ChatTaskProjector"]

_JOB_GETTER = Callable[[str], "dict[str, Any] | None | Awaitable[dict[str, Any] | None]"]


def _summarize(output: dict[str, Any]) -> str:
    """从 worker 输出提取候选摘要；不虚构内容，缺省按类型说明。"""
    kind = str(output.get("kind", ""))
    if rationale := output.get("rationale"):
        return str(rationale)
    if kind == "story_proposal":
        items = output.get("items") or []
        return f"规划候选：{len(items)} 项结构建议，等待作者审阅"
    if kind == "manuscript_patch":
        chapter = output.get("chapter_id") or "目标章节"
        return f"正文候选：{chapter} 的整章草稿，等待作者审阅"
    if kind == "clarification":
        return "模型请求澄清：缺少必要事实，任务暂停等待补充"
    return f"任务产出（{kind or '未知类型'}），等待作者审阅"


class ChatTaskProjector:
    """把已启动 draft 对应 Job 的状态变化投影到对话流。"""

    def __init__(
        self,
        drafts: TaskDraftStore,
        conversations: ConversationStore,
        *,
        job_getter: _JOB_GETTER,
    ) -> None:
        self._drafts = drafts
        self._conversations = conversations
        self._job_getter = job_getter

    async def sync(self) -> int:
        """扫描一轮；返回新投影的候选数。幂等：同一 Job 至多一条候选。"""
        projected = 0
        for draft in await self._drafts.list_by_status(DRAFT_STARTED):
            if not draft.job_ref:
                continue
            fetched = self._job_getter(draft.job_ref)
            job = await fetched if isawaitable(fetched) else fetched
            if job is None:
                continue
            state = str(job.get("state", ""))
            if (
                state == "waiting"
                and draft.candidate_message_id is None
                and job.get("job_type") != "change"
            ):
                if await self._project_candidate(draft, job):
                    projected += 1
                draft = await self._drafts.get_draft(draft.draft_id) or draft
            await self._project_execution(draft, job)
            if state in ("completed", "terminal_failed", "cancelled") and not draft.job_settled:
                await self._update_draft(draft, {**draft.to_dict(), "job_settled": True})
        return projected

    async def _project_execution(self, draft: TaskDraft, job: dict[str, Any]) -> None:
        states = {
            "queued": ("queued", "已排队"),
            "running": ("running", "模型执行中"),
            "waiting": ("waiting", "候选已返回，等待作者审阅"),
            "completed": ("completed", "任务已完成"),
            "retryable_failed": ("queued", "环境故障，等待重试"),
            "terminal_failed": ("failed", "执行失败，需要处理"),
            "cancelled": ("cancelled", "任务已取消"),
        }
        state, stage = states.get(str(job.get("state")), ("queued", "已排队"))
        stage = str(job.get("stage") or stage)
        output = dict(job.get("output") or {})
        error = None
        if state == "failed":
            error = {
                "code": "TASK_EXECUTION_FAILED",
                "category": output.get("category", "environment"),
                "where": "task.execute",
                "why": output.get("reason", "任务执行失败"),
                "fix": "查看任务详情并处理原因后，重新准备任务",
            }
        binding = dict((job.get("payload") or {}).get("model_binding") or {})
        payload = {
            "job_ref": draft.job_ref,
            "draft_id": draft.draft_id,
            "task_kind": draft.task_kind,
            "state": state,
            "stage": stage,
            "model": binding.get("model", ""),
            "candidate_message_id": draft.candidate_message_id,
            "error": error,
            "change_ref": job.get("change_ref"),
        }
        message_id = f"{draft.message_id}:execution"
        message = await self._conversations.get_message(message_id)
        if message is None:
            message = m.ChatMessage(
                message_id,
                draft.conversation_id,
                None,
                m.ROLE_SYSTEM,
                m.KIND_TASK_EXECUTION,
                "",
                created_at=draft.created_at,
            )
        if message.payload == payload:
            return
        await self._conversations.append_message(replace(message, payload=payload))
        await self._conversations.append_event(
            project_id=draft.project_id,
            conversation_id=draft.conversation_id,
            turn_id=None,
            event={"type": "task.updated", "message_id": message_id, "execution": payload},
        )

    async def _project_candidate(self, draft: TaskDraft, job: dict[str, Any]) -> bool:
        output = dict(job.get("output") or {})
        context = dict((job.get("payload") or {}).get("context") or {})
        payload = {
            "job_ref": draft.job_ref,
            "summary": _summarize(output),
            "target_ids": [str(t) for t in context.get("target_ids", [])],
            "change_ref": None,
            "accepted": False,
        }
        now = datetime.now().astimezone().isoformat()
        message_id = f"{draft.message_id}:candidate"
        await self._conversations.append_message(
            m.ChatMessage(
                message_id=message_id,
                conversation_id=draft.conversation_id,
                turn_id=None,
                role=m.ROLE_SYSTEM,
                kind=m.KIND_CANDIDATE,
                text="",
                payload=payload,
                created_at=now,
            )
        )
        await self._conversations.append_event(
            project_id=draft.project_id,
            conversation_id=draft.conversation_id,
            turn_id=None,
            event={
                "type": "candidate.ready",
                "job_ref": draft.job_ref,
                "message_id": message_id,
                "candidate": payload,
            },
        )
        await self._update_draft(draft, {**draft.to_dict(), "candidate_message_id": message_id})
        return True

    async def _update_draft(self, _draft: TaskDraft, record: dict[str, Any]) -> None:
        record = {**record, "updated_at": datetime.now().astimezone().isoformat()}
        await self._drafts.update_draft(TaskDraft.from_dict(record))
