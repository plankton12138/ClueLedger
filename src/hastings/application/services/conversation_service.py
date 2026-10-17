"""ConversationService（Phase 19.3 T19.3d）：对话生命周期与轮次编排。

职责边界（计划 §19.3）：只做「参数检查 → 显式发送 → 流式执行 → 终态落盘」，
不创建创作 Job、不触碰 Commit——服务对象上不存在任何写端口（四透镜②：
硬约束放工具层）。同一会话单轮执行由存储层 ``open_turn`` 裁决；全局并发由
``max_concurrent`` 槽位约束，排队中的轮次停留在 queued，不静默抢占。

取消语义（§4.1）：``cancel_turn`` 先置取消 token、再把轮次收束到 stopped；
runner 看到取消即停并返回部分文本，随后所有迟到分片被存储层丢弃（非运行态
``append_delta`` 返回 False），部分回复保留。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from contextlib import suppress
from datetime import datetime
from typing import Any, cast

from hastings.application.errors import EnvironmentFailure, HastingsError, ViolationError
from hastings.application.ports.chat import ChatRun
from hastings.application.ports.conversations import ConversationStore, OpenTurn
from hastings.domain.conversation import error_codes as ec
from hastings.domain.conversation import models as m
from hastings.domain.conversation.errors import ConversationViolation
from hastings.domain.repository.serialization import content_hash
from hastings.infrastructure.conversations.runner import TurnCancellationToken

__all__ = ["ConversationService"]

# 进入模型上下文的历史轮数上限：保留最新指令与明确引用（§3.4 预算裁剪）。
_HISTORY_WINDOW = 20


def _as_hastings(exc: ConversationViolation) -> HastingsError:
    return ViolationError(
        code=exc.code, where=exc.where, why=exc.why, fix=exc.fix, details=exc.details
    )


class _ActiveRun:
    """一次进行中的执行：取消 token + 后台任务句柄。"""

    def __init__(self) -> None:
        self.token = TurnCancellationToken()
        self.task: asyncio.Task[None] | None = None


class ConversationService:
    """对话应用服务：存储端口 + 执行器工厂 + 模型解析。"""

    def __init__(
        self,
        store: ConversationStore,
        *,
        runner_factory: Callable[[], Any],
        model_resolver: Callable[[], str],
        max_concurrent: int = 2,
        first_token_timeout: float = 90.0,
        total_timeout: float = 600.0,
        max_output_chars: int = 20000,
        history_window: int = _HISTORY_WINDOW,
        ref_validator: Callable[[str, list[dict[str, Any]]], None] | None = None,
    ) -> None:
        self.store = store
        # 公开注入点：测试与服务装配（ProviderBook 接线）都可以替换。
        self.runner_factory = runner_factory
        self.model_resolver = model_resolver
        self.binding_resolver: Callable[[], dict[str, Any]] | None = None
        self.bound_runner_factory: Callable[[dict[str, Any]], Any] | None = None
        self._slots = asyncio.Semaphore(max_concurrent)
        self._first_token_timeout = first_token_timeout
        self._total_timeout = total_timeout
        self._max_output_chars = max_output_chars
        self._history_window = history_window
        # 引用校验挂点（19.4）：实现在装配层（对照项目快照验证存在性）；
        # None 时不校验（测试/本地纯聊天）。
        self.ref_validator = ref_validator
        self._runs: dict[str, _ActiveRun] = {}

    # -- 会话 ------------------------------------------------------------------
    async def create_conversation(
        self, project_id: str, title: str | None = None
    ) -> m.Conversation:
        if not project_id:
            raise ViolationError(
                code=ec.CHAT_CONVERSATION_NOT_FOUND,
                where="chat.conversation",
                why="缺少项目 ID",
                fix="先选择或创建项目，再新建会话",
            )
        try:
            return await self.store.create_conversation(project_id, title or "新对话")
        except ConversationViolation as exc:
            raise _as_hastings(exc) from exc

    async def list_conversations(self, project_id: str) -> list[m.Conversation]:
        return await self.store.list_conversations(project_id)

    async def conversation(self, project_id: str, conversation_id: str) -> m.Conversation:
        """公开的归属校验：跨项目/不存在一律 CHAT_CONVERSATION_NOT_FOUND。"""
        return await self._require_conversation(project_id, conversation_id)

    async def _require_conversation(self, project_id: str, conversation_id: str) -> m.Conversation:
        conversation = await self.store.get_conversation(conversation_id)
        if conversation is None or conversation.project_id != project_id:
            raise self._not_found(project_id, conversation_id)
        return conversation

    @staticmethod
    def _not_found(project_id: str, conversation_id: str) -> HastingsError:
        return ViolationError(
            code=ec.CHAT_CONVERSATION_NOT_FOUND,
            where="chat.conversation",
            why=f"项目 {project_id} 下没有会话 {conversation_id}",
            fix="在当前项目里新建或选择一个会话",
            details={"project_id": project_id, "conversation_id": conversation_id},
        )

    # -- 消息与事件 ---------------------------------------------------------------
    async def messages(self, project_id: str, conversation_id: str) -> list[m.ChatMessage]:
        await self._require_conversation(project_id, conversation_id)
        return await self.store.list_messages(conversation_id)

    async def events(self, project_id: str, conversation_id: str, after: int) -> list[m.ChatEvent]:
        await self._require_conversation(project_id, conversation_id)
        return await self.store.events_after(conversation_id, after)

    # -- 发送与执行 -----------------------------------------------------------------
    async def send_turn(
        self,
        project_id: str,
        conversation_id: str,
        text: str = "",
        refs: Iterable[dict[str, Any]] = (),
        idempotency_key: str = "",
        *,
        idempotency: str | None = None,
        model_binding: dict[str, Any] | None = None,
    ) -> OpenTurn:
        idempotency_key = idempotency or idempotency_key
        if not idempotency_key:
            raise ViolationError(
                code=ec.CHAT_IDEMPOTENCY_CONFLICT,
                where="chat.turn.idempotency",
                why="缺少幂等键，无法区分重放与新请求",
                fix="为每次发送生成稳定 UUID 作为 idempotency_key",
            )
        await self._require_conversation(project_id, conversation_id)
        ref_list = [dict(r) for r in refs]
        if self.ref_validator is not None:
            self.ref_validator(project_id, ref_list)
        fingerprint = content_hash([text, ref_list])
        if model_binding is None:
            # 未配置仍创建可观察的失败轮次。
            with suppress(HastingsError):
                model_binding = (
                    self.binding_resolver()
                    if self.binding_resolver is not None
                    else {"model": self.model_resolver()}
                )
        now = datetime.now().astimezone().isoformat()
        turn_id = f"turn_{content_hash([conversation_id, idempotency_key])[:16]}"
        user_message = m.ChatMessage(
            message_id=f"msg_{content_hash([turn_id, 'user'])[:16]}",
            conversation_id=conversation_id,
            turn_id=turn_id,
            role=m.ROLE_AUTHOR,
            kind=m.KIND_TEXT,
            text=text,
            refs=ref_list,
            created_at=now,
        )
        turn = m.ChatTurn(
            turn_id=turn_id,
            conversation_id=conversation_id,
            status=m.TURN_QUEUED,
            model=None,
            end_reason=None,
            idempotency_key=idempotency_key,
            model_binding=model_binding,
        )
        try:
            opened = await self.store.open_turn(
                project_id=project_id,
                conversation_id=conversation_id,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                user_message=user_message,
                turn=turn,
            )
        except ConversationViolation as exc:
            raise _as_hastings(exc) from exc
        if opened.created:
            self._spawn(project_id, conversation_id, opened.turn.turn_id)
        return opened

    async def send_turn_from_messages(
        self,
        project_id: str,
        conversation_id: str,
        source_message_ids: list[str],
        idempotency_key: str,
        *,
        model_binding: dict[str, Any] | None = None,
    ) -> OpenTurn:
        """接着讨论（M02/V03-1.5）：引用已保存消息启动轮次，不复制作者消息。

        - 源消息必须是本讨论内的 author 消息；主消息（第一条）归入新轮次，
          其余消息保留原位、经历史自然进入模型上下文；
        - 只有作者显式调用本方法才启动模型：连接成功零自动重放（I06）；
        - 幂等：同 (conversation, idempotency_key) 返回原轮次。
        """
        if not source_message_ids:
            raise ViolationError(
                code=ec.CHAT_MESSAGE_NOT_FOUND,
                where="chat.turn.from_messages",
                why="接着讨论至少要引用一条已保存消息",
                fix="从讨论中选择已保存的想法后重试",
            )
        await self._require_conversation(project_id, conversation_id)
        sources: list[m.ChatMessage] = []
        for message_id in source_message_ids:
            message = await self.store.get_message(message_id)
            if message is None or message.conversation_id != conversation_id:
                raise ViolationError(
                    code=ec.CHAT_MESSAGE_NOT_FOUND,
                    where="chat.turn.from_messages",
                    why=f"消息 {message_id!r} 不在该讨论内",
                    fix="从当前讨论的消息列表选择；跨讨论/跨项目引用拒绝",
                    details={"message_id": message_id},
                )
            if message.role != m.ROLE_AUTHOR:
                raise ViolationError(
                    code=ec.CHAT_MESSAGE_NOT_FOUND,
                    where="chat.turn.from_messages",
                    why=f"消息 {message_id!r} 不是作者消息，不能作为接着讨论的起点",
                    fix="选择已保存的作者想法；模型回复不能作为引用源",
                    details={"message_id": message_id},
                )
            sources.append(message)
        if not idempotency_key:
            raise ViolationError(
                code=ec.CHAT_IDEMPOTENCY_CONFLICT,
                where="chat.turn.idempotency",
                why="缺少幂等键，无法区分重放与新请求",
                fix="为每次发送生成稳定 UUID 作为 idempotency_key",
            )
        if model_binding is None:
            with suppress(HastingsError):
                model_binding = (
                    self.binding_resolver()
                    if self.binding_resolver is not None
                    else {"model": self.model_resolver()}
                )
        turn_id = f"turn_{content_hash([conversation_id, idempotency_key])[:16]}"
        primary = m.ChatMessage(
            message_id=sources[0].message_id,
            conversation_id=sources[0].conversation_id,
            turn_id=turn_id,
            role=sources[0].role,
            kind=sources[0].kind,
            text=sources[0].text,
            refs=[dict(ref) for ref in sources[0].refs],
            model=sources[0].model,
            status=sources[0].status,
            created_at=sources[0].created_at,
        )
        turn = m.ChatTurn(
            turn_id=turn_id,
            conversation_id=conversation_id,
            status=m.TURN_QUEUED,
            model=None,
            end_reason=None,
            idempotency_key=idempotency_key,
            model_binding=model_binding,
            source_message_ids=list(source_message_ids),
        )
        fingerprint = content_hash(["from_messages", sorted(source_message_ids)])
        opened = await self.store.open_turn(
            project_id=project_id,
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            user_message=primary,
            turn=turn,
        )
        if opened.created:
            self._spawn(project_id, conversation_id, opened.turn.turn_id)
        return opened

    def _spawn(self, project_id: str, conversation_id: str, turn_id: str) -> None:
        run = _ActiveRun()
        task = asyncio.create_task(self._execute(project_id, conversation_id, turn_id, run))
        run.task = task
        self._runs[turn_id] = run

        def _cleanup(_task: asyncio.Task[None]) -> None:
            self._runs.pop(turn_id, None)

        task.add_done_callback(_cleanup)

    async def _execute(
        self, project_id: str, conversation_id: str, turn_id: str, run: _ActiveRun
    ) -> None:
        # 模型解析失败也走轮次终态：排队中的请求得到明确失败，而不是悬挂。
        async with self._slots:
            turn = await self.store.get_turn(turn_id)
            if turn is None or turn.status != m.TURN_QUEUED:
                return  # 已被取消/中断：不执行。
            try:
                model = str((turn.model_binding or {}).get("model") or self.model_resolver())
                runner = (
                    self.bound_runner_factory(turn.model_binding)
                    if turn.model_binding and self.bound_runner_factory is not None
                    else self.runner_factory()
                )
            except HastingsError as exc:
                await self._settle_failure(project_id, conversation_id, turn_id, exc)
                return
            assistant_message = m.ChatMessage(
                message_id=f"msg_{content_hash([turn_id, 'assistant'])[:16]}",
                conversation_id=conversation_id,
                turn_id=turn_id,
                role=m.ROLE_ASSISTANT,
                kind=m.KIND_TEXT,
                text="",
                model=model,
                status=m.MESSAGE_STREAMING,
                created_at=datetime.now().astimezone().isoformat(),
            )
            started = await self.store.start_turn(
                turn_id, assistant_message=assistant_message, model=model
            )
            if not started:
                return  # start 前已被取消。
            history, refs = await self._build_context(conversation_id, turn_id)
            chat_run = ChatRun(
                model=model,
                system="你是作者的写作伙伴：讨论构想、回答问题。你没有修改项目内容的"
                "任何能力；涉及创作任务时，只会说明建议，不会假装已经开始执行。",
                history=history,
                cancelled=run.token.is_cancelled,
                refs=refs,
            )

            async def sink(delta: str) -> None:
                await self.store.append_delta(turn_id, delta)

            try:
                final = await runner.stream(chat_run, sink)
            except HastingsError as exc:
                await self._settle_failure(project_id, conversation_id, turn_id, exc)
                return
            except (OSError, ConnectionError) as exc:
                await self._settle_failure(
                    project_id,
                    conversation_id,
                    turn_id,
                    EnvironmentFailure(
                        code=ec.CHAT_PROVIDER_UNAVAILABLE,
                        where="chat.runner",
                        why=f"连接模型端点失败：{type(exc).__name__}",
                        fix="确认网络与端点地址后重试；不会自动换用其他厂商",
                    ),
                )
                return
            # 取消后的正常返回：轮次已被 cancel 收束，settle 返回 False 无副作用。
            await self.store.settle_turn(
                turn_id, status=m.TURN_COMPLETED, end_reason="stop", text=final
            )

    async def _settle_failure(
        self, project_id: str, conversation_id: str, turn_id: str, exc: HastingsError
    ) -> None:
        del project_id
        await self.store.settle_turn(
            turn_id,
            status=m.TURN_FAILED,
            end_reason=exc.code,
            error={
                "code": exc.code,
                "category": exc.category.value,
                "where": exc.where,
                "why": exc.why,
                "fix": exc.fix,
                "details": dict(exc.details),
            },
        )

    async def _build_context(
        self, conversation_id: str, turn_id: str
    ) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
        messages = await self.store.list_messages(conversation_id)
        history: list[tuple[str, str]] = []
        refs: list[str] = []
        for message in messages:
            if message.kind != m.KIND_TEXT or not message.text:
                continue
            if message.role == m.ROLE_AUTHOR:
                if message.turn_id == turn_id:
                    refs.extend(
                        str(ref.get("excerpt") or f"[{ref.get('title', '引用')}]")
                        for ref in message.refs
                    )
                    continue  # 最后一条用户输入单独处理。
                history.append(("author", message.text))
            elif message.role == m.ROLE_ASSISTANT:
                history.append(("assistant", message.text))
        current = next(
            (x for x in messages if x.turn_id == turn_id and x.role == m.ROLE_AUTHOR), None
        )
        if current is not None:
            history.append(("author", current.text))
        return tuple(history[-self._history_window :]), tuple(refs)

    # -- 取消与重试 -----------------------------------------------------------------
    async def cancel_turn(self, project_id: str, conversation_id: str, turn_id: str) -> None:
        await self._require_conversation(project_id, conversation_id)
        turn = await self.store.get_turn(turn_id)
        if turn is None or turn.conversation_id != conversation_id:
            raise ViolationError(
                code=ec.CHAT_TURN_NOT_FOUND,
                where="chat.turn",
                why=f"会话 {conversation_id} 没有轮次 {turn_id}",
                fix="刷新对话历史后重试",
            )
        run = self._runs.get(turn_id)
        if run is not None:
            run.token.cancel()
        await self.store.settle_turn(turn_id, status=m.TURN_STOPPED, end_reason="author_stop")
        if run is not None and run.task is not None and not run.task.done():
            run.task.cancel()
            with suppress(asyncio.CancelledError):
                await run.task

    async def retry_turn(
        self,
        project_id: str,
        conversation_id: str,
        turn_id: str,
        *,
        idempotency_key: str | None = None,
    ) -> m.ChatTurn:
        await self._require_conversation(project_id, conversation_id)
        turn = await self.store.get_turn(turn_id)
        if turn is None or turn.conversation_id != conversation_id:
            raise ViolationError(
                code=ec.CHAT_TURN_NOT_FOUND,
                where="chat.turn",
                why=f"会话 {conversation_id} 没有轮次 {turn_id}",
                fix="刷新对话历史后重试",
            )
        if not m.is_terminal_turn(turn.status):
            raise ViolationError(
                code=ec.CHAT_TURN_NOT_RETRYABLE,
                where="chat.turn.retry",
                why="轮次仍在进行中，不能重试",
                fix="等待当前回复结束，或先停止回复",
            )
        original_user = next(
            (
                x
                for x in await self.store.list_messages(conversation_id)
                if x.turn_id == turn_id and x.role == m.ROLE_AUTHOR
            ),
            None,
        )
        text = original_user.text if original_user is not None else ""
        key = idempotency_key or f"{turn.idempotency_key}:retry:{datetime.now().timestamp()}"
        opened = await self.send_turn(
            project_id,
            conversation_id,
            text=text,
            refs=original_user.refs if original_user is not None else (),
            idempotency_key=key,
            model_binding=turn.model_binding or ({"model": turn.model} if turn.model else None),
        )
        retried = opened.turn
        # open_turn 建的是干净轮次；这里把 retry 链接写回（新尝试链接原失败轮次）。
        record = retried.to_dict()
        record["retry_of"] = turn_id
        await self._relink(record)
        return m.ChatTurn.from_dict(record)

    async def _relink(self, record: dict[str, Any]) -> None:
        # 存储端口没有「更新轮次元数据」的原语；重试链接经 append_event 之外
        # 的专用通道写入。为保持端口最小，这里直接复用 start 前的一次性更新：
        # 实现落在存储子类的受限方法上（Memory/SQLite 都提供 _put_turn）。
        raw = cast(Any, self.store)
        put = getattr(raw, "_put_turn", None)
        get = getattr(raw, "_get_turn", None)
        if put is None or get is None:
            return
        current = get(record["turn_id"])
        if current is not None:
            current["retry_of"] = record["retry_of"]
            put(current)

    # -- 启动恢复 -------------------------------------------------------------------
    async def mark_interrupted(self) -> int:
        """启动恢复：遗留的排队/运行轮次标为中断；后台执行任务同步取消。"""
        for run in list(self._runs.values()):
            run.token.cancel()
            if run.task is not None and not run.task.done():
                run.task.cancel()
        return await self.store.mark_interrupted()

    async def shutdown(self) -> None:
        """进程退出：停止后台执行（内容不保证继续生成，落盘部分已保留）。"""
        for run in list(self._runs.values()):
            run.token.cancel()
            if run.task is not None and not run.task.done():
                run.task.cancel()
        tasks = [r.task for r in self._runs.values() if r.task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
