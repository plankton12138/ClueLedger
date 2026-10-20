"""文件工作区独立应用（0.4 V04-1）。

与旧 ``server/app.py`` 的区别是装配范围：这里不挂 ``/api/v2``、不打开旧工作台数据库、
不建按 slug 懒装配的会话注册表。0.4 的进程只服务「本地作品目录 + 应用状态目录」，
模型接口在后续阶段接入，因此当前应用只有文件工作区、健康检查与静态前端。

分开装配的理由不是「新版本要另起一套」，而是让文件闭环可以先在没有旧数据库、
没有模型的情况下被验证：少一个依赖，失败就少一种来源。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from hastings import __version__
from hastings.workspace.api import (
    create_file_workspace_router,
    register_workspace_error_handlers,
)
from hastings.workspace.state import RecentWorkspaces

__all__ = ["HealthResponse", "create_workspace_app"]


class HealthResponse(BaseModel):
    status: Literal["ok"]


def create_workspace_app(*, book: RecentWorkspaces, static_dir: Path | None = None) -> FastAPI:
    """装配只含文件工作区的新应用；``static_dir`` 存在时托管前端 build 产物。"""
    app = FastAPI(title="Hastings", version=__version__)
    app.state.workspaces = book
    app.include_router(create_file_workspace_router(book))
    register_workspace_error_handlers(app)

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    if static_dir is not None and static_dir.is_dir():
        # 挂在路由之后：/api 优先匹配，静态文件只兜底其余路径。
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="spa")

    return app
