"""V2 事件 WS 通道（T17.5）：业务 Commit 事件按 sequence 推送与断线补齐。

通道分工（T17.5 的「分开」落点）：
- **业务 Commit 事件** → 本通道（``/ws/v2/{project_id}``）：事件源是仓库 Outbox
  （与 Commit 同事务落盘，ADR-007），sequence 由仓库提交时分配；
- **Job 进度** → 既有 REST 轮询（``GET /api/v2/projects/{id}/agent-jobs``）不变；
- **对话事件** → 既有 V1 对话 WS（``/ws/{slug}``）；V1 事件流只在历史查看器读取。

补齐协议：客户端连接后发 ``{"type": "subscribe", "after_seq": N}``，服务端先把
sequence > N 的存量事件全部补发（含已发布行——Outbox 行发布后仍保留在表中），
再进入轮询推送增量。客户端以「收到的最大 sequence」为游标，重连时带上即可补齐
断线期间的事件；同序号重复帧由客户端按 seq 去重（服务端至少一次投递）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from hastings.server.v2 import V2Runtime

__all__ = ["create_v2_ws_router"]

_POLL_INTERVAL = 0.3
_BATCH = 200


def _event_frame(event: dict[str, Any]) -> dict[str, Any]:
    """Outbox 行 → WS 帧：帧类型固定 ``event``，事件类型放 ``event_type``。"""
    return {
        "type": "event",
        "id": event["id"],
        "sequence": event["sequence"],
        "commit_id": event["commit_id"],
        "event_type": event["type"],
        "payload": event["payload"],
    }


def create_v2_ws_router(runtime: V2Runtime) -> APIRouter:
    router = APIRouter(tags=["v2-ws"])

    @router.websocket("/ws/v2/{project_id}")
    async def v2_events(websocket: WebSocket, project_id: str) -> None:
        await websocket.accept()
        try:
            hello = json.loads(await websocket.receive_text())
        except WebSocketDisconnect:
            return
        if not isinstance(hello, dict) or hello.get("type") != "subscribe":
            await websocket.close(code=4400, reason='首帧必须是 {"type":"subscribe","after_seq":N}')
            return
        after_seq = hello.get("after_seq", 0)
        if not isinstance(after_seq, int) or after_seq < 0:
            await websocket.close(code=4400, reason="after_seq 必须是非负整数")
            return

        try:
            cursor = after_seq
            # 先补齐存量：分批读到没有更多为止，再进入增量轮询。
            while True:
                batch = await runtime.events_after(project_id, cursor, _BATCH)
                if not batch:
                    break
                for event in batch:
                    await websocket.send_json(_event_frame(event))
                    cursor = max(cursor, int(event["sequence"]))
                if len(batch) < _BATCH:
                    break
            await websocket.send_text(json.dumps({"type": "replayed", "up_to_seq": cursor}))

            # 增量轮询：Outbox 是消费出口而非广播总线，单用户个位数连接轮询足够；
            # 客户端按 sequence 去重，轮询间隙的重复投递不产生重复业务效果。
            while True:
                batch = await runtime.events_after(project_id, cursor, _BATCH)
                for event in batch:
                    if int(event["sequence"]) <= cursor:
                        continue
                    await websocket.send_json(_event_frame(event))
                    cursor = max(cursor, int(event["sequence"]))
                await asyncio.sleep(_POLL_INTERVAL)
        except WebSocketDisconnect:
            return
        except asyncio.CancelledError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            # 环境/连接故障：尽力把原因告诉客户端后关闭（四透镜④：可观测）。
            with contextlib.suppress(RuntimeError):
                await websocket.close(code=1011, reason=f"事件通道故障：{exc}")

    return router
