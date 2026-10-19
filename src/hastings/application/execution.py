"""0.3 显式启动凭据（V03-0.3）：每项模型功能必须有可追溯的作者启动依据。

不变量 I05（每项模型功能有可追溯的作者启动依据）与 I06（打开作品、保存、
恢复、模型连接成功不自行创建模型任务）的执行点。凭据是**应用持久数据**，
随 Job payload 落库；界面不能伪造为后端已授权——派发前统一检查：

1. 凭据存在且功能匹配（讨论/起草/修订/摘要/建议/整体调整）；
2. 分析范围与写范围明确（无写权限的功能 ``write_scope=None``）；
3. 调用预算未耗尽（``calls_used < call_budget``）；
4. 连接快照仍可解析（只含 connection_id/revision/provider/model，绝不含 Key）。

任何一条不满足返回 ``EXECUTION_NOT_AUTHORIZED`` / ``TASK_BUDGET_EXHAUSTED``，
**不运行、不部分运行**（执行指导 §3.4）。恢复策略配套：重启时执行中的
模型调用无法确认完成，标 interrupted 等作者继续，不自动重发（V03-0.3）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal

from hastings.application.errors import ViolationError
from hastings.domain.revision.task import DEFAULT_CALL_BUDGET

__all__ = [
    "DEFAULT_CALL_BUDGET",
    "JOB_TYPE_FUNCTIONS",
    "TASK_KIND_FUNCTIONS",
    "ConnectionSnapshot",
    "ExecutionGrant",
    "GrantFunction",
    "GrantSource",
    "authorize_dispatch",
    "build_grant",
    "grant_from_wire",
    "grant_to_wire",
]

#: 工程初值（M10：默认一次整体任务最多 12 次模型调用）；V03-0.7 以实际样本
#: 测量后写入统一配置——初值是防护值，不是能力承诺。V03-7.3 起与任务级
#: ``RevisionBudget`` 共用同一常量，避免两处默认值漂移。
GrantSource = Literal["author_message", "author_button"]

#: discussion 为普通对话（作者发送即授权，不经 JobStore 派发）增设；
#: 其余五项与执行指导 §3.4 骨架一致。
GrantFunction = Literal[
    "discussion",
    "draft",
    "revision",
    "summary",
    "suggestion",
    "holistic_revision",
]

GRANT_FUNCTIONS: tuple[GrantFunction, ...] = (
    "discussion",
    "draft",
    "revision",
    "summary",
    "suggestion",
    "holistic_revision",
)

#: Job 类型 → 凭据功能。Job 类型即 ``ContextPackage.task_kind``（WorkerRole：
#: coordinator/planner/writer/reviser/auditor/style_observer）。未登记的类型
#: 一律不派发（fail closed）：旧自动队列的 style_observer 与未知类型过不了门禁。
JOB_TYPE_FUNCTIONS: dict[str, GrantFunction] = {
    "planner": "draft",
    "writer": "draft",
    "reviser": "revision",
    "auditor": "suggestion",
    "coordinator": "suggestion",
}

#: 任务草稿类型 → 凭据功能（``TaskDraft.task_kind`` 命名空间，与 Job 类型不同）。
TASK_KIND_FUNCTIONS: dict[str, GrantFunction] = {
    "plan": "draft",
    "chapter_draft": "draft",
    "check": "suggestion",
}


@dataclass(frozen=True, slots=True)
class ConnectionSnapshot:
    """任务启动时的模型连接快照；绝不含 Key（计划 §4.1、§6.5）。"""

    connection_id: str
    revision: int
    provider: str
    model: str

    def to_wire(self) -> dict[str, Any]:
        return {
            "connection_id": self.connection_id,
            "revision": self.revision,
            "provider": self.provider,
            "model": self.model,
        }

    @classmethod
    def from_wire(cls, payload: Any) -> ConnectionSnapshot:
        if not isinstance(payload, Mapping):
            raise _not_authorized("connection", "连接快照缺失或不是对象")
        missing = [f for f in ("connection_id", "provider", "model") if not payload.get(f)]
        if missing:
            raise _not_authorized(
                "connection",
                f"连接快照缺少字段 {missing}",
                fix="任务启动时定格当前连接（connection_id/revision/provider/model）后重试",
            )
        return cls(
            connection_id=str(payload["connection_id"]),
            revision=int(payload.get("revision", 0)),
            provider=str(payload["provider"]),
            model=str(payload["model"]),
        )


@dataclass(frozen=True, slots=True)
class ExecutionGrant:
    """字段即审计记录，缺一不可派发（执行指导 §3.4 骨架收编）。"""

    grant_id: str
    source: GrantSource
    function: GrantFunction
    analysis_scope: tuple[str, ...]
    call_budget: int
    connection: ConnectionSnapshot
    source_message_id: str | None = None
    write_scope: tuple[str, ...] | None = None
    calls_used: int = 0
    grant_version: int = 1

    def to_wire(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "grant_id": self.grant_id,
            "source": self.source,
            "source_message_id": self.source_message_id,
            "function": self.function,
            "analysis_scope": list(self.analysis_scope),
            "write_scope": None if self.write_scope is None else list(self.write_scope),
            "call_budget": self.call_budget,
            "calls_used": self.calls_used,
            "grant_version": self.grant_version,
            "connection": self.connection.to_wire(),
        }
        return payload

    @classmethod
    def from_wire(cls, payload: Any) -> ExecutionGrant:
        if not isinstance(payload, Mapping):
            raise _not_authorized("grant", "凭据缺失或不是对象")
        missing = [
            field
            for field in ("grant_id", "source", "function", "analysis_scope", "call_budget")
            if payload.get(field) is None
        ]
        if missing:
            raise _not_authorized(
                "grant",
                f"凭据缺少字段 {missing}",
                fix="由作者显式启动（消息或按钮）生成完整凭据后重试",
            )
        return cls(
            grant_id=str(payload["grant_id"]),
            source=str(payload["source"]),  # type: ignore[arg-type]
            function=str(payload["function"]),  # type: ignore[arg-type]
            analysis_scope=tuple(str(s) for s in payload["analysis_scope"]),
            call_budget=int(payload["call_budget"]),
            connection=ConnectionSnapshot.from_wire(payload.get("connection")),
            source_message_id=(
                str(payload["source_message_id"]) if payload.get("source_message_id") else None
            ),
            write_scope=(
                None
                if payload.get("write_scope") is None
                else tuple(str(s) for s in payload["write_scope"])
            ),
            calls_used=int(payload.get("calls_used", 0)),
            grant_version=int(payload.get("grant_version", 1)),
        )

    def consume(self) -> ExecutionGrant:
        """记录一次模型调用；预算耗尽显式失败，不静默继续（I05/I11）。"""
        if self.calls_used >= self.call_budget:
            raise ViolationError(
                code="TASK_BUDGET_EXHAUSTED",
                where=f"grant({self.grant_id})",
                why=f"调用预算已耗尽（{self.calls_used}/{self.call_budget}）",
                fix="作者选择继续（新预算）或缩小范围；内部进度已保留",
                details={
                    "grant_id": self.grant_id,
                    "calls_used": self.calls_used,
                    "call_budget": self.call_budget,
                },
            )
        return replace(self, calls_used=self.calls_used + 1)


def _not_authorized(field: str, why: str, *, fix: str | None = None) -> ViolationError:
    return ViolationError(
        code="EXECUTION_NOT_AUTHORIZED",
        where=f"execution_grant.{field}",
        why=why,
        fix=fix or "由作者显式启动对应功能（发送消息或点击按钮）后重试",
    )


def build_grant(
    *,
    grant_id: str,
    source: GrantSource,
    function: GrantFunction,
    analysis_scope: tuple[str, ...] = (),
    write_scope: tuple[str, ...] | None = None,
    call_budget: int = DEFAULT_CALL_BUDGET,
    connection: ConnectionSnapshot,
    source_message_id: str | None = None,
) -> ExecutionGrant:
    """作者显式动作 → 凭据。source_message_id 在 author_button 时可空，
    但按钮事件必须可回查（grant_id 即回查键）。"""
    if source == "author_message" and not source_message_id:
        raise _not_authorized(
            "source",
            "author_message 来源缺少 source_message_id",
            fix="引用触发本次启动的作者消息；按钮启动请用 author_button",
        )
    if function not in GRANT_FUNCTIONS:
        raise _not_authorized(
            "function",
            f"未知功能 {function!r}",
            fix=f"function 只接受 {list(GRANT_FUNCTIONS)}",
        )
    if call_budget <= 0:
        raise _not_authorized("call_budget", "调用预算必须为正", fix="设置大于 0 的预算")
    return ExecutionGrant(
        grant_id=grant_id,
        source=source,
        source_message_id=source_message_id,
        function=function,
        analysis_scope=tuple(analysis_scope),
        write_scope=None if write_scope is None else tuple(write_scope),
        call_budget=call_budget,
        connection=connection,
    )


def authorize_dispatch(
    grant_wire: Any,
    *,
    function: GrantFunction | None,
    where: str,
) -> ExecutionGrant:
    """派发门禁（I05）：凭据存在、功能匹配、预算未耗尽、连接可解析。

        ``function`` 为 None 表示该 Job 类型未登记功能映射——fail closed，
        与「凭据缺失」同样拒绝派发。合法调用方：worker_loop 自动派发与
    ``/agent-jobs/{id}/run`` 手动运行，两条入口同一检查。
    """
    grant = ExecutionGrant.from_wire(grant_wire)
    if function is None:
        raise _not_authorized(
            "function",
            f"任务类型没有登记功能映射（{where}）",
            fix="登记 JOB_TYPE_FUNCTIONS 或改用已登记的任务类型；未登记类型不派发",
        )
    if grant.function != function:
        raise _not_authorized(
            "function",
            f"凭据功能 {grant.function!r} 与任务要求 {function!r} 不符",
            fix="以对应功能重新启动任务；一个凭据只授权一个功能",
        )
    if grant.calls_used >= grant.call_budget:
        raise ViolationError(
            code="TASK_BUDGET_EXHAUSTED",
            where=where,
            why=f"调用预算已耗尽（{grant.calls_used}/{grant.call_budget}）",
            fix="作者选择继续（新预算）或缩小范围；内部进度已保留",
            details={"grant_id": grant.grant_id, "call_budget": grant.call_budget},
        )
    return grant


def grant_to_wire(grant: ExecutionGrant) -> dict[str, Any]:
    return grant.to_wire()


def grant_from_wire(payload: Any) -> ExecutionGrant:
    return ExecutionGrant.from_wire(payload)
