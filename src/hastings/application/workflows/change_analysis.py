"""影响分析工作流（Phase 15 T15.2）：确定性遍历的应用层编排。

编排四步（模块文档 §4 两阶段分析的第一阶段）：
1. 读取变更集并前置检查状态——快速失败，不让调用方先跑一遍遍历再挨拒
   （四透镜①「机制前置展示」）；
2. opened → 先推进 analyzing（迁移与结果落库分两次提交，各自幂等可重试；
   若在两步之间崩溃，重试时状态已是 analyzing，直接走记录步恢复）；
3. 在变更集绑定的 base_commit_id 快照上构建关系图并执行确定性遍历——
   报告绑定基础版本（不变量 1），head 前移后 ``is_stale`` 可判定；
4. 经领域命令 ``record_impact_analysis`` 落库：首次分析同提交转入 analyzed；
   review/executing 的重算只替换报告 Revision，不改变状态。

状态裁决（有意的状态机语义扩展，记入构建笔记）：analyzed 状态重复 analyze
视为**幂等重放**——同一变更集的种子、scope 与 base 全部不可变，确定性遍历
必然得到同一结果，直接返回已落库报告，不报错也不产生新提交（四透镜③：
「已完成」不是故障，报错会把调用方推进道歉-重试循环）。
"""

from __future__ import annotations

from dataclasses import dataclass

from hastings.application.errors import ViolationError
from hastings.application.handlers.change_commands import (
    ChangeSetCommandHandler,
    ChangeSetCommandResult,
    change_set_command,
)
from hastings.application.ports.clock import Clock
from hastings.application.ports.repository import RepositoryReader
from hastings.domain.changes import error_codes as ec
from hastings.domain.changes import kinds
from hastings.domain.changes import status as st
from hastings.domain.changes.catalog import ChangesCatalog
from hastings.domain.changes.commands import advance_change_set, record_impact_analysis
from hastings.domain.changes.impact import ImpactReport, RelationGraph, traverse_impact
from hastings.domain.changes.models import ChangeSet
from hastings.domain.repository import error_codes as repo_ec
from hastings.domain.repository.ids import Actor, CommitId, ProjectId

__all__ = ["AnalyzeChangeImpact", "AnalyzeIntent", "AnalyzeResult"]

# analyzed 走幂等重放；opened/analyzing/review/executing 走正常分析
_PRECHECK_FIX: dict[str, str] = {
    st.CHANGE_APPROVED: "approved 状态等待执行；如需重算影响，先推进到 executing/review",
    st.CHANGE_FAILED: "从 failed 先 advance 回 analyzing（合法迁移）再重试分析",
    st.CHANGE_COMMITTED: "变更集已提交（终态不可迁移）；对新的修改请新建 ChangeSet",
    st.CHANGE_ABANDONED: "变更集已放弃（终态不可迁移）；对新的修改请新建 ChangeSet",
}


@dataclass(frozen=True, slots=True)
class AnalyzeIntent:
    """一次影响分析意图：目标变更集 + 幂等键前缀（两次提交共用）。"""

    project_id: ProjectId
    change_set_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class AnalyzeResult:
    """分析结果：报告身份 + 统计 + 变更集终态。

    ``commit_id`` 为本次记录提交；幂等重放（analyzed 状态重复调用）为 None。
    ``truncated_count`` 单列：界面上「没查到」与「查到但被截断」必须可区分。
    """

    change_set_id: str
    report_id: str
    status: str
    item_count: int
    truncated_count: int
    commit_id: str | None


