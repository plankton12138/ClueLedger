"""V2 工作者装配；V1 的工具和记忆不进入这条执行链。"""

from __future__ import annotations

import os
from collections.abc import Mapping

from hastings.application.errors import EnvironmentFailure
from hastings.application.ports.repository import RepositoryReader
from hastings.infrastructure.agents.tau_runner import EventSink, TauAgentRunner
from hastings.server.provider_config import ServerConfig, load_server_config
from tau_ai.env import OpenAICompatibleConfig
from tau_ai.openai_compatible import OpenAICompatibleProvider


class ConfiguredV2Runner:
    def __init__(self, reader: RepositoryReader, config: ServerConfig | None = None) -> None:
        self.reader = reader
        self.config = config

    def adapter(self) -> TauAgentRunner:
        config = self.config or load_server_config()
        requested = os.environ.get("HASTINGS_PROVIDER", "")
        profiles = [
            p for p in config.providers if p.available and (not requested or p.name == requested)
        ]
        model_override = os.environ.get("HASTINGS_MODEL", "")
        profile = next((p for p in profiles if model_override or p.default_model), None)
        if profile is None:
            raise EnvironmentFailure(
                code="WORKER_PROVIDER_UNCONFIGURED",
                where="agent.provider",
                why="没有同时可用的 Provider 与模型配置",
                fix="在 config.yaml 设置可用档案的 default_model，或设置 HASTINGS_MODEL",
            )
        settings = OpenAICompatibleConfig(
            api_key=profile.resolved_api_key(),
            base_url=profile.base_url,
            timeout_seconds=45,
            max_retries=0,
            max_tokens=4096,
        )
        return TauAgentRunner(
            lambda: OpenAICompatibleProvider(settings),
            model_override or profile.default_model,
            self.reader,
            owns_provider=True,
        )

    async def run(
        self, task_kind: str, context_package: Mapping[str, object]
    ) -> Mapping[str, object]:
        return await self.adapter().run(task_kind, context_package)

    async def run_with_events(
        self, task_kind: str, context_package: Mapping[str, object], sink: EventSink
    ) -> Mapping[str, object]:
        return await self.adapter().run_with_events(task_kind, context_package, sink)
