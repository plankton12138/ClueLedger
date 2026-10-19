"""有限执行的领域约束（V03-7.3）：预算、修正轮次、等待与继续。

验收口径（计划 M10 第 7/8/11 条）：子工作者与修正计入同一预算；到上限不静默
继续、也不丢已用进度；修正最多两轮；继续只追加预算、不清零消耗。
"""

from __future__ import annotations

import pytest

from hastings.domain.revision.errors import CoverageViolation
from hastings.domain.revision.task import (
    DEFAULT_CALL_BUDGET,
    MAX_REPAIR_ROUNDS,
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_QUEUED,
    STATE_RUNNING,
    STATE_WAITING,
    WAIT_AUTHOR_CHOICE,
    WAIT_BUDGET,
    RevisionBudget,
    RevisionTask,
    publication_blockers,
)


def _task() -> RevisionTask:
    base = RevisionTask.create(
        task_id="rtk_1",
        project_id="p1",
        work_id="rwk_1",
        base_commit_id="c1",
        call_budget=4,
    )
    return base.begin()


def test_budget_counts_subworker_calls_as_one_pot() -> None:
    budget = RevisionBudget(call_limit=4)
    budget = budget.consume(3)  # 一个子工作者一次用了 3 次
    assert budget.calls_used == 3
    assert budget.remaining == 1
    # 只剩 1 次时再要 2 次：整笔拒绝，不能只记一半。
    with pytest.raises(CoverageViolation) as exc:
        budget.consume(2)
    assert exc.value.code == "REVISION_BUDGET_EXHAUSTED_CONFLICT"
    assert budget.calls_used == 3


def test_budget_exhaustion_is_exact_and_does_not_wrap() -> None:
    budget = RevisionBudget(call_limit=2).consume(2)
    assert budget.exhausted
    with pytest.raises(CoverageViolation):
        budget.consume()
    assert budget.calls_used == 2


def test_repair_rounds_cap_at_two() -> None:
    budget = RevisionBudget(call_limit=12)
    for expected in (1, 2):
        budget = budget.with_repair_round()
        assert budget.repair_rounds == expected
    assert not budget.can_repair
    with pytest.raises(CoverageViolation) as exc:
        budget.with_repair_round()
    assert exc.value.code == "REVISION_REPAIR_LIMIT_CONFLICT"
    assert budget.repair_rounds == MAX_REPAIR_ROUNDS


def test_start_budget_can_shrink_but_not_grow() -> None:
    shrunk = RevisionTask.create(
        task_id="rtk_2",
        project_id="p1",
        work_id="rwk_1",
        base_commit_id="c1",
        call_budget=3,
    )
    assert shrunk.budget.call_limit == 3
    assert shrunk.state == STATE_QUEUED
    with pytest.raises(CoverageViolation) as exc:
        RevisionTask.create(
            task_id="rtk_3",
            project_id="p1",
            work_id="rwk_1",
            base_commit_id="c1",
            call_budget=DEFAULT_CALL_BUDGET + 1,
        )
    assert exc.value.code == "REVISION_BUDGET_INVALID"
    with pytest.raises(CoverageViolation):
        RevisionBudget(call_limit=0)


def test_continue_adds_budget_without_clearing_usage() -> None:
    task = _task()
    task = task.consume(3)
    paused = task.wait(WAIT_BUDGET, "只剩 1 次")
    assert paused.state == STATE_WAITING
    resumed = paused.continue_with(4)
    assert resumed.state == STATE_RUNNING
    assert resumed.attempt == 2
    assert resumed.grant_version == 2
    assert resumed.budget.calls_used == 3  # 旧消耗保留
    assert resumed.budget.call_limit == 8
    assert resumed.budget.remaining == 5
    assert resumed.requires_revalidation is True


def test_stop_keeps_progress_and_can_be_continued() -> None:
    task = _task().consume(2)
    stopped = task.stop()
    assert stopped.state == STATE_CANCELLED
    assert stopped.budget.calls_used == 2
    resumed = stopped.continue_with(2)
    assert resumed.state == STATE_RUNNING
    assert resumed.budget.calls_used == 2
    assert resumed.attempt == 2


def test_completed_and_running_cannot_continue() -> None:
    done = _task().complete()
    assert done.state == STATE_COMPLETED
    with pytest.raises(CoverageViolation) as exc:
        done.continue_with(1)
    assert exc.value.code == "REVISION_TASK_COMPLETED"
    with pytest.raises(CoverageViolation) as running:
        _task().continue_with(1)
    assert running.value.code == "REVISION_CONTINUE_NOT_ALLOWED"


def test_wait_requires_known_reason_and_is_idempotent() -> None:
    task = _task()
    waiting = task.wait(WAIT_AUTHOR_CHOICE, "选保留哪些线索")
    assert waiting.waiting_reason == WAIT_AUTHOR_CHOICE
    again = waiting.wait(WAIT_BUDGET, "后到的原因")
    assert again.waiting_reason == WAIT_AUTHOR_CHOICE  # 不覆盖先到的原因
    with pytest.raises(CoverageViolation):
        task.wait("unknown_reason")


def test_publication_blockers_report_each_missing_condition() -> None:
    task = _task()
    blockers = publication_blockers(
        task,
        coverage_complete=False,
        head_commit_id="c2",
        pending_choices=1,
    )
    codes = [b.code for b in blockers]
    assert codes == [
        "REVISION_BASE_MOVED_CONFLICT",
        "REVISION_COVERAGE_INCOMPLETE_CONFLICT",
        "REVISION_CHOICE_PENDING_CONFLICT",
    ]
    assert all(b.why and b.fix for b in blockers)


def test_publication_ready_when_conditions_hold() -> None:
    task = _task()
    assert publication_blockers(task, coverage_complete=True, head_commit_id="c1") == ()
    exhausted = task.consume(4)
    blockers = publication_blockers(exhausted, coverage_complete=True, head_commit_id="c1")
    assert [b.code for b in blockers] == ["REVISION_BUDGET_EXHAUSTED_CONFLICT"]
