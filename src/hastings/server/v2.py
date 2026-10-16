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

Phase 16：JobStore 与任务事件使用 SQLite；服务生命周期恢复 Job、发现待发布的
项目 Outbox、派发真实编辑观察并补交分析元数据。独立 V2 Worker 只返回候选。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any, cast

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from hastings.application.errors import HastingsError, ResultCategory, ViolationError
from hastings.application.handlers.change_commands import ChangeSetCommandHandler
from hastings.application.handlers.manuscript_commands import (
    ManuscriptCommandHandler,
    manuscript_command,
)
from hastings.application.ports.agents import AgentRunner
from hastings.application.ports.events import EventOutbox
from hastings.application.queries.author_profile import AuthorProfileQueries
from hastings.application.queries.manuscript import ManuscriptQueries
from hastings.application.services.conversation_service import ConversationService
from hastings.application.services.task_prep_service import TaskPrepService
from hastings.application.services.task_projection import ChatTaskProjector
from hastings.application.workflows.agent_workflows import AgentWorkflows
from hastings.application.workflows.analysis_dispatch import AnalysisDispatcher
from hastings.application.workflows.audit import AuditWorkflow
from hastings.application.workflows.change_analysis import AnalyzeChangeImpact
from hastings.application.workflows.manual_edit import CommitManualEdit, ManualEditIntent
from hastings.application.workflows.plan_revision import PlanRevision
from hastings.application.workflows.restore_version import RestoreChapterVersion, RestoreIntent
from hastings.application.workflows.review import ReviewRevision
from hastings.application.workflows.revision import ExecuteRevisionPlan
from hastings.application.workflows.rewrite import RewriteWorkflow
from hastings.application.workflows.worker_job import WorkerJobRunner
from hastings.domain.author_model.errors import AuthorModelViolation
from hastings.domain.changes.errors import ChangesViolation
from hastings.domain.conversation.task_drafts import ProjectView
from hastings.domain.manuscript import error_codes as ms_ec
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.manuscript.commands import import_chapter as import_chapter_command
from hastings.domain.manuscript.errors import ManuscriptViolation
from hastings.domain.manuscript.models import Chapter
from hastings.domain.manuscript.operations import operation_from_content
from hastings.domain.repository import error_codes as repo_ec
from hastings.domain.repository.ids import Actor, CommitId, ProjectId
from hastings.domain.story.errors import StoryViolation
from hastings.infrastructure.conversations.drafts import SqliteTaskDraftStore
from hastings.infrastructure.conversations.store import SqliteConversationStore
from hastings.infrastructure.events.outbox import OutboxPublisher
from hastings.infrastructure.jobs import SqliteJobStore
from hastings.infrastructure.repository.memory import MemoryRepository, build_memory_repository
from hastings.infrastructure.repository.sqlite import SqliteRepository, build_sqlite_repository
from hastings.infrastructure.runtime import SystemClock, UuidIdGenerator
from hastings.interfaces.api.agents import create_agents_router
from hastings.interfaces.api.changes import create_changes_router
from hastings.interfaces.exporters.v2_package_exporter import export_commit, zip_package
from hastings.server.v2_agents import ConfiguredV2Runner

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
        self.db_path = db_path
        repo: MemoryRepository | SqliteRepository
        if db_path is None:
            repo = build_memory_repository(clock, ids)
        else:
            repo = build_sqlite_repository(db_path, clock, ids)
        self._repo = repo
        self.service = repo.service
        self.clock, self.ids = clock, ids
        author = Actor(kind="human", id="author")
        self.changes = ChangeSetCommandHandler(self.service, self.service, ids, author)
        self.change_analysis = AnalyzeChangeImpact(self.changes, self.service, clock, author)
        self.change_planner = PlanRevision(self.changes, self.service, clock, author)
        self.revision = ExecuteRevisionPlan(
            self.changes,
            self.service,
            self.service,
            repo,
            ids,
            clock,
            Actor(kind="agent", id="revision_worker"),
        )
        self.review = ReviewRevision(self.service, self.service, clock, author)
        self.audit = AuditWorkflow(self.service, self.service, clock, author)
        self.rewrite = RewriteWorkflow(self.service, self.service, repo, ids, clock, author)
        self.handler = ManuscriptCommandHandler(
            self.service, self.service, ids, Actor(kind="human", id="author")
        )
        self.workflow = CommitManualEdit(self.handler)
        self.queries = ManuscriptQueries(self.service)
        self.author_queries = AuthorProfileQueries(self.service)
        self.jobs = SqliteJobStore(db_path or ":memory:")
        v2_runner = ConfiguredV2Runner(self.service)
        self.configure_agent_runner(v2_runner)
        self._v2_runner = v2_runner
        # 对话存储与执行（Phase 19.3）：与 Repository/Job 同一 DB 文件、独立表；
        # 模型解析与流式执行复用 ConfiguredV2Runner 的档案选择（设置页切换生效）。
        self.conversation_store = SqliteConversationStore(db_path or ":memory:")
        self.chat = ConversationService(
            self.conversation_store,
            runner_factory=v2_runner.chat_runner,
            model_resolver=v2_runner.chat_model,
            ref_validator=self.validate_refs,
        )
        # 任务准备（Phase 19.4）：建议本身不创建 Job；启动经 starter 映射到
        # 既有 AgentWorkflows（plan/draft/audit），与 /agent-jobs 同一授权与调度。
        self.task_draft_store = SqliteTaskDraftStore(db_path or ":memory:")
        self.task_prep = TaskPrepService(
            self.task_draft_store,
            self.conversation_store,
            project_view=self.project_view,
            starter=self.start_task_job,
            model_label=self._chat_model_label,
            accepter=self.accept_candidate_job,
        )
        # 任务卡只投影实际 Job（§19.5）：worker_loop 每轮扫描已启动 draft。
        self.task_projector = ChatTaskProjector(
            self.task_draft_store,
            self.conversation_store,
            job_getter=self.jobs.get,
        )
        # 模型绑定快照来源（§19.5）：默认当前选择；测试可注入。
        self.model_binding_provider = v2_runner.model_binding
        self._worker_enabled = False
        self._active_jobs: dict[str, asyncio.Task[None]] = {}
        self._wake_projects: set[str] = set()
        self.dispatcher = AnalysisDispatcher(self.jobs)
        self.restore = RestoreChapterVersion(self.handler, self.service)
        # cast 理由：两个后端的 pending() 都返回 list[dict]（dict 是 Mapping 的子类型，
        # 语义兼容），仅因 list 不变型被协议检查拦下
        self.publisher = OutboxPublisher(cast("EventOutbox", repo), self.dispatcher)

    def configure_agent_runner(self, runner: AgentRunner) -> None:
        self.worker = WorkerJobRunner(self.jobs, runner, self._repo, self.ids, self.clock)
        self.agent_workflows = AgentWorkflows(
            self.service, self.service, self.worker, self.ids, self.clock
        )

    async def wake_workers(self, project_id: str) -> None:
        self._wake_projects.add(project_id)

    async def prepare_analysis_jobs(self) -> None:
        for job in await self.jobs.list_jobs():
            if job["job_type"] != "manuscript.analysis" or job["state"] not in (
                "queued",
                "retryable_failed",
            ):
                continue
            if job["retry_at"] and datetime.fromisoformat(job["retry_at"]) > self.clock.now():
                continue
            claimed = await self.jobs.claim(
                job["job_id"], "analysis-dispatch", now=self.clock.now(), ttl=30
            )
            try:
                payload = job["payload"]
                pid = ProjectId(payload["project_id"])
                after = CommitId(payload["commit_id"])
                history = self.service.commit_history(pid, after)
                commit = next(c for c in history if c.id == after)
                if commit.parent_ids and commit.actor.kind == "human":
                    await self.agent_workflows.style(
                        pid,
                        commit.parent_ids[0].value,
                        after.value,
                        payload["chapter_id"],
                        f"{job['job_id']}:observer",
                    )
                await self.jobs.finish(
                    job["job_id"],
                    claimed["generation"],
                    "completed",
                    {"kind": "no_result", "reason": "已派发真实编辑观察或没有前后版本"},
                )
            except (ValueError, HastingsError, OSError) as exc:
                violation = isinstance(exc, (ValueError, ViolationError))
                retry = not violation and claimed["attempts"] < 3
                await self.jobs.finish(
                    job["job_id"],
                    claimed["generation"],
                    "retryable_failed" if retry else "terminal_failed",
                    {
                        "kind": "worker_failure",
                        "category": "violation" if violation else "environment",
                        "reason": str(exc),
                        "errors": [],
                        "raw_outputs": [],
                    },
                    retry_at=self.clock.now() + timedelta(seconds=2 ** claimed["attempts"])
                    if retry
                    else None,
                )

    async def _execute_job(self, job_id: str) -> None:
        try:
            result = await self.worker.execute(job_id)
            if result["state"] == "completed" and result["job_type"] in (
                "auditor",
                "style_observer",
            ):
                analysis = await self.agent_workflows.apply_analysis(job_id)
                await self.jobs.record_analysis(job_id, analysis)
        except (HastingsError, ValueError, OSError) as exc:
            # 分析提交故障保留结果待重放；不丢掉已完成的 Worker 输出。
            self.last_worker_error = f"{type(exc).__name__}: {exc}"

    async def worker_loop(self) -> None:
        self._worker_enabled = True
        try:
            while True:
                await self.jobs.recover(self.clock.now())
                self._wake_projects.update(await self._repo.pending_projects())
                for project in tuple(self._wake_projects):
                    try:
                        await self.publisher.publish_pending(project)
                    except (HastingsError, OSError):
                        continue
                    self._wake_projects.discard(project)
                await self.prepare_analysis_jobs()
                with suppress(HastingsError):
                    await self.task_projector.sync()
                self._active_jobs = {jid: t for jid, t in self._active_jobs.items() if not t.done()}
                for job in await self.jobs.list_jobs():
                    analysis_pending = (
                        job["state"] == "completed"
                        and job["job_type"] in ("auditor", "style_observer")
                        and not job.get("analysis")
                    )
                    due = (
                        not job["retry_at"]
                        or datetime.fromisoformat(job["retry_at"]) <= self.clock.now()
                    )
                    if (
                        (job["state"] in ("queued", "retryable_failed") or analysis_pending)
                        and job["job_type"] != "manuscript.analysis"
                        and due
                        and job["job_id"] not in self._active_jobs
                        and len(self._active_jobs) < 2
                    ):
                        self._active_jobs[job["job_id"]] = asyncio.create_task(
                            self._execute_job(job["job_id"])
                        )
                await asyncio.sleep(0.3)
        finally:
            self._worker_enabled = False
            for task in self._active_jobs.values():
                task.cancel()
            for task in self._active_jobs.values():
                with suppress(asyncio.CancelledError):
                    await task

    @classmethod
    def memory(cls) -> V2Runtime:
        return cls(db_path=None)

    # -- 任务准备装配（Phase 19.4） --------------------------------------------
    def _chat_model_label(self) -> str | None:
        """当前模型标签；未配置返回 None（缺失项计算用）。"""
        try:
            return self.chat.model_resolver()
        except HastingsError:
            return None

    def project_view(self, project_id: str) -> ProjectView:
        """项目只读视图：模型、已接受节拍、章节集合、head。"""
        pid = ProjectId(project_id)
        try:
            head = self.head(pid)
        except HastingsError:
            head = None
        chapter_ids: frozenset[str] = frozenset()
        has_beats = False
        if head is not None:
            catalog = self.queries.catalog(pid, head)
            chapter_ids = frozenset(
                entry_id for entry_id, _chapter in _chapters(catalog)
            )
            snapshot = self.service.get_snapshot(pid, head)
            has_beats = any(
                v.artifact.kind in ("narrative.beat", "story.plot_beat")
                for v in snapshot.artifacts.values()
            )
        return ProjectView(
            model_configured=self._chat_model_label() is not None,
            has_accepted_beats=has_beats,
            chapter_ids=chapter_ids,
            head=head.value if head else None,
        )

    async def start_task_job(self, project_id: str, draft: Any, key: str) -> str:
        """TaskDraft → 既有工作流映射（§4.3）：与 /agent-jobs 同一授权与调度。

        定向修订走 Change 用例（影响分析 → 批准 → 修订），不经本入口——
        这里明确拒绝而不是静默降级。
        """
        pid = ProjectId(project_id)
        app = self.agent_workflows
        targets = tuple(draft.target_ids)
        # 启动即定格模型快照（§19.5）：未配置在此明确失败，不悬挂任务。
        binding = self.model_binding_provider()
        if draft.task_kind == "targeted_revision":
            raise ViolationError(
                code="TASK_START_UNSUPPORTED",
                where="task.start",
                why="定向修订需要经变更中心批准范围后执行，本入口不直接启动",
                fix="从变更中心发起修订流程；任务建议可作为其说明",
            )
        head = self.head(pid)
        if head is None:
            branch = self.service.get_branch(pid, _BRANCH_NAME)
            if branch is not None:
                head = self.head(pid)
        if head is None:
            raise ViolationError(
                code="TASK_NO_BASE",
                where="task.start",
                why="项目还没有任何提交，没有可依据的内容版本",
                fix="先在创作台导入或保存一章正文，再启动任务",
            )
        base = draft.base_version or head.value
        if draft.task_kind == "plan":
            jid = await app.plan(
                pid, base, targets, key, draft.instruction, model_binding=binding
            )
        elif draft.task_kind == "chapter_draft":
            chapter_id = draft.goal
            if not chapter_id:
                raise ViolationError(
                    code="TASK_NO_CHAPTER_TARGET",
                    where="task.start",
                    why="没有指定要起草的章节",
                    fix="在任务卡调整目标章节后重试",
                )
            jid = await app.draft(
                pid, base, targets, chapter_id, key, draft.instruction, model_binding=binding
            )
        elif draft.task_kind == "check":
            if not targets:
                snapshot = self.service.get_snapshot(pid, CommitId(base))
                targets = tuple(
                    v.artifact.id.value
                    for v in snapshot.artifacts.values()
                    if v.artifact.kind.startswith(("story.", "narrative."))
                )
            jid = await app.audit(pid, base, targets, key, model_binding=binding)
        else:  # pragma: no cover - TASK_KINDS 已约束
            raise ViolationError(
                code="TASK_START_UNSUPPORTED",
                where="task.start",
                why=f"未知任务类型 {draft.task_kind!r}",
                fix="使用 plan / chapter_draft / check / targeted_revision",
            )
        await self.wake_workers(project_id)
        await self.task_prep.record_binding(project_id, draft.draft_id, binding)
        return jid

    async def accept_candidate_job(self, job_ref: str, expected_head: str) -> dict[str, Any]:
        """候选接受复用既有审阅规则（marker Commit 自带幂等）。"""
        return await self.agent_workflows.accept(job_ref, expected_head)

    def validate_refs(self, project_id: str, refs: list[dict[str, Any]]) -> None:
        """发送轮次的引用校验：对象必须存在于当前项目（引用不能跨项目）。"""
        if not refs:
            return
        pid = ProjectId(project_id)
        try:
            head = self.head(pid)
        except HastingsError as exc:
            raise exc
        chapter_ids: set[str] = set()
        artifact_ids: set[str] = set()
        if head is not None:
            catalog = self.queries.catalog(pid, head)
            chapter_ids = {entry_id for entry_id, _ in _chapters(catalog)}
            snapshot = self.service.get_snapshot(pid, head)
            artifact_ids = {v.artifact.id.value for v in snapshot.artifacts.values()}
        for ref in refs:
            kind = str(ref.get("kind", ""))
            target = str(ref.get("target_id", ""))
            if kind == "chapter" and target not in chapter_ids:
                raise ViolationError(
                    code="CHAT_REF_NOT_FOUND",
                    where="chat.refs.chapter",
                    why=f"当前项目没有章节 {target!r}（或引用来自其他项目）",
                    fix="重新选择当前项目的章节作为引用",
                    details={"target_id": target},
                )
            if kind == "artifact" and target not in artifact_ids:
                raise ViolationError(
                    code="CHAT_REF_NOT_FOUND",
                    where="chat.refs.artifact",
                    why=f"当前项目没有对象 {target!r}（或引用来自其他项目）",
                    fix="重新选择当前项目的对象作为引用",
                    details={"target_id": target},
                )
            if kind == "selection" and not ref.get("saved") and not ref.get("excerpt"):
                raise ViolationError(
                    code="CHAT_REF_NOT_FOUND",
                    where="chat.refs.selection",
                    why="未保存选文必须携带摘录内容，空引用没有讨论基础",
                    fix="重新选中文字并引用，或先保存正文后引用章节",
                )

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

    async def events_after(
        self, project_id: str, after_seq: int, limit: int = 200
    ) -> list[dict[str, Any]]:
        """按项目读 sequence 之后的业务事件（已发布与未发布都算，V2 WS 补齐用）。"""
        return await self._repo.events_after(project_id, after_seq, limit)


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


