"""连接保存凭证，选择保存使用意图；两者共享服务实际使用的 ProviderBook。"""

from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, SecretStr, field_validator

from hastings.provider_catalog import catalog_entry
from hastings.server.provider_config import (
    ProviderBook,
    ProviderProfile,
    ServerConfig,
    mask_key,
    save_server_config,
)
from hastings.server.rest import ModelDiscoveryError, discover_models


class ConnectRequest(BaseModel):
    catalog_id: str
    api_key: SecretStr = SecretStr("")
    use_environment: bool = False


class ModelSelection(BaseModel):
    provider: str | None = None
    model: str = ""
    display_name: str = ""
    available: bool = False


class SelectRequest(BaseModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1, max_length=256)

    @field_validator("model")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("模型名不能为空；从模型列表选择一个模型")
        return value.strip()


def create_model_access_router(
    book: ProviderBook, *, transport: httpx.AsyncBaseTransport | None = None
) -> APIRouter:
    router = APIRouter(tags=["models"])

    def persist(config: ServerConfig) -> None:
        try:
            save_server_config(config, book.config_path)
        except OSError as exc:
            raise HTTPException(
                500, "模型设置保存失败：配置目录不可写；修复目录权限后重试"
            ) from exc
        book.update_config(config)

    def selection() -> ModelSelection:
        profile, model = book.selected()
        entry = catalog_entry(profile.catalog_id) if profile else None
        return ModelSelection(
            provider=profile.name if profile else book.config.active_provider or None,
            model=model,
            display_name=entry.display_name if entry else profile.name if profile else "",
            available=bool(profile and profile.available and model),
        )

    def require(name: str) -> ProviderProfile:
        profile = book.profile(name)
        if profile is None:
            raise HTTPException(404, "模型来源不存在；请先连接厂商或本地服务")
        if not profile.available:
            raise HTTPException(422, "模型来源缺少可用密钥；请重新接入该厂商，再选择模型")
        return profile

    @router.get("/api/model-selection")
    async def get_selection() -> ModelSelection:
        return selection()

    @router.put("/api/model-selection")
    async def select(body: SelectRequest) -> ModelSelection:
        require(body.provider)
        persist(
            book.config.model_copy(
                update={"active_provider": body.provider, "active_model": body.model}
            )
        )
        return selection()

    @router.post("/api/providers/connect")
    async def connect(body: ConnectRequest) -> dict[str, object]:
        entry = catalog_entry(body.catalog_id)
        if entry is None:
            raise HTTPException(404, "未收录该厂商；请选择目录中的来源或使用自定义连接")
        previous = book.profile(entry.name)
        same_source = previous is not None and previous.base_url.rstrip(
            "/"
        ) == entry.base_url.rstrip("/")
        key = (
            "" if body.use_environment or entry.no_auth else body.api_key.get_secret_value().strip()
        )
        if (
            not entry.no_auth
            and not key
            and same_source
            and previous is not None
            and not body.use_environment
        ):
            key = previous.api_key.get_secret_value()
        profile = ProviderProfile(
            name=entry.name,
            base_url=entry.base_url,
            catalog_id=entry.name,
            api_key=SecretStr(key),
            api_key_env=entry.api_key_env
            if body.use_environment
            else (previous.api_key_env if same_source and previous is not None and not key else ""),
            default_model=entry.default_model,
            no_auth=entry.no_auth,
        )
        if not profile.available:
            raise HTTPException(422, "尚未提供可用密钥；粘贴本来源的 API Key，或设置对应环境变量")
        persist(
            book.config.model_copy(
                update={
                    "providers": [
                        *[p for p in book.config.providers if p.name != entry.name],
                        profile,
                    ]
                }
            )
        )
        return {
            "name": profile.name,
            "base_url": profile.base_url,
            "default_model": profile.default_model,
            "catalog_id": profile.catalog_id,
            "no_auth": profile.no_auth,
            "available": profile.available,
            "key_hint": mask_key(profile.resolved_api_key()),
        }

    @router.get("/api/providers/{name}/model-choices")
    async def model_choices(name: str) -> dict[str, object]:
        profile = require(name)
        entry = catalog_entry(profile.catalog_id)
        source: Literal["remote", "catalog"] = "remote"
        notice = "模型列表来自服务端；模型是否支持任务工具以实际调用为准。"
        try:
            models = await discover_models(
                profile.base_url, profile.resolved_api_key(), transport=transport
            )
        except ModelDiscoveryError as exc:
            if exc.upstream_status not in (404, 405) or entry is None or not entry.models:
                raise HTTPException(exc.status_code, exc.detail) from exc
            models = list(entry.models)
            source = "catalog"
            notice = "此服务未提供模型列表，显示内置候选；凭证及模型调用尚未验证。"
        return {"models": models, "source": source, "verified": False, "notice": notice}

    return router
