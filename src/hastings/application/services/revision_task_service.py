"""有限执行任务的持久化与用例（V03-7.3，计划 M10 第 7/8/11 条）。

一个作品同时只有**一个**进行中的整体调整任务：它绑定 V03-7.2 的内部工作版本，
持有整次任务的调用预算与修正轮次。作者要再开一个，只能先「继续」或「停止」
当前任务——这就是「不能通过新建子任务绕过预算」在存储层的落点。

存储沿用仓库辅助表（V03-0.5）：当前任务作为一条记录原子读-改-写；被替换的任务
归档到按 id 的键，`GET /revision-tasks/{id}` 仍可回读。预算与状态都是累积的，
「继续」只追加 ``call_limit``、推进 ``attempt`` 与 ``grant_version``，不清零
``calls_used``。

执行循环（谁在什么时候调用 ``consume_calls``）属于 V03-7.4；本服务只保证：
记账要么整笔成功、要么一笔不记；到上限先落等待再报错，内部进度不丢。
"""

from __future__ import annotations

from typing import Any

from hastings.application.ports.ids import IdGenerator
from hastings.application.services.commit_service import CommitService
from hastings.application.services.revision_work_service import RevisionWorkService
from hastings.domain.repository.ids import ProjectId
from hastings.domain.revision import error_codes as ec
from hastings.domain.revision.errors import CoverageViolation, violation
from hastings.domain.revision.task import (
    WAIT_AUTHOR_CHOICE,
    WAIT_BUDGET,
    WAIT_REPAIR_LIMIT,
    WAIT_UNCOVERED,
    RevisionTask,
    publication_blockers,
)

__all__ = ["RevisionTaskService", "TASK_BUCKET"]

#: 当前任务记录（key 固定为 ``current``）；同一作品同一时间只有一条。
TASK_BUCKET = "revision_task"
#: 被替换下来的任务归档（key 为 task_id），供按 id 回读。
ARCHIVE_BUCKET = "revision_task_archive"

_CURRENT_KEY = "current"
_MAIN_BRANCH = "main"


