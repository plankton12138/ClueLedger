"""WebSocket 事件桥（P3 T3）：命令帧进、事件帧出。

协议照抄 tau_coding/rpc.py 的命令语义子集（只抄模式，不 import tau_coding）：

- 客户端 → 服务端（命令帧）：
  ``{"id": 1, "type": "prompt|steer|follow_up|abort|get_state|get_messages", "message"?: str}``
- 服务端 → 客户端（事件帧）：AgentEvent 原样 JSON（``model_dump(by_alias=True)``）；
- 服务端 → 客户端（响应帧）：
  ``{"type": "response", "command", "success", "id"?, "data"? / "error"?}``

投递模型（计划 D4）：每连接一个 listener 订阅 harness——包括发起 prompt 的
连接在内，所有连接都只经 listener 收事件（多标签页天然同步）；命令分发侧
仅 drain 消费 prompt 流以驱动 loop，不重复投递。写锁串行化每连接帧序。
错误帧延续三要素：哪里错、为什么错、怎样算对。

token_usage 帧（P10 T12，计划 D11）：上下文占用的 hastings 协议层加法扩展——
``{"type": "token_usage", "input", "output", "total", "cache_read",
"cache_write", "context_tokens", "window_tokens", "source"}``；每轮 agent_end
前推一帧（前端对话头刷新占用），get_state 的 ``context_usage`` 载荷同形。
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
)

STALE_SESSION_MESSAGE = (
    "当前会话已在其他页面切换或重建。"
    "为什么错：此连接仍绑定旧的 Agent 会话，继续发送会产生脱离当前会话列表的记录。"
    "怎样算对：页面会重新连接并加载当前会话；如未自动恢复，请点击重连后重试。"
)


class CommandFrame(BaseModel):
    """客户端命令帧；字段校验错误经 pydantic loc 原样进 error 帧。"""

    id: int | str | None = None
    type: str
    message: str | None = None


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

    def __init__(
        self,
        websocket: WebSocket,
        session: WritingSession,
        *,
        is_current: Callable[[], bool] | None = None,
    ) -> None:
        self._ws = websocket
        self._session = session
        self._is_current = is_current if is_current is not None else lambda: True
        self._send_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()
        self._unsubscribe: Callable[[], None] | None = None

    async def serve(self) -> None:
        """订阅事件流并循环处理命令帧，直到客户端断开（正常生命周期，非错误）。"""
        self._unsubscribe = self._session.subscribe(self._on_event)
        try:
            while True:
                raw = await self._ws.receive_text()
                await self._dispatch_text(raw)
        except WebSocketDisconnect:
            pass
        finally:
            if self._unsubscribe is not None:
                self._unsubscribe()

    async def _on_event(self, event: AgentEvent) -> None:
        """listener：把事件帧推给本连接（所有连接统一走这条投递路径）。

        轮末在 agent_end 之前补一帧 token_usage（P10 T12，D11）——前端对话头
        据此刷新上下文占用；放在 agent_end 前保证「读到 agent_end 即本轮帧尽」，
        与既有测试/消费者的边界口径一致。
        """
        if event.type == "agent_end":
            await self._push({"type": "token_usage", **self._usage_payload()})
        await self._push(_wire(event))

    async def _push(self, frame: Mapping[str, JSONValue]) -> None:
        try:
            await self._send(frame)
        except Exception as exc:  # noqa: BLE001 — 死连接不再投递
            # 可观测性兜底：投递失败打到服务端控制台，不静默吞
            print(f"[ws] 帧投递失败，连接已退订：{exc}", file=sys.stderr)
            if self._unsubscribe is not None:
                self._unsubscribe()
                self._unsubscribe = None

    async def _dispatch_text(self, raw: str) -> None:
        try:
            frame = CommandFrame.model_validate_json(raw)
        except ValidationError as exc:
            await self._respond(command=None, frame_id=None, error=f"命令帧解析失败：{exc}")
            return
        if not await self._reject_stale_session(frame):
            return
        try:
            await self._dispatch(frame)
        except (AgentBusyError, ValueError) as exc:
            await self._respond(command=frame.type, frame_id=frame.id, error=str(exc))

    async def _reject_stale_session(self, frame: CommandFrame) -> bool:
        """陈旧连接只收到重连指引，不能再驱动已从 registry 移除的 harness。"""
        if self._is_current():
            return True
        await self._respond(command=frame.type, frame_id=frame.id, error=STALE_SESSION_MESSAGE)
        await self._ws.close(code=1012, reason="会话已切换，请重新连接")
        return False

    async def _dispatch(self, frame: CommandFrame) -> None:
        match frame.type:
            case "prompt":
                await self._session.auto_compact()  # L9：prompt 前两档自动压缩
                if not await self._reject_stale_session(frame):
                    return
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

    def _usage_payload(self) -> dict[str, JSONValue]:
        """当前上下文占用载荷（D11）：token_usage 帧与 get_state 共用同一扁平结构。"""
        return cast("dict[str, JSONValue]", self._session.context_usage().to_payload())

    def _state_data(self) -> dict[str, JSONValue]:
        return {
            "slug": self._session.slug,
            "model": self._session.model,
            "is_running": self._session.is_running,
            "tools": list(self._session.tool_names),
            # 首屏/重连即拿到占用（D11）：不等下一轮 agent_end，对话头立即有值
            "context_usage": self._usage_payload(),
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
    await Connection(
        websocket,
        session,
        is_current=lambda: registry.is_current(slug, session),
    ).serve()


__all__ = [
    "AVAILABLE_COMMANDS",
    "STALE_SESSION_MESSAGE",
    "CommandFrame",
    "Connection",
    "router",
]
