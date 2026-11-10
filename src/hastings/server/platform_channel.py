"""线簿订阅作为模型来源接入现有 ProviderProfile。

连接是服务器注入的只读档案：浏览器只看到「线簿订阅」和可选模型，不持有平台密钥。
每次模型调用前由 ``configured_provider_factory`` 用短期 grant 替换 Authorization。
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import SecretStr

from hastings.relay.internal_auth import mint_grant
from hastings.server.provider_config import ProviderProfile, ServerConfig

__all__ = ["PLATFORM_CONNECTION", "PlatformChannel"]

PLATFORM_CONNECTION = "line-book"
DEFAULT_PLATFORM_MODELS = ("deepseek-v4-pro", "deepseek-flash")


@dataclass(frozen=True, slots=True)
class PlatformChannel:
    base_url: str
    secret: str
    models: tuple[str, ...] = DEFAULT_PLATFORM_MODELS
    ttl_seconds: int = 900

    def __post_init__(self) -> None:
        if not self.base_url.strip() or not self.secret.strip():
            raise ValueError("订阅通道需要内部地址与授权密钥")
        if not self.models:
            raise ValueError("订阅通道至少需要一个模型")

    @property
    def connection_name(self) -> str:
        return PLATFORM_CONNECTION

    @property
    def api_base(self) -> str:
        base = self.base_url.rstrip("/")
        return base if base.endswith("/v1") else f"{base}/v1"

    def profile(self) -> ProviderProfile:
        return ProviderProfile(
            name=PLATFORM_CONNECTION,
            display_name="线簿订阅",
            base_url=self.api_base,
            api_key=SecretStr(""),
            no_auth=True,
            catalog_id="line-book",
            api="openai-completions",
            models=list(self.models),
            default_model=self.models[0],
        )

    def attach(self, config: ServerConfig) -> ServerConfig:
        providers = [item for item in config.providers if item.name != PLATFORM_CONNECTION]
        providers.append(self.profile())
        return config.model_copy(update={"providers": providers})

    def grant(self, user_id: str) -> str:
        return mint_grant(user_id, self.secret, ttl_seconds=self.ttl_seconds)
