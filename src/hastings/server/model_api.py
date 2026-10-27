"""模型设置接口（V04-9B）。

路由挂在应用级 ``/api/model-*``，不放在作品目录之下：模型设置不依赖是否打开了作品，
作者可以先连上供应商再去挑作品。

契约要点：
- 所有响应都带同一个配置 ``revision``；写操作要求 ``If-Match``，冲突返回 409。
- 保存、删除、选择返回「更新后的脱敏列表 + 有效选择 + 版本」，前端一次应用，
  避免再发一次请求期间显示「新连接配旧选择」。
- 发现与测试不改配置版本，响应标注它们针对的是哪个连接（草稿时为 null）。
- 请求与响应是两套 DTO，响应字段显式列出，不直接倾倒内部连接对象。
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Header
from pydantic import BaseModel, Field

from hastings.server.model_auth import AuthSessionView as AuthSession
from hastings.server.model_auth import AuthStatus
from hastings.server.model_connections import (
    ConfigView,
    ConnectionDraft,
    ModelConnectionService,
)
from hastings.server.model_limits import ModelLimitOverride
from hastings.server.model_thinking import ThinkingLevel
from hastings.server.provider_catalog import ProviderApi, ProviderPreset
from hastings.server.provider_config import AuthMethod, ModelSelection

__all__ = ["create_model_router"]

PREFIX = "/api"

#: 写操作的并发校验：前端把上一次读到的配置版本原样带回。
IfMatch = Annotated[str, Header(alias="If-Match")]


class RegionView(BaseModel):
    id: str
    label: str
    base_url: str


class ProviderPresetView(BaseModel):
    id: str
    display_name: str
    api: ProviderApi
    api_options: list[ProviderApi]
    base_url: str
    docs_url: str
    discovery: Literal["openai-models", "anthropic-models", "google-models"] | None
    regions: list[RegionView]
    #: 该供应商支持的订阅登录来源；空表示只能填 API Key。
    auth_providers: list[str]
    custom: bool
    note: str


class ConnectionView(BaseModel):
    name: str
    display_name: str
    catalog_id: str
    api: ProviderApi
    base_url: str
    models: list[str]
    default_model: str
    has_key: bool
    masked_key: str
    credential_storage: Literal["system", "session"]
    auth_method: AuthMethod
    auth_provider: str
    #: 订阅账号的脱敏标识；API Key 连接为空串。
    account: str
    #: ``ready`` 可调用、``expired`` 需要续期、``missing`` 没有凭据。
    auth_status: Literal["ready", "expired", "missing"]
    #: 按精确模型名保存的容量；未出现的模型按未知处理。
    model_limits: dict[str, ModelLimitOverride] = Field(default_factory=dict)


class SelectionView(BaseModel):
    connection_id: str | None
    model: str
    source: Literal["saved", "startup", "automatic", "none"]
    available: bool
    reason: str
    thinking_level: ThinkingLevel | None
    thinking_levels: list[ThinkingLevel]


class SelectionResponse(BaseModel):
    revision: str
    selection: SelectionView


class ModelConfigView(BaseModel):
    revision: str
    selection: SelectionView
    connections: list[ConnectionView]


class ConnectionRequest(BaseModel):
    """新增/编辑连接的提交内容。

    ``api_key`` 省略表示「保留旧 key」；新增 API Key 连接时仍然要求非空。
    订阅连接改为带 ``auth_provider`` 与一次已完成的 ``auth_session_id``。
    """

    display_name: str = ""
    catalog_id: str = ""
    base_url: str = ""
    api: ProviderApi = "openai-completions"
    models: list[str] = Field(default_factory=list)
    default_model: str = ""
    api_key: str | None = None
    auth_method: AuthMethod = "api_key"
    auth_provider: str = ""
    auth_session_id: str = ""
    #: 按精确模型名提交的容量；省略表示沿用已保存的配置，传 ``{}`` 才是清空。
    model_limits: dict[str, ModelLimitOverride] | None = None


class SelectionRequest(BaseModel):
    connection_id: str | None = None
    model: str = ""
    thinking_level: ThinkingLevel | None = None


class DiscoverRequest(BaseModel):
    """对已保存连接、未保存草稿或一次已完成授权获取模型列表。"""

    connection_id: str | None = None
    catalog_id: str = ""
    base_url: str = ""
    api: ProviderApi = "openai-completions"
    api_key: str | None = None
    auth_provider: str = ""
    auth_session_id: str = ""


class DiscoveryView(BaseModel):
    revision: str
    connection_id: str | None
    models: list[str]
    complete: bool
    code: str | None
    message: str


class ProbeRequest(DiscoverRequest):
    kind: Literal["response", "tool"]
    model: str = ""


class ProbeView(BaseModel):
    revision: str
    connection_id: str | None
    model: str
    response: Literal["passed", "failed", "untested"]
    tool_call: Literal["passed", "failed", "untested"]
    code: str | None
    message: str


class AuthProviderView(BaseModel):
    """一家可以「登录订阅账号」的来源；与 API Key 预设是两件事。"""

    id: str
    name: str


class AuthSessionStartRequest(BaseModel):
    provider_id: str


class AuthSessionResponse(BaseModel):
    """授权会话的对外形状：只有进度与账号标识，没有任何令牌。"""

    id: str
    provider_id: str
    provider_name: str
    status: AuthStatus
    auth_url: str
    user_code: str
    instructions: str
    expires_at: float
    account: str
    code: str
    message: str


def _auth_session_view(value: AuthSession) -> AuthSessionResponse:
    return AuthSessionResponse(
        id=value.id,
        provider_id=value.provider_id,
        provider_name=value.provider_name,
        status=value.status,
        auth_url=value.auth_url,
        user_code=value.user_code,
        instructions=value.instructions,
        expires_at=value.expires_at,
        account=value.account,
        code=value.code,
        message=value.message,
    )


def _selection(value: ModelSelection) -> SelectionView:
    return SelectionView(
        connection_id=value.connection_id,
        model=value.model,
        source=value.source,
        available=value.available,
        reason=value.reason,
        thinking_level=value.thinking_level,
        thinking_levels=list(value.thinking_levels),
    )


def _config_view(view: ConfigView) -> ModelConfigView:
    return ModelConfigView(
        revision=view.revision,
        selection=_selection(view.selection),
        connections=[
            ConnectionView(
                name=item.name,
                display_name=item.display_name,
                catalog_id=item.catalog_id,
                api=item.api,  # type: ignore[arg-type]
                base_url=item.base_url,
                models=list(item.models),
                default_model=item.default_model,
                has_key=item.has_key,
                masked_key=item.masked_key,
                credential_storage=item.credential_storage,  # type: ignore[arg-type]
                auth_method=item.auth_method,  # type: ignore[arg-type]
                auth_provider=item.auth_provider,
                account=item.account,
                auth_status=item.auth_status,  # type: ignore[arg-type]
                model_limits=dict(item.model_limits),
            )
            for item in view.connections
        ],
    )


def _preset_view(item: ProviderPreset) -> ProviderPresetView:
    return ProviderPresetView(
        id=item.id,
        display_name=item.display_name,
        api=item.api,
        api_options=list(item.apis),
        base_url=item.base_url,
        docs_url=item.docs_url,
        discovery=item.discovery,
        regions=[
            RegionView(id=region.id, label=region.label, base_url=region.base_url)
            for region in item.regions
        ],
        auth_providers=list(item.auth_providers),
        custom=item.custom,
        note=item.note,
    )


def _draft_from_form(body: ConnectionRequest) -> ConnectionDraft:
    return ConnectionDraft(
        display_name=body.display_name,
        catalog_id=body.catalog_id,
        base_url=body.base_url,
        api=body.api,
        models=list(body.models),
        default_model=body.default_model,
        api_key=body.api_key,
        auth_method=body.auth_method,
        auth_provider=body.auth_provider,
        auth_session_id=body.auth_session_id,
        model_limits=(
            dict(body.model_limits) if body.model_limits is not None else None
        ),
    )


def _draft_from_probe(body: DiscoverRequest) -> ConnectionDraft:
    """发现与测试只需要地址、协议与凭据，不需要模型列表。"""
    return ConnectionDraft(
        catalog_id=body.catalog_id,
        base_url=body.base_url,
        api=body.api,
        api_key=body.api_key,
        auth_provider=body.auth_provider,
        auth_session_id=body.auth_session_id,
    )


def create_model_router(service: ModelConnectionService) -> APIRouter:
    """模型设置路由；所有写操作共用一个串行服务。"""
    router = APIRouter(prefix=PREFIX, tags=["models"])

    @router.get("/model-providers", response_model=list[ProviderPresetView])
    def list_presets() -> list[ProviderPresetView]:
        return [_preset_view(item) for item in service.presets()]

    @router.get("/model-connections", response_model=ModelConfigView)
    def list_connections() -> ModelConfigView:
        return _config_view(service.view())

    @router.post("/model-connections", response_model=ModelConfigView)
    async def create_connection(body: ConnectionRequest, if_match: IfMatch = "") -> ModelConfigView:
        view = await service.create(_draft_from_form(body), expected_revision=if_match)
        return _config_view(view)

    @router.patch("/model-connections/{name}", response_model=ModelConfigView)
    async def update_connection(
        name: str, body: ConnectionRequest, if_match: IfMatch = ""
    ) -> ModelConfigView:
        view = await service.update(name, _draft_from_form(body), expected_revision=if_match)
        return _config_view(view)

    @router.delete("/model-connections/{name}", response_model=ModelConfigView)
    async def delete_connection(name: str, if_match: IfMatch = "") -> ModelConfigView:
        view = await service.delete(name, expected_revision=if_match)
        return _config_view(view)

    @router.post("/model-connections/discover", response_model=DiscoveryView)
    async def discover(body: DiscoverRequest) -> DiscoveryView:
        result = await service.discover(_draft_from_probe(body), connection_id=body.connection_id)
        return DiscoveryView(
            revision=service.revision,
            connection_id=body.connection_id,
            models=list(result.models),
            complete=result.complete,
            code=result.code,
            message=result.message,
        )

    @router.post("/model-connections/probe", response_model=ProbeView)
    async def probe(body: ProbeRequest) -> ProbeView:
        result = await service.probe(
            _draft_from_probe(body),
            kind=body.kind,
            model=body.model,
            connection_id=body.connection_id,
        )
        return ProbeView(
            revision=service.revision,
            connection_id=body.connection_id,
            model=result.model,
            response=result.response,
            tool_call=result.tool_call,
            code=result.code,
            message=result.message,
        )

    @router.get("/model-selection", response_model=SelectionResponse)
    def read_selection() -> SelectionResponse:
        view = service.view()
        return SelectionResponse(revision=view.revision, selection=_selection(view.selection))

    @router.put("/model-selection", response_model=ModelConfigView)
    async def write_selection(body: SelectionRequest, if_match: IfMatch = "") -> ModelConfigView:
        if body.connection_id is None:
            view = await service.clear_selection(expected_revision=if_match)
        else:
            view = await service.select(
                body.connection_id,
                body.model,
                expected_revision=if_match,
                thinking_level=body.thinking_level,
            )
        return _config_view(view)

    # -- 订阅登录：会话只存在于进程内，保存连接时才归属 ----------------------

    if service.auth is not None:
        auth = service.auth

        @router.get("/model-auth/providers", response_model=list[AuthProviderView])
        def list_auth_providers() -> list[AuthProviderView]:
            return [AuthProviderView(id=item.id, name=item.name) for item in auth.providers()]

        @router.post(
            "/model-auth/sessions", response_model=AuthSessionResponse, status_code=201
        )
        async def start_auth_session(body: AuthSessionStartRequest) -> AuthSessionResponse:
            return _auth_session_view(await auth.create(body.provider_id))

        @router.get("/model-auth/sessions/{session_id}", response_model=AuthSessionResponse)
        async def read_auth_session(session_id: str) -> AuthSessionResponse:
            return _auth_session_view(await auth.status(session_id))

        @router.delete("/model-auth/sessions/{session_id}", response_model=AuthSessionResponse)
        async def cancel_auth_session(session_id: str) -> AuthSessionResponse:
            return _auth_session_view(await auth.cancel(session_id))

    return router
