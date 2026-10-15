"""修订计划工作流（T15.4）：影响报告 → 四层计划落库的应用层编排。

编排四步（与 AnalyzeChangeImpact 同构）：
1. 读变更集并前置检查状态——计划只在 analyzed 初建、review 重算；
   opened/executing 等状态在入口处拒绝并指向正确时机（四透镜①）；
2. 定位该变更集的影响报告——没有报告（手工推进到 analyzed 的情形）
   机制前置拒绝，不让作者空跑；
3. 在报告上执行**确定性投影**（``build_revision_plan``）——同一报告必然
   得到同一计划；环检测不在这里做（构建是纯投影，见 planning 模块）；
4. 经领域命令 ``record_revision_plan`` 落库：命令层校验覆盖、项级/步级
   无环（不变量 4、5）后才产生提交。

幂等重放（四透镜③「已完成不是故障」）：已落库计划与本次投影逐字段一致
（同一报告的确定性结果）→ 直接返回既有 plan_id，不报错也不产生新提交。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hastings.application.errors import ViolationError
from hastings.application.handlers.change_commands import (
    ChangeSetCommandHandler,
    ChangeSetCommandResult,
    change_set_command,
)
from hastings.application.ports.clock import Clock
from hastings.application.ports.repository import RepositoryReader
from hastings.application.workflows.change_analysis import read_change_state
from hastings.domain.changes import error_codes as ec
from hastings.domain.changes import kinds
from hastings.domain.changes import status as st
from hastings.domain.changes.catalog import ChangesCatalog
from hastings.domain.changes.commands import record_revision_plan
from hastings.domain.changes.impact import ImpactReport
from hastings.domain.changes.models import ChangeSet
from hastings.domain.changes.planning import (
    RevisionPlan,
    build_revision_plan,
    default_layer_of,
)
from hastings.domain.repository.ids import Actor, CommitId, ProjectId

__all__ = ["PlanIntent", "PlanResult", "PlanRevision"]

# 非法状态 → 指向正确时机的修复指引
_PRECHECK_FIX: dict[str, str] = {
    st.CHANGE_OPENED: "先执行影响分析（报告落库后状态转为 analyzed），再制定修订计划",
    st.CHANGE_ANALYZING: "分析尚未完成；等报告落库（analyzed）后再制定修订计划",
    st.CHANGE_APPROVED: "approved 状态等待执行；计划只能在 analyzed 初建或 review 重算",
    st.CHANGE_EXECUTING: "执行中的计划不可替换；等步骤结果回到 review 再重算",
    st.CHANGE_FAILED: "从 failed 先 advance 回 analyzing 重新分析，再制定计划",
    st.CHANGE_COMMITTED: "变更集已提交（终态）；新的修改请新建 ChangeSet",
    st.CHANGE_ABANDONED: "变更集已放弃（终态）；新的修改请新建 ChangeSet",
}


@dataclass(frozen=True, slots=True)
class PlanIntent:
    """一次修订计划意图：目标变更集 + 幂等键前缀。"""

    project_id: ProjectId
    change_set_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class PlanResult:
    """计划结果：计划身份 + 结构统计 + 提交信息。

    ``commit_id`` 为本次记录提交；幂等重放（重复 plan）为 None。
    """

    change_set_id: str
    plan_id: str
    status: str
    step_count: int
    unassigned_count: int
    commit_id: str | None


class PlanRevision:
    """把影响报告投影成四层修订计划并接到 Commit Service 的应用层用例。"""

    def __init__(
        self,
        handler: ChangeSetCommandHandler,
        reader: RepositoryReader,
        clock: Clock,
        actor: Actor,
        *,
        branch_name: str = "main",
    ) -> None:
        self._handler = handler
        self._reader = reader
        self._clock = clock
        self._actor = actor
        self._branch_name = branch_name

    def plan(self, intent: PlanIntent) -> PlanResult:
        catalog, model = read_change_state(
            self._reader,
            intent.project_id,
            intent.change_set_id,
            branch_name=self._branch_name,
            where="changes.plan",
        )
        fix = _PRECHECK_FIX.get(model.status)
        if fix is not None:
            raise ViolationError(
                code=ec.CHANGES_INVALID_TRANSITION,
                where=f"changes.plan({intent.change_set_id})",
                why=f"变更集当前状态是 {model.status!r}，不能制定修订计划",
                fix=fix,
                details={"status": model.status, "change_set_id": intent.change_set_id},
            )
        report = self._require_report(catalog, intent)
        built = build_revision_plan(
            report, kind_of=self._kind_of(intent, model), layer_of=default_layer_of
        )
        existing = self._existing_plan(catalog, intent.change_set_id)
        if existing is not None and self._same_body(existing, built):
            # 同一报告的确定性投影必然一致：重放既有计划，不产生新提交
            return PlanResult(
                change_set_id=intent.change_set_id,
                plan_id=existing.plan_id,
                status=model.status,
                step_count=len(built.steps),
                unassigned_count=len(built.unassigned),
                commit_id=None,
            )
        record = self._record(intent, built)
        final = read_change_state(
            self._reader,
            intent.project_id,
            intent.change_set_id,
            branch_name=self._branch_name,
            where="changes.plan",
        )[1]
        plan_change = next(
            (c for c in record.change.artifact_changes if c.kind == kinds.KIND_REVISION_PLAN),
            None,
        )
        assert plan_change is not None  # record 命令保证计划变更存在
        return PlanResult(
            change_set_id=intent.change_set_id,
            plan_id=plan_change.artifact_id.value,
            status=final.status,
            step_count=len(built.steps),
            unassigned_count=len(built.unassigned),
            commit_id=record.commit_id,
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _require_report(self, catalog: ChangesCatalog, intent: PlanIntent) -> ImpactReport:
        entry = next(
            (
                e
                for e in catalog.by_kind(kinds.KIND_IMPACT_REPORT)
                if isinstance(e.model(), ImpactReport)
                and e.model().change_set_id == intent.change_set_id
            ),
            None,
        )
        if entry is None:
            raise ViolationError(
                code=ec.CHANGES_INVALID_COMMAND,
                where=f"changes.plan({intent.change_set_id})",
                why="找不到该变更集的影响报告，无法生成计划",
                fix="先执行影响分析（AnalyzeChangeImpact.analyze），报告落库后再制定计划",
                details={"change_set_id": intent.change_set_id},
            )
        report: ImpactReport = entry.model()
        return report

    def _kind_of(self, intent: PlanIntent, model: ChangeSet) -> Any:
        snapshot = self._reader.get_snapshot(intent.project_id, CommitId(model.base_commit_id))
        return {
            view.artifact.id.value: view.artifact.kind for view in snapshot.artifacts.values()
        }.get

    def _existing_plan(self, catalog: ChangesCatalog, change_set_id: str) -> RevisionPlan | None:
        entry = next(
            (
                e
                for e in catalog.by_kind(kinds.KIND_REVISION_PLAN)
                if isinstance(e.model(), RevisionPlan) and e.model().change_set_id == change_set_id
            ),
            None,
        )
        return entry.model() if entry is not None else None

    @staticmethod
    def _same_body(existing: RevisionPlan, built: RevisionPlan) -> bool:
        return (
            existing.steps,
            existing.unassigned,
            existing.constraints,
            existing.risk_acceptances,
        ) == (built.steps, built.unassigned, built.constraints, built.risk_acceptances)

    def _record(self, intent: PlanIntent, built: RevisionPlan) -> ChangeSetCommandResult:
        return self._handler.execute(
            change_set_command(
                record_revision_plan,
                change_set_id=intent.change_set_id,
                plan=built,
                actor=self._actor,
                at=self._clock.now(),
                reason="依据影响报告生成修订计划",
            ),
            project_id=intent.project_id,
            idempotency_key=f"{intent.idempotency_key}:plan",
            summary=(f"变更集 {intent.change_set_id} 生成修订计划（{len(built.steps)} 步）"),
        )
