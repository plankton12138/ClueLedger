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
import base64
import binascii
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from datetime import datetime
from typing import Any, cast

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from hastings import __version__
from hastings.application.capabilities import capabilities_payload
from hastings.application.errors import HastingsError, ResultCategory, ViolationError
from hastings.application.execution import (
    JOB_TYPE_FUNCTIONS,
    TASK_KIND_FUNCTIONS,
    ConnectionSnapshot,
    ExecutionGrant,
    authorize_dispatch,
    build_grant,
)
from hastings.application.handlers.change_commands import (
    ChangeSetCommandHandler,
    change_set_command,
)
from hastings.application.handlers.manuscript_commands import (
    ManuscriptCommandHandler,
    manuscript_command,
)
from hastings.application.ports.agents import AgentRunner
from hastings.application.ports.events import EventOutbox
from hastings.application.queries.author_profile import AuthorProfileQueries
from hastings.application.queries.manuscript import ManuscriptQueries
from hastings.application.services.conversation_service import ConversationService
from hastings.application.services.entry_service import EntryService
from hastings.application.services.import_service import ImportService
from hastings.application.services.operation_service import OperationService
from hastings.application.services.publish_manuscript_change import PublishManuscriptChange
from hastings.application.services.task_prep_service import TaskPrepService
from hastings.application.services.task_projection import ChatTaskProjector
from hastings.application.workflows.agent_workflows import AgentWorkflows
from hastings.application.workflows.audit import AuditWorkflow
from hastings.application.workflows.change_analysis import AnalyzeChangeImpact
from hastings.application.workflows.event_projection import CountingProjectionSink
from hastings.application.workflows.manual_edit import CommitManualEdit, ManualEditIntent
from hastings.application.workflows.operation_projection import (
    FanOutSink,
    OperationDiscussionProjector,
)
from hastings.application.workflows.plan_revision import PlanRevision
from hastings.application.workflows.restore_version import RestoreChapterVersion, RestoreIntent
from hastings.application.workflows.review import ReviewRevision
from hastings.application.workflows.revision import ExecuteRevisionPlan
from hastings.application.workflows.rewrite import RewriteWorkflow
from hastings.application.workflows.worker_job import WorkerJobRunner
from hastings.domain.author_model.errors import AuthorModelViolation
from hastings.domain.changes.commands import open_change_set
from hastings.domain.changes.errors import ChangesViolation
from hastings.domain.changes.models import ChangeScope, SeedChange
from hastings.domain.conversation.task_drafts import ProjectView
from hastings.domain.manuscript import error_codes as ms_ec
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.manuscript.commands import import_chapter as import_chapter_command
from hastings.domain.manuscript.errors import ManuscriptViolation
from hastings.domain.manuscript.models import Chapter
from hastings.domain.manuscript.operations import operation_from_content
from hastings.domain.repository import error_codes as repo_ec
from hastings.domain.repository.ids import Actor, ArtifactId, CommitId, ProjectId
from hastings.domain.repository.requests import ArtifactChange, CommitRequest
from hastings.domain.repository.serialization import content_hash
from hastings.domain.story.errors import StoryViolation
from hastings.infrastructure.conversations.drafts import SqliteTaskDraftStore
from hastings.infrastructure.conversations.store import SqliteConversationStore
from hastings.infrastructure.events.outbox import OutboxPublisher
from hastings.infrastructure.execution_log import MemoryExecutionLog, SqliteExecutionLog
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
        # 执行日志（V03-1.6）：运行事件公共投影；SQLite 随库持久，内存版重启清零。
        self.execution_log = (
            MemoryExecutionLog() if db_path is None else SqliteExecutionLog(db_path)
        )
        self.chat = ConversationService(
            self.conversation_store,
            runner_factory=v2_runner.chat_runner,
            model_resolver=v2_runner.chat_model,
            ref_validator=self.validate_refs,
            execution_log=self.execution_log,
        )
        # 无作品入口预约与无模型想法（M02/V03-1.2）：与对话共用存储，
        # 预约记录走仓库辅助表（V03-0.5）。
        self.entries = EntryService(self.service, self.conversation_store, self.clock)
        self.chat.binding_resolver = v2_runner.model_binding
        self.chat.bound_runner_factory = v2_runner.bound_chat_runner
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
            ref_validator=self.validate_refs,
        )
        # 任务卡只投影实际 Job（§19.5）：worker_loop 每轮扫描已启动 draft。
        self.task_projector = ChatTaskProjector(
            self.task_draft_store,
            self.conversation_store,
            job_getter=self.task_fact,
        )
        # 模型绑定快照来源（§19.5）：默认当前选择；测试可注入。
        self.model_binding_provider = v2_runner.model_binding
        self._worker_enabled = False
        self._active_jobs: dict[str, asyncio.Task[None]] = {}
        self._job_project_locks: dict[str, asyncio.Lock] = {}
        self._wake_projects: set[str] = set()
        self._classified_legacy: set[str] = set()
        # 0.3 装配（V03-0.4）：Outbox 订阅者是确定性投影端，不再是
        # AnalysisDispatcher——章提交事件不再自动创建分析 Job。历史事件
        # 与旧资料保留；dispatcher 类留作单元回归，不进 0.3 装配。
        self.event_projection = CountingProjectionSink()
        # 操作记录与固定讨论投影（V03-2.1）：操作事件扇出到计数端与
        # 创作记录投影端；投影失败事件保持待发布，不回滚已生效正文。
        self.operations = OperationService(self.service)
        self.operation_projection = OperationDiscussionProjector(self.conversation_store)
        # 统一正文发布（V03-2.2）：导入/保存/恢复同管线；领域命令纯函数
        # 准备 → 证据核对/摘要失效 → 原子提交（I03）。
        self.manuscript_publisher = PublishManuscriptChange(self.service, self.operations, ids)
        # 正文导入预览与确认（V03-2.3）：预览落辅助表，确认走统一发布。
        self.imports = ImportService(self.service, self.manuscript_publisher, ids, self.clock)
        self.restore = RestoreChapterVersion(self.handler, self.service)
        # cast 理由：两个后端的 pending() 都返回 list[dict]（dict 是 Mapping 的子类型，
        # 语义兼容），仅因 list 不变型被协议检查拦下
        self.publisher = OutboxPublisher(
            cast("EventOutbox", repo),
            FanOutSink(self.event_projection, self.operation_projection),
        )

    def configure_agent_runner(self, runner: AgentRunner) -> None:
        self.worker = WorkerJobRunner(self.jobs, runner, self._repo, self.ids, self.clock)
        self.agent_workflows = AgentWorkflows(
            self.service, self.service, self.worker, self.ids, self.clock
        )

    async def wake_workers(self, project_id: str) -> None:
        self._wake_projects.add(project_id)

    #: 旧自动链路遗留的 Job 类型（V03-0.4 分类报告范围）。作者显式启动的
    #: auditor 等任务不在此列——它们带凭据，走正常派发。
    LEGACY_AUTO_JOB_TYPES = ("manuscript.analysis", "style_observer")

    async def classify_legacy_auto_jobs(self) -> dict[str, int]:
        """旧自动队列分类报告（V03-0.4）：数量/类型/状态，可派发的一律暂停。

        切断装配后不再有新的自动分析 Job；库里遗留的旧任务在此分类：可派发
        状态（queued/retryable_failed）由派发门禁标 ``historical_paused``，
        分类详情（legacy_auto/job_type/原因）随 ``job.paused`` 事件入库；
        终态旧任务原样保留供回看。每个 Job 只分类一次；返回本次分类计数。
        """
        report: dict[str, int] = {}
        for job in await self.jobs.list_jobs():
            if job["job_type"] not in self.LEGACY_AUTO_JOB_TYPES:
                continue
            if job["job_id"] in self._classified_legacy:
                continue
            self._classified_legacy.add(job["job_id"])
            key = f"{job['job_type']}:{job['state']}"
            report[key] = report.get(key, 0) + 1
            await self.jobs.pause(
                job["job_id"],
                reason="historical_paused",
                details={
                    "legacy_auto": True,
                    "job_type": job["job_type"],
                    "why": "0.3 切断旧自动模型调度；保留记录供回看，不派发",
                },
            )
        return report

    async def legacy_auto_report(self) -> dict[str, int]:
        """旧自动队列只读分类快照（数量/类型/状态）；不改变任何状态。"""
        report: dict[str, int] = {}
        for job in await self.jobs.list_jobs():
            if job["job_type"] not in self.LEGACY_AUTO_JOB_TYPES:
                continue
            key = f"{job['job_type']}:{job['state']}"
            report[key] = report.get(key, 0) + 1
        return report

    async def _authorize_job(self, job: Mapping[str, Any]) -> ExecutionGrant | None:
        """派发门禁（V03-0.3，I05/I06）：无有效凭据的任务不派发、不运行。

        - 无凭据（旧自动队列 / 事件派生）：标 ``historical_paused`` 等作者；
        - 凭据无效（功能不匹配 / 预算耗尽 / 连接不可解析）：标 ``not_authorized``
          并携带错误码与三要素，同样不派发。
        返回 None 表示未放行（已暂停或状态不允许暂停）；两条入口共用：
        worker_loop 自动派发与 ``/agent-jobs/{id}/run`` 手动运行。
        """
        job_id = str(job["job_id"])
        grant_wire = job["payload"].get("grant")
        if not isinstance(grant_wire, Mapping) or not grant_wire:
            await self.jobs.pause(
                job_id,
                reason="historical_paused",
                details={
                    "job_type": job["job_type"],
                    "why": "0.3 起模型任务必须由作者显式启动；旧自动队列不派发",
                },
            )
            return None
        try:
            return authorize_dispatch(
                grant_wire,
                function=JOB_TYPE_FUNCTIONS.get(str(job["job_type"])),
                where=f"agent_job({job_id}).grant",
            )
        except HastingsError as exc:
            await self.jobs.pause(
                job_id,
                reason="not_authorized",
                details={
                    "job_type": job["job_type"],
                    "error_code": exc.code,
                    "error": exc.message,
                },
            )
            return None

    async def _execute_job(self, job_id: str) -> None:
        try:
            await self.execute_agent_job(job_id)
        except (HastingsError, ValueError, OSError) as exc:
            # 分析提交故障保留结果待重放；不丢掉已完成的 Worker 输出。
            self.last_worker_error = f"{type(exc).__name__}: {exc}"

    async def execute_agent_job(self, job_id: str) -> dict[str, Any]:
        job = await self.jobs.get(job_id)
        if job is None:
            raise ValueError("Job 不存在")
        # 派发门禁（V03-0.3）：无有效凭据不运行、不部分运行。
        grant = await self._authorize_job(job)
        if grant is None:
            current = await self.jobs.get(job_id)
            return dict(current if current is not None else job)
        project_id = str(job["payload"]["project_id"])
        lock = self.project_execution_lock(project_id)
        async with lock:
            current = await self.jobs.get(job_id)
            assert current is not None
            if current["state"] == "running":
                return current
            result = await self.worker.execute(job_id)
            if result["state"] == "completed" and result["job_type"] in (
                "auditor",
                "style_observer",
            ):
                analysis = await self.agent_workflows.apply_analysis(job_id)
                await self.jobs.record_analysis(job_id, analysis)
                result = await self.jobs.get(job_id) or result
            return result

    def project_execution_lock(self, project_id: str) -> asyncio.Lock:
        return self._job_project_locks.setdefault(project_id, asyncio.Lock())

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
                await self.classify_legacy_auto_jobs()
                with suppress(HastingsError):
                    await self.task_projector.sync()
                self._active_jobs = {jid: t for jid, t in self._active_jobs.items() if not t.done()}
                active_projects = set()
                for jid in self._active_jobs:
                    active = await self.jobs.get(jid)
                    if active:
                        active_projects.add(str(active["payload"]["project_id"]))
                for job in await self.jobs.list_jobs():
                    project_id = str(job["payload"]["project_id"])
                    due = (
                        not job["retry_at"]
                        or datetime.fromisoformat(job["retry_at"]) <= self.clock.now()
                    )
                    if (
                        job["state"] in ("queued", "retryable_failed")
                        and job["job_type"] != "manuscript.analysis"
                        and due
                        and job["job_id"] not in self._active_jobs
                        and len(self._active_jobs) < 2
                        and project_id not in active_projects
                    ):
                        # 派发门禁（V03-0.3）：选择阶段即拦截无凭据任务，
                        # 暂停一次后状态离开 queued/retryable_failed，不会反复重试。
                        if await self._authorize_job(job) is None:
                            continue
                        self._active_jobs[job["job_id"]] = asyncio.create_task(
                            self._execute_job(job["job_id"])
                        )
                        active_projects.add(project_id)
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
            chapter_ids = frozenset(entry_id for entry_id, _chapter in _chapters(catalog))
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

    def _grant_for_draft(self, project_id: str, draft: Any, key: str) -> dict[str, Any]:
        """任务草稿启动 → 显式凭据（V03-0.3）。

        连接快照只含非敏感字段（profile/model/api），绝不含 Key；connection
        revision 的精确跟踪随 V03-1.3 连接同源落地，当前记 0。
        """
        binding = self.model_binding_provider()
        connection = ConnectionSnapshot(
            connection_id=str(binding.get("profile") or ""),
            revision=0,
            provider=str(binding.get("api") or binding.get("profile") or ""),
            model=str(binding.get("model") or ""),
        )
        function = TASK_KIND_FUNCTIONS.get(str(draft.task_kind))
        if function is None:  # fail closed：未登记任务类型不生成凭据
            raise ViolationError(
                code="EXECUTION_NOT_AUTHORIZED",
                where="task.start.grant",
                why=f"任务类型 {draft.task_kind!r} 没有登记功能映射",
                fix="使用 plan / chapter_draft / check；其他类型由作者显式授权后登记",
            )
        grant = build_grant(
            # grant_id 只由操作键派生：同一 idempotency_key 的任意次启动
            # （含卡片绑定丢失后的重放）携带同一凭据，JobStore 指纹才能
            # 匹配并找回既有 Job，不重复创建（V03-0.3 重放契约）。
            grant_id=f"grant_{content_hash([key])[:16]}",
            source="author_message" if draft.source_message_id else "author_button",
            source_message_id=draft.source_message_id,
            function=function,
            analysis_scope=tuple(draft.target_ids),
            write_scope=None,  # 起草/审计只产生提案；正式写入走统一发布（另需授权）
            connection=connection,
        )
        return grant.to_wire()

    async def start_task_job(self, project_id: str, draft: Any, key: str) -> str:
        """TaskDraft → 既有工作流映射（§4.3）：与 /agent-jobs 同一授权与调度。

        定向修订仅打开 Change 用例，继续经影响分析、批准与差异审阅。
        """
        pid = ProjectId(project_id)
        app = self.agent_workflows
        targets = tuple(draft.target_ids)
        # 启动即定格模型快照（§19.5）：未配置在此明确失败，不悬挂任务。
        binding = self.model_binding_provider()
        instruction = draft.instruction
        if draft.refs:
            instruction += "\n\n作者明确附加的引用材料：\n" + "\n\n".join(
                f"[{r.get('title', '引用')} · {r.get('version', '未保存摘录')}]\n"
                f"{r.get('excerpt', '')}"
                for r in draft.refs
            )
        head = self.head(pid)
        if head is None:
            branch = self.service.get_branch(pid, _BRANCH_NAME)
            if branch is not None and draft.task_kind == "plan":
                head = self.service.commit(
                    CommitRequest(
                        pid,
                        branch.id,
                        None,
                        Actor("human", "author"),
                        "agent-workspace:init",
                        "初始化创作工作流",
                        changes=(
                            ArtifactChange(
                                ArtifactId(f"{project_id}:workspace"),
                                {"project_id": project_id},
                                kind="agents.workspace",
                            ),
                        ),
                    )
                ).commit_id
        if head is None:
            raise ViolationError(
                code="TASK_NO_BASE",
                where="task.start",
                why="项目还没有任何提交，没有可依据的内容版本",
                fix="先在创作台导入或保存一章正文，再启动任务",
            )
        base = draft.base_version or head.value
        if draft.task_kind == "targeted_revision":
            await self.task_prep.record_binding(project_id, draft.draft_id, binding)
            result = self.changes.execute(
                change_set_command(
                    open_change_set,
                    intent=instruction or "修订指定范围",
                    source="manual",
                    base_commit_id=base,
                    seed_changes=tuple(SeedChange(artifact_id=t) for t in targets),
                    scope=ChangeScope(),
                    actor=Actor("human", "author"),
                    at=self.clock.now(),
                    reason="作者从对话显式发起定向修订",
                ),
                project_id=pid,
                idempotency_key=f"task-change:{draft.draft_id}:{key}",
                summary="从对话打开变更流程",
            )
            return "change:" + str(result.change.artifact_changes[0].content["change_set_id"])
        # 显式启动凭据（V03-0.3，I05）：任务卡「开始」即作者按钮授权；
        # 从作者消息发起的草稿带 source_message_id，可回查到触发消息。
        grant = self._grant_for_draft(project_id, draft, key)
        if draft.task_kind == "plan":
            jid = await app.plan(
                pid, base, targets, key, instruction, model_binding=binding, grant=grant
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
                pid, base, targets, chapter_id, key, instruction, model_binding=binding, grant=grant
            )
        elif draft.task_kind == "check":
            if not targets:
                snapshot = self.service.get_snapshot(pid, CommitId(base))
                targets = tuple(
                    v.artifact.id.value
                    for v in snapshot.artifacts.values()
                    if v.artifact.kind.startswith(("story.", "narrative."))
                )
            jid = await app.audit(pid, base, targets, key, model_binding=binding, grant=grant)
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

    async def task_fact(self, reference: str) -> dict[str, Any] | None:
        if not reference.startswith("change:"):
            return await self.jobs.get(reference)
        csid = reference.removeprefix("change:")
        drafts = await self.task_draft_store.list_by_status("started")
        draft = next((d for d in drafts if d.job_ref == reference), None)
        if draft is None:
            return None
        pid = ProjectId(draft.project_id)
        head = self.head(pid)
        if head is None:
            return None
        artifact = self.service.get_snapshot(pid, head).artifacts.get(ArtifactId(csid))
        if artifact is None or artifact.revision is None:
            return None
        status = str(artifact.revision.content.get("status", "opened"))
        labels = {
            "opened": "待分析",
            "analyzing": "分析中",
            "analyzed": "待批准",
            "approved": "已批准",
            "executing": "修订中",
            "review": "待审阅",
            "committed": "已提交",
            "failed": "执行失败",
            "abandoned": "已放弃",
        }
        return {
            "job_type": "change",
            "state": "completed" if status == "committed" else "waiting",
            "stage": "变更流程：" + labels.get(status, status) + "；在变更中心继续",
            "payload": {"model_binding": draft.model_binding or {}},
            "change_ref": csid,
        }

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
            if kind not in ("chapter", "artifact", "selection"):
                raise ViolationError(
                    code="CHAT_REF_NOT_FOUND",
                    where="chat.refs.kind",
                    why=f"未知引用类型 {kind!r}",
                    fix="重新添加章节、故事对象或选文引用",
                )
            if kind == "selection":
                if target not in chapter_ids:
                    raise ViolationError(
                        code="CHAT_REF_NOT_FOUND",
                        where="chat.refs.selection",
                        why="选文不属于当前项目的章节",
                        fix="重新选择当前项目的正文",
                    )
                continue
            assert head is not None
            if kind == "chapter":
                version = self.queries.chapter_base(pid, head, target)
                excerpt = self.queries.chapter_text(pid, head, target)
            else:
                artifact = self.service.get_snapshot(pid, head).artifacts[ArtifactId(target)]
                assert artifact.revision is not None
                version = artifact.revision.id.value
                excerpt = json.dumps(artifact.revision.content, ensure_ascii=False)
            if ref.get("version") and ref["version"] not in (version, head.value):
                raise ViolationError(
                    code="CHAT_REF_VERSION_CONFLICT",
                    where="chat.refs.version",
                    why="引用基础版本已过期，不能用当前内容冒充旧引用",
                    fix="移除旧引用并重新添加当前版本",
                    details={"target_id": target},
                )
            ref.update(version=version, saved=True, excerpt=excerpt)

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
class EntryRequest(BaseModel):
    """无作品入口请求（M02/V03-1.2）。"""

    entry_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    mode: str = "save_idea"
    title: str = ""


