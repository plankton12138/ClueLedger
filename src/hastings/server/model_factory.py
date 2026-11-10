"""把 ProviderBook 的选择变成每个用户可用的模型工厂。

抽到单独模块是为了让 server 模式的按用户资源装配复用同一套错误文案：
本地模式与网站模式对「没有选择模型」必须给出同一种可操作提示。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from hastings.identity import current_user_id
from hastings.server.model_limits import ModelLimits, resolve_model_limits
from hastings.server.platform_channel import PLATFORM_CONNECTION, PlatformChannel
from hastings.server.provider_config import ProviderBook, ProviderConfigError
from hastings.server.provider_runtime import ModelSnapshot, snapshot
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.service import ProviderFactory
from tau_ai.env import RuntimeProviderAuth

__all__ = ["configured_limits_factory", "configured_provider_factory"]


def configured_provider_factory(
    book: ProviderBook, *, platform: PlatformChannel | None = None
) -> ProviderFactory:
    """受理一轮时固定模型快照；订阅连接每次模型调用换一份短期授权。"""

    def factory() -> ModelSnapshot:
        selection = book.selection()
        if selection.connection_id == PLATFORM_CONNECTION:
            channel = platform
            if channel is None:
                raise WorkspaceError(
                    code="MODEL_NOT_CONFIGURED",
                    where="turn.model",
                    why="线簿订阅通道尚未配置",
                    fix="联系维护者检查订阅服务地址与内部授权；或改选自带模型连接",
                    details={"connection": PLATFORM_CONNECTION},
                )
            profile = book.profile(PLATFORM_CONNECTION)
            user_id = current_user_id()
            if profile is None or not user_id or not selection.model:
                raise WorkspaceError(
                    code="MODEL_NOT_CONFIGURED",
                    where="turn.model",
                    why=selection.reason or "线簿订阅还没有选择模型",
                    fix="在模型设置里选择「线簿订阅」的模型，或改选自带模型连接",
                    details={
                        "connection": selection.connection_id or "",
                        "model": selection.model,
                    },
                )
            granted = profile.model_copy(update={"no_auth": False})

            async def resolve_credential() -> RuntimeProviderAuth:
                # 代理把 grant 的 jti 当作幂等请求号。一轮里工具循环会发多次模型
                # 请求，共用同一份授权会让第二次起被判成「重放」；同一次调用内的
                # HTTP 重试才应沿用同一份，所以这里按调用签发。
                return RuntimeProviderAuth(api_key=channel.grant(user_id))

            try:
                limits = resolve_model_limits(
                    granted,
                    selection.model,
                    project_limit=book.config.context_policy.max_input_tokens,
                )
            except ValueError as exc:
                raise WorkspaceError(
                    code="MODEL_NOT_CONFIGURED",
                    where="turn.model",
                    why=str(exc),
                    fix="检查订阅模型容量配置后重试",
                ) from exc
            return replace(
                snapshot(
                    granted,
                    selection.model,
                    thinking_level=selection.thinking_level,
                    credential_resolver=resolve_credential,
                ),
                limits=limits,
            )
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


def configured_limits_factory(book: ProviderBook) -> Callable[[], ModelLimits | None]:
    """当前选择的容量与预算，只读配置：首次发送前与空闲时都能显示当前分母。"""

    def factory() -> ModelLimits | None:
        try:
            return book.current_limits()
        except ValueError:
            # 容量配置本身矛盾时由发送路径报错；读取面板不该因此失败。
            return None

    return factory
