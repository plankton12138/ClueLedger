"""按账号装配模型、模块库与作品服务（v1.4 P2）。

server 模式不再共享一份 ``config.yaml``、系统凭据命名空间或私有模块库。每个账号首次
访问时建立自己的 ``ProviderBook``、``ModelConnectionService``、授权服务与模块库；
作品服务仍按作品缓存，但每个作品只由创建它的账号打开。

本地模式注册一个固定的 ``__local__`` 资源，行为与 P1 之前一致。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hastings.application.ports.credentials import CredentialStore
from hastings.identity import current_user_id
from hastings.infrastructure.credentials import ScopedCredentialStore
from hastings.library.modules import ModuleLibrary
from hastings.server.model_auth import ModelAuthService
from hastings.server.model_auth_chatgpt import ChatGPTCodexAuthProvider, ChatGPTHostIdentity
from hastings.server.model_connections import ModelConnectionService
from hastings.server.model_factory import configured_limits_factory, configured_provider_factory
from hastings.server.platform_channel import PlatformChannel
from hastings.server.provider_config import ProviderBook, load_server_config
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.operations import OperationStore
from hastings.workspace.run_registry import RunRegistry
from hastings.workspace.service import ProviderFactory, TurnService
from hastings.workspace.service_hub import WorkspaceServices
from hastings.workspace.state import RecentWorkspaces

__all__ = [
    "LOCAL_KEY",
    "ScopedModuleLibrary",
    "ScopedWorkspaceServices",
    "ServiceContext",
    "UserResources",
    "UserResourcesRegistry",
    "build_user_resources",
]

LOCAL_KEY = "__local__"


@dataclass(slots=True)
class UserResources:
    key: str
    hub: WorkspaceServices
    model_service: ModelConnectionService | None
    auth: ModelAuthService | None
    library: ModuleLibrary | None
    book: ProviderBook | None = None


@dataclass(frozen=True, slots=True)
class ServiceContext:
    workspaces: RecentWorkspaces
    run_registry: RunRegistry
    operations: OperationStore
    lock: Any


class UserResourcesRegistry:
    """按当前账号惰性建立资源；``local`` 存在时始终返回同一份。"""

    def __init__(
        self,
        builder: Callable[[str, ServiceContext], UserResources] | None = None,
        *,
        local: UserResources | None = None,
    ) -> None:
        self._builder = builder
        self._local = local
        self._context: ServiceContext | None = None
        self._resources: dict[str, UserResources] = {}
        self._lock = threading.RLock()

    def bind(self, context: ServiceContext) -> None:
        self._context = context

    @property
    def context(self) -> ServiceContext:
        if self._context is None:
            raise RuntimeError("UserResourcesRegistry 尚未绑定共享服务")
        return self._context

    @property
    def workspaces(self) -> RecentWorkspaces:
        return self.context.workspaces

    @property
    def run_registry(self) -> RunRegistry:
        return self.context.run_registry

    @property
    def operations(self) -> OperationStore:
        return self.context.operations

    @property
    def lock(self) -> Any:
        return self.context.lock

    @property
    def models_available(self) -> bool:
        if self._local is not None:
            return self._local.model_service is not None
        return True

    @property
    def libraries_available(self) -> bool:
        if self._local is not None:
            return self._local.library is not None
        return True

    @property
    def auth_available(self) -> bool:
        if self._local is not None:
            return self._local.auth is not None
        return True

    def current(self) -> UserResources:
        if self._local is not None:
            return self._local
        user_id = current_user_id()
        if not user_id:
            raise WorkspaceError(
                code="WORKSPACE_ACCESS_DENIED",
                where="services.owner",
                why="当前请求没有账号身份",
                fix="重新登录后再试",
            )
        return self.for_user(user_id)

    def for_user(self, user_id: str) -> UserResources:
        with self._lock:
            existing = self._resources.get(user_id)
            if existing is not None:
                return existing
            if self._builder is None:
                raise RuntimeError("没有配置按账号建立的资源")
            resource = self._builder(user_id, self.context)
            self._resources[user_id] = resource
            return resource

    def model_service_for(self, user_id: str | None) -> ModelConnectionService | None:
        if self._local is not None:
            return self._local.model_service
        if not user_id:
            return None
        return self.for_user(user_id).model_service

    def current_model_service(self) -> ModelConnectionService:
        service = self.current().model_service
        if service is None:
            raise WorkspaceError(
                code="MODEL_NOT_CONFIGURED",
                where="model.service",
                why="当前账号没有可用的模型服务",
                fix="在模型设置中检查连接配置",
            )
        return service

    def current_library(self) -> ModuleLibrary:
        library = self.current().library
        if library is None:
            raise WorkspaceError(
                code="MODULE_ACCESS_DENIED",
                where="library.root",
                why="当前账号没有可用的模块库",
                fix="检查模块库目录是否可写",
            )
        return library


class ScopedWorkspaceServices:
    """路由看到的作品服务门面：每次调用解析到当前账号的实际实例。"""

    def __init__(self, registry: UserResourcesRegistry) -> None:
        self._registry = registry

    @property
    def workspaces(self) -> RecentWorkspaces:
        return self._registry.workspaces

    @property
    def run_registry(self) -> RunRegistry:
        return self._registry.run_registry

    @property
    def operations(self) -> OperationStore:
        return self._registry.operations

    @property
    def lock(self) -> Any:
        return self._registry.lock

    @property
    def library(self) -> ModuleLibrary | None:
        return self._registry.current().library

    @property
    def provider_factory(self) -> ProviderFactory:
        return self._registry.current().hub.provider_factory

    @property
    def turns(self) -> dict[str, TurnService]:
        return self._registry.current().hub.turns

    def current_limits(self) -> Any:
        return self._registry.current().hub.current_limits()

    def activity_for(self, workspace_id: str) -> Any:
        return self._registry.current().hub.activity_for(workspace_id)

    def service_for(self, workspace_id: str) -> TurnService:
        return self._registry.current().hub.service_for(workspace_id)

    def runner_for(self, workspace_id: str) -> Any:
        return self._registry.current().hub.runner_for(workspace_id)

    def forget(self, workspace_id: str) -> None:
        self._registry.current().hub.forget(workspace_id)

    def cached_service(self, workspace_id: str) -> TurnService | None:
        return self._registry.current().hub.turns.get(workspace_id)


class ScopedModuleLibrary:
    """模块库门面：路由在构造时拿到它，实际读写落在当前账号的库上。"""

    def __init__(self, resolver: Callable[[], ModuleLibrary]) -> None:
        self._resolver = resolver

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resolver(), name)


def build_user_resources(
    user_id: str,
    context: ServiceContext,
    *,
    state_root: Path,
    libraries_root: Path,
    base_credentials: CredentialStore,
    platform: PlatformChannel | None = None,
) -> UserResources:
    """server 模式的账号资源：独立配置、凭据命名空间、授权会话与模块库。"""
    user_state = Path(state_root) / "users" / user_id
    user_state.mkdir(parents=True, exist_ok=True)
    config_path = user_state / "model-connections.yaml"
    credentials = ScopedCredentialStore(base_credentials, f"hastings:user:{user_id}:")
    auth = ModelAuthService(
        providers={
            "chatgpt": ChatGPTCodexAuthProvider(
                identity=ChatGPTHostIdentity(user_state / "chatgpt-host.json")
            )
        }
    )
    server_config = load_server_config(config_path)
    if platform is not None:
        server_config = platform.attach(server_config)
    book = ProviderBook(
        server_config,
        boot_name="",
        boot_model="",
        config_path=config_path,
        credentials=credentials,
        auth=auth,
    )
    service = ModelConnectionService(
        book=book, config_path=config_path, credentials=credentials, auth=auth
    )
    library = ModuleLibrary(
        Path(libraries_root) / user_id,
        cache_file=user_state / "library-catalog.json",
    )
    hub = WorkspaceServices(
        context.workspaces,
        configured_provider_factory(book, platform=platform),
        library=library,
        run_registry=context.run_registry,
        operations=context.operations,
        limits_factory=configured_limits_factory(book),
    )
    return UserResources(
        key=user_id,
        hub=hub,
        model_service=service,
        auth=auth,
        library=library,
        book=book,
    )
