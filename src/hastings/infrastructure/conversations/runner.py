"""ProviderChatRunner（Phase 19.3 T19.3c）：把 Tau Provider 流接入 ChatRunner 端口。

普通聊天复用 ``OpenAICompatibleProvider.stream_response`` 的文本流：``tools``
恒为空（四透镜②：聊天拿不到任何工具，自然没有写端口），``TextDeltaEvent``
逐段透传给 sink，``ThinkingDelta`` 不下发（不显示内部推理文本，计划 §19.3）。

失败分类是本适配器的主要职责（泄压阀三情形）：空文本、授权失败、连接失败、
超时各自有稳定 code，由应用服务映射到轮次终态；取消不是失败——token 生效后
返回已收到的部分文本，由存储层裁决迟到分片。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from hastings.application.errors import EnvironmentFailure
from hastings.application.ports.chat import ChatRun
from hastings.domain.conversation import error_codes as ec
from tau_agent.messages import AssistantMessage, TextContent, UserMessage
from tau_agent.provider_events import AssistantErrorEvent, TextDeltaEvent

__all__ = ["ProviderChatRunner", "TurnCancellationToken"]


class TurnCancellationToken:
    """轮次取消 token：``asyncio.Event`` 的同步视图（Provider 协议要求同步判）。"""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()


class ProviderChatRunner:
    """按每次执行构造 provider（配置可在轮次之间变化），流式执行纯对话。"""

    def __init__(
        self,
        provider_factory: Callable[[], Any],
        *,
        first_token_timeout: float = 90.0,
        total_timeout: float = 600.0,
        max_output_chars: int = 20000,
        owns_provider: bool = False,
    ) -> None:
        self._provider_factory = provider_factory
        self._first_token_timeout = first_token_timeout
        self._total_timeout = total_timeout
        self._max_output_chars = max_output_chars
        self._owns_provider = owns_provider

    # -- 消息构建 ------------------------------------------------------------
    @staticmethod
    def _build_messages(run: ChatRun) -> list[Any]:
        messages: list[Any] = []
        for role, text in run.history:
            if role == "author":
                messages.append(UserMessage(content=text))
            elif role == "assistant":
                messages.append(
                    AssistantMessage(content=[TextContent(text=text)], stop_reason="stop")
                )
        tail = list(run.refs)
        if messages and tail:
            quoted = "\n\n".join(f"- {ref}" for ref in tail)
            messages[-1] = UserMessage(
                content=f"{run.history[-1][1]}\n\n引用材料：\n{quoted}"
            )
        elif tail:
            quoted = "\n\n".join(f"- {ref}" for ref in tail)
            messages.append(UserMessage(content=f"引用材料：\n{quoted}"))
        return messages

    # -- 失败构造 ------------------------------------------------------------
    @staticmethod
    def _failure(code: str, why: str, fix: str, **details: Any) -> EnvironmentFailure:
        return EnvironmentFailure(
            code=code, where="chat.runner", why=why, fix=fix, details=details or None
        )

    # -- 主流程 --------------------------------------------------------------
    async def stream(self, run: ChatRun, sink: Any) -> str:
        provider = self._provider_factory()
        token = run.cancelled
        chunks: list[str] = []
        # 首段预算与总预算取较小者作为阶段一上限；谁先到期就按谁分类——
        # 首段超时与总时长超时是两个状态，文案与对策不同（四透镜①）。
        phase_one_budget = min(self._first_token_timeout, self._total_timeout)
        phase_one_is_total = self._total_timeout <= self._first_token_timeout
        try:
            async with asyncio.timeout(self._total_timeout):
                stream = provider.stream_response(
                    model=run.model,
                    system=run.system,
                    messages=self._build_messages(run),
                    tools=[],
                    signal=token if isinstance(token, TurnCancellationToken) else None,
                )
                # 阶段一：首段正文等待（独立预算，本地冷启动不改总预算）。
                try:
                    async with asyncio.timeout(phase_one_budget):
                        async for event in stream:
                            if token():
                                return ""
                            if isinstance(event, AssistantErrorEvent):
                                raise self._failure(
                                    ec.CHAT_PROVIDER_FAILED,
                                    "Provider 返回错误事件，本轮没有可用正文",
                                    "检查端点与凭证后重试；不会自动换用其他厂商",
                                )
                            if not isinstance(event, TextDeltaEvent):
                                continue
                            await sink(event.delta)
                            chunks.append(event.delta)
                            break
                except TimeoutError:
                    if phase_one_is_total:
                        raise self._failure(
                            ec.CHAT_TURN_TIMEOUT,
                            "本轮回复超出总时长上限，模型始终没有返回正文",
                            "稍后重试；本地模型可调大 total_timeout",
                        ) from None
                    raise self._failure(
                        ec.CHAT_FIRST_TOKEN_TIMEOUT,
                        "模型连接建立后迟迟没有返回正文",
                        "检查模型端点状态；本地模型冷启动可调大 first_token_timeout 后重试",
                    ) from None
                if token():
                    return "".join(chunks)
                # 阶段二：后续分片，只受总预算与输出上限约束。
                async for event in stream:
                    if token():
                        break
                    if isinstance(event, AssistantErrorEvent):
                        raise self._failure(
                            ec.CHAT_PROVIDER_FAILED,
                            "Provider 在输出中途返回错误事件，已收到的部分文本保留",
                            "稍后重试；不会自动换用其他厂商",
                        )
                    if not isinstance(event, TextDeltaEvent):
                        continue
                    remaining = self._max_output_chars - sum(len(c) for c in chunks)
                    if remaining <= 0:
                        break
                    chunk = event.delta[:remaining] if len(event.delta) > remaining else event.delta
                    await sink(chunk)
                    chunks.append(chunk)
                    if len(chunk) < len(event.delta):
                        break
        except TimeoutError:
            # 阶段二超时：总预算到期（阶段一超时已在内层按语义分类抛出）。
            raise self._failure(
                ec.CHAT_TURN_TIMEOUT,
                "本轮回复超出总时长上限，已收到的部分文本保留",
                "稍后重试或调小问题规模；本地模型可调大 total_timeout",
            ) from None
        except (OSError, ConnectionError) as exc:
            raise self._failure(
                ec.CHAT_PROVIDER_UNAVAILABLE,
                f"连接模型端点失败：{type(exc).__name__}",
                "确认网络与端点地址后重试；不会自动换用其他厂商",
            ) from exc
        finally:
            close = getattr(provider, "aclose", None)
            if self._owns_provider and close:
                await close()
        text = "".join(chunks)
        if not text and not token():
            raise self._failure(
                ec.CHAT_EMPTY_COMPLETION,
                "模型正常结束但没有返回任何正文",
                "换一种问法重试；这不是环境故障，也不代表之前的内容丢失",
            )
        return text
