"""整体调整工作版本与有限执行任务的 REST 出口（V03-7.2/7.3）。

一次整体调整任务在正式发布前有个**内部工作版本**：绑定正式基础、声明要覆盖的
章节集合，逐章记录「读到哪、全文还是摘要、是否确认无需修改、是否已改、是否受阻」。
这组端点就是它的读写面，与前端契约逐字对应：

- ``POST /projects/{id}/revision-work``：打开（或继续）当前工作版本；幂等，已有未结束
  的一轮则原样返回，不悄悄重建基础；
- ``GET  /projects/{id}/revision-work``：回读覆盖台账与**全局上下文**——逐章实际读取
  区间与未覆盖清单都可查（本次任务的验收口径）；
- ``POST /projects/{id}/revision-work/reads``：登记一次真实读取（``full`` 记整章/区间，
  ``summary`` 记目录/摘要）；
- ``POST /projects/{id}/revision-work/coverage/{chapter_id}``：对该章下结论——确认无需
  修改、记为已改、或记为受阻（带原因）。

有限执行（V03-7.3）在同一个工作版本上再加一层任务账本：

- ``POST /projects/{id}/revision-tasks``：开任务（默认 12 次调用、可缩小；同作品同时
  只允许一个进行中任务）；
- ``GET  /projects/{id}/revision-tasks/current``、``GET .../{task_id}``：回读预算、等待
  原因、覆盖摘要与发布前阻断项；
- ``POST .../{task_id}/stop``、``POST .../{task_id}/continue``：停止保留进度；继续追加
  预算、推进 attempt，并要求发布前重验。

统一发布（V03-7.4）先把改动暂存，再一次提交：

- ``POST .../{task_id}/staged-chapters``、``DELETE .../staged-chapters/{chapter_id}``：
  暂存/取消一章全文（不写正式正文）；
- ``POST .../{task_id}/staged-ledger``、``DELETE .../staged-ledger/{stage_id}``：
  暂存/取消一条账本新建或修订；
- ``GET  .../{task_id}/staged``：回读暂存区；
- ``POST .../{task_id}/publish``：正文与账本进同一个 Commit；活动副本、基础前进、
  未覆盖、预算耗尽都会整体拒绝。

硬约束不在这层：只读摘要不得记 ``checked_unchanged`` 由领域层判定，预算与修正轮次
同样在领域层判定，端点只转发结果。领域违规（``CoverageViolation``）经应用边界的
统一 handler 转信封，这里不重复翻译。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from hastings.application.errors import ViolationError
from hastings.domain.revision import error_codes as ec

if TYPE_CHECKING:
    from hastings.server.v2 import V2Runtime

__all__ = ["create_revision_router"]


class RecordReadRequest(BaseModel):
    """登记一次读取：``kind`` 取 ``summary``（目录/摘要）或 ``full``（整章正文）。"""

    chapter_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    start: int | None = None
    end: int | None = None


class CoverageActionRequest(BaseModel):
    """给一章下覆盖结论：``confirm_unchanged`` / ``changed`` / ``blocked``。"""

    action: str = Field(min_length=1)
    reason: str = ""


class StartRevisionTaskRequest(BaseModel):
    """开一次整体任务：``request_id`` 用于重放；``call_budget`` 只能缩小默认值。"""

    request_id: str = Field(min_length=1)
    call_budget: int | None = None


class ContinueRevisionTaskRequest(BaseModel):
    """作者继续：追加预算；已用次数与修正轮次不清零。"""

    additional_calls: int


class StageChapterRequest(BaseModel):
    """暂存一章全文：``base_revision_id`` 是读取时该章的章基础版本。"""

    chapter_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    base_revision_id: str = Field(min_length=1)


class StageLedgerRequest(BaseModel):
    """暂存一条账本新建/修订；修订必须带条目当前 ``revision_id``。"""

    fields: dict[str, Any]
    stage_id: str | None = None
    entry_id: str | None = None
    kind: str = ""
    expected_revision: str | None = None


class PublishRevisionRequest(BaseModel):
    """发布：``operation_id`` 用于回执重放与撤销找回。"""

    operation_id: str = Field(min_length=1)


def create_revision_router(runtime: V2Runtime) -> APIRouter:
    router = APIRouter(prefix="/projects/{project_id}", tags=["revision"])

    @router.post("/revision-work")
    def open_revision_work(project_id: str) -> dict[str, Any]:
        """打开或继续当前整体调整工作版本（幂等）。"""
        return runtime.revision_work.open(project_id)

    @router.get("/revision-work")
    def get_revision_work(project_id: str) -> dict[str, Any]:
        """回读覆盖台账与全局上下文：逐章读取与未覆盖都可查（V03-7.2 验收）。"""
        current = runtime.revision_work.current(project_id)
        if current is None:
            # 合法空结果：没有进行中的工作版本不是错误，但要让前端知道「还没打开」。
            return {"work_id": None, "opened": False}
        return current

    @router.post("/revision-work/reads")
    def record_read(project_id: str, req: RecordReadRequest) -> dict[str, Any]:
        """登记一次真实读取；摘要读取不会把该章记为「无需修改」。"""
        return runtime.revision_work.record_read(
            project_id, req.chapter_id, kind=req.kind, start=req.start, end=req.end
        )

    @router.post("/revision-work/coverage/{chapter_id}")
    def apply_coverage(
        project_id: str, chapter_id: str, req: CoverageActionRequest
    ) -> dict[str, Any]:
        """给一章下覆盖结论；「确认无需修改」要求先做过全文检查。"""
        if req.action == "confirm_unchanged":
            return runtime.revision_work.confirm_unchanged(project_id, chapter_id)
        if req.action == "changed":
            return runtime.revision_work.mark_changed(project_id, chapter_id)
        if req.action == "blocked":
            return runtime.revision_work.mark_blocked(project_id, chapter_id, reason=req.reason)
        raise ViolationError(
            code=ec.COVERAGE_INVALID_TRANSITION,
            where=f"revision-work.coverage({chapter_id})",
            why=f"覆盖动作只能是 confirm_unchanged / changed / blocked，收到 {req.action!r}",
            fix="改用 confirm_unchanged（先做过全文检查）、changed（本次已改）或 blocked（受阻）",
            details={"action": req.action},
        )

    # -- 有限执行任务（V03-7.3）-----------------------------------------------
    @router.post("/revision-tasks")
    def start_revision_task(
        project_id: str, req: StartRevisionTaskRequest
    ) -> dict[str, Any]:
        """开一次整体任务（预算默认 12、可缩小）；同 request_id 重放返回原任务。"""
        return runtime.revision_tasks.start(
            project_id, request_id=req.request_id, call_budget=req.call_budget
        )

    @router.get("/revision-tasks/current")
    def get_current_revision_task(project_id: str) -> dict[str, Any]:
        """当前任务：没有时是合法空结果，不是错误。"""
        current = runtime.revision_tasks.current(project_id)
        if current is None:
            return {"task_id": None, "opened": False}
        return current

    @router.get("/revision-tasks/{task_id}")
    def get_revision_task(project_id: str, task_id: str) -> dict[str, Any]:
        """按 id 回读任务：预算、等待原因、覆盖摘要与发布前阻断项。"""
        return runtime.revision_tasks.get(project_id, task_id)

    @router.post("/revision-tasks/{task_id}/stop")
    def stop_revision_task(project_id: str, task_id: str) -> dict[str, Any]:
        """停止：保留正式稳定版与内部进度；之后可显式继续。"""
        return runtime.revision_tasks.stop(project_id, task_id)

    @router.post("/revision-tasks/{task_id}/continue")
    def continue_revision_task(
        project_id: str, task_id: str, req: ContinueRevisionTaskRequest
    ) -> dict[str, Any]:
        """继续：追加预算、推进 attempt，并要求发布前重验。"""
        return runtime.revision_tasks.continue_task(
            project_id, task_id, additional_calls=req.additional_calls
        )

    # -- 内部工作版本暂存与统一发布（V03-7.4）---------------------------------
    @router.post("/revision-tasks/{task_id}/staged-chapters")
    def stage_chapter(
        project_id: str, task_id: str, req: StageChapterRequest
    ) -> dict[str, Any]:
        """暂存一章全文；不写正式正文，发布时才进入同一个 Commit。"""
        return runtime.revision_publish.stage_chapter(
            project_id,
            task_id,
            chapter_id=req.chapter_id,
            text=req.text,
            base_revision_id=req.base_revision_id,
        )

    @router.delete("/revision-tasks/{task_id}/staged-chapters/{chapter_id}")
    def unstage_chapter(project_id: str, task_id: str, chapter_id: str) -> dict[str, Any]:
        """取消一章的暂存；不影响正式正文。"""
        return runtime.revision_publish.unstage_chapter(project_id, task_id, chapter_id)

    @router.post("/revision-tasks/{task_id}/staged-ledger")
    def stage_ledger(
        project_id: str, task_id: str, req: StageLedgerRequest
    ) -> dict[str, Any]:
        """暂存一条账本新建/修订；暂存时先按基础目录校验字段与引用。"""
        return runtime.revision_publish.stage_ledger(
            project_id,
            task_id,
            fields=req.fields,
            stage_id=req.stage_id,
            entry_id=req.entry_id,
            kind=req.kind,
            expected_revision=req.expected_revision,
        )

    @router.delete("/revision-tasks/{task_id}/staged-ledger/{stage_id}")
    def unstage_ledger(project_id: str, task_id: str, stage_id: str) -> dict[str, Any]:
        """取消一条账本暂存；不影响正式账本。"""
        return runtime.revision_publish.unstage_ledger(project_id, task_id, stage_id)

    @router.get("/revision-tasks/{task_id}/staged")
    def get_staged(project_id: str, task_id: str) -> dict[str, Any]:
        """回读暂存区：章节全文、待发布账本变化与计数。"""
        return runtime.revision_publish.staged(project_id, task_id)

    @router.post("/revision-tasks/{task_id}/publish")
    def publish_revision_task(
        project_id: str, task_id: str, req: PublishRevisionRequest
    ) -> dict[str, Any]:
        """一次发布正文与账本；门禁不通过时整体不提交。"""
        return runtime.revision_publish.publish(
            project_id, task_id, operation_id=req.operation_id
        )

    return router
