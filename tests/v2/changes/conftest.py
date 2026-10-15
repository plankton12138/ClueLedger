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
from hastings.domain.repository.ids import Actor, CommitId, ProjectId
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


# ---------------------------------------------------------------------------
# T15.5 批准流助手：报告/计划/凭证的读取与落库
# ---------------------------------------------------------------------------
def _pid(project_id: Any) -> ProjectId:
    """str 与 ProjectId 双收（状态机测试用 str，工作流测试用 ProjectId）。"""
    return ProjectId(project_id) if isinstance(project_id, str) else project_id


def catalog_of(env: ChangesEnv, project_id: Any) -> Any:
    """当前 head 的变更目录（只读投影）。"""
    from hastings.domain.changes.catalog import ChangesCatalog

    head = head_of(env, project_id)
    assert head is not None
    return ChangesCatalog.from_snapshot(env.service.get_snapshot(_pid(project_id), CommitId(head)))


def change_set_of(env: ChangesEnv, project_id: Any, change_set_id: str) -> Any:
    model = catalog_of(env, project_id).require_change_set(change_set_id, where="test").model()
    return model


def _model_of(env: ChangesEnv, project_id: Any, kind: str, change_set_id: str) -> Any | None:
    for entry in catalog_of(env, project_id).by_kind(kind):
        model = entry.model()
        if getattr(model, "change_set_id", None) == change_set_id:
            return model
    return None


def report_of(env: ChangesEnv, project_id: Any, change_set_id: str) -> Any | None:
    from hastings.domain.changes.kinds import KIND_IMPACT_REPORT

    return _model_of(env, project_id, KIND_IMPACT_REPORT, change_set_id)


def plan_of(env: ChangesEnv, project_id: Any, change_set_id: str) -> Any | None:
    from hastings.domain.changes.kinds import KIND_REVISION_PLAN

    return _model_of(env, project_id, KIND_REVISION_PLAN, change_set_id)


def receipt_of(env: ChangesEnv, project_id: Any, change_set_id: str) -> Any | None:
    from hastings.domain.changes.kinds import KIND_APPROVAL_RECEIPT

    return _model_of(env, project_id, KIND_APPROVAL_RECEIPT, change_set_id)


def record_empty_report(
    env: ChangesEnv, project_id: Any, change_set_id: str, *, key: str, reason: str = "登记影响报告"
) -> Any:
    """在 analyzing 状态登记空报告（同提交自动转入 analyzed）。"""
    from hastings.application.handlers.change_commands import change_set_command
    from hastings.domain.changes.commands import record_impact_analysis
    from hastings.domain.changes.impact import ImpactReport

    cs = change_set_of(env, project_id, change_set_id)
    command = change_set_command(
        record_impact_analysis,
        change_set_id=change_set_id,
        base_commit_id=cs.base_commit_id,
        report=ImpactReport(
            report_id="", change_set_id=change_set_id, base_commit_id=cs.base_commit_id
        ),
        actor=AUTHOR,
        at=env.clock.now(),
        reason=reason,
    )
    return env.handler.execute(
        command, project_id=_pid(project_id), idempotency_key=key, summary="登记影响报告"
    )


def record_projection_plan(
    env: ChangesEnv,
    project_id: Any,
    change_set_id: str,
    *,
    key: str,
    kind_of: Any = None,
) -> Any:
    """从当前报告投影四层计划并落库（覆盖全部 open 项）。"""
    from hastings.application.handlers.change_commands import change_set_command
    from hastings.domain.changes.commands import record_revision_plan
    from hastings.domain.changes.planning import build_revision_plan

    report = report_of(env, project_id, change_set_id)
    assert report is not None, "登记计划前必须先有影响报告"
    plan = build_revision_plan(report, kind_of=kind_of) if kind_of else build_revision_plan(report)
    command = change_set_command(
        record_revision_plan,
        change_set_id=change_set_id,
        plan=plan,
        actor=AUTHOR,
        at=env.clock.now(),
        reason="生成修订计划",
    )
    return env.handler.execute(
        command, project_id=_pid(project_id), idempotency_key=key, summary="生成修订计划"
    )


def issue_receipt(
    env: ChangesEnv,
    project_id: Any,
    change_set_id: str,
    *,
    key: str,
    actor: Any = AUTHOR,
    reason: str = "作者确认修订计划",
) -> Any:
    """签发批准凭证；返回 result（凭证 ID 在 artifact_changes[0]）。"""
    from hastings.application.handlers.change_commands import change_set_command
    from hastings.domain.changes.commands import issue_approval_receipt

    command = change_set_command(
        issue_approval_receipt,
        change_set_id=change_set_id,
        actor=actor,
        at=env.clock.now(),
        reason=reason,
    )
    return env.handler.execute(
        command, project_id=_pid(project_id), idempotency_key=key, summary="签发批准凭证"
    )


def revoke_receipt(
    env: ChangesEnv, project_id: Any, change_set_id: str, *, key: str, reason: str = "作者反悔"
) -> Any:
    from hastings.application.handlers.change_commands import change_set_command
    from hastings.domain.changes.commands import revoke_approval_receipt

    command = change_set_command(
        revoke_approval_receipt,
        change_set_id=change_set_id,
        actor=AUTHOR,
        at=env.clock.now(),
        reason=reason,
    )
    return env.handler.execute(
        command, project_id=_pid(project_id), idempotency_key=key, summary="撤销批准凭证"
    )
