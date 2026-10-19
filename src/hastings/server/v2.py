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
from datetime import datetime
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from hastings import __version__
from hastings.application.capabilities import capabilities_payload
from hastings.application.contexts.collector import (
    DEFAULT_BUDGET_TOKENS,
    MAX_BUDGET_TOKENS,
    BackgroundService,
)
from hastings.application.errors import (
    EnvironmentFailure,
    HastingsError,
    ResultCategory,
    ViolationError,
)
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
from hastings.application.ports.chat import ChatRun
from hastings.application.ports.events import EventOutbox
from hastings.application.queries.chapter_history import ChapterHistoryQueries
from hastings.application.queries.desk_state import DeskStateQueries
from hastings.application.queries.followups import FollowupQueries
from hastings.application.queries.graphs import GraphQueries
from hastings.application.queries.impact import ImpactQueries
from hastings.application.queries.ledger import LedgerQueries
from hastings.application.queries.ledger_forms import ledger_schema
from hastings.application.queries.manuscript import ManuscriptQueries
from hastings.application.services.agreement_service import AgreementService
from hastings.application.services.backup_service import BackupService
from hastings.application.services.conversation_service import ConversationService
from hastings.application.services.disclosure_service import DisclosureService
from hastings.application.services.drafting_service import DraftingService, WrittenChapter
from hastings.application.services.edit_session_service import EditSessionService
from hastings.application.services.entry_service import EntryService
from hastings.application.services.evidence_service import EvidenceService
from hastings.application.services.import_service import ImportService
from hastings.application.services.intent_service import IntentService
from hastings.application.services.ledger_candidate_service import LedgerCandidateService
from hastings.application.services.ledger_service import LedgerService
from hastings.application.services.operation_service import OperationService
from hastings.application.services.publish_manuscript_change import PublishManuscriptChange
from hastings.application.services.revision_execution_service import RevisionExecutionService
from hastings.application.services.revision_publish_service import RevisionPublishService
from hastings.application.services.revision_task_service import RevisionTaskService
from hastings.application.services.revision_work_service import RevisionWorkService
from hastings.application.services.suggestion_service import SuggestionService
from hastings.application.services.summary_service import SummaryDraft, SummaryService
from hastings.application.services.task_prep_service import TaskPrepService
from hastings.application.services.task_projection import ChatTaskProjector
from hastings.application.services.undo_service import UndoService
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
from hastings.application.workflows.summary_jobs import SUMMARY_JOB_TYPE, SummaryJobRunner
from hastings.application.workflows.worker_job import WorkerJobRunner
from hastings.domain.author_model.errors import AuthorModelViolation
from hastings.domain.changes.commands import open_change_set
from hastings.domain.changes.errors import ChangesViolation
from hastings.domain.changes.models import ChangeScope, SeedChange
from hastings.domain.conversation.task_drafts import ProjectView
from hastings.domain.manuscript import error_codes as ms_ec
from hastings.domain.manuscript.catalog import ManuscriptCatalog, next_order_keys
from hastings.domain.manuscript.commands import import_chapter as import_chapter_command
from hastings.domain.manuscript.errors import ManuscriptViolation
from hastings.domain.manuscript.models import Chapter
from hastings.domain.manuscript.operation_record import (
    creation_log_conversation_id,
    operation_reference_message_id,
)
from hastings.domain.manuscript.operations import operation_from_content
from hastings.domain.repository import error_codes as repo_ec
from hastings.domain.repository.ids import Actor, ArtifactId, CommitId, ProjectId
from hastings.domain.repository.requests import ArtifactChange, CommitRequest
from hastings.domain.repository.serialization import content_hash
from hastings.domain.revision.errors import CoverageViolation
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
from hastings.interfaces.api.revision import create_revision_router
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
        # 启动恢复摘要（V03-8.1）：lifespan 写入，GET /recovery 只读取证。
        self.last_recovery: dict[str, Any] | None = None
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
        # 背景装配（V03-4.2）：约定→核心→概览→目录→目标，只读、按版本取材料。
        # 先于对话与试写装配：这两条路径共用同一份背景，避免各拼一套。
        self.background = BackgroundService(self.service)
        # 披露边界（V03-4.7）：按叙事位置算「谁已经知道什么」，只读。
        self.disclosure = DisclosureService(self.service)
        self.chat = ConversationService(
            self.conversation_store,
            runner_factory=v2_runner.chat_runner,
            model_resolver=v2_runner.chat_model,
            ref_validator=self.validate_refs,
            execution_log=self.execution_log,
            background_provider=lambda pid: self.background.render(pid, scope="discussion"),
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
        # 意图分类（V03-7.1）：作者文本/消息 → 讨论/候选/执行三分与范围；
        # 只读，复用同一 conversation 与 draft 存储判断「同范围不重复审批」。
        self.intents = IntentService(self.conversation_store, self.task_draft_store)
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
        # 手工修改副本（V03-2.4）：唯一未结束副本、保存序号 CAS、完成走统一发布。
        self.edit_sessions = EditSessionService(
            self.service, self.manuscript_publisher, self.operations, ids, self.clock
        )
        # 正文导入预览与确认（V03-2.3）：预览落辅助表，确认走统一发布；
        # 与未结束修改副本重叠的替换在确认时拒绝（M06 第 3 条）。
        self.imports = ImportService(
            self.service,
            self.manuscript_publisher,
            ids,
            self.clock,
            edit_sessions=self.edit_sessions,
        )
        self.restore = RestoreChapterVersion(
            self.service, self.manuscript_publisher, self.edit_sessions
        )
        # 章版本历史与证据核对（V03-2.5）：只读历史 + 人工重锚。
        self.chapter_history = ChapterHistoryQueries(self.service)
        self.evidence = EvidenceService(self.service, self.operations)
        # 操作后的派生状态（V03-2.6）：摘要是否待更新、建议是否已整理。
        self.followups = FollowupQueries(self.service)
        # 操作影响（V03-7.7）：按实际版本差异回读改动范围、后续修改与关联采用。
        self.impact = ImpactQueries(self.service, self.operations, self.edit_sessions)
        # 备份预览与恢复（V03-8.2）：覆盖范围显式声明，恢复只经统一写入路径。
        self.backup = BackupService(self.service, self.service, self.manuscript_publisher)
        # 账本只读查询（V03-3.3）：试写引用与前端设定清单共用同一投影。
        self.ledger = LedgerQueries(self.service)
        # 统一图谱投影（V03-6.1）：节点/边固定同一 Commit，只读、无第二份事实源。
        self.graphs = GraphQueries(self.service)
        # 账本直接保存（V03-3.2）：作者显式创建/修订，一次保存一个原子 Commit。
        self.ledger_entries = LedgerService(self.service, ids)
        # 设定候选成组（V03-3.2）：暂存在辅助表，选中后一次提交，假设不入账。
        self.ledger_candidates = LedgerCandidateService(self.service, ids, self.clock)
        # 工作台共享状态（V03-4.5）：侧栏只拿计数与权威 ID，不另存一份列表。
        self.desk_state = DeskStateQueries(self.service, self.ledger_candidates, self.edit_sessions)
        # 试写候选（V03-3.3/3.4）：账本+作者要求生成候选，只读预览，
        # 按预览哈希采用；采用走统一发布（V03-3.5）。
        self.drafting = DraftingService(
            self.service,
            self.manuscript_publisher,
            self.edit_sessions,
            ids,
            self.clock,
            writer=self._chapter_writer,
            background=self.background,
            disclosure=self.disclosure,
        )
        # 本书约定（V03-4.1）：作者显式维护的作品级要求，版本化、无模型。
        self.agreements = AgreementService(self.service, ids)
        # 章摘要（V03-4.3）：只由作者显式启动；生成前冻结来源，迟到结果不落地。
        self.summaries = SummaryService(
            self.service, ids, self.clock, summarizer=self._summary_writer
        )
        # 摘要任务化（V03-4.3 收尾）：与写作任务共用任务表，但走自己的执行器
        # （摘要没有正文可写范围，不需要写作任务的授权与租约）。
        self.summary_jobs = SummaryJobRunner(self.jobs, self.summaries, self.clock)
        # 账本更新建议（V03-4.4）：显式启动，暂存进候选组，采用时核对来源。
        self.suggestions = SuggestionService(
            self.service,
            self.ledger_candidates,
            self.clock,
            proposer=self._suggestion_writer,
        )
        # 基础关联撤销（V03-4.6）：预览只读；写入走统一发布器，多章一次提交。
        self.undo = UndoService(
            self.service, self.operations, self.manuscript_publisher, self.edit_sessions
        )
        # 整体调整工作版本与原文覆盖台账（V03-7.2）：绑定正式 head、声明章节集合，
        # 逐章记录实际读取与未覆盖；只读投影同时作为「全局上下文」回读，不另存事实。
        self.revision_work = RevisionWorkService(self.service, self.ids, self.clock)
        # 有限执行任务（V03-7.3）：预算、修正轮次与等待/停止/继续；同一作品
        # 只允许一个进行中任务，新任务必须先继续或停止（防绕预算）。
        self.revision_tasks = RevisionTaskService(
            self.service, self.revision_work, self.ids, self.clock
        )
        # 内部工作版本暂存与统一发布（V03-7.4）：正文与账本进同一个 Commit；
        # 活动副本、基础前进、未覆盖与预算耗尽都整体拒绝，不产生半套版本。
        self.revision_publish = RevisionPublishService(
            self.service,
            self.revision_tasks,
            self.revision_work,
            self.manuscript_publisher,
            self.edit_sessions,
            self.ids,
            self.clock,
        )
        # 执行循环（V03-7.4 留白接线）：作者显式触发一轮，逐章记账→读章→改稿→
        # 暂存→记覆盖；预算不足自动落 waiting，不产生半套版本。
        self.revision_exec = RevisionExecutionService(
            self.service,
            self.revision_tasks,
            self.revision_work,
            self.revision_publish,
            self.drafting,
            self.clock,
        )
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

    async def ensure_conversation(self, project_id: str, conversation_id: str | None) -> None:
        """来源讨论必须是本作品里存在的会话（V03-4.5）。

        宁可在入口报错，也不要把一条结果静默归到一个不存在或别人的讨论里：
        归属错了以后，作者在两处都找不到它。
        """
        if not conversation_id:
            return
        conversation = await self.conversation_store.get_conversation(conversation_id)
        if conversation is None:
            raise ViolationError(
                code="CONVERSATION_NOT_FOUND",
                where="operation.origin_conversation_id",
                why=f"讨论 {conversation_id!r} 不存在",
                fix="用当前作品的会话列表选择一个存在的讨论，或不传该字段",
                details={"conversation_id": conversation_id},
            )
        if conversation.project_id != project_id:
            raise ViolationError(
                code="CONVERSATION_NOT_FOUND",
                where="operation.origin_conversation_id",
                why=f"讨论 {conversation_id!r} 属于别的作品",
                fix="结果只能归到本作品的讨论；不传该字段时自动回落「创作记录」",
                details={"conversation_id": conversation_id},
            )

    async def projection_state(
        self, project_id: str, operation_id: str, conversation_id: str | None
    ) -> dict[str, Any]:
        """这次操作的讨论投影到什么程度（V03-4.5）。

        只看固定归属会话里有没有那条引用消息：有就是已投影，没有就是待同步。
        投影由 Outbox 消费者补写，崩溃或重启后 worker 循环会继续补——这里只读，
        不触发任何写入，也不因为「没查到」就判失败。
        """
        target = conversation_id or creation_log_conversation_id(project_id)
        message_id = operation_reference_message_id(operation_id)
        stored = await self.conversation_store.get_message(message_id)
        return {
            "state": "projected" if stored is not None else "pending",
            "conversation_id": target,
            "message_id": message_id,
        }

    async def _run_text(self, *, system: str, prompt: str) -> tuple[str, str]:
        """一次无工具的单轮模型调用：返回 (文本, 实际使用的模型)。

        模型来源在调用时解析（作者可能中途换模型）；失败按环境错误如实上报。
        """
        binding = self._v2_runner.model_binding()
        model = str(binding.get("model", ""))
        runner = self._v2_runner.bound_chat_runner(binding)

        async def _sink(_delta: str) -> None:
            return None

        text = await runner.stream(
            ChatRun(
                model=model,
                system=system,
                history=(("author", prompt),),
                cancelled=lambda: False,
            ),
            _sink,
        )
        return text, model

    async def _chapter_writer(self, *, system: str, prompt: str) -> WrittenChapter:
        """试写生成器：复用当前模型选择与流式执行器，无工具、无写端口。"""
        text, model = await self._run_text(system=system, prompt=prompt)
        return WrittenChapter(text=text, model=model)

    async def _summary_writer(self, *, system: str, prompt: str) -> SummaryDraft:
        """摘要生成器：同一条模型通道；来源与版本由 SummaryService 另行固定。"""
        text, model = await self._run_text(system=system, prompt=prompt)
        return SummaryDraft(text=text, model=model)

    async def _suggestion_writer(self, *, system: str, prompt: str) -> tuple[str, str]:
        """建议整理器：返回原始 JSON 文本与模型标识；解析在领域层严格进行。"""
        return await self._run_text(system=system, prompt=prompt)

    async def wake_workers(self, project_id: str) -> None:
        self._wake_projects.add(project_id)

    #: 旧自动链路遗留的 Job 类型（V03-0.4 分类报告范围）。作者显式启动的
    #: auditor 等任务不在此列——它们带凭据，走正常派发。
    def restore_actor(self) -> Actor:
        """恢复写入的 Actor：作者本人（与手工修改同一身份，日志可归因）。"""
        return Actor(kind="human", id="author")

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

    async def recover_on_startup(self) -> dict[str, Any]:
        """启动恢复（V03-8.1 单一入口）：不自动重发任何模型调用。

        三件事按顺序做，且都只改状态不改内容：

        1. 执行中的对话轮次标中断（无法确认的模型调用不重跑）；
        2. 执行中的任务标中断等作者继续（I06/I11）；
        3. 旧自动队列遗留任务分类并暂停为 ``historical_paused``——0.3 起模型
           任务只能由作者显式启动，重启不能把它们复活。

        返回的摘要写进 ``last_recovery``，``GET /recovery`` 供演练取证。
        """
        chat_interrupted = await self.chat.mark_interrupted()
        tasks_interrupted = await self.jobs.mark_interrupted()
        legacy_paused = await self.classify_legacy_auto_jobs()
        report = {
            "chat_rounds_interrupted": chat_interrupted,
            "tasks_interrupted": tasks_interrupted,
            "legacy_paused": legacy_paused,
            "legacy_paused_total": sum(legacy_paused.values()),
        }
        self.last_recovery = {"report": report, "generated_at": self.clock.now().isoformat()}
        return report

    async def recovery_report(self) -> dict[str, Any]:
        """恢复取证（只读）：上次启动恢复摘要 + 旧队列快照 + 任务状态分布。"""
        states: dict[str, int] = {}
        waiting: list[str] = []
        for job in await self.jobs.list_jobs():
            state = str(job.get("state", "unknown"))
            states[state] = states.get(state, 0) + 1
            if state == "waiting":
                waiting.append(str(job["job_id"]))
        return {
            "last_recovery": self.last_recovery,
            "legacy_auto_snapshot": await self.legacy_auto_report(),
            "task_states": states,
            "waiting_task_ids": sorted(waiting),
            "note": "恢复只标记状态，不自动重发模型调用；等待中的任务由作者显式继续。",
        }

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
                    if job["job_type"] == SUMMARY_JOB_TYPE:
                        # 摘要任务：不受写作任务的并发与授权约束，单独派发。
                        # 同项目仍然串行（active_projects 已含该项目时跳过），
                        # 避免同一章两个任务同时读写摘要版本。
                        if (
                            job["state"] in ("queued", "retryable_failed")
                            and due
                            and job["job_id"] not in self._active_jobs
                            and project_id not in active_projects
                        ):

                            async def _run_summary(job_id: str = job["job_id"]) -> None:
                                await self.summary_jobs.execute(job_id)

                            self._active_jobs[job["job_id"]] = asyncio.create_task(_run_summary())
                            active_projects.add(project_id)
                        continue
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
    """单章导入：``order_key`` 不传时接在现有章之后，避免每章都落到 0001。"""

    idempotency_key: str
    title: str
    text: str
    order_key: str | None = None


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
    #: 发起这次导入的讨论；不给则归本作品「创作记录」（V03-4.5）。
    conversation_id: str | None = None


class SaveDraftRequest(BaseModel):
    expected_save_seq: int
    request_id: str
    text: str


class StartEditSessionRequest(BaseModel):
    """开始一轮手工修改（V03-5.1）：可同时声明本轮要改的章。

    声明是可选的：不声明就走有机流式——首次保存的章自动进入本轮清单。
    声明了的章必须在作品里存在，且完成前要全部保存过（flush）。
    """

    chapter_ids: list[str] = Field(default_factory=list)


class MergeChoice(BaseModel):
    """一处重叠冲突的选择（V03-5.2）：序号即上次 409 报告里该章冲突的顺序。"""

    chapter_id: str
    conflict: int
    choice: Literal["ours", "theirs"]


class FinishEditRequest(BaseModel):
    operation_id: str | None = None
    expected_save_sequences: dict[str, int] | None = None
    #: 发起本轮修改的讨论；不给则归本作品「创作记录」（V03-4.5）。
    conversation_id: str | None = None
    #: 重叠冲突的逐处选择：必须逐处覆盖本次重算出的冲突集合，否则带着新报告
    # 再次 409。不给而又有冲突时，完成被拒绝并返回事实（V03-5.2）。
    merge_choices: list[MergeChoice] = Field(default_factory=list)


class EditSnapshotRequest(BaseModel):
    """冻结某个已保存的草稿序号（可选段落选区），创建不可变引用（V03-5.3）。"""

    chapter_id: str
    save_seq: int
    #: 可选的期望文本哈希：与草稿当前内容不符时显式冲突，防止用旧界面冻结。
    text_hash: str | None = None
    start_paragraph: int | None = None
    end_paragraph: int | None = None


class ApplyCandidateToEditRequest(BaseModel):
    """把模型候选用进手工修改副本（V03-5.3）：只写副本，不发布正式正文。"""

    session_id: str
    expected_save_seq: int
    request_id: str
    preview_hash: str
    revision: int | None = None
    #: 只替换副本里的一段段落；不给表示整章替换。选区外的人工段落保留来源。
    start_paragraph: int | None = None
    end_paragraph: int | None = None
    allow_disclosure_change: bool = False


class UndoApplyRequest(BaseModel):
    """撤销选择（V03-5.4）：每个待选择项给 restore（恢复旧内容）或 keep。"""

    choices: dict[str, str] = Field(default_factory=dict)
    #: 预览令牌：预览之后 head 又变了，旧选择会被拒绝并要求重新预览。
    preview_token: str | None = None


class ReanchorRequest(BaseModel):
    quote: str | None = None


class DraftCreateRequest(BaseModel):
    """试写请求：目标 + 作者要求 + 可选账本引用（空项目也可用）。"""

    target: str = "new"
    chapter_title: str = ""
    instruction: str
    viewpoint: str = ""
    length_target: str = ""
    narrative_boundary: str = ""
    #: 叙事结构中的目标章：给了才有披露边界，没给就不猜。
    story_chapter_id: str = ""
    refs: list[str] = Field(default_factory=list)
    idempotency_key: str | None = None


class LedgerEntryCreateRequest(BaseModel):
    """直接保存账本条目：种类 + 字段 + 请求标识（重试不重复入账）。"""

    kind: str
    fields: dict[str, Any] = Field(default_factory=dict)
    request_id: str = Field(min_length=1)


class LedgerEntryUpdateRequest(BaseModel):
    """修订账本条目：只覆盖提供的字段；expected_revision 防止覆盖他人的新版本。"""

    fields: dict[str, Any] = Field(default_factory=dict)
    expected_revision: str | None = None
    request_id: str = Field(min_length=1)


class LedgerStageRequest(BaseModel):
    """暂存一条设定候选：尚未确认的内容不进入正式账本。"""

    kind: str
    fields: dict[str, Any] = Field(default_factory=dict)
    hypothesis: bool = False
    request_id: str = Field(min_length=1)


class LedgerCandidateAcceptRequest(BaseModel):
    """确认选中的候选：组版本防止基于旧列表确认，一次提交整组选中项。"""

    item_ids: list[str] = Field(default_factory=list)
    expected_revision: int
    request_id: str = Field(min_length=1)
    #: 可选：这次采用基于哪次整体操作（V03-7.7 因果关联）。
    source_operation_id: str | None = None


class LedgerCandidateSkipRequest(BaseModel):
    """暂不处理：只改候选组状态，不产生正式版本。"""

    item_ids: list[str] = Field(default_factory=list)
    expected_revision: int
    request_id: str = Field(min_length=1)


class AgreementCreateRequest(BaseModel):
    """新增本书约定：默认生效；与生效项同主题时必须 supersedes，否则用 pending_choice。"""

    statement: str = Field(min_length=1)
    topic: str = "other"
    scope: str = "all"
    status: str = "active"
    source_kind: str = "settings_edit"
    source_message_id: str = ""
    supersedes: list[str] = Field(default_factory=list)
    request_id: str = Field(min_length=1)


class AgreementUpdateRequest(BaseModel):
    """修订本书约定：只覆盖给出的字段；替换/移除都在这里表达。"""

    statement: str | None = None
    topic: str | None = None
    scope: str | None = None
    status: str | None = None
    source_message_id: str | None = None
    retired_reason: str | None = None
    supersedes: list[str] = Field(default_factory=list)
    expected_revision: str | None = None
    request_id: str = Field(min_length=1)


class DraftReviseRequest(BaseModel):
    instruction: str = ""
    expected_revision: int | None = None


class DraftAdoptRequest(BaseModel):
    preview_hash: str
    revision: int | None = None
    operation_id: str | None = None
    #: 发起本次试写的讨论；不给则归本作品「创作记录」（V03-4.5）。
    conversation_id: str | None = None
    #: 作者明确授权改变揭示安排：默认润色不扩大披露。
    allow_disclosure_change: bool = False


class DisclosureCheckRequest(BaseModel):
    """检查一段新稿是否扩大披露；基线是原文，不是边界本身。"""

    original: str
    candidate: str
    chapter_id: str | None = None
    beat_id: str | None = None
    commit_id: str | None = None
    authorized: bool = False


class RestoreBackupRequest(BaseModel):
    """恢复上传的备份 zip：``content_base64`` 是导出 zip 的字节。

    ``conflict_choice`` 只在出现 ID 冲突时起作用：``merge_missing`` 只导入缺失
    对象（不覆盖已有内容），``cancel`` 取消恢复。不给选择而存在冲突时整体拒绝。
    """

    content_base64: str = Field(min_length=1)
    #: 覆盖确认：备份与当前内容有差异时必须显式为 true，否则只列出差异不改写。
    confirm_overwrite: bool = False


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
    conversation_id: str | None = None


class SummaryStartRequest(BaseModel):
    """显式启动一次章摘要：request_id 复用同来源任务，guidance 只影响本次提示词。"""

    request_id: str = Field(min_length=1)
    guidance: str = ""


class SuggestionStartRequest(BaseModel):
    """显式整理某章的账本建议：零建议是合法结果，不建空卡片。"""

    request_id: str = Field(min_length=1)
    guidance: str = ""


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
    from hastings.interfaces.api.conversations import create_conversations_router
    from hastings.interfaces.api.execution_log import create_execution_log_router

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # 启动恢复（§19.3 / V03-0.3 / V03-8.1）：执行中的对话与任务标中断，旧自动
        # 队列任务分类暂停——只改状态，不自动重发任何模型调用（I06/I11）；已完成结果
        # 回读，无模型投影继续。摘要写入 runtime.last_recovery，GET /recovery 供演练取证。
        await runtime.recover_on_startup()
        task = asyncio.create_task(runtime.worker_loop())
        try:
            yield
        finally:
            await runtime.chat.shutdown()
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    router = APIRouter(prefix="/api/v2", tags=["v2"], lifespan=lifespan)

    @router.get("/recovery")
    async def get_recovery() -> dict[str, Any]:
        """启动恢复取证（V03-8.1，只读）：上次恢复摘要、旧队列快照、任务状态分布。

        恢复只标记状态、不自动重发模型调用；等待中的任务由作者显式继续。
        """
        return await runtime.recovery_report()

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
    async def get_operation(project_id: str, operation_id: str) -> dict[str, Any]:
        """按 operation_id 找回操作结果（V03-2.1）。

        只依赖仓库（回执 + 记录 Artifact），不依赖对话投影：投影失败或
        尚未完成时本查询仍可定位结果，展示层显示「同步中」。

        V03-2.6：附带确定性的后续状态（摘要是否待更新、建议是否已整理）。
        这里只报告状态，不创建任务、不调用模型。
        """
        envelope = runtime.operations.get_operation(ProjectId(project_id), operation_id)
        payload = envelope.to_wire()
        # 操作记录的来源/选择/段落来源（V03-5.2/5.3）：终态事实可回读。
        payload["refs"] = runtime.operations.record_refs(ProjectId(project_id), operation_id)
        payload.update(runtime.followups.states(project_id, envelope.changed_chapter_ids))
        payload["projection"] = await runtime.projection_state(
            project_id, operation_id, envelope.conversation_id
        )
        payload["result_url"] = f"/api/v2/projects/{project_id}/operations/{operation_id}"
        return payload

    @router.get("/projects/{project_id}/operations/{operation_id}/impact")
    def get_operation_impact(project_id: str, operation_id: str) -> dict[str, Any]:
        """影响高亮（V03-7.7）：实际版本差异、后续修改与关联采用。

        只报告差异，不评价修订质量；范围来自操作记录与 Commit 差异。
        """
        return runtime.impact.impact(project_id, operation_id)

    @router.get("/projects/{project_id}/operations/{operation_id}/undo")
    def preview_undo(project_id: str, operation_id: str) -> dict[str, Any]:
        """撤销预览（V03-4.6）：受影响章节、账本变化、冲突与阻断原因。

        只读：不写版本、不调用模型。能否撤销取决于操作之后有没有新修改。
        """
        return runtime.undo.preview(project_id, operation_id)

    @router.post("/projects/{project_id}/operations/{operation_id}/undo")
    def apply_undo(
        project_id: str,
        operation_id: str,
        req: UndoApplyRequest | None = None,
    ) -> dict[str, Any]:
        """执行撤销（V03-5.4）：按预览与逐项选择一次提交恢复正文与关联账本。

        有未结束副本、新建章节/账本，或仍有未选择的后续修改时零写入；
        选择覆盖后才发布，后续独立内容按选择保留。
        """
        return runtime.undo.apply(
            project_id,
            operation_id,
            choices=req.choices if req is not None else None,
            preview_token=req.preview_token if req is not None else None,
        )

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
    async def confirm_import_preview(
        project_id: str, preview_id: str, req: ImportConfirmRequest
    ) -> dict[str, Any]:
        """整批确认：一次提交，失败无半份正文；重试不重复建章。"""
        await runtime.ensure_conversation(project_id, req.conversation_id)
        return runtime.imports.confirm(
            project_id,
            preview_id,
            preview_hash=req.preview_hash,
            duplicate_decision=req.duplicate_decision,
            operation_id=req.operation_id,
            conversation_id=req.conversation_id,
        )

    # -- 手工修改副本（V03-2.4）：唯一未结束副本 + 保存序号 CAS -------------

    @router.post("/projects/{project_id}/edit-sessions")
    def start_edit_session(
        project_id: str, req: StartEditSessionRequest | None = None
    ) -> dict[str, Any]:
        """开始或恢复唯一未结束副本；已有则原样返回（不重复建副本）。

        可带 ``chapter_ids`` 声明本轮章节清单（V03-5.1）；不传 body 的旧
        客户端行为不变。
        """
        return runtime.edit_sessions.start(
            project_id, chapter_ids=list(req.chapter_ids) if req is not None else None
        )

    @router.get("/projects/{project_id}/edit-sessions/current")
    def current_edit_session(project_id: str) -> dict[str, Any]:
        return runtime.edit_sessions.current(project_id) or {
            "session": None,
            "drafts": [],
        }

    @router.get("/projects/{project_id}/edit-sessions/{session_id}")
    def get_edit_session(project_id: str, session_id: str) -> dict[str, Any]:
        return runtime.edit_sessions.get(project_id, session_id)

    @router.put("/projects/{project_id}/edit-sessions/{session_id}/chapters/{chapter_id}")
    def save_edit_draft(
        project_id: str,
        session_id: str,
        chapter_id: str,
        req: SaveDraftRequest,
    ) -> dict[str, Any]:
        """按章自动保存：expected_save_seq 不符即冲突（迟到响应不覆盖新文本）。"""
        return runtime.edit_sessions.save_draft(
            project_id,
            session_id,
            chapter_id,
            expected_save_seq=req.expected_save_seq,
            request_id=req.request_id,
            text=req.text,
        )

    @router.post("/projects/{project_id}/edit-sessions/{session_id}/snapshots", status_code=201)
    def create_edit_snapshot(
        project_id: str, session_id: str, req: EditSnapshotRequest
    ) -> dict[str, Any]:
        """冻结已保存的草稿序号（V03-5.3）：未落盘的界面缓冲不能冒充快照。"""
        return runtime.edit_sessions.create_snapshot(
            project_id,
            session_id,
            chapter_id=req.chapter_id,
            save_seq=req.save_seq,
            text_hash=req.text_hash,
            start_paragraph=req.start_paragraph,
            end_paragraph=req.end_paragraph,
        )

    @router.get("/projects/{project_id}/edit-sessions/{session_id}/snapshots/{snapshot_id}")
    def get_edit_snapshot(project_id: str, session_id: str, snapshot_id: str) -> dict[str, Any]:
        """读取不可变快照：副本继续前进也不改变已冻结的引用。"""
        return runtime.edit_sessions.get_snapshot(project_id, session_id, snapshot_id)

    @router.post("/projects/{project_id}/edit-sessions/{session_id}/suspend")
    def suspend_edit_session(project_id: str, session_id: str) -> dict[str, Any]:
        return runtime.edit_sessions.suspend(project_id, session_id)

    @router.post("/projects/{project_id}/edit-sessions/{session_id}/abandon")
    def abandon_edit_session(project_id: str, session_id: str) -> dict[str, Any]:
        return runtime.edit_sessions.abandon(project_id, session_id)

    @router.post("/projects/{project_id}/edit-sessions/{session_id}/finish")
    async def finish_edit_session(
        project_id: str, session_id: str, req: FinishEditRequest
    ) -> dict[str, Any]:
        """修改完成：整批一次发布，成功后关闭副本；重试不产生第二个版本。"""
        await runtime.ensure_conversation(project_id, req.conversation_id)
        return runtime.edit_sessions.finish(
            project_id,
            session_id,
            operation_id=req.operation_id,
            expected_save_sequences=req.expected_save_sequences,
            conversation_id=req.conversation_id,
            merge_choices=[choice.model_dump() for choice in req.merge_choices],
        )

    @router.get("/projects/{project_id}/ledger")
    def get_ledger(project_id: str) -> dict[str, Any]:
        """可引用的账本条目（只读）：人物、地点、前提、线索等，不含节拍。"""
        return {"entries": runtime.ledger.entries(ProjectId(project_id))}

    @router.get("/projects/{project_id}/graphs")
    def get_graphs(project_id: str, at_commit_id: str | None = None) -> dict[str, Any]:
        """统一图谱投影（只读）：节点/边带语义层与证据状态，固定到读取的 Commit。"""
        return runtime.graphs.graph(project_id, at_commit_id=at_commit_id)

    @router.get("/projects/{project_id}/graphs/characters")
    def get_character_graph(
        project_id: str,
        at_commit_id: str | None = None,
        perspective: str | None = None,
        at_beat_id: str | None = None,
        at_chapter_id: str | None = None,
        focus: str | None = None,
    ) -> dict[str, Any]:
        """人物图（只读）：真关系/认知分层，支持作者全知或角色视角、叙述位置与局部聚焦。

        ``at_beat_id`` 与 ``at_chapter_id`` 互斥：前者按节拍，后者先解析为该章内
        最后一个节拍，两者走同一折叠路径，不用章节下标冒充叙述位置。
        """
        return runtime.graphs.characters(
            project_id,
            at_commit_id=at_commit_id,
            perspective=perspective,
            at_beat_id=at_beat_id,
            at_chapter_id=at_chapter_id,
            focus=focus,
        )

    @router.get("/projects/{project_id}/graphs/clues")
    def get_clue_graph(
        project_id: str,
        at_commit_id: str | None = None,
        at_beat_id: str | None = None,
        at_chapter_id: str | None = None,
        focus: str | None = None,
    ) -> dict[str, Any]:
        """线索图（只读）：线索子图 + 计划/正文实现分离，支持叙述位置与局部聚焦。

        ``at_beat_id`` 与 ``at_chapter_id`` 互斥；「截至某章」先解析为该章内最后
        一个节拍。后文线索以 ``within_position=False`` 标出，不提前进入视图。
        """
        return runtime.graphs.clues(
            project_id,
            at_commit_id=at_commit_id,
            at_beat_id=at_beat_id,
            at_chapter_id=at_chapter_id,
            focus=focus,
        )

    @router.get("/projects/{project_id}/graphs/method")
    def get_method_graph(
        project_id: str,
        at_commit_id: str | None = None,
        focus: str | None = None,
    ) -> dict[str, Any]:
        """手法图（只读）：既有真相文字与关系子图 + 缺口 + 完整度。

        不新增步骤/条件/证据对象，也不按模型措辞补边；缺失只表示「尚未记录」。
        """
        return runtime.graphs.method(project_id, at_commit_id=at_commit_id, focus=focus)

    @router.get("/projects/{project_id}/graphs/timeline")
    def get_timeline_graph(
        project_id: str,
        at_commit_id: str | None = None,
        group_by: str = "character",
        focus: str | None = None,
    ) -> dict[str, Any]:
        """时间线（只读）：真实事件按角色/地点分轨，证词与认知单独成轨。

        时间原文显示、不排序；叙述位置只来自角色获知事件；``focus`` 只收窄范围。
        """
        return runtime.graphs.timeline(
            project_id,
            at_commit_id=at_commit_id,
            group_by=group_by,
            focus=focus,
        )

    @router.get("/projects/{project_id}/ledger/schema")
    def get_ledger_schema(project_id: str) -> dict[str, Any]:
        """账本编辑表单描述：种类、字段、控件与引用目标，前后端共用一份口径。"""
        del project_id  # 表单描述与具体作品无关；路径保持作品归属一致
        return {"kinds": ledger_schema()}

    @router.get("/projects/{project_id}/ledger/entries/{entry_id}")
    def get_ledger_entry(project_id: str, entry_id: str) -> dict[str, Any]:
        """单条账本详情：完整内容 + 当前 revision（编辑时作为 CAS 基础）。"""
        return runtime.ledger_entries.get_entry(project_id, entry_id)

    @router.post("/projects/{project_id}/ledger/entries", status_code=201)
    def create_ledger_entry(project_id: str, req: LedgerEntryCreateRequest) -> dict[str, Any]:
        """直接保存新条目：不运行模型，不重写正文，保存后回读真实版本。"""
        return runtime.ledger_entries.create_entry(
            project_id,
            kind=req.kind,
            fields=req.fields,
            request_id=req.request_id,
        )

    @router.patch("/projects/{project_id}/ledger/entries/{entry_id}")
    def update_ledger_entry(
        project_id: str, entry_id: str, req: LedgerEntryUpdateRequest
    ) -> dict[str, Any]:
        """修订条目：只覆盖提供的字段；revision 冲突时拒绝覆盖。"""
        return runtime.ledger_entries.update_entry(
            project_id,
            entry_id,
            fields=req.fields,
            expected_revision=req.expected_revision,
            request_id=req.request_id,
        )

    @router.get("/projects/{project_id}/ledger/candidates")
    def get_ledger_candidates(project_id: str) -> dict[str, Any]:
        """待确认的设定候选：独立于正式账本，假设不会自动入账。"""
        return runtime.ledger_candidates.view(project_id)

    @router.post("/projects/{project_id}/ledger/candidates/items")
    def stage_ledger_candidate(project_id: str, req: LedgerStageRequest) -> dict[str, Any]:
        """暂存一条候选：只写辅助表，不产生版本，不占用正式条目 ID。"""
        return runtime.ledger_candidates.stage(
            project_id,
            kind=req.kind,
            fields=req.fields,
            hypothesis=req.hypothesis,
            request_id=req.request_id,
        )

    @router.post("/projects/{project_id}/ledger/candidates/accept")
    def accept_ledger_candidates(
        project_id: str, req: LedgerCandidateAcceptRequest
    ) -> dict[str, Any]:
        """确认选中条目：一次提交、依赖闭合；未选中的继续留在候选组。"""
        return runtime.ledger_candidates.accept(
            project_id,
            item_ids=req.item_ids,
            expected_revision=req.expected_revision,
            request_id=req.request_id,
            source_operation_id=req.source_operation_id,
        )

    @router.post("/projects/{project_id}/ledger/candidates/skip")
    def skip_ledger_candidates(project_id: str, req: LedgerCandidateSkipRequest) -> dict[str, Any]:
        """暂不处理：只更新候选组状态，不写正式账本。"""
        return runtime.ledger_candidates.skip(
            project_id,
            item_ids=req.item_ids,
            expected_revision=req.expected_revision,
            request_id=req.request_id,
        )

    @router.get("/projects/{project_id}/context/background")
    def get_context_background(
        project_id: str,
        scope: str = "manuscript",
        target: Annotated[list[str] | None, Query()] = None,
        budget_tokens: int | None = None,
        include_temporary_text: bool = False,
        story_chapter_id: str | None = None,
    ) -> dict[str, Any]:
        """一次任务会看到的背景：约定、故事核心、概览、目录与目标展开。

        只读、按版本取；``include_temporary_text`` 为真时，缺失或过期摘要的目标章
        由当前正文临时顶替（标 ``temporary``，不写回正式摘要）。
        """
        if scope not in ("manuscript", "discussion"):
            raise ViolationError(
                code="BACKGROUND_SCOPE_INVALID",
                where="context.background.scope",
                why=f"未知的适用范围 {scope!r}",
                fix="scope 只接受 manuscript（正文写作）或 discussion（讨论回答）",
                details={"scope": scope},
            )
        tokens = budget_tokens if budget_tokens is not None else DEFAULT_BUDGET_TOKENS
        if tokens < 200 or tokens > MAX_BUDGET_TOKENS:
            raise ViolationError(
                code="BACKGROUND_BUDGET_INVALID",
                where="context.background.budget_tokens",
                why=f"预算 {tokens} 超出允许区间 200—{MAX_BUDGET_TOKENS}",
                fix="给出区间内的预算；过小无法保留约定与核心设定，过大会拖慢装配",
                details={"budget_tokens": tokens, "max": MAX_BUDGET_TOKENS},
            )
        try:
            return runtime.background.build(
                project_id,
                scope=scope,
                target_chapter_ids=list(target or []),
                budget_tokens=tokens,
                include_temporary_text=include_temporary_text,
                story_chapter_id=story_chapter_id,
            )
        except ValueError as exc:
            # 装配器在「必留层级装不下」时抛出 ValueError（纯函数不依赖应用错误类）；
            # 这里转成可用错误码，避免落到通用的 AGENT_INVALID_INPUT。
            raise ViolationError(
                code="BACKGROUND_BUDGET_TOO_SMALL",
                where="context.background",
                why=str(exc),
                fix="增大 budget_tokens，或减少生效约定与故事核心设定",
                details={"budget_tokens": tokens},
            ) from exc

    @router.get("/projects/{project_id}/desk-state")
    def get_desk_state(project_id: str) -> dict[str, Any]:
        """工作台共享状态：待确认数量 + 权威 ID（V03-4.5）。

        侧栏用它对账和跳转，条目正文一律回各自接口取；这里不返回任何列表正文，
        因此不会出现「侧栏里那份和建议列表不一样」。
        """
        return runtime.desk_state.states(project_id)

    @router.get("/projects/{project_id}/disclosure-boundary")
    def get_disclosure_boundary(
        project_id: str,
        chapter_id: str | None = None,
        beat_id: str | None = None,
        commit_id: str | None = None,
    ) -> dict[str, Any]:
        """某叙事位置上的知情与披露边界：读者已知、角色已知、不得断言、待核对。

        只给章或节拍之一；都不给时返回保守边界（不声称任何事实已披露）。
        """
        return runtime.disclosure.boundary(
            project_id, chapter_id=chapter_id, beat_id=beat_id, commit_id=commit_id
        )

    @router.post("/projects/{project_id}/disclosure-boundary/check")
    def check_disclosure_expansion(project_id: str, req: DisclosureCheckRequest) -> dict[str, Any]:
        """检查新稿有没有把后文事实提前写出来；``authorized`` 表示作者已明确授权。"""
        return runtime.disclosure.check(
            project_id,
            original=req.original,
            candidate=req.candidate,
            chapter_id=req.chapter_id,
            beat_id=req.beat_id,
            commit_id=req.commit_id,
            authorized=req.authorized,
        )

    @router.get("/projects/{project_id}/summaries")
    def list_summaries(project_id: str) -> dict[str, Any]:
        """当前版本的各章摘要状态：正文里已有摘要才能被背景使用。"""
        return runtime.summaries.list_summaries(project_id)

    @router.get("/projects/{project_id}/chapters/{chapter_id}/summary")
    def get_chapter_summary(project_id: str, chapter_id: str) -> dict[str, Any]:
        """单章摘要：正文、来源清单、模型与状态；没有生成过时 status=missing。"""
        return runtime.summaries.get(project_id, chapter_id)

    @router.post("/projects/{project_id}/chapters/{chapter_id}/summary")
    async def update_chapter_summary(
        project_id: str, chapter_id: str, req: SummaryStartRequest
    ) -> dict[str, Any]:
        """同步更新摘要：建任务后立即执行并等待结果（桌面端「点一下等它算完」）。

        与异步入口共用同一个执行器；失败按任务结果翻译成错误码，不静默成功。
        """
        submitted = await runtime.summary_jobs.submit(
            project_id,
            chapter_id,
            request_id=req.request_id,
            guidance=req.guidance,
        )
        # 同一 request_id 命中已完成任务：不再执行，直接回读首次结果。
        already_done = submitted["state"] == "completed"
        finished = await runtime.summary_jobs.execute(submitted["job_id"])
        result = _summary_sync_result(finished)
        if already_done:
            result["replayed"] = True
        return result

    @router.post("/projects/{project_id}/chapters/{chapter_id}/summary-jobs", status_code=202)
    async def submit_summary_job(
        project_id: str, chapter_id: str, req: SummaryStartRequest
    ) -> dict[str, Any]:
        """异步建任务：立刻拿到 job_id，关掉页面也会继续（worker 执行）。"""
        return await runtime.summary_jobs.submit(
            project_id,
            chapter_id,
            request_id=req.request_id,
            guidance=req.guidance,
        )

    @router.get("/projects/{project_id}/summary-jobs/{job_id}")
    async def get_summary_job(project_id: str, job_id: str) -> dict[str, Any]:
        """按作品作用域查询：别的作品的任务 ID 与不存在不可区分。"""
        return await runtime.summary_jobs.get(project_id, job_id)

    @router.post("/projects/{project_id}/summary-jobs/{job_id}/stop")
    async def stop_summary_job(project_id: str, job_id: str) -> dict[str, Any]:
        """停止：已进入提交前的执行会被拦下，不写摘要版本。"""
        return await runtime.summary_jobs.stop(project_id, job_id)

    @router.post("/projects/{project_id}/summary-jobs/{job_id}/retry")
    async def retry_summary_job(project_id: str, job_id: str) -> dict[str, Any]:
        """重试：失败任务放回队列；已停止的任务不允许重试。"""
        return await runtime.summary_jobs.retry(project_id, job_id)

    @router.get("/projects/{project_id}/suggestions")
    def list_suggestions(project_id: str) -> dict[str, Any]:
        """待确认的账本建议：与手工候选同一份数据，带生成来源。"""
        return runtime.suggestions.view(project_id)

    @router.post("/projects/{project_id}/chapters/{chapter_id}/suggestions")
    async def start_suggestions(
        project_id: str, chapter_id: str, req: SuggestionStartRequest
    ) -> dict[str, Any]:
        """显式整理本章建议：只暂存候选，不改正文、不写正式账本。"""
        return await runtime.suggestions.start(
            project_id,
            chapter_id,
            request_id=req.request_id,
            guidance=req.guidance,
        )

    @router.get("/projects/{project_id}/agreements")
    def list_agreements(project_id: str, include_retired: bool = False) -> dict[str, Any]:
        """本书约定：生效/待选择在前，已停用项按需展开（保历史，不物理删除）。"""
        return runtime.agreements.list(project_id, include_retired=include_retired)

    @router.get("/projects/{project_id}/agreements/snapshot")
    def get_agreement_snapshot(project_id: str, commit_id: str | None = None) -> dict[str, Any]:
        """某个版本的生效约定快照：任务绑定后，改约定不换已启动任务的输入。"""
        return runtime.agreements.snapshot(project_id, commit_id)

    @router.post("/projects/{project_id}/agreements", status_code=201)
    def create_agreement(project_id: str, req: AgreementCreateRequest) -> dict[str, Any]:
        """新增约定：不运行模型；同主题冲突返回 409，等作者选择替换或保留。"""
        return runtime.agreements.create(
            project_id,
            statement=req.statement,
            topic=req.topic,
            scope=req.scope,
            status=req.status,
            source_kind=req.source_kind,
            source_message_id=req.source_message_id,
            supersedes=req.supersedes,
            request_id=req.request_id,
        )

    @router.patch("/projects/{project_id}/agreements/{agreement_id}")
    def update_agreement(
        project_id: str, agreement_id: str, req: AgreementUpdateRequest
    ) -> dict[str, Any]:
        """修订/替换/移除：只覆盖给出的字段，一次提交内完成旧项停用。"""
        fields: dict[str, Any] = {}
        for name in ("statement", "topic", "scope", "status", "source_message_id"):
            value = getattr(req, name)
            if value is not None:
                fields[name] = value
        if req.retired_reason is not None:
            fields["retired_reason"] = req.retired_reason
        return runtime.agreements.update(
            project_id,
            agreement_id,
            fields=fields,
            expected_revision=req.expected_revision,
            supersedes=req.supersedes,
            request_id=req.request_id,
        )

    @router.post("/projects/{project_id}/drafts")
    async def create_draft(project_id: str, req: DraftCreateRequest) -> dict[str, Any]:
        """试写候选：从账本与作者要求出发；空项目是合法首章来源。"""
        return await runtime.drafting.create(
            project_id,
            target=req.target,
            chapter_title=req.chapter_title,
            instruction=req.instruction,
            viewpoint=req.viewpoint,
            length_target=req.length_target,
            narrative_boundary=req.narrative_boundary,
            story_chapter_id=req.story_chapter_id,
            refs=req.refs,
            idempotency_key=req.idempotency_key,
        )

    @router.get("/projects/{project_id}/drafts")
    def list_drafts(project_id: str) -> dict[str, Any]:
        return {"candidates": runtime.drafting.list_candidates(project_id)}

    @router.get("/projects/{project_id}/drafts/{candidate_id}")
    def get_draft(project_id: str, candidate_id: str) -> dict[str, Any]:
        return runtime.drafting.get(project_id, candidate_id)

    @router.post("/projects/{project_id}/drafts/{candidate_id}/revise")
    async def revise_draft(
        project_id: str, candidate_id: str, req: DraftReviseRequest
    ) -> dict[str, Any]:
        """继续调整：生成新修订，旧修订不再是可采用目标。"""
        return await runtime.drafting.revise(
            project_id,
            candidate_id,
            instruction=req.instruction,
            expected_revision=req.expected_revision,
        )

    @router.post("/projects/{project_id}/drafts/{candidate_id}/adopt")
    async def adopt_draft(
        project_id: str, candidate_id: str, req: DraftAdoptRequest
    ) -> dict[str, Any]:
        """按预览哈希采用：一次发布，迟到候选不能覆盖新稿或未结束副本。"""
        await runtime.ensure_conversation(project_id, req.conversation_id)
        return runtime.drafting.adopt(
            project_id,
            candidate_id,
            preview_hash=req.preview_hash,
            revision=req.revision,
            operation_id=req.operation_id,
            allow_disclosure_change=req.allow_disclosure_change,
            conversation_id=req.conversation_id,
        )

    @router.post("/projects/{project_id}/drafts/{candidate_id}/apply-to-edit-session")
    def apply_draft_to_edit_session(
        project_id: str, candidate_id: str, req: ApplyCandidateToEditRequest
    ) -> dict[str, Any]:
        """把候选用进手工修改副本（V03-5.3）：只写副本并标记生成来源。

        这是作者动作：候选不会自动写入副本；进副本后仍要「修改完成」才会
        发布正式正文。采用片段时选区外的人工段落保留原来源。
        """
        return runtime.drafting.apply_to_edit_session(
            project_id,
            candidate_id,
            session_id=req.session_id,
            expected_save_seq=req.expected_save_seq,
            request_id=req.request_id,
            preview_hash=req.preview_hash,
            revision=req.revision,
            start_paragraph=req.start_paragraph,
            end_paragraph=req.end_paragraph,
            allow_disclosure_change=req.allow_disclosure_change,
        )

    @router.post("/projects/{project_id}/drafts/{candidate_id}/discard")
    def discard_draft(project_id: str, candidate_id: str) -> dict[str, Any]:
        return runtime.drafting.discard(project_id, candidate_id)

    @router.post("/projects/{project_id}/chapters/import")
    def import_chapter(project_id: str, req: ImportChapterRequest) -> dict[str, Any]:
        """旧单章导入（V1 兼容语义）：V03-8.6 起与预览导入同一发布管线。

        旧入口不再直接提交领域命令——操作记录、证据/摘要派生与事件投影
        与「导入正文」完全一致；响应补 ``operation_id`` 供回执查询。
        """
        chapter_id = runtime.new_artifact_id()
        order_key = req.order_key
        if order_key is None:
            pid = ProjectId(project_id)
            head = runtime.head(pid)
            catalog = (
                ManuscriptCatalog.from_snapshot(runtime.service.get_snapshot(pid, head))
                if head is not None
                else None
            )
            order_key = next_order_keys(catalog, 1)[0] if catalog is not None else "0001"
        result = runtime.manuscript_publisher.publish_command(
            project_id=ProjectId(project_id),
            command=manuscript_command(
                import_chapter_command,
                chapter_id=chapter_id,
                title=req.title,
                text=req.text,
                order_key=order_key,
            ),
            operation_id=(
                "op_"
                + content_hash(["chapter.import", project_id, chapter_id, req.idempotency_key])[:16]
            ),
            kind="chapter.create",
            summary=f"导入「{req.title}」",
        )
        return {
            "status": result.status,
            "chapter_id": chapter_id,
            "commit_id": result.commit_id,
            "new_head": result.new_head,
            "summary": result.summary,
            "operation_id": result.operation_id,
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
    async def restore_chapter(
        project_id: str, chapter_id: str, req: RestoreChapterRequest
    ) -> dict[str, Any]:
        """把章恢复到历史 Commit 的版本：恢复本身是一次新 Commit（不重写历史）。"""
        await runtime.ensure_conversation(project_id, req.conversation_id)
        result = runtime.restore.restore(
            RestoreIntent(
                project_id=ProjectId(project_id),
                chapter_id=chapter_id,
                source_commit_id=CommitId(req.source_commit_id),
                idempotency_key=req.idempotency_key,
                expected_chapter_base=req.expected_chapter_base,
            ),
            conversation_id=req.conversation_id,
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
            "operation_id": result.operation_id,
            "source_commit_id": req.source_commit_id,
        }

    @router.get("/projects/{project_id}/chapters/{chapter_id}/history")
    def get_chapter_history(project_id: str, chapter_id: str, limit: int = 20) -> dict[str, Any]:
        """章版本历史：只读投影，按章基础判重；limit 上限 50。"""
        return runtime.chapter_history.versions(
            project_id, chapter_id, limit=max(1, min(limit, 50))
        )

    @router.get("/projects/{project_id}/evidence-links")
    def list_evidence_links(
        project_id: str,
        chapter_id: str | None = None,
        target_id: str | None = None,
    ) -> dict[str, Any]:
        """证据列表（可按章或按被锚定的账本对象过滤）；需核对的排在前面。

        ``target_id`` 供图谱节点「来源定位」：查出该对象在正文中的全部依据。
        """
        return runtime.evidence.list(project_id, chapter_id=chapter_id, target_id=target_id)

    @router.post("/projects/{project_id}/evidence-links/{evidence_id}/reanchor")
    def reanchor_evidence(
        project_id: str, evidence_id: str, req: ReanchorRequest
    ) -> dict[str, Any]:
        """人工重新关联：引用片段必须在当前正文中可定位，否则拒绝。"""
        return runtime.evidence.reanchor(project_id, evidence_id, quote=req.quote)

    @router.get("/projects/{project_id}/backup")
    def backup_preview(project_id: str) -> dict[str, Any]:
        """备份预览（V03-8.2，只读）：清单、计数、整包指纹与覆盖范围。

        与 ``/export`` 用同一次确定性构建：这里回报「包里有什么、没有什么」，
        下载 zip 仍走 ``/export``。覆盖范围逐条列出，不把过程状态说成已备份。
        """
        return runtime.backup.preview(project_id)

    @router.post("/projects/{project_id}/restore")
    def restore_backup(project_id: str, req: RestoreBackupRequest) -> dict[str, Any]:
        """从备份 zip 恢复本作品（V03-8.2）：差异 → 确认 → 一次原子提交。

        恢复只针对同一作品：包内容与当前一致是合法空结果；有差异而未确认时
        拒绝并列出会被改写的章与账本字段；确认后正文与账本进同一个 Commit，
        原历史保留为新提交。新建条目、跨作品搬运都不在此路径。
        """
        try:
            archive = base64.b64decode(req.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ViolationError(
                code="EXPORT_PACKAGE_INVALID",
                where="v2.restore",
                why=f"content_base64 不是合法 base64：{exc}",
                fix="上传从「导出」下载的 zip 文件的 base64 内容，不要先解压再打包",
                details={"project_id": project_id},
            ) from exc
        return runtime.backup.restore(project_id, archive, confirm_overwrite=req.confirm_overwrite)

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
    router.include_router(create_conversations_router(runtime))
    router.include_router(create_execution_log_router(runtime))
    router.include_router(create_revision_router(runtime))
    return router


def _summary_sync_result(finished: dict[str, Any]) -> dict[str, Any]:
    """同步入口的统一翻译：任务失败要变成能看见的错误，而不是空结果。"""
    state = str(finished.get("state", ""))
    failure = finished.get("failure") or {}
    if state == "terminal_failed":
        raise ViolationError(
            code=str(failure.get("code") or "SUMMARY_JOB_FAILED"),
            where="summary.job",
            why=str(failure.get("why") or "摘要任务失败"),
            fix=str(failure.get("fix") or "重试一次；持续失败时换模型"),
            details={"job_id": finished.get("job_id")},
        )
    if state == "retryable_failed":
        raise EnvironmentFailure(
            code="SUMMARY_JOB_ENVIRONMENT",
            where="summary.job",
            why=str(failure.get("why") or "摘要任务遇到环境故障"),
            fix="稍后重试；服务恢复后可继续这次任务",
            details={"job_id": finished.get("job_id")},
        )
    if state == "cancelled":
        return {
            "status": "cancelled",
            "committed": False,
            "commit_id": None,
            "summary": {},
            "replayed": False,
            "reason": "任务已被停止，未写入任何摘要版本",
            "job_id": finished.get("job_id"),
        }
    result = dict(finished.get("result") or {})
    result["job_id"] = finished.get("job_id")
    return result


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
    # 0.3 已退役入口（V03-8.6）：曾经存在、本版不再提供——410 与「参数写错」区分。
    if error.code == "FEATURE_RETIRED_IN_03":
        return 410
    if error.code.endswith("_NOT_FOUND") or "UNKNOWN" in error.code:
        return 404
    if error.code.endswith("_CONFLICT"):
        return 409
    # 来源已变不是输入写错，而是「先重新整理再采用」的选择：按冲突返回。
    # 未 flush 同理：先保存声明过的章，或放弃本轮——不是请求格式错误。
    if error.code in (
        "SOURCE_NEEDS_RECHECK",
        "DISCLOSURE_NEEDS_AUTHORIZATION",
        "EDIT_SESSION_UNFLUSHED",
    ):
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

    @app.exception_handler(CoverageViolation)
    async def coverage_violation_handler(request: Request, exc: CoverageViolation) -> JSONResponse:
        return _violation_response(exc)


def _as_violation(
    exc: ManuscriptViolation | AuthorModelViolation | StoryViolation | CoverageViolation,
) -> ViolationError:
    """边界转换：领域违规 → 应用错误信封（四要素原样透传，与 handler 同构）。"""
    return ViolationError(
        code=exc.code, where=exc.where, why=exc.why, fix=exc.fix, details=exc.details
    )


def _violation_response(
    exc: ManuscriptViolation | AuthorModelViolation | StoryViolation | CoverageViolation,
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
