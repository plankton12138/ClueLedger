"""整体调整工作版本的 REST 出口（V03-7.2）。

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

硬约束不在这层：只读摘要不得记 ``checked_unchanged`` 由领域层判定，端点只转发结果。
领域违规（``CoverageViolation``）经应用边界的统一 handler 转信封，这里不重复翻译。
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

    return router
