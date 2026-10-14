"""V2 repository 契约测试共享夹具：FakeClock / FakeIds / 双后端环境。"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hastings.domain.repository.ids import Actor

SYSTEM_ACTOR = Actor(kind="system", id="sys_test")
AGENT_ACTOR = Actor(kind="agent", id="agt_test")


class FakeClock:
    """可手动推进的时钟；绝不 sleep（T12.6）。"""

    def __init__(self) -> None:
        self._now = datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)


class FakeIds:
    def __init__(self) -> None:
        self._n = 0

    def new_id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}_{self._n:04d}"


@dataclass
class ContractEnv:
    """一个后端环境：repo 门面 + 可控时钟 + 关闭/重开钩子。"""

    repo: object  # MemoryRepository / SqliteRepository
    clock: FakeClock
    ids: FakeIds
    reopen: Callable[[], object] | None = None


EnvFactory = Callable[[], ContractEnv]


@pytest.fixture
def make_env_memory() -> Iterator[EnvFactory]:
    from hastings.infrastructure.repository.memory import build_memory_repository

    def make() -> ContractEnv:
        clock, ids = FakeClock(), FakeIds()
        repo = build_memory_repository(clock, ids)

        def reopen() -> ContractEnv:
            return ContractEnv(repo=repo, clock=clock, ids=ids, reopen=None)

        return ContractEnv(repo=repo, clock=clock, ids=ids, reopen=reopen)

    yield make


def make_env_sqlite_factory(db_path: Path) -> EnvFactory:
    """SQLite 环境工厂：每次调用关闭旧连接并重开同一数据库文件。"""

    from hastings.infrastructure.repository.sqlite import build_sqlite_repository

    holder: dict[str, object] = {}

    def make() -> ContractEnv:
        clock, ids = FakeClock(), FakeIds()
        old = holder.pop("repo", None)
        if old is not None:
            old.close()  # type: ignore[attr-defined]
        repo = build_sqlite_repository(db_path, clock=clock, ids=ids)
        holder["repo"] = repo
        return ContractEnv(repo=repo, clock=clock, ids=ids, reopen=make)

    return make
