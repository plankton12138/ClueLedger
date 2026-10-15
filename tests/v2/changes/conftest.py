"""tests/v2/changes 公共夹具：内存仓库 + ChangeSet handler（Phase 15）。

与 tests/v2/manuscript 的 ManualEditEnv 同构：FakeClock/FakeIds 绝不 sleep、
ID 确定性递增；仓库用内存后端，领域测试不需要数据库、Web 或模型。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from hastings.application.handlers.change_commands import ChangeSetCommandHandler
from hastings.domain.repository.ids import Actor, ProjectId
from hastings.infrastructure.repository.memory import MemoryRepository, build_memory_repository


class FakeClock:
    """可手动推进的时钟；绝不 sleep。"""

    def __init__(self) -> None:
        self._now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)

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


AUTHOR = Actor(kind="human", id="author_test")


@dataclass
class ChangesEnv:
    """应用层环境：仓库门面 + ChangeSet handler。"""

    repo: MemoryRepository
    service: Any  # CommitService（读+写门面）
    handler: ChangeSetCommandHandler
    ids: FakeIds
    clock: FakeClock


@pytest.fixture
def changes_env() -> ChangesEnv:
    clock, ids = FakeClock(), FakeIds()
    repo = build_memory_repository(clock, ids)
    handler = ChangeSetCommandHandler(repo.service, repo.service, ids, AUTHOR)
    return ChangesEnv(repo=repo, service=repo.service, handler=handler, ids=ids, clock=clock)


def run_command(
    env: ChangesEnv, project_id: Any, command: Any, *, key: str, summary: str = "提交"
) -> Any:
    """执行一条 changes 命令；幂等键由调用方显式给出（重放测试需要同键）。"""
    return env.handler.execute(command, project_id=project_id, idempotency_key=key, summary=summary)


def head_of(env: ChangesEnv, project_id: Any) -> str | None:
    pid = ProjectId(project_id) if isinstance(project_id, str) else project_id
    branch = env.service.get_branch(pid, "main")
    assert branch is not None
    return branch.head_commit_id.value if branch.head_commit_id else None


def make_open_command(**overrides: Any) -> Any:
    """构造一条 open_change_set 命令（测试默认意图）。"""
    from hastings.application.handlers.change_commands import change_set_command
    from hastings.domain.changes.commands import ChangeScope, SeedChange, open_change_set

    kwargs: dict[str, Any] = {
        "intent": "把协助者改为幕后操纵者",
        "source": "manual",
        "base_commit_id": "cmt_base_1",
        "seed_changes": (SeedChange(artifact_id="art_char_1", field="role"),),
        "scope": ChangeScope(),
        "actor": AUTHOR,
        "at": datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC),
        "reason": "作者发起设定修改",
    }
    kwargs.update(overrides)
    return change_set_command(open_change_set, **kwargs)


def make_advance_command(target: str, **overrides: Any) -> Callable[..., Any]:
    """构造一条 advance_change_set 命令工厂（change_set_id 由测试补齐）。"""
    from hastings.application.handlers.change_commands import change_set_command
    from hastings.domain.changes.commands import advance_change_set

    def build(change_set_id: str) -> Any:
        kwargs: dict[str, Any] = {
            "change_set_id": change_set_id,
            "target": target,
            "actor": AUTHOR,
            "at": datetime(2026, 10, 15, 12, 5, 0, tzinfo=UTC),
            "reason": f"推进到 {target}",
        }
        kwargs.update(overrides)
        return change_set_command(advance_change_set, **kwargs)

    return build  # type: ignore[return-value]
