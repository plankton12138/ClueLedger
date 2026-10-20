"""执行日志 REST/SSE 出口（V03-1.6）。

三通道分工（计划 §6.2）：对话 SSE 是消息投影、业务 WS 是版本变化，本模块是
运行事件（执行日志）的独立通道——历史分页与实时流共用同一作品游标与事件 ID
空间，断线补齐 = 从上次游标重新分页。端点形状对应计划 ``GET /execution-events``
与 ``GET /execution-events/stream``；``tail=0`` 为有限流：排空当前积压后关闭
（积压超过 page_size 时连续取页不截断），``tail>=1`` 为持续流并以注释帧心跳。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from hastings.application.capabilities import PERFORMANCE_TARGETS
from hastings.application.errors import ViolationError
from hastings.domain.repository import error_codes as repo_ec
from hastings.domain.repository.ids import ProjectId

if TYPE_CHECKING:
    from hastings.server.v2 import V2Runtime

_POLL_INTERVAL = 0.25
_HEARTBEAT_EVERY = 40  # 轮询次数；~10s 一个心跳注释帧


def _page_size_cap() -> int:
    target = PERFORMANCE_TARGETS.get("execution_log", {})
    size = target.get("page_size", 200) if isinstance(target, dict) else 200
    return int(size)


async def event_stream(
    runtime: V2Runtime, project_id: str, after_sequence: int, tail: int
) -> AsyncIterator[str]:
    """执行日志 SSE 帧序列（路由与测试共用的唯一实现）。

    - 游标从 ``after_sequence`` 起严格向后，断线重连从同一游标继续；
    - ``tail=0`` 有限流：排空当前积压后关闭——连续取页，超过 page_size 的
      积压不被截断（对照 §6.3「完成历史补齐后合并」）；
    - ``tail>=1`` 持续流：每 ``_HEARTBEAT_EVERY`` 次空轮询一个注释帧心跳，
      只证明连接存活，不伪装执行进度。
    """
    cursor = max(0, after_sequence)
    idle = 0
    while True:
        page = await runtime.execution_log.page(project_id, cursor, _page_size_cap())
        emitted = bool(page.events)
        for event in page.events:
            frame = json.dumps(event.to_payload(), ensure_ascii=False)
            yield f"data: {frame}\n\n"
            cursor = event.sequence
            idle = 0
        if tail == 0:
            if emitted:
                continue
            return
        idle += 1
        if idle % _HEARTBEAT_EVERY == 0:
            yield ": heartbeat\n\n"
        await asyncio.sleep(_POLL_INTERVAL)


def create_execution_log_router(runtime: V2Runtime) -> APIRouter:
    router = APIRouter(prefix="/projects/{project_id}", tags=["execution-log"])

    def _require_project(project_id: str) -> None:
        # 归属校验先行：不存在的项目返回 404 信封，而不是空流/空页。
        if runtime.service.get_project(ProjectId(project_id)) is None:
            raise ViolationError(
                code=repo_ec.REPO_PROJECT_NOT_FOUND,
                where=f"execution_events({project_id})",
                why=f"项目 {project_id} 不存在",
                fix="使用 POST /api/v2/projects 返回的 project_id 查询执行日志",
                details={"project_id": project_id},
            )

    @router.get("/execution-events")
    async def list_events(
        project_id: str,
        after_sequence: int = 0,
        limit: int = 50,
        task_id: str | None = None,
        event_type: str | None = Query(default=None, alias="type"),
    ) -> dict[str, Any]:
        _require_project(project_id)
        page = await runtime.execution_log.page(
            project_id,
            after_sequence,
            min(max(0, limit), _page_size_cap()),
            task_id=task_id,
            event_type=event_type,
        )
        return {
            "events": [event.to_payload() for event in page.events],
            "next_cursor": page.next_cursor,
        }

    @router.get("/execution-events/stream")
    async def stream_events(
        project_id: str, after_sequence: int = 0, tail: int = 1
    ) -> StreamingResponse:
        _require_project(project_id)

        async def stream() -> Any:
            async for frame in event_stream(runtime, project_id, after_sequence, tail):
                yield frame

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
