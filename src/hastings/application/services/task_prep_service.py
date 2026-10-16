"""TaskPrepService（Phase 19.4 T19.4b）：任务建议的生命周期与显式启动。

职责边界（计划 §19.4）：只做「参数检查 → 建议落盘 → 显式启动」，建议本身
不创建 Job、不触碰领域写端口；「开始」在缺失项清空且基础版本未过期时才
放行，把 job_ref 与幂等键绑定回 draft（同键重放返回同一绑定）。

事件与消息：draft 的每次变化同步落到对话流——``task.draft.created/updated/
abandoned`` 与 ``task.linked`` 事件 + kind=task_draft 的消息载体，前端从
SSE 与历史重放两个入口看到同一份事实。
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from hastings.application.errors import ViolationError
from hastings.application.ports.conversations import ConversationStore
from hastings.application.ports.task_drafts import TaskDraftStore
from hastings.domain.conversation import error_codes as ec
from hastings.domain.conversation import models as m
from hastings.domain.conversation.task_drafts import (
    DRAFT_ABANDONED,
    DRAFT_ACTIVE,
    DRAFT_STARTED,
    TASK_KINDS,
    ProjectView,
    TaskDraft,
    missing_items,
)

__all__ = ["TaskPrepService"]

Starter = Callable[[str, TaskDraft, str], Awaitable[str]]


class TaskPrepService:
    """任务准备应用服务：draft 存储 + 项目只读视图 + 显式启动入口。"""

    def __init__(
        self,
        drafts: TaskDraftStore,
        conversations: ConversationStore,
        *,
        project_view: Callable[[str], ProjectView],
        starter: Starter | None = None,
        model_label: Callable[[], str | None] | None = None,
        accepter: Callable[[str, str], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self._drafts = drafts
        self._conversations = conversations
        self._project_view = project_view
        self._starter = starter
        self._model_label = model_label
        # 候选接受入口（19.5）：注入既有工作流的 accept（job_ref, expected_head），
        # 服务本身不实现第二套审阅规则（§3.3：聊天文本不代替审批）。
        self._accepter = accepter
        self._lock = threading.Lock()

    # -- 助手 ---------------------------------------------------------------
    def _now(self) -> str:
        return datetime.now().astimezone().isoformat()

    async def _require_conversation(self, project_id: str, conversation_id: str) -> None:
        conversation = await self._conversations.get_conversation(conversation_id)
        if conversation is None or conversation.project_id != project_id:
            raise ViolationError(
                code=ec.CHAT_CONVERSATION_NOT_FOUND,
                where="chat.conversation",
                why=f"项目 {project_id} 下没有会话 {conversation_id}",
                fix="在当前项目里新建或选择一个会话",
            )

    async def _require_draft(self, project_id: str, draft_id: str) -> TaskDraft:
        draft = await self._drafts.get_draft(draft_id)
        if draft is None or draft.project_id != project_id:
            raise ViolationError(
                code=ec.TASK_DRAFT_NOT_FOUND,
                where="task.draft",
                why=f"项目 {project_id} 下没有任务建议 {draft_id}",
                fix="从消息「准备为任务」重新创建，或检查 draft_id",
            )
        return draft

    async def _emit(
        self, draft: TaskDraft, event: dict[str, Any]
    ) -> None:
        await self._conversations.append_event(
            project_id=draft.project_id,
            conversation_id=draft.conversation_id,
            turn_id=None,
            event=event,
        )

    async def _sync_message(self, draft: TaskDraft) -> None:
        message = await self._conversations.get_message(draft.message_id)
        if message is None:
            return
        await self._conversations.append_message(
            m.ChatMessage(
                message_id=message.message_id,
                conversation_id=message.conversation_id,
                turn_id=message.turn_id,
                role=message.role,
                kind=m.KIND_TASK_DRAFT,
                text=message.text,
                refs=message.refs,
                model=message.model,
                status=message.status,
                error=message.error,
                created_at=message.created_at,
                payload=draft.to_payload(),
            )
        )

    # -- 创建 ------------------------------------------------------------------
    async def create_draft(
        self,
        project_id: str,
        conversation_id: str,
        task_kind: str,
        *,
        instruction: str = "",
        goal: str = "",
        target_ids: list[str] | None = None,
        source_message_id: str | None = None,
    ) -> tuple[TaskDraft, str]:
        await self._require_conversation(project_id, conversation_id)
        if task_kind not in TASK_KINDS:
            raise ViolationError(
                code=ec.CHAT_UNKNOWN_FIELD_VALUE,
                where="task.draft.task_kind",
                why=f"未知任务类型 {task_kind!r}",
                fix=f"使用 {sorted(TASK_KINDS)} 之一",
            )
        refs: list[dict[str, Any]] = []
        basis: list[str] = []
        if source_message_id is not None:
            source = await self._conversations.get_message(source_message_id)
            if source is None or source.conversation_id != conversation_id:
                raise ViolationError(
                    code=ec.CHAT_MESSAGE_NOT_FOUND,
                    where="task.draft.source_message",
                    why=f"会话 {conversation_id} 下没有消息 {source_message_id}",
                    fix="从当前会话的一条消息重新准备任务",
                )
            # 引用只来自作者明确指定的源消息，不做任何自动附加。
            refs = [dict(r) for r in source.refs]
            basis = [str(r.get("title", "")) for r in refs if r.get("title")]
        with self._lock:
            view = self._project_view(project_id)
            now = self._now()
            draft_id = f"draft_{uuid.uuid4().hex[:16]}"
            message_id = f"msg_{uuid.uuid4().hex[:16]}"
            draft = TaskDraft(
                draft_id=draft_id,
                project_id=project_id,
                conversation_id=conversation_id,
                message_id=message_id,
                revision=1,
                task_kind=task_kind,
                instruction=instruction,
                goal=goal,
                target_ids=list(target_ids or []),
                base_version=view.head,
                refs=refs,
                basis=basis,
                missing=missing_items(
                    task_kind,
                    goal=goal,
                    target_ids=list(target_ids or []),
                    base_version=view.head,
                    view=view,
                ),
                source_message_id=source_message_id,
                status=DRAFT_ACTIVE,
                created_at=now,
                updated_at=now,
            )
            await self._drafts.create_draft(draft)
            await self._conversations.append_message(
                m.ChatMessage(
                    message_id=message_id,
                    conversation_id=conversation_id,
                    turn_id=None,
                    role=m.ROLE_ASSISTANT,
                    kind=m.KIND_TASK_DRAFT,
                    text=f"任务建议：{instruction or task_kind}",
                    payload=draft.to_payload(),
                    created_at=now,
                )
            )
            await self._emit(
                draft,
                {"type": "task.draft.created", "draft": draft.to_payload(),
                 "message_id": message_id},
            )
        return draft, message_id

    # -- 查看 ------------------------------------------------------------------
    async def get_draft(self, project_id: str, draft_id: str) -> TaskDraft:
        return await self._require_draft(project_id, draft_id)

    async def list_drafts(self, project_id: str, conversation_id: str) -> list[TaskDraft]:
        await self._require_conversation(project_id, conversation_id)
        drafts = await self._drafts.list_drafts(conversation_id)
        return [d for d in drafts if d.project_id == project_id]

    # -- 调整 ------------------------------------------------------------------
    async def adjust_draft(
        self,
        project_id: str,
        draft_id: str,
        expected_revision: int | None = None,
        patch: dict[str, Any] | None = None,
    ) -> TaskDraft:
        patch = dict(patch or {})
        with self._lock:
            draft = await self._require_draft(project_id, draft_id)
            if draft.status == DRAFT_ABANDONED:
                raise ViolationError(
                    code=ec.TASK_DRAFT_ABANDONED,
                    where="task.draft.adjust",
                    why="任务建议已放弃，不能再调整",
                    fix="重新创建一个任务建议",
                )
            if draft.status == DRAFT_STARTED:
                raise ViolationError(
                    code=ec.TASK_DRAFT_ALREADY_STARTED,
                    where="task.draft.adjust",
                    why="任务已启动，补充讨论不修改正在执行的目标（§4.2）",
                    fix="用新要求另建建议，或在允许的检查点补充",
                )
            if expected_revision is not None and draft.revision != expected_revision:
                raise ViolationError(
                    code=ec.TASK_REVISION_CONFLICT,
                    where="task.draft.adjust",
                    why=f"建议已被修改为修订 {draft.revision}，提交基于修订 {expected_revision}",
                    fix="重新读取任务建议后基于最新修订调整",
                    details={"current_revision": draft.revision,
                             "expected_revision": expected_revision},
                )
            view = self._project_view(project_id)
            instruction = str(patch.get("instruction", draft.instruction))
            goal = str(patch.get("goal", draft.goal))
            target_ids = [str(t) for t in patch.get("target_ids", draft.target_ids)]
            updated = TaskDraft(
                **{
                    **draft.to_dict(),
                    "instruction": instruction,
                    "goal": goal,
                    "target_ids": target_ids,
                    "revision": draft.revision + 1,
                    "missing": missing_items(
                        draft.task_kind,
                        goal=goal,
                        target_ids=target_ids,
                        base_version=draft.base_version,
                        view=view,
                    ),
                    "updated_at": self._now(),
                }
            )
            await self._drafts.update_draft(updated)
            await self._sync_message(updated)
            await self._emit(updated, {"type": "task.draft.updated", "draft": updated.to_payload()})
            return updated

    # -- 放弃 ------------------------------------------------------------------
    async def abandon_draft(self, project_id: str, draft_id: str) -> None:
        with self._lock:
            draft = await self._require_draft(project_id, draft_id)
            if draft.status == DRAFT_ABANDONED:
                return  # 幂等放弃。
            if draft.status == DRAFT_STARTED:
                raise ViolationError(
                    code=ec.TASK_DRAFT_ALREADY_STARTED,
                    where="task.draft.abandon",
                    why="任务已启动，放弃建议不等于取消任务",
                    fix="用任务卡的「取消任务」停止执行",
                )
            abandoned = TaskDraft(
                **{**draft.to_dict(), "status": DRAFT_ABANDONED, "updated_at": self._now()}
            )
            await self._drafts.update_draft(abandoned)
            await self._sync_message(abandoned)
            await self._emit(abandoned, {"type": "task.draft.abandoned", "draft_id": draft_id})

    # -- 启动 ------------------------------------------------------------------
    async def start_draft(
        self,
        project_id: str,
        draft_id: str,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
        *,
        idempotency: str | None = None,
    ) -> TaskDraft:
        idempotency_key = idempotency or idempotency_key or ""
        if not idempotency_key:
            raise ViolationError(
                code=ec.TASK_REVISION_CONFLICT if False else ec.CHAT_IDEMPOTENCY_CONFLICT,
                where="task.draft.start",
                why="缺少幂等键，无法区分重放与新请求",
                fix="为每次启动生成稳定 UUID 作为 idempotency_key",
            )
        with self._lock:
            draft = await self._require_draft(project_id, draft_id)
            if draft.status == DRAFT_ABANDONED:
                raise ViolationError(
                    code=ec.TASK_DRAFT_ABANDONED,
                    where="task.draft.start",
                    why="任务建议已放弃，不能启动",
                    fix="重新创建一个任务建议",
                )
            if draft.status == DRAFT_STARTED:
                if draft.start_key == idempotency_key:
                    return draft  # 幂等重放：同一请求返回同一绑定。
                raise ViolationError(
                    code=ec.TASK_DRAFT_ALREADY_STARTED,
                    where="task.draft.start",
                    why="该建议已经启动过一次，不能重复开工（§4.2）",
                    fix="在任务卡查看进行中的任务；新要求另建建议",
                    details={"job_ref": draft.job_ref},
                )
            if expected_revision is not None and draft.revision != expected_revision:
                raise ViolationError(
                    code=ec.TASK_REVISION_CONFLICT,
                    where="task.draft.start",
                    why=f"建议已被修改为修订 {draft.revision}，启动基于修订 {expected_revision}",
                    fix="重新读取任务建议后用最新修订启动",
                    details={"current_revision": draft.revision,
                             "expected_revision": expected_revision},
                )
            view = self._project_view(project_id)
            if draft.base_version is not None and view.head != draft.base_version:
                raise ViolationError(
                    code=ec.TASK_BASE_STALE,
                    where="task.draft.start",
                    why=f"基础版本 {draft.base_version} 已过期，项目当前在 {view.head}",
                    fix="重新读取内容并重建任务，不静默换用新版本（§3.4）",
                    details={"base_version": draft.base_version, "current_head": view.head},
                )
            if draft.missing:
                raise ViolationError(
                    code=ec.TASK_MISSING_ITEMS,
                    where="task.draft.start",
                    why="任务范围不齐全，还有缺失项未解决",
                    fix="按缺失项逐条补齐后重试；每条缺失都写明了做法",
                    details={"missing": list(draft.missing)},
                )
            if self._starter is None:
                raise ViolationError(
                    code=ec.TASK_START_UNSUPPORTED,
                    where="task.draft.start",
                    why="当前部署没有接通创作周期启动服务",
                    fix="使用既有任务页提交入口，或部署完整服务",
                )
            job_ref = await self._starter(project_id, draft, idempotency_key)
            model = self._model_label() if self._model_label is not None else None
            started = TaskDraft(
                **{
                    **draft.to_dict(),
                    "status": DRAFT_STARTED,
                    "job_ref": job_ref,
                    "start_key": idempotency_key,
                    "updated_at": self._now(),
                }
            )
            await self._drafts.update_draft(started)
            await self._sync_message(started)
            await self._emit(
                started,
                {
                    "type": "task.linked",
                    "draft_id": draft.draft_id,
                    "job_ref": job_ref,
                    "task_kind": draft.task_kind,
                    "model": model or "",
                    "message_id": draft.message_id,
                },
            )
            return started

    async def record_binding(
        self, project_id: str, draft_id: str, binding: dict[str, Any]
    ) -> None:
        """把启动时的模型快照持久化到 draft（装配层启动映射完成后调用）。"""
        draft = await self._require_draft(project_id, draft_id)
        await self._drafts.update_draft(
            TaskDraft.from_dict(
                {**draft.to_dict(), "model_binding": dict(binding),
                 "updated_at": self._now()}
            )
        )

    # -- 候选接受（19.5） ---------------------------------------------------------
    async def accept_candidate(
        self,
        project_id: str,
        conversation_id: str,
        message_id: str,
        *,
        expected_head: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """把候选消息映射回 Job 并复用既有审阅规则；聊天文本不代替审批。

        幂等：已接受的候选直接返回既有结果消息（不重复调用接受入口，
        也不产生重复结果消息）；提交层幂等由 marker Commit 兜底。
        """
        del idempotency_key  # 提交幂等由 agent_workflows.accept 的 marker 保证
        if not expected_head:
            raise ViolationError(
                code=ec.CHAT_MISSING_BASE_VERSION,
                where="chat.accept",
                why="项目版本尚未读取，无法确认接受候选的基础版本",
                fix="等正文与版本读取完成后再接受候选，避免把候选提交到过期版本",
                details={"message_id": message_id},
            )
        await self._require_conversation(project_id, conversation_id)
        message = await self._conversations.get_message(message_id)
        if message is None or message.conversation_id != conversation_id:
            raise ViolationError(
                code=ec.CHAT_MESSAGE_NOT_FOUND,
                where="chat.accept",
                why=f"会话 {conversation_id} 下没有消息 {message_id}",
                fix="刷新对话历史后重试",
            )
        if message.kind != m.KIND_CANDIDATE:
            raise ViolationError(
                code=ec.CHAT_NOT_A_CANDIDATE,
                where="chat.accept",
                why=f"消息 {message_id} 不是待审阅候选（kind={message.kind}）",
                fix="只接受任务卡产出的候选消息；普通回复不能提交项目内容",
            )
        payload = dict(message.payload or {})
        job_ref = str(payload.get("job_ref", ""))
        if payload.get("accepted"):
            # 幂等重放：返回既有结果消息的 payload。
            for existing in await self._conversations.list_messages(conversation_id):
                if existing.kind == m.KIND_RESULT and (
                    existing.payload or {}
                ).get("job_ref") == job_ref:
                    return dict(existing.payload or {})
        if self._accepter is None:
            raise ViolationError(
                code=ec.TASK_START_UNSUPPORTED,
                where="chat.accept",
                why="当前部署没有接通候选接受服务",
                fix="使用审阅页的接受入口，或部署完整服务",
            )
        resolved = await self._accepter(job_ref, expected_head)
        result = {
            "job_ref": job_ref,
            "commit_id": resolved.get("commit_id"),
            "target_ids": list(payload.get("target_ids", [])),
            "summary": str(payload.get("summary", "")),
        }
        now = self._now()
        result_message_id = f"msg_{uuid.uuid4().hex[:16]}"
        await self._conversations.append_message(
            m.ChatMessage(
                message_id=result_message_id,
                conversation_id=conversation_id,
                turn_id=None,
                role=m.ROLE_SYSTEM,
                kind=m.KIND_RESULT,
                text="",
                payload=result,
                created_at=now,
            )
        )
        accepted_candidate = m.ChatMessage(
            message_id=message.message_id,
            conversation_id=message.conversation_id,
            turn_id=message.turn_id,
            role=message.role,
            kind=message.kind,
            text=message.text,
            refs=message.refs,
            model=message.model,
            status=message.status,
            error=message.error,
            created_at=message.created_at,
            payload={**payload, "accepted": True},
        )
        await self._conversations.append_message(accepted_candidate)
        await self._conversations.append_event(
            project_id=project_id,
            conversation_id=conversation_id,
            turn_id=None,
            event={
                "type": "result.accepted",
                "job_ref": job_ref,
                "message_id": result_message_id,
                "result": result,
            },
        )
        return result
