"""无作品入口预约与无模型想法记录（M02/V03-1.2）。

三个独立能力（计划 §6.2 用例矩阵）：

1. ``reserve_once(entry_id, request_id)``：持久 ``entry_id + request_id``
   预约作品/讨论 ID。作品身份由 entry_id 确定性派生——重试找回同一作品，
   不重复创建；预约记录落仓库辅助表（V03-0.5 的 aux 端口）。
2. ``save_author_once``：无模型端口保存想法——只写 ``author`` 消息与
   ``not_requested`` 状态；本类**不持有任何模型运行端口**（保存与启动
   是两个用例，计划 M02）。
3. ``save_author_message``：已有作品的后续想法，复用消息库，同样无模型。

消息 ID 由 (conversation_id, request_id) 确定性派生：存储崩溃窗口内
重试不会产生重复消息（读在前 + 唯一约束兜底）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from hastings.application.errors import ViolationError
from hastings.application.ports.conversations import ConversationStore
from hastings.application.services.commit_service import CommitService
from hastings.domain.conversation import models as m
from hastings.domain.repository.ids import Actor, ProjectId
from hastings.domain.repository.requests import AuxWrite, CommitRequest
from hastings.domain.repository.serialization import content_hash

__all__ = ["EntryService", "EntryTarget", "SavedIdea"]

#: 预约记录在辅助表中的 bucket（key = entry_id）。
ENTRY_BUCKET = "entry"
_AUTHOR = Actor(kind="human", id="author")


@dataclass(frozen=True, slots=True)
class EntryTarget:
    """入口预约结果：实际作品/讨论/消息归属。"""

    entry_id: str
    project_id: str
    conversation_id: str


@dataclass(frozen=True, slots=True)
class SavedIdea:
    """无模型保存的想法：目标 + 消息 + 状态（not_requested）。"""

    target: EntryTarget
    message_id: str
    status: str


class EntryService:
    """入口预约与无模型想法；模型启动是另一个用例（不在此类）。"""

    def __init__(
        self,
        repo: CommitService,
        conversations: ConversationStore,
        clock: Any,
    ) -> None:
        self._repo = repo
        self._conversations = conversations
        self._clock: Any = clock

    # -- 确定性身份 ------------------------------------------------------------

    @staticmethod
    def deterministic_project_id(entry_id: str) -> str:
        """entry_id → 作品 ID：同入口的任何次重试都落到同一作品。"""
        return f"prj_{content_hash(['entry', entry_id])[:24]}"

    @staticmethod
    def _message_id(conversation_id: str, request_id: str) -> str:
        return f"msg_{content_hash([conversation_id, request_id])[:16]}"

    # -- 预约 ------------------------------------------------------------------

    async def reserve_once(
        self, entry_id: str, request_id: str, title: str = "新想法"
    ) -> EntryTarget:
        """预约（entry_id, request_id）→ 作品+讨论；幂等，重试不重复建作品。"""
        if not entry_id or not request_id:
            raise ViolationError(
                code="ENTRY_ID_REQUIRED",
                where="entries.reserve",
                why="entry_id/request_id 不能为空（为什么错：入口预约依赖两者做幂等）",
                fix="首次进入生成稳定 entry_id；每次保存生成新的 request_id",
            )
        project_id = self.deterministic_project_id(entry_id)
        # 1) 作品：确定性身份，已存在即找回（不覆盖显示名）
        self._repo.create_project(title, project_id=project_id)
        # 2) 预约记录：决定讨论归属
        record = self._repo.aux_read(ProjectId(project_id), ENTRY_BUCKET, entry_id) or {}
        conversation_id = record.get("conversation_id")
        if not conversation_id:
            conversation = await self._conversations.create_conversation(project_id, title)
            conversation_id = conversation.conversation_id
            self._write_record(project_id, entry_id, {**record, "conversation_id": conversation_id})
        return EntryTarget(
            entry_id=entry_id, project_id=project_id, conversation_id=str(conversation_id)
        )

    # -- 无模型保存 --------------------------------------------------------------

    async def save_author_once(
        self, entry_id: str, request_id: str, text: str, title: str = "新想法"
    ) -> SavedIdea:
        """保存想法（无模型）：author 消息 + not_requested；重试返回原消息。"""
        target = await self.reserve_once(entry_id, request_id, title=title)
        record = self._repo.aux_read(ProjectId(target.project_id), ENTRY_BUCKET, entry_id) or {}
        messages: dict[str, Any] = dict(record.get("messages") or {})
        previous = messages.get(request_id)
        text_hash = content_hash([text])
        if previous is not None:
            if previous.get("text_hash") != text_hash:
                raise ViolationError(
                    code="OPERATION_KEY_REUSED",
                    where="entries.save_idea",
                    why=f"request_id {request_id!r} 已被不同内容的想法使用",
                    fix="新想法使用新的 request_id；重试请携带相同文本",
                    details={"request_id": request_id},
                )
            return SavedIdea(
                target=target,
                message_id=str(previous["message_id"]),
                status=m.MESSAGE_NOT_REQUESTED,
            )
        message_id = self._message_id(target.conversation_id, request_id)
        existing = await self._conversations.get_message(message_id)
        if existing is None:
            message = m.ChatMessage(
                message_id=message_id,
                conversation_id=target.conversation_id,
                turn_id=None,
                role=m.ROLE_AUTHOR,
                kind=m.KIND_TEXT,
                text=text,
                status=m.MESSAGE_NOT_REQUESTED,
                created_at=self._now(),
            )
            await self._conversations.append_message(message)
        messages[request_id] = {"message_id": message_id, "text_hash": text_hash}
        self._write_record(target.project_id, entry_id, {**record, "messages": messages})
        return SavedIdea(target=target, message_id=message_id, status=m.MESSAGE_NOT_REQUESTED)

    async def save_author_message(self, conversation_id: str, request_id: str, text: str) -> str:
        """已有作品的后续想法（无模型）：幂等返回消息 ID。"""
        conversation = await self._conversations.get_conversation(conversation_id)
        if conversation is None:
            raise ViolationError(
                code="CHAT_CONVERSATION_NOT_FOUND",
                where="chat.author_message",
                why=f"讨论 {conversation_id!r} 不存在",
                fix="先创建或选择当前作品的讨论",
            )
        del conversation  # 归属校验已由端点层完成；这里只确认存在
        message_id = self._message_id(conversation_id, request_id)
        existing = await self._conversations.get_message(message_id)
        if existing is not None:
            if existing.text != text:
                raise ViolationError(
                    code="OPERATION_KEY_REUSED",
                    where="chat.author_message",
                    why=f"request_id {request_id!r} 已被不同内容的消息使用",
                    fix="新消息使用新的 request_id；重试请携带相同文本",
                    details={"request_id": request_id},
                )
            return message_id
        message = m.ChatMessage(
            message_id=message_id,
            conversation_id=conversation_id,
            turn_id=None,
            role=m.ROLE_AUTHOR,
            kind=m.KIND_TEXT,
            text=text,
            status=m.MESSAGE_NOT_REQUESTED,
            created_at=self._now(),
        )
        await self._conversations.append_message(message)
        return message_id

    # -- 内部 ------------------------------------------------------------------

    def _now(self) -> str:
        return datetime.now().astimezone().isoformat()

    def _write_record(self, project_id: str, entry_id: str, payload: Mapping[str, Any]) -> None:
        branch = self._repo.get_branch(ProjectId(project_id), "main")
        if branch is None:  # pragma: no cover - create_project 保证分支存在
            raise ViolationError(
                code="REPO_BRANCH_NOT_FOUND",
                where="entries.reserve",
                why=f"作品 {project_id!r} 没有 main 分支",
                fix="重新创建作品后重试",
            )
        self._repo.commit(
            CommitRequest(
                project_id=ProjectId(project_id),
                branch_id=branch.id,
                expected_head=branch.head_commit_id,
                actor=_AUTHOR,
                idempotency_key=f"entry:{entry_id}:{content_hash([payload])[:12]}",
                summary="入口预约记录",
                aux_writes=(AuxWrite(ENTRY_BUCKET, entry_id, dict(payload)),),
            )
        )
