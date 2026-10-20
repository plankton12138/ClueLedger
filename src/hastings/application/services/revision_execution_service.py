"""整体调整的执行循环：读章 → 记调用 → 改稿 → 暂存 → 记覆盖（V03-7.4 留白接线）。

V03-7.3 建了任务账本（预算/等待/继续）、V03-7.2 建了覆盖台账、V03-7.4 建了
暂存与统一发布，但「谁读章、谁调模型、谁记账」一直没接——预算因此是记账面
而不是门禁。本服务补的就是这一段，且只做**作者显式触发的一轮**：

1. 任务必须是进行中（queued 首次执行即开始；waiting/cancelled 要求作者先继续）；
2. 逐章按顺序执行，**先记账再调用模型**：预算不足时账本自动落
   ``waiting/budget_exhausted``，循环停在那一章之前，已完成部分原样保留；
   默认只跑还没下结论的章（要重改某章就显式给 ``chapter_ids``）；
3. 读到的是任务基础版本里的正文，登记 ``full`` 读取后才下 ``changed`` 结论；
4. 改稿复用试写候选服务（同一提示词与「现有正文」段），生成的候选只作为
   暂存来源，不自动采用、不直接写正式正文；
5. 暂存按任务基础版本的章基础 CAS，正文与账本仍由 ``publish`` 一次提交。

幂等：``request_id`` 一条执行记录；同一 id 重放返回原摘要，不重复记账、不重复
调用模型。作者继续后用**新的 request_id** 再跑一轮。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.repository.ids import CommitId, ProjectId
from hastings.domain.revision import error_codes as ec
from hastings.domain.revision.coverage import STATUS_CHECKED_UNCHANGED
from hastings.domain.revision.errors import CoverageViolation, violation
from hastings.domain.revision.task import (
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_QUEUED,
    STATE_RETRYABLE_FAILED,
    STATE_RUNNING,
    STATE_TERMINAL_FAILED,
    STATE_WAITING,
)

if TYPE_CHECKING:
    from hastings.application.services.commit_service import CommitService
    from hastings.application.services.drafting_service import DraftingService
    from hastings.application.services.revision_publish_service import RevisionPublishService
    from hastings.application.services.revision_task_service import RevisionTaskService
    from hastings.application.services.revision_work_service import RevisionWorkService

__all__ = ["RUN_BUCKET", "RevisionExecutionService"]

#: 执行轮次记录桶：键是作者给的 ``request_id``，值是那一轮的摘要。
RUN_BUCKET = "revision_runs"

_MAIN_BRANCH = "main"


class RevisionExecutionService:
    """把「任务账本 + 覆盖台账 + 暂存」串成一次可复跑的执行循环。"""

    def __init__(
        self,
        repo: CommitService,
        tasks: RevisionTaskService,
        work: RevisionWorkService,
        publish: RevisionPublishService,
        drafting: DraftingService,
        clock: Any,
    ) -> None:
        self._repo = repo
        self._tasks = tasks
        self._work = work
        self._publish = publish
        self._drafting = drafting
        self._clock = clock

    async def run_round(
        self,
        project_id: str,
        task_id: str,
        *,
        instruction: str,
        chapter_ids: list[str] | None = None,
        request_id: str,
    ) -> dict[str, Any]:
        """执行一轮：逐章改稿并暂存；预算不足时停下并如实报告停在哪一章。"""
        pid = ProjectId(project_id)
        replay = self._repo.aux_read(pid, RUN_BUCKET, request_id)
        if replay is not None:
            return {**replay, "status": "replayed"}

        task = self._tasks.load(project_id, task_id)
        if task.state == STATE_COMPLETED:
            raise violation(
                ec.REVISION_TASK_COMPLETED,
                where=f"revision_exec.run({task_id})",
                why="任务已完成并发布过，继续执行不会产生新的修改",
                fix="要再改一次就新建整体任务；已完成的结果已进入正式版",
                task_id=task_id,
            )
        if task.state in (
            STATE_WAITING,
            STATE_CANCELLED,
            STATE_RETRYABLE_FAILED,
            STATE_TERMINAL_FAILED,
        ):
            raise violation(
                ec.REVISION_TASK_NOT_RUNNING,
                where=f"revision_exec.run({task_id})",
                why=f"任务当前是 {task.state}，不能直接执行",
                fix="作者先「继续」追加预算（或停止后新建任务），再执行下一轮",
                state=task.state,
                waiting_reason=task.waiting_reason,
            )
        if task.state == STATE_QUEUED:
            self._tasks.begin(project_id, task_id)
            task = self._tasks.load(project_id, task_id)
        if task.state != STATE_RUNNING:  # pragma: no cover - 上面的分支已覆盖全部状态
            raise violation(
                ec.REVISION_TASK_NOT_RUNNING,
                where=f"revision_exec.run({task_id})",
                why=f"任务当前是 {task.state}，只有运行中才能执行",
                fix="新建任务或继续已有任务",
                state=task.state,
            )

        head = self._head(pid)
        if head is None or head != task.base_commit_id:
            raise violation(
                ec.REVISION_BASE_MOVED_CONFLICT,
                where=f"revision_exec.run({task_id})",
                why="正式版在任务开始后已前进，暂存会基于旧内容",
                fix="停止本轮并按新版本新建任务；或先发布/撤销当前改动再继续",
                task_base_commit_id=task.base_commit_id,
                head_commit_id=head,
            )

        catalog = ManuscriptCatalog.from_snapshot(
            self._repo.get_snapshot(pid, CommitId(task.base_commit_id))
        )
        coverage_now = self._work.current(project_id) or {}
        settled = {
            str(row.get("chapter_id"))
            for row in coverage_now.get("coverage", [])
            if row.get("status") in ("changed", STATUS_CHECKED_UNCHANGED)
        }
        if chapter_ids:
            targets = list(chapter_ids)
        else:
            # 默认只跑还没下结论的章：继续一轮不会把预算再花在已改过的章上
            # （要重改某章就显式给 chapter_ids）。
            targets = [e.artifact_id for e in catalog.chapters() if e.artifact_id not in settled]
        unknown = [
            chapter_id for chapter_id in targets if catalog.chapter_entry(chapter_id) is None
        ]
        if unknown:
            raise violation(
                ec.REVISION_STAGE_UNKNOWN_CHAPTER,
                where=f"revision_exec.run({task_id})",
                why=f"章 {unknown} 不在任务基础版本里",
                fix="只对本轮基础版本已有的章执行；新章先随发布进入正文再纳入",
                chapter_ids=unknown,
            )

        done: list[dict[str, Any]] = []
        stopped: dict[str, Any] | None = None
        for chapter_id in targets:
            task = self._tasks.load(project_id, task_id)
            if task.state != STATE_RUNNING:
                # 上一章执行期间任务被停止/等待：立即停手，不继续花预算。
                stopped = {"reason": task.waiting_reason or task.state, "chapter_id": chapter_id}
                break
            try:
                usage = self._tasks.consume_calls(project_id, task_id, calls=1)
            except CoverageViolation as exc:
                if exc.code != ec.REVISION_BUDGET_EXHAUSTED_CONFLICT:
                    raise
                # consume_calls 已把任务落到 waiting/budget_exhausted；这一章没调用模型。
                stopped = {
                    "reason": "budget_exhausted",
                    "chapter_id": chapter_id,
                    "why": exc.why,
                }
                break

            self._work.record_read(project_id, chapter_id, kind="full")
            candidate = await self._drafting.create(
                project_id,
                target=chapter_id,
                instruction=instruction,
                idempotency_key=f"{request_id}:{chapter_id}",
            )
            text = str(candidate.get("text") or "")
            self._publish.stage_chapter(
                project_id,
                task_id,
                chapter_id=chapter_id,
                text=text,
                base_revision_id=catalog.chapter_base(chapter_id),
            )
            self._work.mark_changed(project_id, chapter_id)
            done.append(
                {
                    "chapter_id": chapter_id,
                    "candidate_id": candidate.get("candidate_id"),
                    "chars": len(text),
                    "calls_used": (usage.get("budget") or {}).get("calls_used"),
                }
            )

        summary: dict[str, Any] = {
            "status": "stopped" if stopped else ("executed" if done else "noop"),
            "task_id": task_id,
            "request_id": request_id,
            "chapters": done,
            "stopped": stopped,
            "task": self._tasks.get(project_id, task_id),
        }
        self._repo.aux_mutate(pid, RUN_BUCKET, request_id, lambda _current: dict(summary))
        return summary

    def _head(self, pid: ProjectId) -> str | None:
        branch = self._repo.get_branch(pid, _MAIN_BRANCH)
        return branch.head_commit_id.value if branch and branch.head_commit_id else None
