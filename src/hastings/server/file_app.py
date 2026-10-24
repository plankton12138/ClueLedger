"""文件工作区独立应用（0.4）。

与旧 ``server/app.py`` 的区别是装配范围：这里不挂 ``/api/v2``、不打开旧工作台数据库、
不建按 slug 懒装配的会话注册表。0.4 的进程只服务「本地作品目录 + 应用状态目录」，
模型能力只保留一条：按当前选择的连接取 provider。

分开装配的理由不是「新版本要另起一套」，而是让文件闭环可以在没有旧数据库的情况下
被验证：少一个依赖，失败就少一种来源。

V04-9 起多挂一组应用级 ``/api/model-*`` 路由。它们与写作轮次共用同一个连接管理服务：
设置页保存的连接就是下一轮实际调用的连接，不存在「界面显示 A、实际调用 B」的第二份
配置。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from hastings import __version__
from hastings.library.api import create_library_router
from hastings.library.assembly_api import create_assembly_router
from hastings.library.modules import ModuleLibrary
from hastings.server.model_api import create_model_router
from hastings.server.model_connections import ModelConnectionService
from hastings.server.provider_config import ProviderBook, ProviderConfigError
from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.api import (
    create_file_workspace_router,
    register_workspace_error_handlers,
)
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.ideas_api import create_ideas_router
from hastings.workspace.run_registry import RunRegistry
from hastings.workspace.service import ProviderFactory
from hastings.workspace.state import RecentWorkspaces

__all__ = [
    "HealthResponse",
    "configured_provider_factory",
    "create_workspace_app",
]


class HealthResponse(BaseModel):
    status: Literal["ok"]


def configured_provider_factory(book: ProviderBook) -> ProviderFactory:
    """受理一轮时固定模型快照；未配置时给可操作的错误。"""

    def factory() -> ModelSnapshot:
        selection = book.selection()
        try:
            return book.snapshot()
        except ProviderConfigError as exc:
            raise WorkspaceError(
                code="MODEL_NOT_CONFIGURED",
                where="turn.model",
                why=selection.reason or str(exc),
                fix="在顶栏「模型设置」里添加并选择一个连接与模型，"
                "或在 config.yaml 里补齐 base_url、api_key 与模型名",
                details={
                    "connection": selection.connection_id or "",
                    "model": selection.model,
                },
            ) from exc

    return factory


def create_workspace_app(
    *,
    workspaces: RecentWorkspaces,
    provider_factory: ProviderFactory,
    model_service: ModelConnectionService | None = None,
    static_dir: Path | None = None,
    library: ModuleLibrary | None = None,
) -> FastAPI:
    """装配文件工作区应用；``model_service`` 存在时同时挂模型设置接口。

    参数名 ``workspaces`` 是作品登记，``model_service`` 是模型连接：两者曾经都叫
    ``book``，放在一起读不清哪个是哪个。``library`` 是应用级模块库，缺省时库接口不挂载。
    """
    app = FastAPI(title="Hastings", version=__version__)
    app.state.workspaces = workspaces
    # 写作轮次与装配接口共享同一张占用表：运行中不接受本作品的装卸。
    run_registry = RunRegistry()
    app.state.run_registry = run_registry
    app.include_router(
        create_file_workspace_router(
            workspaces,
            provider_factory,
            library=library,
            run_registry=run_registry,
        )
    )
    app.include_router(create_ideas_router(workspaces))
    if model_service is not None:
        app.include_router(create_model_router(model_service))
    if library is not None:
        app.include_router(create_library_router(library))
        app.include_router(create_assembly_router(workspaces, library, busy=run_registry.is_busy))
    register_workspace_error_handlers(app)

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    if static_dir is not None and static_dir.is_dir():
        # 挂在路由之后：/api 优先匹配，静态文件只兜底其余路径。
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="spa")

    return app
