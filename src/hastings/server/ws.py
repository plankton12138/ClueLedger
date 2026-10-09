"""WebSocket 事件桥（P3 T3）：命令帧进、事件帧出。

协议照抄 tau_coding/rpc.py 的命令语义子集（只抄模式，不 import tau_coding）：

- 客户端 → 服务端（命令帧）：
  ``{"id": 1, "type": "prompt|steer|follow_up|abort|get_state|get_messages", "message"?: str}``
  以及 P5 T5 的 ``{"id": 1, "type": "sync", "chapter": "ch03"}``（手动重跑对账）。
- 服务端 → 客户端（事件帧）：AgentEvent 原样 JSON（``model_dump(by_alias=True)``）；
- 服务端 → 客户端（响应帧）：
  ``{"type": "response", "command", "success", "id"?, "data"? / "error"?}``

投递模型（计划 D4）：每连接一个 listener 订阅 harness——包括发起 prompt 的
连接在内，所有连接都只经 listener 收事件（多标签页天然同步）；命令分发侧
仅 drain 消费 prompt 流以驱动 loop，不重复投递。写锁串行化每连接帧序。
错误帧延续三要素：哪里错、为什么错、怎样算对。

notice 帧（P5 T4，计划 D5）：对账进度的 hastings 协议层加法扩展——
``{"type": "notice", "kind": "sync_queued|sync_start|sync_end", "chapter",
"hunks", "updates", "status"?}``；tau 事件帧契约本身不动。所有连接同样经
listener 订阅收到（前端凭 notice 把运行窗口标记为「对账中」并渲染同步卡片）。
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator, Callable, Mapping
from typing import cast

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError

from hastings.bible.store import InvalidEntityNameError
from hastings.server.reconcile import ReconcileNotice
from hastings.server.registry import WorkspaceRegistry
from hastings.server.session import AgentBusyError, WritingSession
from tau_agent.events import AgentEvent
from tau_agent.types import JSONValue

router = APIRouter()

AVAILABLE_COMMANDS = (
    "prompt",
    "steer",
    "follow_up",
    "abort",
    "get_state",
    "get_messages",
    "sync",
)


class CommandFrame(BaseModel):
    """客户端命令帧；字段校验错误经 pydantic loc 原样进 error 帧。

    ``chapter`` 只被 sync 命令使用（P5 T5 手动重跑对账：以章节 id 为目标，
    复用该章最近一次保存的差异）。其余命令不读它。
    """

    id: int | str | None = None
    type: str
    message: str | None = None
    chapter: str | None = None


def _wire(model: BaseModel) -> dict[str, JSONValue]:
    return cast("dict[str, JSONValue]", model.model_dump(mode="json", by_alias=True))


def _required_message(frame: CommandFrame) -> str:
    if frame.message is None or not frame.message.strip():
        raise ValueError(
            f"{frame.type} 命令的 message 字段缺失或为空。"
            f"为什么错：{frame.type} 需要一条非空的消息文本。"
            f'怎样算对：{{"id": 1, "type": "{frame.type}", "message": "..."}}'
        )
    return frame.message


class Connection:
    """一条 WebSocket 连接：写锁串行化 + listener 生命周期 + drain 任务。"""

    def __init__(self, websocket: WebSocket, session: WritingSession) -> None:
        self._ws = websocket
        self._session = session
        self._send_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()
        self._unsubscribe: Callable[[], None] | None = None
        self._unsubscribe_notice: Callable[[], None] | None = None

    async def serve(self) -> None:
        """订阅事件流并循环处理命令帧，直到客户端断开（正常生命周期，非错误）。"""
        self._unsubscribe = self._session.subscribe(self._on_event)
        self._unsubscribe_notice = self._session.subscribe_notice(self._on_notice)
        try:
            while True:
                raw = await self._ws.receive_text()
                await self._dispatch_text(raw)
        except WebSocketDisconnect:
            pass
        finally:
            if self._unsubscribe is not None:
                self._unsubscribe()
            if self._unsubscribe_notice is not None:
                self._unsubscribe_notice()

    async def _on_event(self, event: AgentEvent) -> None:
        """listener：把事件帧推给本连接（所有连接统一走这条投递路径）。"""
        await self._push(_wire(event))

    async def _on_notice(self, notice: ReconcileNotice) -> None:
        """listener：把对账进度翻成 notice 帧（hastings 协议层加法扩展）。"""
        payload: dict[str, JSONValue] = {
            "type": "notice",
            "kind": notice.kind,
            "chapter": notice.chapter,
            "hunks": notice.hunks,
            "updates": notice.updates,
        }
        if notice.status is not None:
            payload["status"] = notice.status
        await self._push(payload)

    async def _push(self, frame: Mapping[str, JSONValue]) -> None:
        try:
            await self._send(frame)
        except Exception as exc:  # noqa: BLE001 — 死连接不再投递
            # 可观测性兜底：投递失败打到服务端控制台，不静默吞
            print(f"[ws] 帧投递失败，连接已退订：{exc}", file=sys.stderr)
            if self._unsubscribe is not None:
                self._unsubscribe()
                self._unsubscribe = None
            if self._unsubscribe_notice is not None:
                self._unsubscribe_notice()
                self._unsubscribe_notice = None

    async def _dispatch_text(self, raw: str) -> None:
        try:
            frame = CommandFrame.model_validate_json(raw)
        except ValidationError as exc:
            await self._respond(command=None, frame_id=None, error=f"命令帧解析失败：{exc}")
            return
        try:
            await self._dispatch(frame)
        except (AgentBusyError, ValueError) as exc:
            await self._respond(command=frame.type, frame_id=frame.id, error=str(exc))

    async def _dispatch(self, frame: CommandFrame) -> None:
        match frame.type:
            case "prompt":
                stream = self._session.prompt(_required_message(frame))
                await self._respond(command=frame.type, frame_id=frame.id, data={"started": True})
                self._drain(stream)
            case "steer" | "follow_up":
                message = _required_message(frame)
                if frame.type == "steer":
                    self._session.steer(message)
                else:
                    self._session.follow_up(message)
                await self._respond(command=frame.type, frame_id=frame.id)
            case "abort":
                self._session.cancel()
                await self._respond(command=frame.type, frame_id=frame.id)
            case "get_state":
                await self._respond(command=frame.type, frame_id=frame.id, data=self._state_data())
            case "get_messages":
                await self._respond(
                    command=frame.type,
                    frame_id=frame.id,
                    data={"messages": [_wire(m) for m in self._session.messages]},
                )
            case "sync":
                await self._resync(frame)
            case _:
                raise ValueError(
                    f"未知命令：{frame.type}。"
                    f"为什么错：服务端只接受 {'、'.join(AVAILABLE_COMMANDS)}。"
                    "怎样算对：改用上述命令之一。"
                )

    def _drain(self, stream: AsyncIterator[AgentEvent]) -> None:
        """消费 prompt 流以驱动 loop；事件本身经 listener 投递，这里不转发。"""

        async def run() -> None:
            async for _event in stream:
                pass

        task = asyncio.create_task(run(), name="ws-prompt-drain")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _resync(self, frame: CommandFrame) -> None:
        """sync 命令（P5 T5）：手动重跑某章最近一次修订的对账。

        复用队列保存的差异——不重新读盘、不重新 diff；重跑以正常 notice
        序列（queued→start→end）反馈，与自动对账同一条可观测通道。
        """
        chapter = frame.chapter
        if chapter is None or not chapter.strip():
            raise ValueError(
                "sync 命令的 chapter 字段缺失或为空。"
                "为什么错：手动重跑对账必须指定目标章节。"
                '怎样算对：{"id": 1, "type": "sync", "chapter": "ch03"}'
            )
        if not await self._session.resync_reconcile(chapter):
            raise ValueError(
                f"章节 {chapter} 没有可重跑的对账修订。"
                "为什么错：该章尚无经保存端点提交过的修改（或修订已过期）。"
                "怎样算对：先在正文编辑器中保存一次修改，再点重新同步。"
            )
        await self._respond(command=frame.type, frame_id=frame.id, data={"chapter": chapter})

    def _state_data(self) -> dict[str, JSONValue]:
        return {
            "slug": self._session.slug,
            "model": self._session.model,
            "is_running": self._session.is_running,
            "tools": list(self._session.tool_names),
        }

    async def _respond(
        self,
        *,
        command: str | None,
        frame_id: int | str | None,
        data: dict[str, JSONValue] | None = None,
        error: str | None = None,
    ) -> None:
        payload: dict[str, JSONValue] = {
            "type": "response",
            "command": command,
            "success": error is None,
        }
        if frame_id is not None:
            payload["id"] = frame_id
        if error is not None:
            payload["error"] = error
        elif data is not None:
            payload["data"] = data
        await self._send(payload)

    async def _send(self, payload: Mapping[str, JSONValue]) -> None:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        async with self._send_lock:
            await self._ws.send_text(text)


@router.websocket("/ws/{slug}")
async def websocket_endpoint(websocket: WebSocket, slug: str) -> None:
    """按路径 slug 从 registry 解析会话（P4 T5）；坏 slug 拒绝连接不装配。"""
    registry: WorkspaceRegistry = websocket.app.state.registry
    try:
        session = await registry.get_or_create(slug)
    except InvalidEntityNameError:
        await websocket.accept()
        await websocket.close(code=1008, reason=f"slug 不合法：{slug}")
        return
    await websocket.accept()
    await Connection(websocket, session).serve()


__all__ = ["AVAILABLE_COMMANDS", "CommandFrame", "Connection", "router"]