class AnalyzeChangeImpact:
    """把确定性影响遍历接到 Commit Service 的应用层用例。"""

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

    def analyze(self, intent: AnalyzeIntent) -> AnalyzeResult:
        catalog, model = self._read_state(intent)
        if model.status == st.CHANGE_ANALYZED:
            return self._replay_analyzed(catalog, model, intent)
        fix = _PRECHECK_FIX.get(model.status)
        if fix is not None:
            raise ViolationError(
                code=ec.CHANGES_INVALID_TRANSITION,
                where=f"changes.analyze({intent.change_set_id})",
                why=f"变更集当前状态是 {model.status!r}，不能执行影响分析",
                fix=fix,
                details={"status": model.status, "change_set_id": intent.change_set_id},
            )
        if model.status == st.CHANGE_OPENED:
            self._advance_analyzing(intent)
        report = self._traverse(model, intent)
        record = self._record(model, report, intent)
        final = self._read_state(intent)[1]
        report_change = next(
            (c for c in record.change.artifact_changes if c.kind == kinds.KIND_IMPACT_REPORT),
            None,
        )
        assert report_change is not None  # record 命令保证报告变更存在
        return AnalyzeResult(
            change_set_id=intent.change_set_id,
            report_id=report_change.artifact_id.value,
            status=final.status,
            item_count=report.stats.item_count,
            truncated_count=report.stats.truncated_count,
            commit_id=record.commit_id,
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _read_state(self, intent: AnalyzeIntent) -> tuple[ChangesCatalog, ChangeSet]:
        branch = self._reader.get_branch(intent.project_id, self._branch_name)
        if branch is None:
            raise ViolationError(
                code=repo_ec.REPO_BRANCH_NOT_FOUND,
                where=f"changes.analyze({intent.project_id.value})",
                why=f"项目 {intent.project_id.value} 下没有名为 {self._branch_name!r} 的分支",
                fix="先用 create_project 建立项目与 main 分支，再执行影响分析",
                details={"project_id": intent.project_id.value, "branch_name": self._branch_name},
            )
        head = branch.head_commit_id
        if head is None:
            raise ViolationError(
                code=ec.CHANGES_UNKNOWN_CHANGE_SET,
                where=f"changes.analyze({intent.change_set_id})",
                why="项目还没有任何提交，当前 head 为空",
                fix="先播种对象与关系（提交），再 open_change_set 并分析",
                details={"project_id": intent.project_id.value, "head": None},
            )
        catalog = ChangesCatalog.from_snapshot(self._reader.get_snapshot(intent.project_id, head))
        entry = catalog.get(intent.change_set_id)
        if entry is None or entry.kind != kinds.KIND_CHANGE_SET:
            raise ViolationError(
                code=ec.CHANGES_UNKNOWN_CHANGE_SET,
                where=f"changes.analyze({intent.change_set_id})",
                why=f"变更集 {intent.change_set_id!r} 在当前 head 上不存在",
                fix="改用 open_change_set 返回的 change_set_id（先查变更中心列表）",
                details={"change_set_id": intent.change_set_id, "head": head.value},
            )
        model: ChangeSet = entry.model()
        return catalog, model

    def _advance_analyzing(self, intent: AnalyzeIntent) -> ChangeSetCommandResult:
        return self._handler.execute(
            change_set_command(
                advance_change_set,
                change_set_id=intent.change_set_id,
                target=st.CHANGE_ANALYZING,
                actor=self._actor,
                at=self._clock.now(),
                reason="开始影响分析",
            ),
            project_id=intent.project_id,
            idempotency_key=f"{intent.idempotency_key}:start",
            summary=f"变更集 {intent.change_set_id} 开始影响分析",
        )

    def _traverse(self, model: ChangeSet, intent: AnalyzeIntent) -> ImpactReport:
        """在 base 快照上建图遍历；节点→kind 解析器由快照提供（scope.kinds 生效）。"""
        snapshot = self._reader.get_snapshot(intent.project_id, CommitId(model.base_commit_id))
        graph = RelationGraph.from_snapshot(snapshot)
        kind_of = {
            view.artifact.id.value: view.artifact.kind for view in snapshot.artifacts.values()
        }
        seeds = tuple(seed.artifact_id for seed in model.seed_changes)
        return traverse_impact(graph, seeds, scope=model.scope, kind_of=kind_of.get)

    def _record(
        self, model: ChangeSet, report: ImpactReport, intent: AnalyzeIntent
    ) -> ChangeSetCommandResult:
        return self._handler.execute(
            change_set_command(
                record_impact_analysis,
                change_set_id=intent.change_set_id,
                base_commit_id=model.base_commit_id,
                report=report,
                actor=self._actor,
                at=self._clock.now(),
            ),
            project_id=intent.project_id,
            idempotency_key=f"{intent.idempotency_key}:record",
            summary=f"变更集 {intent.change_set_id} 影响分析落库",
        )

    def _replay_analyzed(
        self, catalog: ChangesCatalog, model: ChangeSet, intent: AnalyzeIntent
    ) -> AnalyzeResult:
        """analyzed 状态的重复 analyze = 幂等重放：返回已落库报告，不产生新提交。"""
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
                code=ec.CHANGES_INVALID_TRANSITION,
                where=f"changes.analyze({intent.change_set_id})",
                why="变更集状态是 analyzed，但找不到已落库的影响报告（异常历史）",
                fix="新建 ChangeSet 重新走 open → analyze 流程",
                details={"change_set_id": intent.change_set_id},
            )
        report: ImpactReport = entry.model()
        return AnalyzeResult(
            change_set_id=intent.change_set_id,
            report_id=entry.artifact_id,
            status=model.status,
            item_count=report.stats.item_count,
            truncated_count=report.stats.truncated_count,
            commit_id=None,
        )
