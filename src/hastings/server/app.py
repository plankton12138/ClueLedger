"""FastAPI 应用工厂（P3 T1）。

装配点：T1 只有 health；T3 挂 WS 事件桥路由，T4 挂 REST 只读面路由。
工厂依赖 WritingSession 协议 + StoryWorkspace（REST 读账本/正文）——
换任何会话实现（含测试桩）都不动 HTTP 层。
"""

from __future__ import annotations

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from hastings.bible.store import StoryWorkspace
from hastings.server.rest import create_rest_router
from hastings.server.session import WritingSession
from hastings.server.ws import router as ws_router


class HealthResponse(BaseModel):
    status: Literal["ok"]
    story: str
    model: str
    is_running: bool
    tools: list[str]


def create_app(session: WritingSession, workspace: StoryWorkspace) -> FastAPI:
    app = FastAPI(title="Hastings", version="0.1.0")
    app.state.session = session
    app.include_router(create_rest_router(workspace=workspace, session=session))
    app.include_router(ws_router)

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            story=session.slug,
            model=session.model,
            is_running=session.is_running,
            tools=list(session.tool_names),
        )

    return app


__all__ = ["HealthResponse", "create_app"]
