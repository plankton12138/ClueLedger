"""对话 REST/SSE 出口（Phase 19.3 T19.3e）。

端点形状与前端契约 ``frontend/src/api/chat.ts`` 逐字对应（§6.2）。设计取向：

- **项目隔离**：所有会话/轮次引用先验证项目归属，跨项目一律
  ``CHAT_CONVERSATION_NOT_FOUND``（404 信封带三要素）；
- **SSE 快照与流式同一端点**：``tail=0`` 返回当前积压后关闭（测试/脚本用），
  默认长连推送；帧格式 ``data: <信封 JSON>``，序号仅在会话内递增；
- **错误信封直通**：应用层 HastingsError 经既有 handler 转换，这里不重复翻译。
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from hastings.server.v2 import V2Runtime

CHAT_PROTOCOL = "chat.v1"
_POLL_INTERVAL = 0.25
_HEARTBEAT_EVERY = 40  # 轮询次数；~10s 一个心跳注释帧


class CreateConversationRequest(BaseModel):
    title: str | None = None


class SendTurnRequest(BaseModel):
    text: str = Field(min_length=1)
    refs: list[dict[str, Any]] = Field(default_factory=list)
    idempotency_key: str = Field(min_length=1)


class SaveAuthorMessageRequest(BaseModel):
    """无模型保存后续想法（M02/V03-1.2）：只落 author 消息，不启动模型。"""

    request_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class CancelRequest(BaseModel):
    pass


class CreateTaskDraftRequest(BaseModel):
    conversation_id: str = Field(min_length=1)
    task_kind: str = Field(min_length=1)
    instruction: str = ""
    goal: str = ""
    target_ids: list[str] = Field(default_factory=list)
    source_message_id: str | None = None
    refs: list[dict[str, Any]] = Field(default_factory=list)


class AdjustTaskDraftRequest(BaseModel):
    expected_revision: int | None = None
    instruction: str | None = None
    goal: str | None = None
    target_ids: list[str] | None = None


class StartTaskDraftRequest(BaseModel):
    expected_revision: int | None = None
    idempotency_key: str = Field(min_length=1)


class AcceptCandidateRequest(BaseModel):
    expected_head: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)


def create_conversations_router(runtime: V2Runtime) -> APIRouter:
    router = APIRouter(prefix="/projects/{project_id}", tags=["chat"])

    @router.get("/conversations")
    async def list_conversations(project_id: str) -> dict[str, Any]:
        conversations = await runtime.chat.list_conversations(project_id)
        return {
            "protocol": CHAT_PROTOCOL,
            "conversations": [c.to_dict() for c in conversations],
        }

    @router.post("/conversations")
    async def create_conversation(
        project_id: str, req: CreateConversationRequest
    ) -> dict[str, Any]:
        conversation = await runtime.chat.create_conversation(project_id, req.title)
        return conversation.to_dict()

    @router.get("/conversations/{conversation_id}/messages")
    async def messages(project_id: str, conversation_id: str) -> dict[str, Any]:
        found = await runtime.chat.messages(project_id, conversation_id)
        return {"messages": [x.to_dict() for x in found]}

    @router.post("/conversations/{conversation_id}/author-messages")
    async def save_author_message(
        project_id: str, conversation_id: str, req: SaveAuthorMessageRequest
    ) -> dict[str, Any]:
        """保存后续想法（无模型端口）：not_requested，作者主动发送才启动。"""
        await runtime.chat.conversation(project_id, conversation_id)
        message_id = await runtime.entries.save_author_message(
            conversation_id, req.request_id, req.text
        )
        return {"message_id": message_id, "status": "not_requested"}

    @router.post("/conversations/{conversation_id}/turns")
    async def send_turn(
        project_id: str, conversation_id: str, req: SendTurnRequest
    ) -> dict[str, Any]:
        opened = await runtime.chat.send_turn(
            project_id,
            conversation_id,
            text=req.text,
            refs=req.refs,
            idempotency_key=req.idempotency_key,
        )
        return {
            "turn": opened.turn.to_dict(),
            "user_message": opened.user_message.to_dict(),
        }

    @router.post("/conversations/{conversation_id}/turns/{turn_id}/cancel")
    async def cancel_turn(
        project_id: str, conversation_id: str, turn_id: str, _req: CancelRequest
    ) -> dict[str, Any]:
        await runtime.chat.cancel_turn(project_id, conversation_id, turn_id)
        return {}

    @router.post("/conversations/{conversation_id}/turns/{turn_id}/retry")
    async def retry_turn(project_id: str, conversation_id: str, turn_id: str) -> dict[str, Any]:
        turn = await runtime.chat.retry_turn(project_id, conversation_id, turn_id)
        return turn.to_dict()

    @router.post("/task-drafts")
    async def create_task_draft(project_id: str, req: CreateTaskDraftRequest) -> dict[str, Any]:
        # 建议本身不创建 Job（§3.3）：这里只落盘一份可检查的参数草案。
        draft, message_id = await runtime.task_prep.create_draft(
            project_id,
            req.conversation_id,
            req.task_kind,
            instruction=req.instruction,
            goal=req.goal,
            target_ids=req.target_ids,
            source_message_id=req.source_message_id,
            refs=req.refs,
        )
        return {"draft": draft.to_payload(), "message_id": message_id}

    @router.get("/task-drafts/{draft_id}")
    async def get_task_draft(project_id: str, draft_id: str) -> dict[str, Any]:
        draft = await runtime.task_prep.get_draft(project_id, draft_id)
        return {"draft": draft.to_payload()}

    @router.patch("/task-drafts/{draft_id}")
    async def adjust_task_draft(
        project_id: str, draft_id: str, req: AdjustTaskDraftRequest
    ) -> dict[str, Any]:
        patch: dict[str, Any] = {}
        if req.instruction is not None:
            patch["instruction"] = req.instruction
        if req.goal is not None:
            patch["goal"] = req.goal
        if req.target_ids is not None:
            patch["target_ids"] = req.target_ids
        draft = await runtime.task_prep.adjust_draft(
            project_id, draft_id, req.expected_revision, patch
        )
        return {"draft": draft.to_payload()}

    @router.post("/task-drafts/{draft_id}/abandon")
    async def abandon_task_draft(project_id: str, draft_id: str) -> dict[str, Any]:
        await runtime.task_prep.abandon_draft(project_id, draft_id)
        return {}

    @router.post("/task-drafts/{draft_id}/start")
    async def start_task_draft(
        project_id: str, draft_id: str, req: StartTaskDraftRequest
    ) -> dict[str, Any]:
        # 显式启动：缺失项与基础版本校验在服务层；返回已绑定的任务引用。
        started = await runtime.task_prep.start_draft(
            project_id, draft_id, req.expected_revision, req.idempotency_key
        )
        return {"job_ref": started.job_ref}

    @router.post("/conversations/{conversation_id}/messages/{message_id}/accept")
    async def accept_candidate(
        project_id: str,
        conversation_id: str,
        message_id: str,
        req: AcceptCandidateRequest,
    ) -> dict[str, Any]:
        # 候选接受复用既有审阅规则（marker Commit 自带幂等）；聊天文本不代替审批。
        return await runtime.task_prep.accept_candidate(
            project_id,
            conversation_id,
            message_id,
            expected_head=req.expected_head,
            idempotency_key=req.idempotency_key,
        )

    @router.get("/conversations/{conversation_id}/events")
    async def events(
        project_id: str, conversation_id: str, after: int = 0, tail: int = 1
    ) -> StreamingResponse:
        # 归属校验先行：不存在的会话返回 404 信封，而不是挂着一个空流。
        await runtime.chat.events(project_id, conversation_id, after=0)

        async def stream() -> Any:
            cursor = max(0, after)
            idle = 0
            while True:
                batch = await runtime.chat.events(project_id, conversation_id, after=cursor)
                for event in batch:
                    frame = json.dumps(event.to_dict(), ensure_ascii=False)
                    yield f"data: {frame}\n\n"
                    cursor = event.sequence
                    idle = 0
                if tail == 0:
                    return
                idle += 1
                if idle % _HEARTBEAT_EVERY == 0:
                    # 心跳只证明连接存活，不伪装模型进度（§6.3）。
                    yield ": heartbeat\n\n"
                await asyncio.sleep(_POLL_INTERVAL)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
