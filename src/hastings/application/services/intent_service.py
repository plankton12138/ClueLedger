"""V03-7.1 意图分类应用服务：把作者文本/消息落成三分意图与范围。

职责边界（计划 M10 第 1–4 条、执行指导 §11.1）：

- **只读**：本服务只分类与组装范围，不写正式内容、不创建 Job、不调用模型；
- **凭据来源只能是作者**：分类输入只有作者消息文本与引用；模型回复不能作为授权源
  （模型说「已获授权」不构成授权）——非作者消息一律拒绝；
- **同范围不重复审批**：执行意图若与本次会话中已启动的、范围相同的任务一致，
  标记 ``reuses_authorization=True``，调用方据此不再追加确认；
- **硬断言**：讨论/候选意图不得携带非空写范围（``guard_intent_write_scope``），
  在凭据构建边界再加一层防御。

分类是确定性的，见 ``domain/conversation/intent.py``。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from hastings.application.errors import ViolationError
from hastings.application.ports.conversations import ConversationStore
from hastings.application.ports.task_drafts import TaskDraftStore
from hastings.domain.conversation import error_codes as ec
from hastings.domain.conversation import models as m
from hastings.domain.conversation.intent import IntentClassification, classify_author_intent
from hastings.domain.conversation.task_drafts import DRAFT_STARTED

__all__ = ["IntentService", "guard_intent_write_scope"]

#: 兼容的执行功能等价类：整体调整与定向修订在「同范围」判定上互相认作已授权。
_REVISION_FAMILY = frozenset(("revision", "holistic_revision"))

#: 已启动任务种类 → 执行功能。定向修订不复用 ``TASK_KIND_FUNCTIONS``（它走变更
#: 流程、不经模型凭据），这里按「作者视角的功能等价类」单列，供同范围复用判定。
_DRAFT_KIND_FUNCTIONS: dict[str, str] = {
    "plan": "draft",
    "chapter_draft": "draft",
    "check": "suggestion",
    "targeted_revision": "revision",
}


def guard_intent_write_scope(classification: IntentClassification) -> None:
    """不变量：只有 execution 意图允许携带非空写范围（四透镜②硬约束）。

    讨论/候选即便将来由模型提议写范围，也必须在此被拒——这是「讨论无写权限」
    的第二道锁，第一道在领域分类（discussion/candidate 恒为 ``write_scope=None``）。
    """
    if classification.intent != "execution" and classification.write_scope:
        raise ViolationError(
            code="INTENT_WRITE_SCOPE_INVALID",
            where="intent.write_scope",
            why=f"{classification.intent} 意图不允许携带写范围 {list(classification.write_scope)}",
            fix="讨论只产生回复、候选只产生可比较方案；写范围只能由执行意图在明确授权后携带",
            details={"intent": classification.intent},
        )


def _ref_targets(refs: Sequence[dict[str, Any]]) -> tuple[str, ...]:
    """从作者消息引用提取只读目标 ID（``target_id`` 优先，回落 ``ref_id``）。"""
    out: list[str] = []
    for ref in refs:
        value = ref.get("target_id") or ref.get("ref_id")
        if isinstance(value, str) and value:
            out.append(value)
    return tuple(dict.fromkeys(out))


def _functions_compatible(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return False
    if a == b:
        return True
    return {a, b} == _REVISION_FAMILY


class IntentService:
    """作者意图分类服务：文本/消息 → 意图 + 分析/写范围 + 关键选择。"""

    def __init__(
        self,
        conversations: ConversationStore,
        drafts: TaskDraftStore,
        *,
        ref_targets: Callable[[Sequence[dict[str, Any]]], tuple[str, ...]] | None = None,
    ) -> None:
        self._conversations = conversations
        self._drafts = drafts
        self._ref_targets = ref_targets or _ref_targets

    async def classify(
        self,
        project_id: str,
        conversation_id: str,
        *,
        text: str | None = None,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        """分类一次发送：给 ``message_id`` 用已保存作者消息，或给 ``text`` 预览。

        返回只读载荷：意图、功能、分析/写范围、待澄清选择、是否复用既有授权。
        没有写权限就不产生写范围；不创建任务、不调用模型。
        """
        await self._require_conversation(project_id, conversation_id)
        if message_id is not None:
            source, targets = await self._from_message(conversation_id, message_id)
        elif text is not None:
            source, targets = text, ()
        else:
            raise ViolationError(
                code=ec.CHAT_INVALID_COMMAND,
                where="intent.classify",
                why="缺少分类输入（text 与 message_id 至少一个）",
                fix="发送前给 text 预览；已保存消息给 message_id",
            )
        classification = classify_author_intent(source, target_ids=targets)
        guard_intent_write_scope(classification)
        reuse = False
        if classification.intent == "execution":
            reuse = await self._same_scope_authorized(conversation_id, classification)
        payload = classification.to_wire()
        payload.update(
            {
                "source": "author_message",
                "source_message_id": message_id,
                "reuses_authorization": reuse,
            }
        )
        return payload

    async def _from_message(
        self, conversation_id: str, message_id: str
    ) -> tuple[str, tuple[str, ...]]:
        message = await self._conversations.get_message(message_id)
        if message is None or message.conversation_id != conversation_id:
            raise ViolationError(
                code=ec.CHAT_MESSAGE_NOT_FOUND,
                where="intent.message",
                why=f"会话 {conversation_id} 下没有消息 {message_id}",
                fix="从当前会话选择一条作者消息再分类",
                details={"message_id": message_id},
            )
        if message.role != m.ROLE_AUTHOR:
            raise ViolationError(
                code=ec.CHAT_MESSAGE_NOT_FOUND,
                where="intent.message",
                why=f"消息 {message_id!r} 不是作者消息，不能作为执行授权来源",
                fix="只有作者消息/按钮能授权执行；模型回复仅供参考",
                details={"message_id": message_id},
            )
        return message.text, self._ref_targets(message.refs)

    async def _same_scope_authorized(
        self, conversation_id: str, classification: IntentClassification
    ) -> bool:
        """执行意图与已启动任务范围一致时，视为同范围已授权，不重复审批。"""
        wanted = set(classification.write_scope or ())
        if not wanted:
            return False  # 范围未知：仍需作者选择，谈不上复用。
        for draft in await self._drafts.list_drafts(conversation_id):
            if draft.status != DRAFT_STARTED or not draft.job_ref:
                continue
            if set(draft.target_ids) != wanted:
                continue
            if _functions_compatible(
                classification.function, _DRAFT_KIND_FUNCTIONS.get(draft.task_kind)
            ):
                return True
        return False

    async def _require_conversation(self, project_id: str, conversation_id: str) -> m.Conversation:
        conversation = await self._conversations.get_conversation(conversation_id)
        if conversation is None or conversation.project_id != project_id:
            raise ViolationError(
                code=ec.CHAT_CONVERSATION_NOT_FOUND,
                where="intent.conversation",
                why=f"项目 {project_id} 下没有会话 {conversation_id}",
                fix="在当前项目里新建或选择一个会话",
                details={"project_id": project_id, "conversation_id": conversation_id},
            )
        return conversation
