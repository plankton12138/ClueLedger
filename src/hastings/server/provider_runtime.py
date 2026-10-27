"""模型实例构造与关闭（V04-9A/9B）。

这一层只做两件事：按连接保存的协议构造仓库里已有的适配器；让调用方可以明确关闭
本轮使用的实例。它不读环境变量、不读第三方目录、也不缓存实例——连接的编辑与删除
只影响之后新建的快照，正在运行的一轮始终用自己受理时拿到的那一个。

为什么不缓存：
- 缓存实例就要回答「谁负责关闭」：切换连接时关掉可能正在被一轮使用的 client，
  会把「作者换了模型」变成「正在写的这一轮失败」。
- 首版选择每轮独占实例，代价是放弃轮次之间的 HTTP 连接复用；这个开销比「关错对象」
  的复杂度低得多，等真有性能需求再引入引用计数。

本机地址（localhost / 127.0.0.1 / ::1）的请求不经系统代理：作者在自己机器上跑本地
兼容服务时，代理设置不该介入。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

import httpx

from hastings.server.model_limits import WORKER_OUTPUT_TOKENS, ModelLimits
from hastings.server.model_thinking import ThinkingLevel, is_deepseek, thinking_levels
from hastings.server.provider_catalog import ProviderApi
from tau_agent.messages import AgentMessage
from tau_agent.provider import CancellationToken
from tau_agent.provider_events import AssistantMessageEvent
from tau_agent.tools import AgentTool
from tau_ai.anthropic import AnthropicProvider
from tau_ai.env import (
    AnthropicConfig,
    OpenAICompatibleConfig,
    RuntimeProviderAuthResolver,
)
from tau_ai.google import GoogleGenerativeAIProvider
from tau_ai.openai_compatible import OpenAICompatibleProvider

__all__ = [
    "MANAGED_APIS",
    "ManagedModelProvider",
    "ModelSnapshot",
    "ProfileLike",
    "build_provider",
    "is_loopback_url",
    "snapshot",
    "subscription_provider_config",
]

#: 连接可保存的四类协议；与 provider_catalog.ProviderApi 同一个集合。
MANAGED_APIS = (
    "openai-completions",
    "openai-responses",
    "anthropic-messages",
    "google-generative-ai",
)

#: 写作轮的连接级重试次数：连接没建立时服务端还没收到请求，重发不会产生费用。
#: 代理握手失败与 DNS 抖动都是间歇性的，一次就放弃会让整轮写作作废。
WORKER_CONNECT_RETRIES = 2


@runtime_checkable
class ManagedModelProvider(Protocol):
    """ModelProvider + 可关闭。关闭不是模型协议的一部分，但实例生命周期是。"""

    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
        session_id: str | None = None,
    ) -> AsyncIterator[AssistantMessageEvent]: ...

    async def aclose(self) -> None: ...


class ProfileLike(Protocol):
    """构造实例所需的连接事实（结构化，不反向依赖配置模型）。"""

    api: ProviderApi
    base_url: str
    no_auth: bool

    def resolved_api_key(self) -> str: ...


def is_loopback_url(url: str) -> bool:
    return urlsplit(url).hostname in ("localhost", "127.0.0.1", "::1")


class _LocalProvider(OpenAICompatibleProvider):
    """保持 Tau 的请求协议，本机请求不经系统代理，client 生命周期仍由 provider 拥有。"""

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._config.timeout_seconds, trust_env=False)
        return self._client


def build_provider(
    profile: ProfileLike,
    *,
    worker: bool = False,
    max_retries: int | None = None,
    timeout_seconds: float | None = None,
    credential_resolver: RuntimeProviderAuthResolver | None = None,
    model: str = "",
    thinking_level: ThinkingLevel | None = None,
) -> ManagedModelProvider:
    """按连接保存的协议构造实例。

    ``worker`` 是写作轮次的预算（本机端点更长、重试关闭）；探针与列表请求显式给出
    更短的超时并且不重试——一次点击不该悄悄产生多次付费调用。
    订阅连接另走一条：只发 Responses 请求，凭据在每次请求前由 ``credential_resolver``
    解析（快过期就先续期）。
    """
    if thinking_level is not None and thinking_level not in thinking_levels(profile, model):
        raise ValueError(f"模型 {model!r} 不支持思考深度 {thinking_level!r}")
    if getattr(profile, "auth_method", "api_key") == "subscription":
        return OpenAICompatibleProvider(
            subscription_provider_config(
                profile,
                worker=worker,
                max_retries=max_retries,
                timeout_seconds=timeout_seconds,
                credential_resolver=credential_resolver,
                thinking_level=thinking_level,
            )
        )
    if profile.api == "anthropic-messages":
        return AnthropicProvider(
            AnthropicConfig(
                api_key=profile.resolved_api_key(),
                base_url=profile.base_url.rstrip("/"),
                max_retries=_retries(max_retries, worker),
                timeout_seconds=timeout_seconds or (180.0 if worker else 60.0),
                max_tokens=WORKER_OUTPUT_TOKENS if worker else None,
            )
        )
    if profile.api == "google-generative-ai":
        # Google 适配器复用 OpenAI 风格的配置对象：它只需要 key 与地址。
        return GoogleGenerativeAIProvider(
            _compatible_config(
                profile,
                worker=worker,
                max_retries=max_retries,
                timeout_seconds=timeout_seconds,
            )
        )
    return _openai_compatible(
        profile,
        worker=worker,
        max_retries=max_retries,
        timeout_seconds=timeout_seconds,
        thinking_level=thinking_level,
    )


def _retries(explicit: int | None, worker: bool) -> int:
    if explicit is not None:
        return explicit
    return 0 if worker else 2


def _compatible_config(
    profile: ProfileLike,
    *,
    worker: bool,
    max_retries: int | None,
    timeout_seconds: float | None,
    thinking_level: ThinkingLevel | None = None,
) -> OpenAICompatibleConfig:
    key = profile.resolved_api_key()
    loopback = is_loopback_url(profile.base_url)
    return OpenAICompatibleConfig(
        api_key=key,
        base_url=profile.base_url.rstrip("/"),
        # 免密档案（本地兼容服务）：空 key 不能发 `Authorization: Bearer `——
        # httpx 会以 Illegal header value 拒绝该请求。
        omit_authorization_header=profile.no_auth and not key,
        api=profile.api,
        # 协议由连接保存，不按模型名推断：同一个模型经由不同平台提供时可用的
        # 端点并不相同，猜错的表现是「模型存在但调用 404」。
        infer_api_from_model=False,
        max_retries=_retries(max_retries, worker),
        # 本机服务没有代理与 DNS 环节，连接失败通常是服务没起，重试没有意义。
        max_connect_retries=WORKER_CONNECT_RETRIES if worker and not loopback else 0,
        timeout_seconds=timeout_seconds or ((180.0 if loopback else 45.0) if worker else 60.0),
        max_tokens=WORKER_OUTPUT_TOKENS if worker else None,
        reasoning_effort="none" if thinking_level == "off" else thinking_level,
        include_reasoning_effort_none=thinking_level == "off",
        # 默认不发送开关；DeepSeek 的默认行为应由服务自身决定。
        thinking_format=(
            "deepseek" if thinking_level is not None and is_deepseek(profile) else "openai"
        ),
    )


def _openai_compatible(
    profile: ProfileLike,
    *,
    worker: bool,
    max_retries: int | None,
    timeout_seconds: float | None,
    thinking_level: ThinkingLevel | None = None,
) -> ManagedModelProvider:
    config = _compatible_config(
        profile,
        worker=worker,
        max_retries=max_retries,
        timeout_seconds=timeout_seconds,
        thinking_level=thinking_level,
    )
    provider_cls = _LocalProvider if is_loopback_url(profile.base_url) else OpenAICompatibleProvider
    return provider_cls(config)


def subscription_provider_config(
    profile: ProfileLike,
    *,
    worker: bool,
    max_retries: int | None,
    timeout_seconds: float | None,
    credential_resolver: RuntimeProviderAuthResolver | None,
    thinking_level: ThinkingLevel | None = None,
) -> OpenAICompatibleConfig:
    """订阅连接的请求配置。

    三件与按量付费不同的事：只走 Responses；不带 ``max_output_tokens``（订阅侧不接受
    这个字段）；凭据不固定在构造时，而是每次请求前解析一次，过期就先续期。
    """
    credential = getattr(profile, "subscription", None)
    if credential is None:
        raise RuntimeError(
            "这条订阅连接没有可用的凭据；需要重新登录后再使用"
        )
    return OpenAICompatibleConfig(
        api_key=credential.access_token,
        base_url=profile.base_url.rstrip("/"),
        api="openai-responses",
        # 协议由订阅决定，不按模型名推断：订阅只有 Responses 一条路。
        infer_api_from_model=False,
        max_tokens=None,
        max_retries=_retries(max_retries, worker),
        max_connect_retries=WORKER_CONNECT_RETRIES if worker else 0,
        timeout_seconds=timeout_seconds or (180.0 if worker else 45.0),
        credential_resolver=credential_resolver,
        provider_name="chatgpt-subscription",
        reasoning_effort="none" if thinking_level == "off" else thinking_level,
    )


@dataclass(frozen=True, slots=True)
class ModelSnapshot:
    """一轮实际使用的模型事实：受理时固定，之后不再查询配置。

    凭据只在 provider 内部持有，本对象只记录能公开的标识——快照会进事件与日志，
    所以这里刻意不放 api_key。
    """

    connection_id: str
    display_name: str
    model: str
    provider: ManagedModelProvider
    thinking_level: ThinkingLevel | None = None
    #: 受理时冻结的容量；只由 ``provider_config.snapshot_for`` 填入，界面显示与实际
    #: 预算共用这一份，避免「显示 8K、实际只允许 4K」。
    limits: ModelLimits | None = None

    def describe(self) -> dict[str, str]:
        return {
            "connection_id": self.connection_id,
            "connection_name": self.display_name,
            "model": self.model,
            **({"thinking_level": self.thinking_level} if self.thinking_level is not None else {}),
        }

    async def aclose(self) -> None:
        """关闭本轮实例；测试用假 provider 没有 aclose 时视为无需关闭。"""
        close = getattr(self.provider, "aclose", None)
        if close is None:
            return
        await close()


def snapshot(
    profile: ProfileLike,
    model: str,
    *,
    worker: bool = True,
    credential_resolver: RuntimeProviderAuthResolver | None = None,
    thinking_level: ThinkingLevel | None = None,
) -> ModelSnapshot:
    """由连接事实构造一轮快照（新实例，调用方负责关闭）。"""
    connection_id = getattr(profile, "name", "")
    display_name = getattr(profile, "display_name", "") or connection_id
    return ModelSnapshot(
        connection_id=connection_id,
        display_name=display_name,
        model=model,
        thinking_level=thinking_level,
        provider=build_provider(
            profile,
            worker=worker,
            credential_resolver=credential_resolver,
            model=model,
            thinking_level=thinking_level,
        ),
    )
