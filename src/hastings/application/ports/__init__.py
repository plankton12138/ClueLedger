"""应用层端口：Repository、AgentRunner、Clock、IdGenerator、JobStore、EventOutbox。

Phase 11 只冻结名称与语义（T11.5）；Phase 12 已把 repository 端口签名收窄为
领域值对象，并新增 Lease 端口与 RepositoryStorage 事务接缝。实现按依赖方向
落在 infrastructure，不得反向 import 业务代码。
"""

from __future__ import annotations

from hastings.application.ports.agents import AgentRunner
from hastings.application.ports.clock import Clock
from hastings.application.ports.events import EventOutbox
from hastings.application.ports.ids import IdGenerator
from hastings.application.ports.jobs import JobStore
from hastings.application.ports.repository import (
    CommitRepository,
    LeaseRepository,
    RepositoryReader,
    RepositoryStorage,
)

__all__ = [
    "AgentRunner",
    "Clock",
    "CommitRepository",
    "EventOutbox",
    "IdGenerator",
    "JobStore",
    "LeaseRepository",
    "RepositoryReader",
    "RepositoryStorage",
]
