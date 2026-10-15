"""V2 REST 面（Phase 14 T14.8，仅后端）：正文保存、正文查询、作者模型查询。

V2 领域与应用层在此之前的全部出口都是测试；本模块是第一个 HTTP 装配点。
设计取向：

- **独立路由前缀** ``/api/v2``，与 V1 路由共存但互不依赖——退出条件要求「V2 人工
  保存完全不调用旧 ``.revisions``/reconcile 写链」，独立前缀让两条链路在 HTTP 层
  就可区分，前端迁移期可对照联调；
- **project_id 直连**：V2 的身份体系是 ``ProjectId``（Repository 内核），不再引入
  slug→project 的第二套映射；项目经 ``POST /api/v2/projects`` 创建后把 ID 交给
  调用方保存（minimal UI 之前由调用方持有）；
- **错误信封直通**：应用层 ``HastingsError`` 的 code/where/why/fix/details 原样
  进 HTTP 信封（四透镜①：错误消息是喂给客户端的 API，不做「人话翻译」），状态
  码按 code 后缀分类（`*_CONFLICT`→409、`*_NOT_FOUND`/`UNKNOWN*`→404、环境故障
  →503、其余违规→400）；
- **保存不等待后台任务**（评审 #3 的 HTTP 面）：保存端点只返回 Commit 结果；分析
  调度由 ``dispatch_pending`` / 后台发布循环驱动，JobStore 故障不影响保存响应。

已知留白：JobStore 为进程内实现；发布循环未接入服务生命周期（本阶段只提供
``dispatch_pending`` 驱动点，供测试与后续 lifespan 接线）。
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any, cast

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from hastings.application.errors import HastingsError, ResultCategory, ViolationError
from hastings.application.handlers.manuscript_commands import (
    ManuscriptCommandHandler,
    manuscript_command,
)
from hastings.application.ports.events import EventOutbox
from hastings.application.queries.author_profile import AuthorProfileQueries
from hastings.application.queries.manuscript import ManuscriptQueries
from hastings.application.workflows.analysis_dispatch import AnalysisDispatcher
from hastings.application.workflows.manual_edit import CommitManualEdit, ManualEditIntent
from hastings.domain.author_model.errors import AuthorModelViolation
from hastings.domain.manuscript import error_codes as ms_ec
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.manuscript.commands import import_chapter as import_chapter_command
from hastings.domain.manuscript.errors import ManuscriptViolation
from hastings.domain.manuscript.models import Chapter
from hastings.domain.manuscript.operations import operation_from_content
from hastings.domain.repository import error_codes as repo_ec
from hastings.domain.repository.ids import Actor, CommitId, ProjectId
from hastings.infrastructure.events.outbox import OutboxPublisher
from hastings.infrastructure.jobs import MemoryJobStore
from hastings.infrastructure.repository.memory import MemoryRepository, build_memory_repository
from hastings.infrastructure.repository.sqlite import SqliteRepository, build_sqlite_repository
from hastings.infrastructure.runtime import SystemClock, UuidIdGenerator

__all__ = ["V2Runtime", "build_v2_app", "create_v2_router", "register_v2_error_handlers"]

_BRANCH_NAME = "main"


# ---------------------------------------------------------------------------
# 运行时装配：一个进程一份仓库 + 应用层服务面
# ---------------------------------------------------------------------------
class V2Runtime:
    """V2 服务运行时：仓库后端 + handler/工作流/查询 + Outbox 消费者。

    ``db_path=None`` 用内存后端（测试 / 纯 API 模式）；给路径则用 SQLite
    （重启恢复场景，见 tests/v2/manuscript/test_recovery.py 的同构验收）。
    """

    def __init__(self, db_path: str | None = None) -> None:
        clock, ids = SystemClock(), UuidIdGenerator()
        repo: MemoryRepository | SqliteRepository
        if db_path is None:
            repo = build_memory_repository(clock, ids)
        else:
            repo = build_sqlite_repository(db_path, clock, ids)
        self._repo = repo
        self.service = repo.service
        self.handler = ManuscriptCommandHandler(
            self.service, self.service, ids, Actor(kind="human", id="author")
        )
        self.workflow = CommitManualEdit(self.handler)
        self.queries = ManuscriptQueries(self.service)
        self.author_queries = AuthorProfileQueries(self.service)
        self.jobs = MemoryJobStore()
        self.dispatcher = AnalysisDispatcher(self.jobs)
        # cast 理由：两个后端的 pending() 都返回 list[dict]（dict 是 Mapping 的子类型，
        # 语义兼容），仅因 list 不变型被协议检查拦下
        self.publisher = OutboxPublisher(cast("EventOutbox", repo), self.dispatcher)

    @classmethod
    def memory(cls) -> V2Runtime:
        return cls(db_path=None)

    @classmethod
    def sqlite(cls, path: str) -> V2Runtime:
        return cls(db_path=path)

    def create_project(self, name: str) -> ProjectId:
        return self.handler.create_project(name).id

    def head(self, project_id: ProjectId) -> CommitId | None:
        """当前分支 head；项目或 main 分支不存在即 404 信封（REPO_BRANCH_NOT_FOUND）。"""
        branch = self.service.get_branch(project_id, _BRANCH_NAME)
        if branch is None:
            raise ViolationError(
                code=repo_ec.REPO_BRANCH_NOT_FOUND,
                where=f"v2.project({project_id.value})",
                why=f"项目 {project_id.value!r} 不存在，或没有 {_BRANCH_NAME!r} 分支",
                fix="先 POST /api/v2/projects 创建项目，再用返回的 project_id 访问",
                details={"project_id": project_id.value, "branch_name": _BRANCH_NAME},
            )
        return branch.head_commit_id

    def new_artifact_id(self) -> str:
        return self.handler._new_id("art")

    def dispatch_pending(self, project_id: str) -> int:
        """同步驱动一轮 Outbox 发布（测试 / 脚本 / 定时任务用）。"""
        return asyncio.run(self.publisher.publish_pending(project_id))


# ---------------------------------------------------------------------------
# 请求 / 响应模型
# ---------------------------------------------------------------------------
class ProjectCreateRequest(BaseModel):
    name: str


class ImportChapterRequest(BaseModel):
    idempotency_key: str
    title: str
    text: str
    order_key: str = "0001"


class SaveChapterRequest(BaseModel):
    idempotency_key: str
    operations: list[dict[str, Any]] | None = None
    full_text: str | None = None
    expected_chapter_base: str | None = None
    summary: str = "保存正文"


# ---------------------------------------------------------------------------
# 投影序列化：查询视图 → JSON（只读派生结果，不缓存）
# ---------------------------------------------------------------------------
def _manuscript_payload(runtime: V2Runtime, project_id: ProjectId) -> dict[str, Any]:
    head = runtime.head(project_id)
    if head is None:
        return {"project_id": project_id.value, "commit_id": None, "chapters": []}
    catalog = runtime.queries.catalog(project_id, head)
    return {
        "project_id": project_id.value,
        "commit_id": head.value,
        "chapters": [
            _chapter_payload_from(catalog, entry_id) for entry_id, _ in _chapters(catalog)
        ],
    }


def _chapters(catalog: ManuscriptCatalog) -> list[tuple[str, Chapter]]:
    return [(entry.artifact_id, entry.model()) for entry in catalog.chapters()]


def _chapter_payload_from(catalog: ManuscriptCatalog, chapter_id: str) -> dict[str, Any]:
    chapter: Chapter = catalog.require_chapter(chapter_id, where="v2.manuscript").model()
    units = catalog.units_of(chapter_id)
    return {
        "chapter_id": chapter_id,
        "title": chapter.title,
        "order_key": chapter.order_key,
        "chapter_base": catalog.chapter_base(chapter_id),
        "units": [{"unit_id": u.artifact_id, "text": u.model().text} for u in units],
    }


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
def create_v2_router(runtime: V2Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v2", tags=["v2"])

    @router.post("/projects")
    def create_project(req: ProjectCreateRequest) -> dict[str, Any]:
        project = runtime.create_project(req.name)
        return {"project_id": project.value, "name": req.name}

    @router.get("/projects/{project_id}/manuscript")
    def get_manuscript(project_id: str) -> dict[str, Any]:
        return _manuscript_payload(runtime, ProjectId(project_id))

    @router.post("/projects/{project_id}/chapters/import")
    def import_chapter(project_id: str, req: ImportChapterRequest) -> dict[str, Any]:
        chapter_id = runtime.new_artifact_id()
        result = runtime.handler.execute(
            manuscript_command(
                import_chapter_command,
                chapter_id=chapter_id,
                title=req.title,
                text=req.text,
                order_key=req.order_key,
            ),
            project_id=ProjectId(project_id),
            idempotency_key=req.idempotency_key,
            summary=f"导入「{req.title}」",
        )
        return {
            "status": result.status,
            "chapter_id": chapter_id,
            "commit_id": result.commit_id,
            "new_head": result.new_head,
            "summary": result.summary,
        }

    @router.get("/projects/{project_id}/chapters/{chapter_id}")
    def get_chapter(project_id: str, chapter_id: str) -> dict[str, Any]:
        pid = ProjectId(project_id)
        head = runtime.head(pid)
        if head is None:
            return {"project_id": project_id, "commit_id": None, **_empty_chapter(chapter_id)}
        catalog = runtime.queries.catalog(pid, head)
        payload = _chapter_payload_from(catalog, chapter_id)
        payload["commit_id"] = head.value
        payload["project_id"] = project_id
        payload["text"] = "\n\n".join(unit["text"] for unit in payload["units"])
        return payload

    @router.post("/projects/{project_id}/chapters/{chapter_id}/save")
    async def save_chapter(
        project_id: str, chapter_id: str, req: SaveChapterRequest
    ) -> dict[str, Any]:
        if (req.operations is None) == (req.full_text is None):
            raise ViolationError(
                code=ms_ec.MANUSCRIPT_INVALID_OPERATION,
                where=f"v2.save({project_id}.{chapter_id})",
                why="operations 与 full_text 必须且只能提供其一"
                "（为什么错：两者都给无从确定意图，都不给没有内容可提交）",
                fix="优先给 operations（规范化编辑操作），兼容模式给 full_text（整章文本）",
                details={"chapter_id": chapter_id},
            )
        try:
            operations = (
                tuple(operation_from_content(op) for op in req.operations)
                if req.operations is not None
                else None
            )
        except ManuscriptViolation as exc:
            raise ViolationError(
                code=exc.code, where=exc.where, why=exc.why, fix=exc.fix, details=exc.details
            ) from exc
        intent = ManualEditIntent(
            project_id=ProjectId(project_id),
            chapter_id=chapter_id,
            idempotency_key=req.idempotency_key,
            summary=req.summary,
            operations=operations,
            full_text=req.full_text,
            expected_chapter_base=req.expected_chapter_base,
        )
        result = await runtime.workflow.save(intent)
        head = runtime.head(ProjectId(project_id))
        chapter_base = result.edit.change.chapter_base_after
        if not chapter_base and head is not None:
            chapter_base = runtime.queries.chapter_base(ProjectId(project_id), head, chapter_id)
        return {
            "status": result.status,
            "commit_id": result.commit_id,
            "new_head": result.new_head,
            "summary": result.edit.summary,
            "warnings": list(result.warnings),
            "chapter_base": chapter_base,
        }

    @router.get("/projects/{project_id}/author-model/preferences")
    def get_preferences(project_id: str) -> dict[str, Any]:
        pid = ProjectId(project_id)
        head = runtime.head(pid)
        if head is None:
            return {"project_id": project_id, "commit_id": None, "preferences": []}
        preferences = runtime.author_queries.preferences(pid, head)
        return {
            "project_id": project_id,
            "commit_id": head.value,
            "preferences": [asdict(p) for p in preferences],
        }

    @router.get("/projects/{project_id}/author-model/observations")
    def get_observations(project_id: str) -> dict[str, Any]:
        pid = ProjectId(project_id)
        head = runtime.head(pid)
        if head is None:
            return {"project_id": project_id, "commit_id": None, "observations": []}
        observations = runtime.author_queries.observations(pid, head)
        return {
            "project_id": project_id,
            "commit_id": head.value,
            "observations": [asdict(o) for o in observations],
        }

    @router.get("/projects/{project_id}/author-model/preferences/{preference_id}/evidence")
    def get_preference_evidence(project_id: str, preference_id: str) -> dict[str, Any]:
        pid = ProjectId(project_id)
        head = runtime.head(pid)
        evidence = (
            runtime.author_queries.preference_evidence(pid, head, preference_id) if head else ()
        )
        return {
            "project_id": project_id,
            "commit_id": head.value if head else None,
            "preference_id": preference_id,
            "observations": [asdict(o) for o in evidence],
        }

    return router


def _empty_chapter(chapter_id: str) -> dict[str, Any]:
    return {
        "chapter_id": chapter_id,
        "title": "",
        "order_key": "",
        "chapter_base": "",
        "units": [],
        "text": "",
    }


# ---------------------------------------------------------------------------
# 错误信封：HastingsError → HTTP（四透镜①：三要素原样透传）
# ---------------------------------------------------------------------------
def _status_for(error: HastingsError) -> int:
    if error.category is ResultCategory.ENVIRONMENT:
        return 503
    if error.code.endswith("_NOT_FOUND") or "UNKNOWN" in error.code:
        return 404
    if error.code.endswith("_CONFLICT"):
        return 409
    return 400


def register_v2_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HastingsError)
    async def hastings_error_handler(request: Request, exc: HastingsError) -> JSONResponse:
        del request
        return JSONResponse(status_code=_status_for(exc), content={"error": _error_body(exc)})

    @app.exception_handler(ManuscriptViolation)
    async def manuscript_violation_handler(
        request: Request, exc: ManuscriptViolation
    ) -> JSONResponse:
        return _violation_response(exc)

    @app.exception_handler(AuthorModelViolation)
    async def author_model_violation_handler(
        request: Request, exc: AuthorModelViolation
    ) -> JSONResponse:
        return _violation_response(exc)


def _as_violation(
    exc: ManuscriptViolation | AuthorModelViolation,
) -> ViolationError:
    """边界转换：领域违规 → 应用错误信封（四要素原样透传，与 handler 同构）。"""
    return ViolationError(
        code=exc.code, where=exc.where, why=exc.why, fix=exc.fix, details=exc.details
    )


def _violation_response(exc: ManuscriptViolation | AuthorModelViolation) -> JSONResponse:
    envelope = _as_violation(exc)
    return JSONResponse(status_code=_status_for(envelope), content={"error": _error_body(envelope)})


def _error_body(exc: HastingsError) -> dict[str, Any]:
    return {
        "code": exc.code,
        "category": exc.category.value,
        "where": exc.where,
        "why": exc.why,
        "fix": exc.fix,
        "details": dict(exc.details),
    }


def build_v2_app(runtime: V2Runtime) -> FastAPI:
    """独立 V2 应用（测试 / 纯 V2 部署）；与 V1 共存挂载见 ``server/app.py``。"""
    app = FastAPI(title="Hastings V2", version="0.2.0")
    app.include_router(create_v2_router(runtime))
    register_v2_error_handlers(app)
    return app
