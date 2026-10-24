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

from hastings.server.model_connections import (
    ConfigView,
    ConnectionDraft,
    ModelConnectionService,
)
from hastings.server.provider_catalog import ProviderApi, ProviderPreset
from hastings.server.provider_config import ModelSelection

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


class SelectionView(BaseModel):
    connection_id: str | None
    model: str
    source: Literal["saved", "startup", "automatic", "none"]
    available: bool
    reason: str


class SelectionResponse(BaseModel):
    revision: str
    selection: SelectionView


class ModelConfigView(BaseModel):
    revision: str
    selection: SelectionView
    connections: list[ConnectionView]


class ConnectionRequest(BaseModel):
    """新增/编辑连接的提交内容。

    ``api_key`` 省略表示「保留旧 key」；新增时后端仍然要求非空。
    """

    display_name: str = ""
    catalog_id: str = ""
    base_url: str = ""
    api: ProviderApi = "openai-completions"
    models: list[str] = Field(default_factory=list)
    default_model: str = ""
    api_key: str | None = None


class SelectionRequest(BaseModel):
    connection_id: str | None = None
    model: str = ""


class DiscoverRequest(BaseModel):
    """对已保存连接或未保存草稿获取模型列表。"""

    connection_id: str | None = None
    catalog_id: str = ""
    base_url: str = ""
    api: ProviderApi = "openai-completions"
    api_key: str | None = None


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


def _selection(value: ModelSelection) -> SelectionView:
    return SelectionView(
        connection_id=value.connection_id,
        model=value.model,
        source=value.source,
        available=value.available,
        reason=value.reason,
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
    )


def _draft_from_probe(body: DiscoverRequest) -> ConnectionDraft:
    """发现与测试只需要地址、协议与 key，不需要模型列表。"""
    return ConnectionDraft(
        catalog_id=body.catalog_id,
        base_url=body.base_url,
        api=body.api,
        api_key=body.api_key,
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
            view = await service.select(body.connection_id, body.model, expected_revision=if_match)
        return _config_view(view)

    return router
