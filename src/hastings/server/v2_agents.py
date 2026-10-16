"""V2 工作者装配；V1 的工具和记忆不进入这条执行链。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from hastings.application.errors import EnvironmentFailure
from hastings.application.ports.repository import RepositoryReader
from hastings.infrastructure.agents.tau_runner import EventSink, TauAgentRunner
from hastings.infrastructure.conversations.runner import ProviderChatRunner
from hastings.server.provider_config import (
    ProviderBook,
    ProviderProfile,
    ServerConfig,
    build_provider,
    is_loopback_url,
    load_server_config,
)


class ConfiguredV2Runner:
    def __init__(self, reader: RepositoryReader, config: ServerConfig | None = None) -> None:
        self.reader = reader
        self.config = config
        self.book: ProviderBook | None = None

    def _resolve_profile_model(self) -> tuple[ProviderProfile, str]:
        """解析生效档案与模型；book 优先（设置页选择），其次显式配置/环境变量。

        凭证失效保留选择、不跨厂商回退：profile 未通过可用性检查时直接失败，
        由调用方按 CHAT/WORKER 语义包装错误。
        """
        config = self.book.config if self.book is not None else self.config or load_server_config()
        requested = os.environ.get("HASTINGS_PROVIDER", "")
        model_override = os.environ.get("HASTINGS_MODEL", "")
        if self.book is not None:
            profile, model = self.book.selected()
        elif config.active_provider:
            profile = next((p for p in config.providers if p.name == config.active_provider), None)
            model = config.active_model
        else:
            profiles = [
                p
                for p in config.providers
                if p.available and (not requested or p.name == requested)
            ]
            profile = next((p for p in profiles if model_override or p.default_model), None)
            model = model_override or (profile.default_model if profile else "")
        if profile is None or not profile.available or not model:
            raise EnvironmentFailure(
                code="WORKER_PROVIDER_UNCONFIGURED",
                where="agent.provider",
                why="当前选择的模型来源没有可用凭证或模型，任务不会自动换用其他厂商",
                fix="打开设置→模型接入，连接厂商或本地服务并选择当前模型后重试",
            )
        return profile, model

    def chat_model(self) -> str:
        """当前对话轮次的模型标签；未配置抛 CHAT_MODEL_NOT_CONFIGURED 语义错误。"""
        try:
            profile, model = self._resolve_profile_model()
        except EnvironmentFailure as exc:
            raise EnvironmentFailure(
                code="CHAT_MODEL_NOT_CONFIGURED",
                where="chat.model",
                why=exc.why,
                fix=exc.fix,
            ) from exc
        del profile
        return model

    def chat_runner(self) -> ProviderChatRunner:
        """纯对话流式执行器：无工具、无写端口；本地模型放宽超时预算。"""
        return self.bound_chat_runner(self.model_binding())

    def bound_chat_runner(self, binding: dict[str, Any]) -> ProviderChatRunner:
        profile, _model = self._resolve_bound(binding)
        loopback = is_loopback_url(profile.base_url)
        return ProviderChatRunner(
            lambda: build_provider(profile),
            first_token_timeout=300.0 if loopback else 60.0,
            total_timeout=1800.0 if loopback else 600.0,
            max_output_chars=20000,
            owns_provider=True,
        )

    def model_binding(self) -> dict[str, Any]:
        """当前选择的绑定快照：任务启动时定格（切模型不影响已启动任务）。"""
        profile, model = self._resolve_profile_model()
        return {"profile": profile.name, "model": model, "api": profile.api}

    def _resolve_bound(self, binding: Mapping[str, Any]) -> tuple[ProviderProfile, str]:
        """按任务快照解析模型来源；失效明确失败，不静默换厂商（§19.5）。"""
        name = str(binding.get("profile", ""))
        model = str(binding.get("model", ""))
        if not name or not model:
            raise EnvironmentFailure(
                code="JOB_MODEL_SOURCE_UNAVAILABLE",
                where="agent.model_binding",
                why="任务缺少模型绑定快照",
                fix="重建任务（新尝试会绑定当前选择的模型）",
            )
        config = self.book.config if self.book is not None else self.config or load_server_config()
        profile = next((p for p in config.providers if p.name == name), None)
        if profile is None or not profile.available:
            raise EnvironmentFailure(
                code="JOB_MODEL_SOURCE_UNAVAILABLE",
                where="agent.model_binding",
                why=f"任务绑定的模型来源 {name!r} 已失效或不再可用，不会自动换用其他厂商",
                fix="在设置页恢复该来源的凭证，或为任务创建新尝试并明确选择模型",
                details={"profile": name, "model": model},
            )
        return profile, model

    async def run_with_events_bound(
        self,
        task_kind: str,
        context_package: Mapping[str, object],
        sink: EventSink,
        binding: Mapping[str, Any],
    ) -> Mapping[str, object]:
        """按任务启动时的绑定快照执行（切模型不改变已启动任务）。"""
        profile, model = self._resolve_bound(binding)
        runner = TauAgentRunner(
            lambda: build_provider(profile, worker=True),
            model,
            self.reader,
            owns_provider=True,
            provider_name=profile.name,
            timeout=600 if is_loopback_url(profile.base_url) else 120,
        )
        return await runner.run_with_events(task_kind, context_package, sink)

    def adapter(self) -> TauAgentRunner:
        profile, model = self._resolve_profile_model()
        return TauAgentRunner(
            lambda: build_provider(profile, worker=True),
            model,
            self.reader,
            owns_provider=True,
            provider_name=profile.name,
            timeout=600 if is_loopback_url(profile.base_url) else 120,
        )

    async def run(
        self, task_kind: str, context_package: Mapping[str, object]
    ) -> Mapping[str, object]:
        return await self.adapter().run(task_kind, context_package)

    async def run_with_events(
        self, task_kind: str, context_package: Mapping[str, object], sink: EventSink
    ) -> Mapping[str, object]:
        return await self.adapter().run_with_events(task_kind, context_package, sink)
