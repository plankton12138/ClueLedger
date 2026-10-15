"""FastAPI 应用工厂（P4 T4 多工作区化）。

装配点：工厂只认 WorkspaceRegistry——REST 与 WS 路由均按 slug 参数从
registry 解析会话；/api/health 是全局探活（不绑定具体工作区）。
换任何注册表实现（含测试桩工厂）都不动 HTTP 层。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from hastings.server.provider_config import ProviderBook
from hastings.server.registry import WorkspaceRegistry
from hastings.server.rest import create_rest_router
from hastings.server.v2 import V2Runtime, create_v2_router, register_v2_error_handlers
from hastings.server.ws import router as ws_router


class HealthResponse(BaseModel):
    status: Literal["ok"]


def create_app(
    registry: WorkspaceRegistry,
    static_dir: Path | None = None,
    book: ProviderBook | None = None,
    model_transport: httpx.AsyncBaseTransport | None = None,
    corpus_root: Path | None = None,
    ollama_transport: httpx.AsyncBaseTransport | None = None,
    installed_probe: Callable[[], bool] | None = None,
    v2_runtime: V2Runtime | None = None,
) -> FastAPI:
    """装配应用；``static_dir`` 存在时托管前端 build 产物（P3 T9）。

    挂载在路由之后——/api 与 /ws 优先匹配，静态文件只兜底其余路径。
    ``book``（P6 T2）提供档案目录与跨档案重建的端点解析；缺省时 REST 层按
    env 回落单档案惰性新建（测试 / 纯 API 模式）。``model_transport`` 仅供
    测试注入 MockTransport（模型发现走真实网络时为 None）。
    ``corpus_root``（P7 T6）语料目录，缺省 ./corpus（服务级，作者级资产）。
    ``ollama_transport``/``installed_probe``（本地模型）仅供测试注入。
    """
    app = FastAPI(title="Hastings", version="0.1.0")
    app.state.registry = registry  # WS 路由与测试内省用；REST 路由经参数绑定
    app.include_router(
        create_rest_router(
            registry=registry,
            book=book,
            model_transport=model_transport,
            corpus_root=corpus_root,
            ollama_transport=ollama_transport,
            installed_probe=installed_probe,
        )
    )
    app.include_router(ws_router)

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    if v2_runtime is not None:
        # V2 REST 面与 V1 共存（T14.8）：独立前缀 /api/v2，错误信封独立注册
        app.include_router(create_v2_router(v2_runtime))
        register_v2_error_handlers(app)

    if static_dir is not None and static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="spa")

    return app


__all__ = ["HealthResponse", "create_app"]
