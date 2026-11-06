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

import threading
from collections.abc import Callable
from pathlib import Path
from typing import cast

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from hastings import __version__
from hastings.accounts.api import create_auth_router, register_auth_error_handlers
from hastings.accounts.middleware import AuthGuardMiddleware, RequestContextMiddleware
from hastings.accounts.runtime import AuthRuntime
from hastings.library.api import create_library_router
from hastings.library.assembly_api import create_assembly_router
from hastings.library.modules import ModuleLibrary
from hastings.server.model_api import create_model_router
from hastings.server.model_connections import ModelConnectionService
from hastings.server.model_factory import (
    configured_limits_factory,
    configured_provider_factory,
)
from hastings.server.model_limits import ModelLimits
from hastings.server.probe_ops import ProbeRunner
from hastings.server.user_services import (
    LOCAL_KEY,
    ScopedModuleLibrary,
    ScopedWorkspaceServices,
    ServiceContext,
    UserResources,
    UserResourcesRegistry,
)
from hastings.workspace.api import (
    create_file_workspace_router,
    register_workspace_error_handlers,
)
from hastings.workspace.clue_api import create_clue_router
from hastings.workspace.creative_api import create_creative_router
from hastings.workspace.ideas_api import create_ideas_router
from hastings.workspace.operation_api import create_operation_router
from hastings.workspace.operations import OperationStore
from hastings.workspace.run_registry import RunRegistry
from hastings.workspace.service import ProviderFactory
from hastings.workspace.service_hub import WorkspaceServices
from hastings.workspace.state import RecentWorkspaces
from hastings.workspace.story_api import create_story_router

__all__ = [
    "configured_limits_factory",
    "configured_provider_factory",
    "create_workspace_app",
]


def create_workspace_app(
    *,
    workspaces: RecentWorkspaces,
    provider_factory: ProviderFactory | None = None,
    model_service: ModelConnectionService | None = None,
    static_dir: Path | None = None,
    library: ModuleLibrary | None = None,
    limits_factory: Callable[[], ModelLimits | None] | None = None,
    auth: AuthRuntime | None = None,
    resources: UserResourcesRegistry | None = None,
) -> FastAPI:
    """装配文件工作区应用；``model_service`` 存在时同时挂模型设置接口。

    本地模式（``resources`` 缺省）用传入的单份 provider、模型服务与模块库；
    server 模式由 ``resources`` 按当前账号解析，不共享配置、凭据或私有库。
    ``auth`` 存在时进入 server 模式：``/api/*`` 要求会话，写请求校验来源与 CSRF。
    """
    app = FastAPI(title="线簿 ClueLedger", version=__version__)
    app.state.workspaces = workspaces
    # 写作轮次与装配接口共享同一张占用表：运行中不接受本作品的装卸。
    run_registry = RunRegistry()
    app.state.run_registry = run_registry
    # 统一操作放在应用级状态目录：控制接口按操作标识定位，不需要先知道是哪个作品。
    operations = OperationStore(workspaces.root)
    app.state.operations = operations
    if resources is None:
        if provider_factory is None:
            raise ValueError("本地模式需要 provider_factory")
        local_hub = WorkspaceServices(
            workspaces,
            provider_factory,
            library=library,
            run_registry=run_registry,
            operations=operations,
            limits_factory=limits_factory,
        )
        local = UserResources(
            key=LOCAL_KEY,
            hub=local_hub,
            model_service=model_service,
            auth=getattr(model_service, "auth", None),
            library=library,
        )
        resources = UserResourcesRegistry(local=local)
    resources.bind(
        ServiceContext(
            workspaces=workspaces,
            run_registry=run_registry,
            operations=operations,
            lock=threading.RLock(),
        )
    )
    hub = cast(WorkspaceServices, ScopedWorkspaceServices(resources))
    app.state.services = hub
    # 连接测试也登记为统一操作：提交即返回 operation_id，可暂停与被读取。
    probe_runner = (
        ProbeRunner(operations, resources.model_service_for) if resources.models_available else None
    )
    app.state.probe_runner = probe_runner
    app.include_router(create_file_workspace_router(hub))
    app.include_router(
        create_operation_router(
            workspaces,
            operations,
            hub.cached_service,
            runner_for=hub.runner_for,
            probe_runner=probe_runner,
        )
    )
    app.include_router(create_auth_router(auth))
    register_auth_error_handlers(app)
    if auth is not None:
        app.add_middleware(AuthGuardMiddleware, service=auth.service, config=auth.config)
    # 最后添加的中间件在最外层：认证拒绝也要带请求编号。
    app.add_middleware(RequestContextMiddleware)
    app.include_router(create_ideas_router(workspaces))
    library_proxy = (
        cast(ModuleLibrary, ScopedModuleLibrary(resources.current_library))
        if resources.libraries_available
        else None
    )
    app.include_router(create_clue_router(workspaces, library_proxy))
    app.include_router(create_story_router(workspaces, hub))
    app.include_router(create_creative_router(workspaces, hub))
    if resources.models_available:
        app.include_router(
            create_model_router(
                resources.current_model_service,
                probe_runner=probe_runner,
                auth_available=resources.auth_available,
            )
        )
    if library_proxy is not None:
        app.include_router(create_library_router(library_proxy))
        app.include_router(
            create_assembly_router(workspaces, library_proxy, busy=run_registry.is_busy)
        )
    register_workspace_error_handlers(app)

    @app.get("/api/health")
    def health() -> Response:
        if auth is not None:
            try:
                with auth.database.read() as connection:
                    connection.execute("SELECT 1").fetchone()
            except Exception:
                return JSONResponse(status_code=503, content={"status": "degraded"})
        return JSONResponse(content={"status": "ok"})

    if static_dir is not None and static_dir.is_dir():
        # 挂在路由之后：/api 优先匹配，静态文件只兜底其余路径。
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="spa")

    return app