class RestoreChapterRequest(BaseModel):
    idempotency_key: str
    source_commit_id: str
    expected_chapter_base: str | None = None


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
    from hastings.interfaces.api.author_decisions import create_author_decisions_router
    from hastings.interfaces.api.conversations import create_conversations_router
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # 启动恢复（§19.3）：遗留的排队/运行轮次标为中断，不自动重发模型调用。
        await runtime.chat.mark_interrupted()
        task = asyncio.create_task(runtime.worker_loop())
        try:
            yield
        finally:
            await runtime.chat.shutdown()
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    router = APIRouter(prefix="/api/v2", tags=["v2"], lifespan=lifespan)

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
        await runtime.wake_workers(project_id)
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

    @router.post("/projects/{project_id}/chapters/{chapter_id}/restore")
    def restore_chapter(
        project_id: str, chapter_id: str, req: RestoreChapterRequest
    ) -> dict[str, Any]:
        """把章恢复到历史 Commit 的版本：恢复本身是一次新 Commit（不重写历史）。"""
        result = runtime.restore.restore(
            RestoreIntent(
                project_id=ProjectId(project_id),
                chapter_id=chapter_id,
                source_commit_id=CommitId(req.source_commit_id),
                idempotency_key=req.idempotency_key,
                expected_chapter_base=req.expected_chapter_base,
            )
        )
        head = runtime.head(ProjectId(project_id))
        chapter_base = (
            runtime.queries.chapter_base(ProjectId(project_id), head, chapter_id)
            if head is not None
            else ""
        )
        return {
            "status": result.status,
            "commit_id": result.commit_id,
            "new_head": result.new_head,
            "summary": result.summary,
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

    @router.get("/projects/{project_id}/export")
    def export_package(project_id: str, commit_id: str | None = None) -> Response:
        """把指定 Commit（默认 main head）导出为可读备份包 zip（确定性字节）。"""
        pid = ProjectId(project_id)
        target = CommitId(commit_id) if commit_id else runtime.head(pid)
        if target is None:
            raise ViolationError(
                code=repo_ec.REPO_INVALID_CHANGE,
                where=f"v2.export({project_id})",
                why="项目还没有任何提交，没有可导出的内容",
                fix="先导入或创建章节，再导出",
                details={"project_id": project_id},
            )
        package = export_commit(runtime.service, pid, target)
        filename = f"export-{package.package_hash[:12]}.zip"
        return Response(
            content=zip_package(package),
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename={filename!r}"},
        )

    router.include_router(create_changes_router(runtime))
    router.include_router(create_agents_router(runtime))
    router.include_router(create_author_decisions_router(runtime))
    router.include_router(create_conversations_router(runtime))
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
    @app.exception_handler(ValueError)
    async def worker_input_handler(request: Request, exc: ValueError) -> JSONResponse:
        del request
        error = ViolationError(
            code="AGENT_INVALID_INPUT",
            where="agent.request",
            why=str(exc),
            fix="按任务约束修正输入或重新生成候选",
        )
        return JSONResponse(status_code=400, content={"error": _error_body(error)})

    @app.exception_handler(StoryViolation)
    async def story_violation_handler(request: Request, exc: StoryViolation) -> JSONResponse:
        return _violation_response(exc)

    @app.exception_handler(ChangesViolation)
    async def changes_violation_handler(request: Request, exc: ChangesViolation) -> JSONResponse:
        del request
        error = ViolationError(
            code=exc.code, where=exc.where, why=exc.why, fix=exc.fix, details=exc.details
        )
        return JSONResponse(status_code=_status_for(error), content={"error": _error_body(error)})

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
    exc: ManuscriptViolation | AuthorModelViolation | StoryViolation,
) -> ViolationError:
    """边界转换：领域违规 → 应用错误信封（四要素原样透传，与 handler 同构）。"""
    return ViolationError(
        code=exc.code, where=exc.where, why=exc.why, fix=exc.fix, details=exc.details
    )


def _violation_response(
    exc: ManuscriptViolation | AuthorModelViolation | StoryViolation,
) -> JSONResponse:
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
    from hastings.server.v2_ws import create_v2_ws_router

    app = FastAPI(title="Hastings V2", version="0.2.0")
    app.include_router(create_v2_router(runtime))
    app.include_router(create_v2_ws_router(runtime))
    register_v2_error_handlers(app)
    return app
