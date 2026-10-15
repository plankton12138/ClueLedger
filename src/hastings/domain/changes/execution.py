"""修订执行（T15.6，modules/change-impact.md §5 / 不变量 3、5、6）。

执行是把已批准的计划落到**修订分支**上的过程。本模块持有三件事：

- **执行契约**：``StepContext``（Worker 拿到的 Repair Context——只含当前
  步骤的目标现状与影响理由，正文步拿不到 Canon 内容，隐式改 Canon 无从
  发生）；``PatchProposal``/``PatchUnit``（Worker 提案，content 是**全量
  新内容**——相对补丁重放会二次叠加，全量内容天然幂等收敛）；
- **提案守卫**（纯函数）：``validate_proposal`` 在提案进入提交前校验
  「只能在批准范围内提案」（§8 退出条件）——越界目标、重复目标、空目标
  都在这里拒绝，硬约束不靠 Worker 自觉（四透镜②）；
- **执行记录**：``record_execution`` 领域命令把一次执行尝试（含失败）
  写成 ``changes.execution`` Artifact，并与状态迁移**同提交原子生效**
  （完成 → review；失败 → failed）——「失败保存 Job 状态」不是旁路日志，
  是变更工作流的一等事实，任何步骤都可从记录恢复（§7 验收第 10 步）。

本模块是纯函数：不做模型调用、不触达 Repository（与 planning/receipt 同构）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hastings.domain.changes import error_codes as ec
from hastings.domain.changes.errors import violation
from hastings.domain.changes.planning import PlanStep

__all__ = [
    "EVENT_EXECUTION_RECORDED",
    "EVENT_STEP_COMMITTED",
    "EXECUTION_WORKER_FAILED",
    "ExecutionRecord",
    "OUTCOME_COMPLETED",
    "OUTCOME_FAILED",
    "OUTCOMES",
    "PatchProposal",
    "PatchUnit",
    "STEP_EXECUTED",
    "STEP_FAILED",
    "STEP_NO_CHANGE",
    "STEP_OUTCOMES",
    "StepContext",
    "StepRecord",
    "TargetView",
    "validate_proposal",
]

# 执行记录整体结局 / 单步结局
OUTCOME_COMPLETED = "completed"
OUTCOME_FAILED = "failed"
OUTCOMES = frozenset((OUTCOME_COMPLETED, OUTCOME_FAILED))

STEP_EXECUTED = "executed"
STEP_NO_CHANGE = "no_change"
STEP_FAILED = "failed"
STEP_OUTCOMES = frozenset((STEP_EXECUTED, STEP_NO_CHANGE, STEP_FAILED))

# 非 ViolationError 的 Worker/环境故障统一归因码（错误消息保留原始异常文本）
EXECUTION_WORKER_FAILED = "EXECUTION_WORKER_FAILED"

# 业务事件（领域契约）：随 Commit 同事务写入 Outbox（ADR-007）
EVENT_EXECUTION_RECORDED = "changes.execution.recorded"
EVENT_STEP_COMMITTED = "changes.execution.step_committed"


# ---------------------------------------------------------------------------
# Worker 契约值对象
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class TargetView:
    """步骤目标在分支头的现状：身份、kind 与当前内容（Repair Context 主体）。"""

    artifact_id: str
    kind: str
    content: dict[str, Any]

    def to_content(self) -> dict[str, Any]:
        return {"artifact_id": self.artifact_id, "kind": self.kind, "content": self.content}

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> TargetView:
        return cls(
            artifact_id=str(content.get("artifact_id", "")),
            kind=str(content.get("kind", "")),
            content=dict(content.get("content") or {}),
        )


@dataclass(frozen=True, slots=True)
class StepContext:
    """一步的 Repair Context：目标现状 + 影响理由（§5「每个步骤只得到
    当前步骤所需的 Repair Context」）。"""

    change_set_id: str
    step_id: str
    layer: str
    title: str
    targets: tuple[TargetView, ...]
    reasons: tuple[str, ...] = ()

    def to_content(self) -> dict[str, Any]:
        return {
            "change_set_id": self.change_set_id,
            "step_id": self.step_id,
            "layer": self.layer,
            "title": self.title,
            "targets": [t.to_content() for t in self.targets],
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class PatchUnit:
    """单个目标的全量新内容（不是 diff）：重放天然收敛，不二次叠加。"""

    artifact_id: str
    content: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PatchProposal:
    """Worker 的一次提案：零补丁是合法空结果（该步 no_change，四透镜③）。"""

    patches: tuple[PatchUnit, ...] = ()
    note: str = ""


# ---------------------------------------------------------------------------
# 提案守卫（纯函数，硬约束）
# ---------------------------------------------------------------------------
def validate_proposal(proposal: PatchProposal, step: PlanStep, *, where: str) -> None:
    """Worker 只能在批准范围内提案：越界/重复/空目标一律拒绝（§8 退出条件）。

    错误消息三要素（四透镜①）：哪里错（哪个 patch）、为什么错（不在本步
    批准范围/重复）、怎样算对（本步允许的目标清单）。
    """
    allowed = set(step.target_artifact_ids)
    seen: set[str] = set()
    for unit in proposal.patches:
        if not unit.artifact_id:
            raise violation(
                ec.CHANGES_INVALID_COMMAND,
                where,
                "提案里有一个补丁没有目标（artifact_id 为空）",
                f"每个补丁必须指向本步目标之一：{sorted(allowed)}",
                step_id=step.step_id,
            )
        if unit.artifact_id in seen:
            raise violation(
                ec.CHANGES_INVALID_COMMAND,
                where,
                f"目标 {unit.artifact_id!r} 在提案里出现多次",
                "每个目标只提案一个补丁（全量内容）；要表达多次修改请合并内容",
                step_id=step.step_id,
                artifact_id=unit.artifact_id,
            )
        seen.add(unit.artifact_id)
        if unit.artifact_id not in allowed:
            raise violation(
                ec.CHANGES_PROPOSAL_OUT_OF_SCOPE,
                where,
                f"补丁目标 {unit.artifact_id!r} 不在本步批准范围内",
                f"本步（{step.step_id}）只允许修改 {sorted(allowed)}；"
                "其他目标请在各自的步骤提案，或由作者调整计划范围",
                step_id=step.step_id,
                artifact_id=unit.artifact_id,
                allowed=sorted(allowed),
            )


# ---------------------------------------------------------------------------
# 执行记录模型
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class StepRecord:
    """单步执行结果：提交到分支的 Commit ID 可追溯（失败步 commit_id 为空）。"""

    step_id: str
    outcome: str
    commit_id: str = ""
    note: str = ""

    def to_content(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "outcome": self.outcome,
            "commit_id": self.commit_id,
            "note": self.note,
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> StepRecord:
        return cls(
            step_id=str(content.get("step_id", "")),
            outcome=str(content.get("outcome", STEP_FAILED)),
            commit_id=str(content.get("commit_id", "")),
            note=str(content.get("note", "")),
        )


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    """一次执行尝试的 Job 状态（kind ``changes.execution``）。

    每次尝试一条记录（不复用 Artifact）：尝试本身就是可审计事件，
    失败原因与已完成步骤都在记录里，重启恢复据此续行。
    """

    execution_id: str
    change_set_id: str
    job_id: str
    branch_name: str
    base_commit_id: str
    outcome: str
    steps: tuple[StepRecord, ...] = ()
    error_code: str = ""
    error_message: str = ""
    executed_by: str = ""
    at: str = ""
    schema_version: int = 1

    def to_content(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "execution_id": self.execution_id,
            "change_set_id": self.change_set_id,
            "job_id": self.job_id,
            "branch_name": self.branch_name,
            "base_commit_id": self.base_commit_id,
            "outcome": self.outcome,
            "steps": [s.to_content() for s in self.steps],
            "error_code": self.error_code,
            "error_message": self.error_message,
            "executed_by": self.executed_by,
            "at": self.at,
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> ExecutionRecord:
        return cls(
            execution_id=str(content.get("execution_id", "")),
            change_set_id=str(content.get("change_set_id", "")),
            job_id=str(content.get("job_id", "")),
            branch_name=str(content.get("branch_name", "")),
            base_commit_id=str(content.get("base_commit_id", "")),
            outcome=str(content.get("outcome", OUTCOME_FAILED)),
            steps=tuple(StepRecord.from_content(s) for s in (content.get("steps") or ())),
            error_code=str(content.get("error_code", "")),
            error_message=str(content.get("error_message", "")),
            executed_by=str(content.get("executed_by", "")),
            at=str(content.get("at", "")),
            schema_version=int(content.get("schema_version", 1)),
        )
