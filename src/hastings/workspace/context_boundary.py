"""真实推理请求之前的整理边界；不改 harness 或工具读取账本。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hastings.workspace.compaction import active_compaction
from hastings.workspace.conversation_context import replay_public_messages
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.model_activity import _request_estimate
from tau_agent.messages import AgentMessage
from tau_agent.provider import CancellationToken

if TYPE_CHECKING:
    from hastings.server.provider_runtime import ModelSnapshot
    from hastings.workspace.service import TurnInfo, TurnService


class RequestBoundary:
    def __init__(
        self,
        service: TurnService,
        info: TurnInfo,
        snapshot: ModelSnapshot,
        history: tuple[dict[str, Any], ...],
    ) -> None:
        self.service, self.info, self.snapshot = service, info, snapshot
        self.history = history
        self.prefix_length = len(
            replay_public_messages(history, compaction=active_compaction(history))
        )
        self.attempted = False

    def events(self) -> tuple[dict[str, Any], ...]:
        # 新摘要提交晚于受理游标，但只能用于受理时的历史，不能带入当前作者消息。
        return (
            *self.history,
            *(
                event
                for event in self.service.store.events(self.info.conversation_id)
                if event["seq"] > self.info.history_seq and event.get("type") == "context_compacted"
            ),
        )

    def project(self, messages: list[AgentMessage]) -> list[AgentMessage]:
        history = self.events()
        return [
            *replay_public_messages(history, compaction=active_compaction(history)),
            *messages[self.prefix_length :],
        ]

    async def __call__(
        self,
        messages: list[AgentMessage],
        system: str,
        definitions: list[dict[str, Any]],
        signal: CancellationToken | None,
    ) -> list[AgentMessage]:
        if signal is not None and signal.is_cancelled():
            return self.project(messages)
        handled = False
        if self.service.compactions is not None:
            handled = await self.service.compactions.at_boundary(
                self.info.turn_id,
                self.snapshot,
                self.events(),
                signal,
            )
        working = self.project(messages)
        limits = self.snapshot.limits
        limit = limits.input_limit() if limits else None
        estimate = _request_estimate(
            identity=f"{self.snapshot.connection_id}:{self.snapshot.model}",
            system=system,
            messages=working,
            definitions=definitions,
            limits=limits,
            source_revisions={},
        )
        total = estimate["total_tokens"]
        if (
            self.service.auto_compaction
            and not self.attempted
            and not handled
            and limit is not None
            and isinstance(total, int)
            and total >= limit * self.service.compaction_threshold
        ):
            # 同一受理历史最多自动尝试一次；失败不能在每个工具往返重复收费。
            self.attempted = True
            self.service.store.append(
                self.info.conversation_id,
                {
                    "type": "context_compaction_started",
                    "turn_id": self.info.turn_id,
                    "reason": "budget",
                },
            )
            try:
                record = await self.service._compact_locked(
                    self.info.conversation_id,
                    reason="budget",
                    snapshot=self.snapshot,
                    events=self.events(),
                    signal=signal,
                )
            except WorkspaceError as exc:
                self.service.store.append(
                    self.info.conversation_id,
                    {
                        "type": "context_compaction_failed",
                        "turn_id": self.info.turn_id,
                        "code": exc.code,
                        "message": exc.message,
                        "reason": "budget",
                    },
                )
            else:
                if record is None:
                    self.service.store.append(
                        self.info.conversation_id,
                        {
                            "type": "context_compaction_noop",
                            "turn_id": self.info.turn_id,
                            "reason": "budget",
                        },
                    )
            working = self.project(messages)
        return working
