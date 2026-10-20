"""内部工作版本的统一发布（V03-7.4，计划 M10 第 9/10 条）。

执行期间的所有改动先落在暂存区（``domain/revision/staging.py``）；发布时把它们
放进**一个 Commit**：正文命令与账本变化同事务提交，任一对象的基础已变则整体不
发布——稳定版不会出现「正文更新了、账本没更新」的半套状态。

发布门禁按顺序检查：

1. 任务必须是运行中（等待/停止/已完成都不发布）；
2. **活动副本**：存在未结束的手工修改副本就拒绝（先完成或放弃）；
3. **基础重验**：正式 head 已离开任务基础 → 拒绝，要求重新核对范围；
4. **覆盖**：未覆盖章节必须都已暂存（暂存内容就是覆盖证据），否则先读完；
5. **预算与待定选择**：到上限或关键选择未定 → 等待作者。

暂存章按 ``base_revision_id`` 与正式版逐章 CAS；账本修订按 ``expected_revision``
做 revision CAS。发布成功后：暂存章记为 ``changed``、任务标完成并记录
``operation_id``/``commit_id``、清空暂存区。发布失败时暂存区原样保留，作者修正后
可用同一或新的操作 ID 重试。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hastings.application.errors import ViolationError
from hastings.application.handlers.manuscript_commands import manuscript_command
from hastings.application.ports.ids import IdGenerator
from hastings.application.services.commit_service import CommitService
from hastings.application.services.edit_session_service import EditSessionService
from hastings.application.services.publish_manuscript_change import PublishManuscriptChange
from hastings.application.services.revision_task_service import RevisionTaskService
from hastings.application.services.revision_work_service import RevisionWorkService
from hastings.domain.manuscript import commands as manuscript_commands
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.repository.ids import CommitId, ProjectId
from hastings.domain.repository.requests import ArtifactChange
from hastings.domain.repository.serialization import content_hash
from hastings.domain.revision import error_codes as ec
from hastings.domain.revision.errors import violation
from hastings.domain.revision.staging import (
    RevisionStaging,
    StagedChapter,
    StagedLedgerChange,
)
from hastings.domain.revision.task import (
    STATE_COMPLETED,
    STATE_QUEUED,
    STATE_RUNNING,
    WAIT_AUTHOR_CHOICE,
    RevisionTask,
    publication_blockers,
)
from hastings.domain.story import error_codes as story_ec
from hastings.domain.story.canon import entries as ledger_entries
from hastings.domain.story.catalog import StoryCatalog
from hastings.domain.story.changes import DomainChange
from hastings.domain.story.errors import StoryViolation

__all__ = ["RevisionPublishService", "STAGE_BUCKET"]

#: 暂存区（key 为 task_id）；一次任务一份，发布成功即清空。
STAGE_BUCKET = "revision_stage"

_MAIN_BRANCH = "main"


class RevisionPublishService:
    """暂存正文/账本变化，并按统一发布管线一次提交。"""

    def __init__(
        self,
        repo: CommitService,
        tasks: RevisionTaskService,
        work: RevisionWorkService,
        publisher: PublishManuscriptChange,
        edits: EditSessionService,
        ids: IdGenerator,
        clock: Any,
    ) -> None:
        self._repo = repo
        self._tasks = tasks
        self._work = work
        self._publisher = publisher
        self._edits = edits
        self._ids = ids
        self._clock: Any = clock

    # -- 暂存 ----------------------------------------------------------------
    def stage_chapter(
        self,
        project_id: str,
        task_id: str,
        *,
        chapter_id: str,
        text: str,
        base_revision_id: str,
    ) -> dict[str, Any]:
        """暂存一章全文；章必须在本轮工作版本里，且基础版本与正式版一致。"""
        pid, task = self._task_for_write(project_id, task_id)
        coverage = self._require_work(task, project_id)
        if chapter_id not in {row["chapter_id"] for row in coverage["context"]["chapters"]}:
            raise violation(
                ec.REVISION_STAGE_UNKNOWN_CHAPTER,
                where=f"revision_stage.chapter({chapter_id})",
                why=f"章 {chapter_id!r} 不在本轮工作版本声明的章节集合内",
                fix="只暂存本轮要覆盖的章；新章先随发布进入正文再纳入清单",
                chapter_id=chapter_id,
                work_id=task.work_id,
            )
        expected = self._chapter_base(pid, task, chapter_id)
        if base_revision_id != expected:
            raise violation(
                ec.REVISION_STAGE_CHAPTER_CONFLICT,
                where=f"revision_stage.chapter({chapter_id})",
                why="暂存依据的章基础与正式版不一致",
                fix="重新读取该章当前正文（含章基础版本）后再暂存；不要用旧内容覆盖新版本",
                chapter_id=chapter_id,
                expected_base_revision_id=expected,
                given_base_revision_id=base_revision_id,
            )
        staged = StagedChapter(
            chapter_id=chapter_id,
            base_revision_id=base_revision_id,
            text=text,
            updated_at=self._now(),
        )
        self._mutate_staging(pid, task_id, lambda current: current.with_chapter(staged))
        return self.staged(project_id, task_id)

    def unstage_chapter(self, project_id: str, task_id: str, chapter_id: str) -> dict[str, Any]:
        pid, _task = self._task_for_write(project_id, task_id)
        self._mutate_staging(
            pid, task_id, lambda current: current.without_chapter(chapter_id, at=self._now())
        )
        return self.staged(project_id, task_id)

    def stage_ledger(
        self,
        project_id: str,
        task_id: str,
        *,
        fields: dict[str, Any],
        stage_id: str | None = None,
        entry_id: str | None = None,
        kind: str = "",
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """暂存一条账本新建/修订；暂存时就按基础目录校验字段与引用。

        ``stage_id`` 缺省：修订用 ``entry_id``；新建用 ``kind + 内容指纹``——
        相同内容重复暂存是覆盖，不同内容才是另一条新条目。
        """
        pid, task = self._task_for_write(project_id, task_id)
        story = self._story_catalog(pid, task)
        self._run_ledger_change(
            story,
            entry_id=entry_id,
            kind=kind,
            fields=fields,
            expected_revision=expected_revision,
        )
        resolved_stage_id = stage_id or entry_id or f"new:{kind}:{content_hash([kind, fields])}"
        staged = StagedLedgerChange(
            stage_id=resolved_stage_id,
            fields=dict(fields),
            entry_id=entry_id,
            kind=kind,
            expected_revision=expected_revision,
            updated_at=self._now(),
        )
        self._mutate_staging(pid, task_id, lambda current: current.with_ledger(staged))
        return self.staged(project_id, task_id)

    def unstage_ledger(self, project_id: str, task_id: str, stage_id: str) -> dict[str, Any]:
        pid, _task = self._task_for_write(project_id, task_id)
        self._mutate_staging(
            pid, task_id, lambda current: current.without_ledger(stage_id, at=self._now())
        )
        return self.staged(project_id, task_id)

    def staged(self, project_id: str, task_id: str) -> dict[str, Any]:
        """回读暂存区：章节全文 + 待发布账本变化 + 计数。"""
        pid = ProjectId(project_id)
        self._tasks.load(project_id, task_id)  # 归属校验：不跨作品读暂存
        staging = self._load_staging(pid, task_id)
        return {
            "task_id": task_id,
            "chapters": [c.to_wire() for c in staging.chapters],
            "ledger": [item.to_wire() for item in staging.ledger],
            "chapter_count": len(staging.chapters),
            "ledger_count": len(staging.ledger),
            "empty": staging.is_empty,
            "updated_at": staging.updated_at,
        }

    # -- 发布 ----------------------------------------------------------------
    def publish(self, project_id: str, task_id: str, *, operation_id: str) -> dict[str, Any]:
        """把暂存内容一次发布；门禁不通过时说明原因，不产生半套版本。"""
        if not operation_id:
            raise violation(
                ec.REVISION_STAGE_INVALID,
                where="revision.publish",
                why="发布缺少 operation_id",
                fix="给出稳定的 op_ 前缀操作 ID，便于回执重放与后续撤销",
            )
        pid = ProjectId(project_id)
        task = self._tasks.load(project_id, task_id)
        if task.state == STATE_COMPLETED:
            if task.published_operation_id == operation_id:
                return {
                    "status": "replayed",
                    "operation_id": operation_id,
                    "commit_id": task.published_commit_id,
                    "changed_chapters": [],
                    "task": self._tasks.get(project_id, task_id),
                }
            raise violation(
                ec.REVISION_TASK_COMPLETED,
                where=f"revision.publish({task_id})",
                why="任务已完成并发布过；继续不会产生新的修改",
                fix="要再改一次就新建整体任务；已完成的结果已进入正式版",
                task_id=task_id,
                published_operation_id=task.published_operation_id,
            )
        if task.state != STATE_RUNNING:
            # 空暂存优先按「没有内容可发布」报，避免把「还没开始」误报成状态冲突。
            if self._load_staging(pid, task_id).is_empty:
                raise violation(
                    ec.REVISION_PUBLISH_EMPTY,
                    where=f"revision.publish({task_id})",
                    why="暂存区为空，没有可发布的正文或账本变化",
                    fix="先暂存要发布的章节或账本变化；空发布不创建版本",
                    task_id=task_id,
                )
            raise violation(
                ec.REVISION_TASK_NOT_RUNNING,
                where=f"revision.publish({task_id})",
                why=f"任务当前是 {task.state}，只有运行中才能发布",
                fix="等待/停止的任务先由作者继续；发布前会重新核对基础与覆盖",
                state=task.state,
            )

        staging = self._load_staging(pid, task_id)
        if staging.is_empty:
            raise violation(
                ec.REVISION_PUBLISH_EMPTY,
                where=f"revision.publish({task_id})",
                why="暂存区为空，没有可发布的正文或账本变化",
                fix="先暂存要发布的章节或账本变化；空发布不创建版本",
                task_id=task_id,
            )

        session = self._edits.current(project_id)
        if session is not None:
            raise violation(
                ec.REVISION_EDIT_SESSION_ACTIVE_CONFLICT,
                where=f"revision.publish({task_id})",
                why="存在未结束的手工修改副本，正式版可能正要前进",
                fix="先完成或放弃该副本，再发布整体调整；两者不能同时改正式版",
                task_id=task_id,
                session_id=session["session"]["session_id"],
            )

        coverage = self._require_work(task, project_id)
        head = self._head(pid)
        staged_ids = {c.chapter_id for c in staging.chapters}
        uncovered = {row["chapter_id"] for row in coverage["uncovered"]}
        effective_complete = bool(coverage["complete"]) or uncovered <= staged_ids
        blockers = publication_blockers(
            task,
            coverage_complete=effective_complete,
            head_commit_id=head,
            pending_choices=1 if task.waiting_reason == WAIT_AUTHOR_CHOICE else 0,
        )
        if blockers:
            first = blockers[0]
            raise violation(
                first.code,
                where=f"revision.publish({task_id})",
                why=first.why,
                fix=first.fix,
                blockers=[b.to_wire() for b in blockers],
            )

        base = CommitId(task.base_commit_id)
        catalog = ManuscriptCatalog.from_snapshot(self._repo.get_snapshot(pid, base))
        commands = []
        for staged in staging.chapters:
            if catalog.chapter_entry(staged.chapter_id) is None:
                raise violation(
                    ec.REVISION_STAGE_UNKNOWN_CHAPTER,
                    where=f"revision.publish({staged.chapter_id})",
                    why=f"章 {staged.chapter_id!r} 在任务基础版本里不存在",
                    fix="重新暂存本轮范围内的章；新章需要先进入正式正文",
                    chapter_id=staged.chapter_id,
                )
            if catalog.chapter_base(staged.chapter_id) != staged.base_revision_id:
                raise violation(
                    ec.REVISION_STAGE_CHAPTER_CONFLICT,
                    where=f"revision.publish({staged.chapter_id})",
                    why="暂存之后该章基础已变化，不能用旧内容覆盖",
                    fix="重新读取该章并重新暂存，再发布",
                    chapter_id=staged.chapter_id,
                )
            commands.append(
                manuscript_command(
                    manuscript_commands.save_full_text,
                    chapter_id=staged.chapter_id,
                    text=staged.text,
                    expected_chapter_base=staged.base_revision_id,
                )
            )

        story = self._story_catalog(pid, task)
        extra_changes: list[ArtifactChange] = []
        for item in staging.ledger:
            change = self._run_ledger_change(
                story,
                entry_id=item.entry_id,
                kind=item.kind,
                fields=item.fields,
                expected_revision=item.expected_revision,
            )
            extra_changes.extend(change.artifact_changes)

        if not commands and not extra_changes:
            raise violation(
                ec.REVISION_PUBLISH_EMPTY,
                where=f"revision.publish({task_id})",
                why="暂存内容没有产生实际变化（账本条目内容未变）",
                fix="修改暂存内容或取消暂存；空发布不创建版本",
                task_id=task_id,
            )

        result = self._publisher.publish_commands(
            project_id=pid,
            commands=tuple(commands),
            operation_id=operation_id,
            kind="revision.publish",
            summary=(
                f"整体调整发布：{len(commands)} 章、{len(extra_changes)} 条账本变化"
            ),
            refs={"task_id": task_id, "work_id": task.work_id},
            base_commit_id=base,
            extra_changes=tuple(extra_changes),
        )

        for staged in staging.chapters:
            self._work.mark_changed(project_id, staged.chapter_id)
        view = self._tasks.mark_published(
            project_id,
            task_id,
            operation_id=result.operation_id,
            commit_id=result.commit_id or "",
        )
        self._clear_staging(pid, task_id)
        return {
            "status": result.status,
            "operation_id": result.operation_id,
            "commit_id": result.commit_id,
            "warnings": list(result.warnings),
            "changed_chapters": [c.chapter_id for c in staging.chapters],
            "task": view,
        }

    # -- 内部 ----------------------------------------------------------------
    def _task_for_write(self, project_id: str, task_id: str) -> tuple[ProjectId, RevisionTask]:
        """暂存只发生在运行中的任务上；queued 首次暂存即开始执行。"""
        pid = ProjectId(project_id)
        task = self._tasks.load(project_id, task_id)
        if task.state == STATE_QUEUED:
            self._tasks.begin(project_id, task_id)
            task = self._tasks.load(project_id, task_id)
        if task.state != STATE_RUNNING:
            raise violation(
                ec.REVISION_TASK_NOT_RUNNING,
                where=f"revision_stage({task_id})",
                why=f"任务当前是 {task.state}，只有运行中才能暂存改动",
                fix="等待/停止的任务先由作者继续；已完成的任务不能继续暂存",
                state=task.state,
                waiting_reason=task.waiting_reason,
            )
        return pid, task

    def _require_work(self, task: RevisionTask, project_id: str) -> dict[str, Any]:
        coverage = self._work.current(project_id)
        if coverage is None or coverage.get("work_id") != task.work_id:
            raise violation(
                ec.REVISION_WORK_NOT_FOUND,
                where=f"revision.publish({task.task_id})",
                why="任务绑定的工作版本已不是当前版本",
                fix="新建一轮整体任务；旧任务的覆盖记录不再对应当前正式基础",
                task_id=task.task_id,
                work_id=task.work_id,
            )
        return coverage

    def _chapter_base(self, pid: ProjectId, task: RevisionTask, chapter_id: str) -> str:
        catalog = ManuscriptCatalog.from_snapshot(
            self._repo.get_snapshot(pid, CommitId(task.base_commit_id))
        )
        entry = catalog.chapter_entry(chapter_id)
        if entry is None:
            raise violation(
                ec.REVISION_STAGE_UNKNOWN_CHAPTER,
                where=f"revision_stage.chapter({chapter_id})",
                why=f"章 {chapter_id!r} 在任务基础版本里不存在",
                fix="只暂存任务基础版本里已有的章",
                chapter_id=chapter_id,
            )
        return catalog.chapter_base(chapter_id)

    def _story_catalog(self, pid: ProjectId, task: RevisionTask) -> StoryCatalog:
        snapshot = self._repo.get_snapshot(pid, CommitId(task.base_commit_id))
        return StoryCatalog.from_snapshot(snapshot)

    def _run_ledger_change(
        self,
        catalog: StoryCatalog,
        *,
        entry_id: str | None,
        kind: str,
        fields: dict[str, Any],
        expected_revision: str | None,
    ) -> DomainChange:
        """跑领域账本命令（纯函数）；违规转应用信封，冲突归 409 类。"""
        try:
            if entry_id is None:
                return ledger_entries.create_entry(
                    catalog,
                    lambda _kind: self._ids.new_id("art"),
                    kind=kind,
                    fields=fields,
                )
            return ledger_entries.revise_entry(
                catalog,
                lambda _kind: self._ids.new_id("art"),
                entry_id=entry_id,
                fields=fields,
                expected_revision=expected_revision,
            )
        except StoryViolation as exc:
            code = (
                "LEDGER_ENTRY_CONFLICT"
                if exc.code == story_ec.STORY_STALE_REVISION
                else exc.code
            )
            raise ViolationError(
                code=code, where=exc.where, why=exc.why, fix=exc.fix, details=exc.details
            ) from exc

    def _load_staging(self, pid: ProjectId, task_id: str) -> RevisionStaging:
        payload = self._repo.aux_read(pid, STAGE_BUCKET, task_id)
        if payload is None:
            return RevisionStaging.empty(task_id=task_id, project_id=pid.value)
        return RevisionStaging.from_wire(payload)

    def _mutate_staging(
        self,
        pid: ProjectId,
        task_id: str,
        mutate: Callable[[RevisionStaging], RevisionStaging],
    ) -> RevisionStaging:
        def apply(current: dict[str, Any] | None) -> dict[str, Any]:
            staging = (
                RevisionStaging.empty(task_id=task_id, project_id=pid.value)
                if current is None
                else RevisionStaging.from_wire(current)
            )
            return mutate(staging).to_wire()

        saved = self._repo.aux_mutate(pid, STAGE_BUCKET, task_id, apply)
        assert saved is not None
        return RevisionStaging.from_wire(saved)

    def _clear_staging(self, pid: ProjectId, task_id: str) -> None:
        self._repo.aux_mutate(pid, STAGE_BUCKET, task_id, lambda _current: None)

    def _head(self, pid: ProjectId) -> str | None:
        branch = self._repo.get_branch(pid, _MAIN_BRANCH)
        return branch.head_commit_id.value if branch and branch.head_commit_id else None

    def _now(self) -> str:
        return str(self._clock.now().isoformat())
