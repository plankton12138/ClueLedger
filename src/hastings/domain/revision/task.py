"""有限执行：调用预算、修正轮次与任务状态（V03-7.3，计划 M10 第 7/8/11 条）。

一次整体调整不是「一直跑到模型说完了」：它有一个**总调用预算**（工程初值 12 次，
启动前显示、作者可以缩小），修正最多**两轮**，并且子工作者与修正都从同一份预算里
扣——不存在「再开一个子任务就有了新预算」这种绕法。

达到上限、覆盖没做完、或关键选择还没定时，任务进入 ``waiting`` 并**保留内部进度**
（预算已用数、修正轮次、覆盖台账都在），不标完成。作者显式「继续」才追加预算、
推进 attempt；停止/失败同样保留正式稳定版与内部进度。

本模块是纯函数：不读库、不调模型、不写任何东西。持久化与端点见
``application/services/revision_task_service.py`` 与 ``interfaces/api/revision.py``。

与 ``application/execution.ExecutionGrant`` 的关系：Grant 是**单次派发**的凭据
（一次模型功能调用一份），本模块的 ``CallBudget`` 是**整次任务**的账本，两者
计数口径一致但生命周期不同——Grant 随 Job 落库，预算随任务记录落库。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from hastings.domain.revision import error_codes as ec
from hastings.domain.revision.errors import violation

__all__ = [
    "ACTIVE_STATES",
    "DEFAULT_CALL_BUDGET",
    "MAX_REPAIR_ROUNDS",
    "PublishBlocker",
    "RevisionBudget",
    "RevisionTask",
    "STATE_CANCELLED",
    "STATE_COMPLETED",
    "STATE_QUEUED",
    "STATE_RETRYABLE_FAILED",
    "STATE_RUNNING",
    "STATE_TERMINAL_FAILED",
    "STATE_WAITING",
    "TASK_STATES",
    "WAIT_AUTHOR_CHOICE",
    "WAIT_BUDGET",
    "WAIT_INTERRUPTED",
    "WAIT_REASONS",
    "WAIT_REPAIR_LIMIT",
    "WAIT_UNCOVERED",
    "publication_blockers",
]

#: 工程初值（计划 M10 第 7 条）：一次整体任务默认最多 12 次模型调用。
#: 启动时可以缩小，不能放大；放大只能经作者显式「继续」追加。
DEFAULT_CALL_BUDGET = 12
#: 同一授权内的修正轮次上限（计划 M10 第 7 条）。
MAX_REPAIR_ROUNDS = 2

# ---- 任务状态（与 JobStore.STATES 同名，便于复用任务查询口径）-------------
STATE_QUEUED = "queued"
STATE_RUNNING = "running"
STATE_WAITING = "waiting"
STATE_COMPLETED = "completed"
STATE_CANCELLED = "cancelled"
STATE_RETRYABLE_FAILED = "retryable_failed"
STATE_TERMINAL_FAILED = "terminal_failed"

TASK_STATES: tuple[str, ...] = (
    STATE_QUEUED,
    STATE_RUNNING,
    STATE_WAITING,
    STATE_COMPLETED,
    STATE_CANCELLED,
    STATE_RETRYABLE_FAILED,
    STATE_TERMINAL_FAILED,
)

#: 「进行中」：同一作品同时只允许一个，新的整体任务必须继续或先停止它。
ACTIVE_STATES: frozenset[str] = frozenset((STATE_QUEUED, STATE_RUNNING, STATE_WAITING))

# ---- 等待原因（等待不是失败，必须能说清「在等什么」）---------------------
WAIT_BUDGET = "budget_exhausted"
WAIT_UNCOVERED = "uncovered"
WAIT_AUTHOR_CHOICE = "author_choice"
WAIT_REPAIR_LIMIT = "repair_limit"
WAIT_INTERRUPTED = "interrupted"

WAIT_REASONS: tuple[str, ...] = (
    WAIT_BUDGET,
    WAIT_UNCOVERED,
    WAIT_AUTHOR_CHOICE,
    WAIT_REPAIR_LIMIT,
    WAIT_INTERRUPTED,
)


@dataclass(frozen=True, slots=True)
class RevisionBudget:
    """整次任务的调用账本：总上限、已用次数、已用修正轮次。

    所有计数都是**累积**的：继续任务只追加 ``call_limit``，不清零 ``calls_used``，
    这样作者能看出「这一轮又花了多少次」，也无法通过继续把旧消耗洗掉。
    """

    call_limit: int
    calls_used: int = 0
    repair_rounds: int = 0

    def __post_init__(self) -> None:
        if self.call_limit < 1:
            raise violation(
                ec.REVISION_BUDGET_INVALID,
                where="revision_task.budget",
                why=f"调用预算必须为正，收到 {self.call_limit}",
                fix="给至少 1 次的预算；默认上限是 12 次",
                call_limit=self.call_limit,
            )
        if self.calls_used < 0 or self.calls_used > self.call_limit:
            raise violation(
                ec.REVISION_BUDGET_INVALID,
                where="revision_task.budget",
                why=f"已用次数 {self.calls_used} 超出 0..{self.call_limit}",
                fix="已用次数不能为负，也不能超过总预算",
                calls_used=self.calls_used,
                call_limit=self.call_limit,
            )
        if self.repair_rounds < 0 or self.repair_rounds > MAX_REPAIR_ROUNDS:
            raise violation(
                ec.REVISION_BUDGET_INVALID,
                where="revision_task.budget",
                why=f"修正轮次 {self.repair_rounds} 超出 0..{MAX_REPAIR_ROUNDS}",
                fix=f"一次授权内最多修正 {MAX_REPAIR_ROUNDS} 轮",
                repair_rounds=self.repair_rounds,
            )

    @property
    def remaining(self) -> int:
        return self.call_limit - self.calls_used

    @property
    def exhausted(self) -> bool:
        return self.calls_used >= self.call_limit

    @property
    def can_repair(self) -> bool:
        return self.repair_rounds < MAX_REPAIR_ROUNDS

    def consume(self, calls: int = 1) -> RevisionBudget:
        """记录 ``calls`` 次模型调用；**要么整笔记上，要么一次也不记**。

        子工作者必须把它的调用数一起传进来（``calls=3``），不能自己开一份预算。
        超出剩余次数时直接拒绝，不静默截断——否则「预算 12」会变成「预算 12+半次」。
        """
        if calls < 1:
            raise violation(
                ec.REVISION_BUDGET_INVALID,
                where="revision_task.budget.consume",
                why=f"每次记账至少 1 次调用，收到 {calls}",
                fix="子工作者把自身调用数合并进来（calls=n），不要新建子预算",
                calls=calls,
            )
        if self.calls_used + calls > self.call_limit:
            raise violation(
                ec.REVISION_BUDGET_EXHAUSTED_CONFLICT,
                where="revision_task.budget.consume",
                why=(
                    f"本次要记 {calls} 次调用，但只剩 {self.remaining} 次"
                    f"（{self.calls_used}/{self.call_limit}）"
                ),
                fix="作者选择继续（追加预算）或缩小范围；内部进度已保留，不会丢弃已读章节",
                calls=calls,
                calls_used=self.calls_used,
                call_limit=self.call_limit,
            )
        return replace(self, calls_used=self.calls_used + calls)

    def with_repair_round(self) -> RevisionBudget:
        """记一轮「发现冲突并修正」；超过两轮即等待作者，不再自动重试。"""
        if not self.can_repair:
            raise violation(
                ec.REVISION_REPAIR_LIMIT_CONFLICT,
                where="revision_task.budget.repair",
                why=f"修正轮次已达上限（{self.repair_rounds}/{MAX_REPAIR_ROUNDS}）",
                fix="作者选择继续（新一轮授权）或接受当前结果；不会自动再修",
                repair_rounds=self.repair_rounds,
            )
        return replace(self, repair_rounds=self.repair_rounds + 1)

    def with_additional_calls(self, extra: int) -> RevisionBudget:
        """作者继续：在既有账本上追加预算，保留已用次数与修正轮次。"""
        if extra < 1:
            raise violation(
                ec.REVISION_BUDGET_INVALID,
                where="revision_task.budget.continue",
                why=f"继续时至少追加 1 次调用，收到 {extra}",
                fix="给本次继续一个正的追加预算（默认 12 次以内）",
                extra=extra,
            )
        return replace(self, call_limit=self.call_limit + extra)

    def to_wire(self) -> dict[str, Any]:
        return {
            "call_limit": self.call_limit,
            "calls_used": self.calls_used,
            "remaining": self.remaining,
            "repair_rounds": self.repair_rounds,
            "max_repair_rounds": MAX_REPAIR_ROUNDS,
            "exhausted": self.exhausted,
        }

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> RevisionBudget:
        return cls(
            call_limit=int(payload.get("call_limit", DEFAULT_CALL_BUDGET)),
            calls_used=int(payload.get("calls_used", 0)),
            repair_rounds=int(payload.get("repair_rounds", 0)),
        )


@dataclass(frozen=True, slots=True)
class RevisionTask:
    """一次整体调整任务的执行账本与状态；不含正文与覆盖台账本身。

    ``requires_revalidation`` 在「继续」后置为真：作者继续时正式版可能已经前进，
    发布前必须重新核对基础与范围（具体发布在 V03-7.4）。
    """

    task_id: str
    project_id: str
    work_id: str
    base_commit_id: str
    state: str
    budget: RevisionBudget
    attempt: int = 1
    grant_version: int = 1
    request_id: str = ""
    waiting_reason: str = ""
    waiting_detail: str = ""
    requires_revalidation: bool = False
    published_operation_id: str = ""
    published_commit_id: str = ""
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if self.state not in TASK_STATES:
            raise violation(
                ec.REVISION_TASK_INVALID_TRANSITION,
                where="revision_task.state",
                why=f"未知任务状态 {self.state!r}",
                fix=f"状态只能是 {list(TASK_STATES)} 之一",
            )
        if self.waiting_reason and self.waiting_reason not in WAIT_REASONS:
            raise violation(
                ec.REVISION_TASK_INVALID_TRANSITION,
                where="revision_task.waiting_reason",
                why=f"未知等待原因 {self.waiting_reason!r}",
                fix=f"等待原因只能是 {list(WAIT_REASONS)} 之一",
            )

    @classmethod
    def create(
        cls,
        *,
        task_id: str,
        project_id: str,
        work_id: str,
        base_commit_id: str,
        request_id: str = "",
        call_budget: int | None = None,
        at: str = "",
    ) -> RevisionTask:
        """开任务：预算只能等于或小于默认上限（作者可缩小，不能放大）。"""
        limit = DEFAULT_CALL_BUDGET if call_budget is None else call_budget
        if limit < 1 or limit > DEFAULT_CALL_BUDGET:
            raise violation(
                ec.REVISION_BUDGET_INVALID,
                where="revision_task.create.budget",
                why=f"启动预算须在 1..{DEFAULT_CALL_BUDGET} 之间，收到 {limit}",
                fix=(
                    f"启动时只能缩小预算（默认 {DEFAULT_CALL_BUDGET} 次）；"
                    "需要更多次数时，任务等待后用「继续」追加"
                ),
                call_budget=limit,
                default_budget=DEFAULT_CALL_BUDGET,
            )
        return cls(
            task_id=task_id,
            project_id=project_id,
            work_id=work_id,
            base_commit_id=base_commit_id,
            state=STATE_QUEUED,
            budget=RevisionBudget(call_limit=limit),
            request_id=request_id,
            created_at=at,
            updated_at=at,
        )

    @property
    def is_active(self) -> bool:
        return self.state in ACTIVE_STATES

    def _require_running(self, action: str) -> None:
        if self.state != STATE_RUNNING:
            raise violation(
                ec.REVISION_TASK_NOT_RUNNING,
                where=f"revision_task.{action}",
                why=f"任务当前是 {self.state}，只有 running 才能{action}",
                fix="先开始或继续任务；等待中的任务需要作者追加预算",
                state=self.state,
                task_id=self.task_id,
            )

    def begin(self, *, at: str = "") -> RevisionTask:
        if self.state != STATE_QUEUED:
            raise violation(
                ec.REVISION_TASK_INVALID_TRANSITION,
                where="revision_task.begin",
                why=f"只有 queued 能开始，当前 {self.state}",
                fix="新建任务或继续已有任务",
                state=self.state,
            )
        return replace(self, state=STATE_RUNNING, updated_at=at)

    def consume(self, calls: int = 1, *, at: str = "") -> RevisionTask:
        self._require_running("记调用")
        return replace(self, budget=self.budget.consume(calls), updated_at=at)

    def start_repair(self, *, at: str = "") -> RevisionTask:
        self._require_running("开始修正")
        return replace(self, budget=self.budget.with_repair_round(), updated_at=at)

    def wait(self, reason: str, detail: str = "", *, at: str = "") -> RevisionTask:
        """进入等待：保留预算与进度，只改状态与原因。"""
        if reason not in WAIT_REASONS:
            raise violation(
                ec.REVISION_TASK_INVALID_TRANSITION,
                where="revision_task.wait",
                why=f"未知等待原因 {reason!r}",
                fix=f"等待原因只能是 {list(WAIT_REASONS)} 之一",
            )
        if self.state not in (STATE_QUEUED, STATE_RUNNING):
            return self  # 已在等待/终态：幂等，不覆盖先到的原因
        return replace(
            self,
            state=STATE_WAITING,
            waiting_reason=reason,
            waiting_detail=detail,
            updated_at=at,
        )

    def stop(self, *, at: str = "") -> RevisionTask:
        """作者停止：保留已用预算与内部进度；已完成的任务不回滚。"""
        if self.state in (STATE_COMPLETED, STATE_CANCELLED):
            return self
        return replace(
            self,
            state=STATE_CANCELLED,
            waiting_reason="",
            waiting_detail="",
            updated_at=at,
        )

    def fail(self, reason: str, *, retryable: bool, at: str = "") -> RevisionTask:
        if self.state != STATE_RUNNING:
            return self
        return replace(
            self,
            state=STATE_RETRYABLE_FAILED if retryable else STATE_TERMINAL_FAILED,
            waiting_detail=reason,
            updated_at=at,
        )

    def continue_with(self, additional_calls: int, *, at: str = "") -> RevisionTask:
        """作者显式继续：追加预算、推进 attempt 与授权版本，并要求发布前重验。

        已停止（``cancelled``）、等待、失败都能继续——计划第 11 条把「停止后继续」
        当作正常路径；只有已完成的任务不能继续。
        """
        if self.state == STATE_COMPLETED:
            raise violation(
                ec.REVISION_TASK_COMPLETED,
                where="revision_task.continue",
                why="任务已完成，继续不会产生新的修改",
                fix="要再改一次就新建整体任务；已完成的结果已进入正式版",
                task_id=self.task_id,
            )
        if self.state not in (
            STATE_WAITING,
            STATE_CANCELLED,
            STATE_RETRYABLE_FAILED,
            STATE_TERMINAL_FAILED,
        ):
            raise violation(
                ec.REVISION_CONTINUE_NOT_ALLOWED,
                where="revision_task.continue",
                why=f"任务当前是 {self.state}，只有等待/停止/失败的任务需要继续",
                fix="运行中的任务会自己往下走；已完成的任务不能继续",
                state=self.state,
            )
        return replace(
            self,
            state=STATE_RUNNING,
            budget=self.budget.with_additional_calls(additional_calls),
            attempt=self.attempt + 1,
            grant_version=self.grant_version + 1,
            waiting_reason="",
            waiting_detail="",
            requires_revalidation=True,
            updated_at=at,
        )

    def complete(self, *, at: str = "") -> RevisionTask:
        self._require_running("完成")
        return replace(self, state=STATE_COMPLETED, updated_at=at)

    def mark_published(
        self, *, operation_id: str, commit_id: str, at: str = ""
    ) -> RevisionTask:
        """发布成功：任务完成，并记下本次操作与正式版本（V03-7.4）。

        先由统一发布管线提交、再标完成——发布失败时任务仍是 running，暂存内容
        原样保留，作者可以修正后重试。
        """
        self._require_running("发布")
        if not operation_id:
            raise violation(
                ec.REVISION_PUBLISH_EMPTY,
                where="revision_task.publish",
                why="发布结果缺少 operation_id",
                fix="发布必须带稳定的操作 ID（op_ 前缀），以便回执与撤销找回",
            )
        return replace(
            self,
            state=STATE_COMPLETED,
            published_operation_id=operation_id,
            published_commit_id=commit_id,
            waiting_reason="",
            waiting_detail="",
            updated_at=at,
        )

    def to_wire(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "work_id": self.work_id,
            "base_commit_id": self.base_commit_id,
            "state": self.state,
            "budget": self.budget.to_wire(),
            "attempt": self.attempt,
            "grant_version": self.grant_version,
            "request_id": self.request_id,
            "waiting_reason": self.waiting_reason,
            "waiting_detail": self.waiting_detail,
            "requires_revalidation": self.requires_revalidation,
            "published_operation_id": self.published_operation_id,
            "published_commit_id": self.published_commit_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> RevisionTask:
        return cls(
            task_id=str(payload["task_id"]),
            project_id=str(payload["project_id"]),
            work_id=str(payload.get("work_id", "")),
            base_commit_id=str(payload.get("base_commit_id", "")),
            state=str(payload.get("state", STATE_QUEUED)),
            budget=RevisionBudget.from_wire(payload.get("budget") or {}),
            attempt=int(payload.get("attempt", 1)),
            grant_version=int(payload.get("grant_version", 1)),
            request_id=str(payload.get("request_id", "")),
            waiting_reason=str(payload.get("waiting_reason", "")),
            waiting_detail=str(payload.get("waiting_detail", "")),
            requires_revalidation=bool(payload.get("requires_revalidation", False)),
            published_operation_id=str(payload.get("published_operation_id", "")),
            published_commit_id=str(payload.get("published_commit_id", "")),
            created_at=str(payload.get("created_at", "")),
            updated_at=str(payload.get("updated_at", "")),
        )


@dataclass(frozen=True, slots=True)
class PublishBlocker:
    """发布前的阻断项；只是**报告**，不代替发布本身（发布在 V03-7.4）。"""

    code: str
    why: str
    fix: str

    def to_wire(self) -> dict[str, str]:
        return {"code": self.code, "why": self.why, "fix": self.fix}


def publication_blockers(
    task: RevisionTask,
    *,
    coverage_complete: bool,
    head_commit_id: str | None,
    pending_choices: int = 0,
) -> tuple[PublishBlocker, ...]:
    """发布前检查：状态、基础、覆盖、预算、待定选择。

    返回空元组才是「可以进入统一发布」。这里不发布、不改任务状态，只把
    「为什么还不能发布、先做什么」逐条说清（计划第 8/11 条）。
    """
    blockers: list[PublishBlocker] = []
    if task.state != STATE_RUNNING:
        blockers.append(
            PublishBlocker(
                code=ec.REVISION_TASK_NOT_READY_CONFLICT,
                why=f"任务当前是 {task.state}，不是运行中",
                fix="等待/停止的任务先由作者继续；已完成的任务不会重复发布",
            )
        )
    if head_commit_id is not None and head_commit_id != task.base_commit_id:
        blockers.append(
            PublishBlocker(
                code=ec.REVISION_BASE_MOVED_CONFLICT,
                why=(
                    f"正式版已从 {task.base_commit_id} 前进到 {head_commit_id}，"
                    "本次内部版本的基础过期"
                ),
                fix="重新核对改动范围并重建提交计划；重叠处交给作者选择，不复用旧检查结果",
            )
        )
    if not coverage_complete:
        blockers.append(
            PublishBlocker(
                code=ec.REVISION_COVERAGE_INCOMPLETE_CONFLICT,
                why="还有章节没有覆盖（未读/只读摘要/受阻）",
                fix="继续读取未覆盖章节并逐章下结论；不能把未读当作无需修改",
            )
        )
    if task.budget.exhausted:
        blockers.append(
            PublishBlocker(
                code=ec.REVISION_BUDGET_EXHAUSTED_CONFLICT,
                why=f"调用预算已用尽（{task.budget.calls_used}/{task.budget.call_limit}）",
                fix="作者选择继续（追加预算）或缩小范围；内部进度已保留",
            )
        )
    if pending_choices > 0:
        blockers.append(
            PublishBlocker(
                code=ec.REVISION_CHOICE_PENDING_CONFLICT,
                why=f"还有 {pending_choices} 个关键选择未定",
                fix="先回答影响执行的选择（最多 1–2 个），再发布",
            )
        )
    return tuple(blockers)