class RevisionTaskService:
    """整体调整任务的开始、记账、等待、停止与继续。"""

    def __init__(
        self,
        repo: CommitService,
        work: RevisionWorkService,
        ids: IdGenerator,
        clock: Any,
    ) -> None:
        self._repo = repo
        self._work = work
        self._ids = ids
        self._clock: Any = clock

    # -- 开始与查询 -----------------------------------------------------------
    def start(
        self,
        project_id: str,
        *,
        request_id: str,
        call_budget: int | None = None,
    ) -> dict[str, Any]:
        """开一次整体调整任务；同 ``request_id`` 重放返回原任务。

        已有进行中的任务时，**不同** request_id 一律拒绝（409）：作者必须先继续
        或停止它。这是「不新建隐含任务绕预算」的第一道门。
        """
        pid = ProjectId(project_id)
        existing = self._current_task(pid)
        if existing is not None:
            if existing.is_active and existing.request_id and existing.request_id == request_id:
                return self._view(pid, existing)  # 幂等重放：不重复开任务、不重置预算
            if existing.is_active:
                raise violation(
                    ec.REVISION_TASK_ACTIVE_CONFLICT,
                    where=f"revision_task.start({project_id})",
                    why=(
                        f"已有进行中的整体任务 {existing.task_id}"
                        f"（{existing.state}/{existing.waiting_reason or '运行中'}）"
                    ),
                    fix="继续该任务（追加预算）或先停止它；同一时间只允许一个整体任务",
                    task_id=existing.task_id,
                    state=existing.state,
                )
            # 上一轮已结束：归档后开新的（崩溃窗口可重入）。新任务是一份新授权，
            # 必须从当前 head 重新开账，不继承上一轮用旧预算读出的覆盖进度。
            self._archive(pid, existing)

        work = self._work.open(project_id, force_new=existing is not None)
        task = RevisionTask.create(
            task_id=self._ids.new_id("rtk"),
            project_id=project_id,
            work_id=str(work["work_id"]),
            base_commit_id=str(work["base_commit_id"]),
            request_id=request_id,
            call_budget=call_budget,
            at=self._now(),
        )
        self._save(pid, task)
        return self._view(pid, task)

    def current(self, project_id: str) -> dict[str, Any] | None:
        pid = ProjectId(project_id)
        task = self._current_task(pid)
        return None if task is None else self._view(pid, task)

    def get(self, project_id: str, task_id: str) -> dict[str, Any]:
        pid = ProjectId(project_id)
        return self._view(pid, self._load(pid, task_id))

    def load(self, project_id: str, task_id: str) -> RevisionTask:
        """供发布/执行侧读取领域任务；调用方不得直接改它。"""
        return self._load(ProjectId(project_id), task_id)

    def mark_published(
        self, project_id: str, task_id: str, *, operation_id: str, commit_id: str
    ) -> dict[str, Any]:
        """统一发布成功后标完成，并记下本次操作与正式版本（V03-7.4）。"""
        pid, task = self._load_with_pid(project_id, task_id)
        updated = task.mark_published(
            operation_id=operation_id, commit_id=commit_id, at=self._now()
        )
        self._save(pid, updated)
        return self._view(pid, updated)

    # -- 执行记账（执行循环在 V03-7.4 调用）-----------------------------------
    def begin(self, project_id: str, task_id: str) -> dict[str, Any]:
        pid, task = self._load_with_pid(project_id, task_id)
        updated = task.begin(at=self._now())
        self._save(pid, updated)
        return self._view(pid, updated)

    def consume_calls(
        self, project_id: str, task_id: str, *, calls: int = 1
    ) -> dict[str, Any]:
        """记 ``calls`` 次模型调用；子工作者把自身调用数合并进来，不开子预算。

        预算不够时先把任务落到 ``waiting/budget_exhausted`` 再抛错——调用方拿到
        错误时，任务记录里已经保留了已用次数与覆盖进度（计划第 8 条）。
        """
        pid, task = self._load_with_pid(project_id, task_id)
        try:
            updated = task.consume(calls, at=self._now())
        except CoverageViolation as exc:
            if exc.code == ec.REVISION_BUDGET_EXHAUSTED_CONFLICT:
                self._save(pid, task.wait(WAIT_BUDGET, exc.why, at=self._now()))
            raise
        self._save(pid, updated)
        return self._view(pid, updated)

    def start_repair(self, project_id: str, task_id: str) -> dict[str, Any]:
        """记一轮修正；两轮用尽后落等待并报错，不自动再修。"""
        pid, task = self._load_with_pid(project_id, task_id)
        try:
            updated = task.start_repair(at=self._now())
        except CoverageViolation as exc:
            if exc.code == ec.REVISION_REPAIR_LIMIT_CONFLICT:
                self._save(pid, task.wait(WAIT_REPAIR_LIMIT, exc.why, at=self._now()))
            raise
        self._save(pid, updated)
        return self._view(pid, updated)

    def mark_waiting(
        self,
        project_id: str,
        task_id: str,
        *,
        reason: str,
        detail: str = "",
    ) -> dict[str, Any]:
        """执行循环发现未覆盖/待选/中断时落等待；内部进度原样保留。"""
        if reason == WAIT_BUDGET:
            raise violation(
                ec.REVISION_TASK_INVALID_TRANSITION,
                where="revision_task.wait",
                why="预算等待由记账自动产生，不能手工指定",
                fix="让 consume_calls 在预算不足时自动落等待",
            )
        pid, task = self._load_with_pid(project_id, task_id)
        if reason == WAIT_UNCOVERED and not detail:
            detail = "还有章节未覆盖（未读/只读摘要/受阻）"
        if reason == WAIT_AUTHOR_CHOICE and not detail:
            detail = "关键选择未定：范围、保留内容或冲突处理需要作者回答"
        updated = task.wait(reason, detail, at=self._now())
        self._save(pid, updated)
        return self._view(pid, updated)

    def stop(self, project_id: str, task_id: str) -> dict[str, Any]:
        """作者停止：保留正式稳定版与内部进度；已完成的任务不回滚。"""
        pid, task = self._load_with_pid(project_id, task_id)
        updated = task.stop(at=self._now())
        self._save(pid, updated)
        return self._view(pid, updated)

    def continue_task(
        self, project_id: str, task_id: str, *, additional_calls: int
    ) -> dict[str, Any]:
        """作者显式继续：追加预算、推进 attempt、要求发布前重验。

        不做「顺手把基础更新一下」这类隐式动作：正式版若已前进，发布前检查会
        以 ``REVISION_BASE_MOVED_CONFLICT`` 报出来，交给作者选择（V03-7.4）。
        """
        pid, task = self._load_with_pid(project_id, task_id)
        updated = task.continue_with(additional_calls, at=self._now())
        self._save(pid, updated)
        return self._view(pid, updated)

    # -- 内部 ----------------------------------------------------------------
    def _load_with_pid(self, project_id: str, task_id: str) -> tuple[ProjectId, RevisionTask]:
        pid = ProjectId(project_id)
        return pid, self._load(pid, task_id)

    def _current_task(self, pid: ProjectId) -> RevisionTask | None:
        payload = self._repo.aux_read(pid, TASK_BUCKET, _CURRENT_KEY)
        return None if payload is None else RevisionTask.from_wire(payload)

    def _load(self, pid: ProjectId, task_id: str) -> RevisionTask:
        current = self._current_task(pid)
        if current is not None and current.task_id == task_id:
            return current
        archived = self._repo.aux_read(pid, ARCHIVE_BUCKET, task_id)
        if archived is not None:
            return RevisionTask.from_wire(archived)
        raise violation(
            ec.REVISION_TASK_NOT_FOUND,
            where=f"revision_task({task_id})",
            why=f"作品 {pid.value} 没有整体任务 {task_id!r}",
            fix="从当前任务或任务列表选择；任务 ID 不跨作品复用",
            task_id=task_id,
        )

    def _save(self, pid: ProjectId, task: RevisionTask) -> None:
        self._repo.aux_mutate(pid, TASK_BUCKET, _CURRENT_KEY, lambda _current: task.to_wire())

    def _archive(self, pid: ProjectId, task: RevisionTask) -> None:
        """先写归档，再清当前指针（崩溃可重入）。"""
        self._repo.aux_mutate(pid, ARCHIVE_BUCKET, task.task_id, lambda _c: task.to_wire())
        self._repo.aux_mutate(pid, TASK_BUCKET, _CURRENT_KEY, lambda _c: None)

    def _head(self, pid: ProjectId) -> str | None:
        branch = self._repo.get_branch(pid, _MAIN_BRANCH)
        return branch.head_commit_id.value if branch and branch.head_commit_id else None

    def _view(self, pid: ProjectId, task: RevisionTask) -> dict[str, Any]:
        coverage = self._work.current(pid.value)
        same_work = bool(coverage and coverage.get("work_id") == task.work_id)
        complete = bool(same_work and coverage and coverage["complete"])
        head = self._head(pid)
        pending = 1 if task.waiting_reason == WAIT_AUTHOR_CHOICE else 0
        blockers = publication_blockers(
            task,
            coverage_complete=complete,
            head_commit_id=head,
            pending_choices=pending,
        )
        view = task.to_wire()
        view.update(
            {
                "coverage": None
                if coverage is None
                else {
                    "work_id": coverage["work_id"],
                    "complete": bool(coverage["complete"]),
                    "covered_count": int(coverage["covered_count"]),
                    "total_count": int(coverage["total_count"]),
                    "uncovered": list(coverage["uncovered"]),
                },
                "publication": {
                    "ready": not blockers,
                    "blockers": [b.to_wire() for b in blockers],
                    "head_commit_id": head,
                },
            }
        )
        return view

    def _now(self) -> str:
        return str(self._clock.now().isoformat())
