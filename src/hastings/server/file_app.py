"""文件工作区独立应用（0.4）。

与旧 ``server/app.py`` 的区别是装配范围：这里不挂 ``/api/v2``、不打开旧工作台数据库、
不建按 slug 懒装配的会话注册表。0.4 的进程只服务「本地作品目录 + 应用状态目录」，
模型能力只保留一条：按当前选择的档案取 provider。

分开装配的理由不是「新版本要另起一套」，而是让文件闭环可以在没有旧数据库的情况下
被验证：少一个依赖，失败就少一种来源。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from hastings import __version__
from hastings.server.provider_config import ProviderBook
from hastings.workspace.api import (
    create_file_workspace_router,
    register_workspace_error_handlers,
)
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.service import ProviderFactory
from hastings.workspace.state import RecentWorkspaces
from tau_agent.provider import ModelProvider

__all__ = [
    "HealthResponse",
    "configured_provider_factory",
    "create_workspace_app",
]


class HealthResponse(BaseModel):
    status: Literal["ok"]


def configured_provider_factory(book: ProviderBook) -> ProviderFactory:
    """按当前选择的档案取 provider 与模型；未配置时给可操作的三要素错误。"""

    def factory() -> tuple[ModelProvider, str]:
        profile, model = book.selected()
        if profile is None or not profile.available or not model:
            raise WorkspaceError(
                code="MODEL_NOT_CONFIGURED",
                where="turn.model",
                why="当前没有可用的模型来源或模型名，这一轮不会调用模型",
                fix="在 config.yaml 里配置可用的厂商与模型，或用 --model 指定启动模型",
                details={"provider": profile.name if profile else "", "model": model},
            )
        return book.provider(profile.name), model

    return factory


def create_workspace_app(
    *,
    book: RecentWorkspaces,
    provider_factory: ProviderFactory,
    static_dir: Path | None = None,
) -> FastAPI:
    """装配只含文件工作区的新应用；``static_dir`` 存在时托管前端 build 产物。"""
    app = FastAPI(title="Hastings", version=__version__)
    app.state.workspaces = book
    app.include_router(create_file_workspace_router(book, provider_factory))
    register_workspace_error_handlers(app)

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    if static_dir is not None and static_dir.is_dir():
        # 挂在路由之后：/api 优先匹配，静态文件只兜底其余路径。
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="spa")

    return app
