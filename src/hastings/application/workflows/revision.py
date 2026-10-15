"""修订执行工作流（Phase 15 T15.6，modules/change-impact.md §5）。

编排（模块文档 §5 时序图的 W/R/A 段）：

1. 前置拒绝（四透镜①）：状态非 approved/executing、计划缺失或绑定漂移
   在入口失败，不白跑分支创建；
2. 空计划是合法空结果（§8 退出条件）：no-impact 不建空分支、不推进状态；
3. approved → 先推进 executing（状态与执行解耦为两次提交，各自幂等可重试；
   崩溃窗口靠 executing 恢复路径覆盖）；
4. 获取执行 Lease（资源键 ``revision:{project}:{change_set}``）——拿不到
   即失败留痕，不与在跑的 Job 抢写；
5. 从 base Commit 建/复用 revision branch；按 ``execution_order`` 逐步：
   组装 Repair Context → Worker 提案 → 领域守卫校验范围 → 绑定分支头版本
   → 携 Lease 凭据提交（Lease 丢失后旧 Job 的提交被内核拒绝，§6 清单）；
6. 任何一步失败：释放租约（尽力）、``record_execution`` 与
   executing → failed **同提交**落 main——Job 状态是变更工作流的一等事实，
   main 上的故事内容零改动（不变量 6）；已完成的步骤留在分支上可恢复；
7. 全部完成：``record_execution`` 与 executing → review 同提交落 main，
   进入差异审阅（T15.7）。

Stale 语义的边界（有意设计，记入构建笔记）：报告/计划的过期在**批准时**被
凭证指纹挡住；执行锚定 base 分支，与 main 的 head 前移**解耦**——这正是
分支存在的意义（执行期间作者仍可在 main 上做别的提交），合并时（T15.7）
再处理与 main 的分叉。
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from hastings.application.errors import ViolationError
from hastings.application.handlers.change_commands import (
    ChangeSetCommandHandler,
    change_set_command,
)
from hastings.application.ports.clock import Clock
from hastings.application.ports.ids import IdGenerator
from hastings.application.ports.repository import CommitRepository, RepositoryReader
from hastings.application.ports.revision_worker import RevisionWorker
from hastings.application.workflows.change_analysis import read_change_state
from hastings.domain.changes import error_codes as ec
from hastings.domain.changes import kinds
from hastings.domain.changes import status as st
from hastings.domain.changes.commands import advance_change_set, record_execution
from hastings.domain.changes.errors import ChangesViolation
from hastings.domain.changes.execution import (
    EVENT_STEP_COMMITTED,
    EXECUTION_WORKER_FAILED,
    OUTCOME_COMPLETED,
    OUTCOME_FAILED,
    STEP_EXECUTED,
    STEP_FAILED,
    STEP_NO_CHANGE,
    ExecutionRecord,
    PatchProposal,
    StepContext,
    StepRecord,
    TargetView,
    validate_proposal,
)
from hastings.domain.changes.impact import ImpactReport
from hastings.domain.changes.models import ChangeSet
from hastings.domain.changes.planning import RevisionPlan, execution_order
from hastings.domain.repository import error_codes as repo_ec
from hastings.domain.repository.ids import Actor, ArtifactId, CommitId, JobId, ProjectId, RevisionId
from hastings.domain.repository.models import Snapshot
from hastings.domain.repository.requests import ArtifactChange, CommitRequest, CreateBranchRequest

__all__ = ["ExecuteIntent", "ExecuteRevisionPlan", "ExecutionResult"]

RESULT_EXECUTED = "executed"
RESULT_NO_STEPS = "no_steps"
RESULT_FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ExecuteIntent:
    """一次执行意图：目标变更集 + 幂等键（推进/各步/记录共用前缀）。"""

    project_id: ProjectId
    change_set_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """执行结果：结局 + 分支 + 各步明细；失败携带错误码与三要素消息。

    ``commit_id`` 是 main 上执行记录提交（no_steps 时为 None）；
    分支上的逐步提交 ID 见 ``steps``。
    """

    change_set_id: str
    job_id: str
    branch_name: str
    status: str
    steps: tuple[StepRecord, ...] = ()
    commit_id: str | None = None
    error_code: str = ""
    error_message: str = ""


class ExecuteRevisionPlan:
    """把已批准的修订计划接到分支执行与 Commit Service 的应用层用例。"""

    def __init__(
        self,
        handler: ChangeSetCommandHandler,
        reader: RepositoryReader,
        committer: CommitRepository,
        leases: Any,  # LeaseRepository；内存与 SQLite 同契约
        ids: IdGenerator,
        clock: Clock,
        actor: Actor,
        *,
        branch_prefix: str = "revision",
        lease_ttl_seconds: float = 900.0,
    ) -> None:
        self._handler = handler
        self._reader = reader
        self._committer = committer
        self._leases = leases
        self._ids = ids
        self._clock = clock
        self._actor = actor
        self._branch_prefix = branch_prefix
        self._lease_ttl = lease_ttl_seconds

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def execute(self, intent: ExecuteIntent, worker: RevisionWorker) -> ExecutionResult:
        where = f"changes.execute({intent.change_set_id})"
        catalog, model = self._read_state(intent)
        resume = self._precheck(model, where)

        plan = self._require_plan(catalog, model, where)
        order = execution_order(plan)  # 成环即拒绝（计划登记时已防，此处防御性兜底）
        branch_name = f"{self._branch_prefix}/{intent.change_set_id}"
        if not order:
            # no-impact 是合法空结果：不建空分支、不推进状态（不变量 9）
            return ExecutionResult(
                change_set_id=intent.change_set_id,
                job_id="",
                branch_name=branch_name,
                status=RESULT_NO_STEPS,
            )

        job_id = self._ids.new_id("job")
        if not resume:
            self._advance_executing(intent)
        resource_key = f"revision:{intent.project_id.value}:{intent.change_set_id}"

        try:
            lease = self._leases.acquire(resource_key, JobId(job_id), self._lease_ttl)
        except ViolationError as exc:
            return self._finish(
                intent,
                model,
                job_id,
                branch_name,
                (),
                OUTCOME_FAILED,
                error_code=exc.code,
                error_message=f"{exc.why}（{exc.fix}）",
                key=intent.idempotency_key,
            )

        branch = self._ensure_branch(intent, model, branch_name)
        report = self._require_report(catalog, intent.change_set_id, where)
        steps: list[StepRecord] = []
        try:
            for step_id in order:
                step = plan.step_of(step_id)
                assert step is not None  # execution_order 只返回计划内的步
                steps.append(
                    self._run_step(
                        intent,
                        branch,
                        step,
                        report,
                        worker,
                        worker_key=resource_key,
                        lease_owner=job_id,
                        lease_version=lease.version,
                        idempotency_key=intent.idempotency_key,
                    )
                )
        except ViolationError as exc:
            self._release(resource_key, lease)
            return self._finish(
                intent,
                model,
                job_id,
                branch_name,
                (
                    *steps,
                    StepRecord(
                        step_id=self._failed_step_id(plan, steps, order), outcome=STEP_FAILED
                    ),
                ),
                OUTCOME_FAILED,
                error_code=exc.code,
                error_message=f"{exc.where}: {exc.why}（{exc.fix}）",
                key=intent.idempotency_key,
            )
        except Exception as exc:  # Worker/环境故障：归因后走同一失败路径
            self._release(resource_key, lease)
            return self._finish(
                intent,
                model,
                job_id,
                branch_name,
                (
                    *steps,
                    StepRecord(
                        step_id=self._failed_step_id(plan, steps, order), outcome=STEP_FAILED
                    ),
                ),
                OUTCOME_FAILED,
                error_code=EXECUTION_WORKER_FAILED,
                error_message=f"{type(exc).__name__}: {exc}",
                key=intent.idempotency_key,
            )
        self._release(resource_key, lease)
        return self._finish(
            intent,
            model,
            job_id,
            branch_name,
            tuple(steps),
            OUTCOME_COMPLETED,
            key=intent.idempotency_key,
        )

    # ------------------------------------------------------------------
    # 前置与读取
    # ------------------------------------------------------------------
    def _read_state(self, intent: ExecuteIntent) -> tuple[Any, ChangeSet]:
        return read_change_state(
            self._reader,
            intent.project_id,
            intent.change_set_id,
            where="changes.execute",
        )

    @staticmethod
    def _precheck(model: ChangeSet, where: str) -> bool:
        """状态前置：approved 全新执行；executing 崩溃恢复；其余拒绝。"""
        if model.status == st.CHANGE_EXECUTING:
            return True  # 上次推进后崩溃：直接续跑（各步幂等键保证重放安全）
        if model.status != st.CHANGE_APPROVED:
            raise ViolationError(
                code=ec.CHANGES_INVALID_TRANSITION,
                where=where,
                why=f"变更集尚未获得作者批准（当前 {model.status!r}）："
                "只有 approved（已批准）或 executing（恢复执行）能执行修订",
                fix="先完成影响分析、计划与作者批准凭证，再执行；"
                "review 状态请走差异审阅与合并，终态不可复活",
                details={"status": model.status},
            )
        return False

    def _require_plan(self, catalog: Any, model: ChangeSet, where: str) -> RevisionPlan:
        entry = next(
            (
                e
                for e in catalog.by_kind(kinds.KIND_REVISION_PLAN)
                if isinstance(e.model(), RevisionPlan)
                and e.model().change_set_id == model.change_set_id
            ),
            None,
        )
        if entry is None:
            raise ViolationError(
                code=ec.CHANGES_INVALID_COMMAND,
                where=where,
                why="找不到该变更集的修订计划，无从执行",
                fix="先由报告生成计划并取得作者批准，再执行",
                details={"change_set_id": model.change_set_id},
            )
        plan: RevisionPlan = entry.model()
        if plan.base_commit_id != model.base_commit_id:
            raise ViolationError(
                code=ec.CHANGES_BASE_STALE,
                where=where,
                why=f"计划绑定的基础 Commit {plan.base_commit_id!r} 与变更集的 "
                f"{model.base_commit_id!r} 不一致（不变量 1）",
                fix="回到变更集的 base_commit_id 重新分析、计划并取得作者批准",
                details={"plan_base": plan.base_commit_id, "expected": model.base_commit_id},
            )
        return plan

    def _require_report(self, catalog: Any, change_set_id: str, where: str) -> ImpactReport:
        entry = next(
            (
                e
                for e in catalog.by_kind(kinds.KIND_IMPACT_REPORT)
                if isinstance(e.model(), ImpactReport) and e.model().change_set_id == change_set_id
            ),
            None,
        )
        if entry is None:
            raise ViolationError(
                code=ec.CHANGES_INVALID_COMMAND,
                where=where,
                why="找不到该变更集的影响报告（异常历史：计划存在而报告缺失）",
                fix="重新执行影响分析生成报告后再执行修订",
                details={"change_set_id": change_set_id},
            )
        report: ImpactReport = entry.model()
        return report

    # ------------------------------------------------------------------
    # 执行步
    # ------------------------------------------------------------------
    def _run_step(
        self,
        intent: ExecuteIntent,
        branch: Any,
        step: Any,
        report: ImpactReport,
        worker: RevisionWorker,
        *,
        worker_key: str,
        lease_owner: str,
        lease_version: int,
        idempotency_key: str,
    ) -> StepRecord:
        where = f"changes.execute.step({step.step_id})"
        head = self._reader.get_branch(intent.project_id, branch.name)
        if head is None or head.head_commit_id is None:
            raise ViolationError(
                code=repo_ec.REPO_BRANCH_NOT_FOUND,
                where=where,
                why=f"修订分支 {branch.name!r} 消失或 head 为空",
                fix="这是数据完整性故障：检查仓库状态后重试执行",
            )
        branch_head: CommitId = head.head_commit_id
        snapshot = self._reader.get_snapshot(intent.project_id, branch_head)
        context = self._build_context(intent, step, report, snapshot)
        try:
            proposal: PatchProposal = worker.propose(context)
            validate_proposal(proposal, step, where=where)
        except ChangesViolation as exc:
            # 边界转换：四要素原样透传，不做「人话翻译」（四透镜①）
            raise ViolationError(
                code=exc.code, where=exc.where, why=exc.why, fix=exc.fix, details=exc.details
            ) from exc
        if not proposal.patches:
            return StepRecord(step_id=step.step_id, outcome=STEP_NO_CHANGE, note=proposal.note)

        changes: list[ArtifactChange] = []
        for unit in proposal.patches:
            view = snapshot.artifacts.get(ArtifactId(unit.artifact_id))
            if view is None or view.revision is None:
                raise ViolationError(
                    code=ec.CHANGES_INVALID_COMMAND,
                    where=where,
                    why=f"目标 {unit.artifact_id!r} 在分支头上没有有效版本",
                    fix="目标应自 base 即存在（不变量 5）；这是数据完整性故障，"
                    "请检查变更集种子与计划目标",
                    details={"artifact_id": unit.artifact_id, "step_id": step.step_id},
                )
            changes.append(
                ArtifactChange(
                    artifact_id=ArtifactId(unit.artifact_id),
                    content=unit.content,
                    old_revision_id=RevisionId(view.revision.id.value),
                )
            )
        result = self._committer.commit(
            CommitRequest(
                project_id=intent.project_id,
                branch_id=branch.id,
                expected_head=branch_head,
                actor=self._actor,
                idempotency_key=f"{idempotency_key}:step:{step.step_id}",
                summary=f"执行修订步 {step.step_id}（{step.title}）",
                changes=tuple(changes),
                events=(
                    (
                        EVENT_STEP_COMMITTED,
                        {
                            "change_set_id": intent.change_set_id,
                            "step_id": step.step_id,
                            "job_id": lease_owner,
                            "targets": [u.artifact_id for u in proposal.patches],
                        },
                    ),
                ),
                required_leases=((worker_key, lease_owner, lease_version),),
            )
        )
        if result.is_no_change:
            # Worker 提案内容与分支现状一致：合法空结果（重放收敛的证明）
            return StepRecord(
                step_id=step.step_id,
                outcome=STEP_NO_CHANGE,
                note=result.no_change_reason or "提案与现状一致",
            )
        return StepRecord(
            step_id=step.step_id,
            outcome=STEP_EXECUTED,
            commit_id=result.commit_id.value if result.commit_id else "",
        )

    def _build_context(
        self, intent: ExecuteIntent, step: Any, report: ImpactReport, snapshot: Snapshot
    ) -> StepContext:
        targets: list[TargetView] = []
        for target_id in step.target_artifact_ids:
            view = snapshot.artifacts.get(ArtifactId(target_id))
            if view is None or view.revision is None:
                raise ViolationError(
                    code=ec.CHANGES_INVALID_COMMAND,
                    where=f"changes.execute.context({step.step_id})",
                    why=f"步骤目标 {target_id!r} 在分支头上不存在或没有版本",
                    fix="目标应自 base 即存在；这是数据完整性故障，请检查计划目标",
                    details={"artifact_id": target_id, "step_id": step.step_id},
                )
            targets.append(
                TargetView(
                    artifact_id=target_id,
                    kind=view.artifact.kind,
                    content=dict(view.revision.content),
                )
            )
        reasons = tuple(
            report.items[i].reason for i in step.item_indexes if 0 <= i < len(report.items)
        )
        return StepContext(
            change_set_id=intent.change_set_id,
            step_id=step.step_id,
            layer=step.layer,
            title=step.title,
            targets=tuple(targets),
            reasons=reasons,
        )

    # ------------------------------------------------------------------
    # 分支 / 推进 / 收尾
    # ------------------------------------------------------------------
    def _ensure_branch(self, intent: ExecuteIntent, model: ChangeSet, branch_name: str) -> Any:
        existing = self._reader.get_branch(intent.project_id, branch_name)
        if existing is not None:
            return existing  # 崩溃恢复：复用既有分支，从其 head 续跑
        return self._committer.create_branch(
            CreateBranchRequest(
                project_id=intent.project_id,
                name=branch_name,
                at_commit_id=CommitId(model.base_commit_id),
            )
        )

    def _advance_executing(self, intent: ExecuteIntent) -> None:
        self._handler.execute(
            change_set_command(
                advance_change_set,
                change_set_id=intent.change_set_id,
                target=st.CHANGE_EXECUTING,
                actor=self._actor,
                at=self._clock.now(),
                reason="开始执行修订计划",
            ),
            project_id=intent.project_id,
            idempotency_key=f"{intent.idempotency_key}:start",
            summary=f"变更集 {intent.change_set_id} 开始执行修订",
        )

    def _failed_step_id(
        self, plan: RevisionPlan, done: list[StepRecord], order: tuple[str, ...]
    ) -> str:
        completed = {s.step_id for s in done}
        return next((sid for sid in order if sid not in completed), "")

    def _release(self, resource_key: str, lease: Any) -> None:
        """尽力释放租约：被接管时静默放弃（新 owner 已负责该资源）。"""
        with suppress(ViolationError):
            self._leases.release(lease)

    def _finish(
        self,
        intent: ExecuteIntent,
        model: ChangeSet,
        job_id: str,
        branch_name: str,
        steps: tuple[StepRecord, ...],
        outcome: str,
        *,
        error_code: str = "",
        error_message: str = "",
        key: str,
    ) -> ExecutionResult:
        """登记执行记录并与状态迁移同提交（完成 → review；失败 → failed）。"""
        command = change_set_command(
            record_execution,
            change_set_id=intent.change_set_id,
            job_id=job_id,
            branch_name=branch_name,
            base_commit_id=model.base_commit_id,
            outcome=outcome,
            steps=steps,
            actor=self._actor,
            at=self._clock.now(),
            error_code=error_code,
            error_message=error_message,
        )
        result = self._handler.execute(
            command,
            project_id=intent.project_id,
            idempotency_key=f"{key}:record",
            summary=(
                f"变更集 {intent.change_set_id} 执行完成（{branch_name}）"
                if outcome == OUTCOME_COMPLETED
                else f"变更集 {intent.change_set_id} 执行失败：{error_message}"
            ),
        )
        record_change = next(
            (c for c in result.change.artifact_changes if c.kind == kinds.KIND_EXECUTION_RECORD),
            None,
        )
        assert record_change is not None  # record_execution 保证记录变更存在
        record = ExecutionRecord.from_content(record_change.content)
        return ExecutionResult(
            change_set_id=intent.change_set_id,
            job_id=job_id,
            branch_name=branch_name,
            status=RESULT_EXECUTED if outcome == OUTCOME_COMPLETED else RESULT_FAILED,
            steps=tuple(record.steps),
            commit_id=result.commit_id,
            error_code=error_code,
            error_message=error_message,
        )
