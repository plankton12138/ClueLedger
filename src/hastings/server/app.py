"""FastAPI 应用工厂（P3 T1）。

装配点：T1 只有 health；T3 挂 WS 事件桥路由，T4 挂 REST 只读面路由。
工厂依赖 WritingSession 协议 + StoryWorkspace（REST 读账本/正文）——
换任何会话实现（含测试桩）都不动 HTTP 层。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
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


def create_app(
    session: WritingSession,
    workspace: StoryWorkspace,
    static_dir: Path | None = None,
) -> FastAPI:
    """装配应用；``static_dir`` 存在时托管前端 build 产物（T9）。

    挂载在路由之后——/api 与 /ws 优先匹配，静态文件只兜底其余路径。
    """
    app = FastAPI(title="Hastings", version="0.1.0")
    app.state.session = session
    app.state.workspace = workspace  # 调试内省与入口测试用；路由经参数绑定
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

    if static_dir is not None and static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="spa")

    return app


__all__ = ["HealthResponse", "create_app"]