class ProjectCreateRequest(BaseModel):
    name: str


class ImportChapterRequest(BaseModel):
    idempotency_key: str
    title: str
    text: str
    order_key: str = "0001"


class ImportPreviewRequest(BaseModel):
    """上传原始字节（base64）：解码与编码错误由服务端如实报告。"""

    filename: str
    content_base64: str


class ImportSelectionRequest(BaseModel):
    expected_revision: int
    mode: str = "append"
    replacements: list[dict[str, Any]] = Field(default_factory=list)


class ImportConfirmRequest(BaseModel):
    preview_hash: str
    duplicate_decision: str | None = None
    operation_id: str | None = None


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
    from hastings.interfaces.api.execution_log import create_execution_log_router

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # 启动恢复（§19.3、V03-0.3）：遗留的排队/运行轮次与执行中任务标为中断，
        # 不自动重发模型调用（I06/I11）；已完成结果回读，无模型投影继续。
        await runtime.chat.mark_interrupted()
        await runtime.jobs.mark_interrupted()
        task = asyncio.create_task(runtime.worker_loop())
        try:
            yield
        finally:
            await runtime.chat.shutdown()
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    router = APIRouter(prefix="/api/v2", tags=["v2"], lifespan=lifespan)

    @router.get("/capabilities")
    def capabilities() -> dict[str, Any]:
        """统一能力公布（V03-0.7 草案）：格式/上限/性能目标/功能状态。

        前端只消费本接口，不另写常量；excluded 功能不公布为可用。
        """
        return capabilities_payload()

    @router.post("/entries")
    async def create_entry(req: EntryRequest) -> dict[str, Any]:
        """无作品入口（M02/V03-1.2）：预约 + 无模型保存想法。

        ``mode=save_idea`` 只落 author 消息（not_requested），不启动模型；
        发送模式（明确授权后启动）在 V03-1.5「接着讨论」落地。
        """
        if req.mode != "save_idea":
            raise ViolationError(
                code="ENTRY_MODE_UNSUPPORTED",
                where="entries.create",
                why=f"入口模式 {req.mode!r} 尚未开放（为什么错：发送必须由作者显式授权）",
                fix="先用 save_idea 保存想法；从已保存消息继续讨论见「接着讨论」",
                details={"mode": req.mode},
            )
        saved = await runtime.entries.save_author_once(
            req.entry_id, req.request_id, req.text, title=req.title or "新想法"
        )
        return {
            "entry_id": saved.target.entry_id,
            "project_id": saved.target.project_id,
            "conversation_id": saved.target.conversation_id,
            "message_id": saved.message_id,
            "status": saved.status,
        }

    @router.get("/projects/{project_id}/operations/{operation_id}")
    def get_operation(project_id: str, operation_id: str) -> dict[str, Any]:
        """按 operation_id 找回操作结果（V03-2.1）。

        只依赖仓库（回执 + 记录 Artifact），不依赖对话投影：投影失败或
        尚未完成时本查询仍可定位结果，展示层显示「同步中」。
        """
        return runtime.operations.get_operation(ProjectId(project_id), operation_id).to_wire()

    @router.post("/projects")
    def create_project(req: ProjectCreateRequest) -> dict[str, Any]:
        project = runtime.create_project(req.name)
        return {"project_id": project.value, "name": req.name}

    @router.get("/projects/{project_id}/manuscript")
    def get_manuscript(project_id: str) -> dict[str, Any]:
        return _manuscript_payload(runtime, ProjectId(project_id))

    # -- 正文导入（V03-2.3）：预览 → 调整目标 → 整批确认 -------------------

    @router.post("/projects/{project_id}/imports/previews", status_code=201)
    def create_import_preview(project_id: str, req: ImportPreviewRequest) -> dict[str, Any]:
        """只解析并生成预览；不写正式正文（计划 M06 第 1 条）。"""
        try:
            data = base64.b64decode(req.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ViolationError(
                code=ms_ec.IMPORT_ENCODING_UNSUPPORTED,
                where="imports.preview",
                why=f"content_base64 不是合法 base64：{exc}",
                fix="上传原始文件字节的 base64 编码（不要先自行转成文本）",
            ) from exc
        return runtime.imports.create_preview(project_id, req.filename, data)

    @router.get("/projects/{project_id}/imports/previews/{preview_id}")
    def get_import_preview(project_id: str, preview_id: str) -> dict[str, Any]:
        return runtime.imports.get_preview(project_id, preview_id)

    @router.patch("/projects/{project_id}/imports/previews/{preview_id}")
    def update_import_preview(
        project_id: str, preview_id: str, req: ImportSelectionRequest
    ) -> dict[str, Any]:
        """调整新增/替换目标；替换必须给目标章与当前章基础。"""
        return runtime.imports.update_selection(
            project_id,
            preview_id,
            expected_revision=req.expected_revision,
            mode=req.mode,
            replacements=req.replacements,
        )

    @router.post("/projects/{project_id}/imports/previews/{preview_id}/confirm")
    def confirm_import_preview(
        project_id: str, preview_id: str, req: ImportConfirmRequest
    ) -> dict[str, Any]:
        """整批确认：一次提交，失败无半份正文；重试不重复建章。"""
        return runtime.imports.confirm(
            project_id,
            preview_id,
            preview_hash=req.preview_hash,
            duplicate_decision=req.duplicate_decision,
            operation_id=req.operation_id,
        )

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
        result = await runtime.manuscript_publisher.publish_save(
            project_id=ProjectId(project_id),
            chapter_id=chapter_id,
            operations=intent.operations,
            full_text=intent.full_text,
            expected_chapter_base=intent.expected_chapter_base,
            operation_id=(
                "op_"
                + content_hash(["chapter.save", project_id, chapter_id, req.idempotency_key])[:16]
            ),
            summary=req.summary,
        )
        await runtime.wake_workers(project_id)
        head = runtime.head(ProjectId(project_id))
        chapter_base = result.change.chapter_base_after
        if not chapter_base and head is not None:
            chapter_base = runtime.queries.chapter_base(ProjectId(project_id), head, chapter_id)
        return {
            "status": result.status,
            "commit_id": result.commit_id,
            "new_head": result.new_head,
            "summary": result.summary,
            "warnings": list(result.warnings),
            "chapter_base": chapter_base,
            "operation_id": result.operation_id,
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
    router.include_router(create_execution_log_router(runtime))
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

    app = FastAPI(title="Hastings", version=__version__)
    app.include_router(create_v2_router(runtime))
    app.include_router(create_v2_ws_router(runtime))
    register_v2_error_handlers(app)
    return app
