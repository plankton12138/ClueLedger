"""会话累计用量的统计口径（0.9 U0）。

事件里的 ``usage`` 是适配层归一化后的供应商数据，但四类协议对「输入」的定义不同：
OpenAI 两类的 input 已经扣掉缓存读写，Anthropic 的 input 本身就不含缓存。
界面要显示的是同一个东西——这一次请求让模型看了多少输入、写了多少输出，
所以统计前先换算成一份共同口径，再谈累计。

三条规则固定下来，后续阶段不再各自解释：

- **输入包含缓存。** 完整输入 = 未缓存输入 + 缓存读取 + 缓存写入；缓存只是计费方式，
  它同样占上下文，不能让「命中缓存的请求」看起来消耗很小。
- **推理不重复计入。** 两类协议都把推理算在输出里，输出字段直接使用，不再叠加推理分项。
- **零不等于上报。** 一次真实请求必然有输入。整份用量全为零，说明适配层没解析到字段，
  和「供应商确实报了零」不是同一件事，按未上报处理。

分项仍然可能只报了一部分。此时已知项照常累计，未知项保持 ``None``：
把它填成 0 会让累计值看起来精确，实际上是猜的。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from hastings.server.model_limits import ModelLimits
from hastings.workspace.context_usage import (
    MESSAGE_OVERHEAD_TOKENS,
    METHOD,
    BudgetState,
    budget_for_limit,
    count_tokens,
)

__all__ = [
    "ContextSnapshot",
    "ReportedUsage",
    "UsageSummary",
    "UsageTotals",
    "context_snapshot",
    "reported_usage",
    "summarize_events",
]


@dataclass(frozen=True, slots=True)
class ReportedUsage:
    """一次请求的用量，按统计口径换算后的形状。

    ``input_tokens`` 是含缓存的完整输入，``cache_read_tokens`` 与
    ``cache_write_tokens`` 是它的子项而不是额外消耗。任何字段为 ``None``
    都表示这一项没有得到可靠数值，不表示零。
    """

    total_tokens: int | None
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None


def _count(value: Any) -> int | None:
    """只接受非负整数；布尔与字符串不做静默转换。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _sum_known(values: list[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def reported_usage(raw: Any) -> ReportedUsage | None:
    """把事件里的一条 usage 换算成统计口径；无法确认时返回 ``None``。"""
    if not isinstance(raw, Mapping):
        return None
    fresh = _count(raw.get("input"))
    cache_read = _count(raw.get("cache_read"))
    cache_write = _count(raw.get("cache_write"))
    output = _count(raw.get("output"))
    input_tokens = _sum_known([fresh, cache_read, cache_write])

    # 整份为零说明这次请求没有可用用量：真实请求不可能没有任何输入。
    components = [input_tokens, output, cache_read, cache_write]
    if all(value is None or value == 0 for value in components):
        return None

    # 适配层只填了 input/output 时 total 仍是默认零；用已知部分重算，不留假总量。
    total = _count(raw.get("total_tokens"))
    if not total:
        total = _sum_known([input_tokens, output])

    return ReportedUsage(
        total_tokens=total,
        input_tokens=input_tokens,
        output_tokens=output,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
    )


@dataclass(frozen=True, slots=True)
class UsageTotals:
    """一个会话的累计用量对外视图。

    数值与计数分开：``finished_requests`` 是已结束的真实请求数，
    ``requests_without_usage`` 说明其中多少条没有可靠用量。界面据此决定显示
    具体数值、零，还是「部分请求未统计」。请求计数不用成功状态过滤——失败和
    停止的请求只要消耗了用量，就已经发生。

    ``requests_in_progress`` 单独计数：运行中的请求已经发出，但还没有终态用量。
    """

    total_tokens: int | None
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    finished_requests: int
    requests_without_usage: int
    requests_in_progress: int
    model_duration_ms: int | None

    def as_dict(self) -> dict[str, int | None]:
        """接口形状；字段名与前端 ``UsageTotals`` 一一对应。"""
        return {
            "total_tokens": self.total_tokens,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "finished_requests": self.finished_requests,
            "requests_without_usage": self.requests_without_usage,
            "requests_in_progress": self.requests_in_progress,
            "model_duration_ms": self.model_duration_ms,
        }


@dataclass(frozen=True, slots=True)
class UsageSummary:
    """一个会话的累计结果，以及它读到的事件游标。"""

    totals: UsageTotals
    #: 本次统计覆盖到的会话事件序号；客户端据此判断视图是否落后。
    through_seq: int
    #: 记录链路自身的问题（例如同一请求出现两个终态）。只作诊断，不参与累计。
    diagnostics: tuple[str, ...]


def summarize_events(events: Sequence[Mapping[str, Any]]) -> UsageSummary:
    """从会话事件累计用量。

    以请求为单位去重：一个 ``operation_id`` 只算一次。事件日志会被重放（断线重连、
    客户端带旧游标回读），把重放当成新请求会让累计值凭空翻倍。

    ``model_request_started`` 与 ``model_request_finished`` 各自出场。只出现过开始事件的
    请求计入「进行中」：它确实发出去了，但还没有终态用量。反过来，只有终态而开始事件
    被裁剪的记录仍然计入——少一次计数比漏一次消耗更容易误导。
    """
    started: set[str] = set()
    finished: dict[str, tuple[ReportedUsage | None, int | None]] = {}
    diagnostics: list[str] = []
    through_seq = 0

    for event in events:
        seq = event.get("seq")
        if isinstance(seq, int) and not isinstance(seq, bool):
            through_seq = max(through_seq, seq)
        operation = event.get("operation_id")
        if not isinstance(operation, str) or not operation:
            continue
        kind = event.get("type")
        if kind == "model_request_started":
            started.add(operation)
        elif kind == "model_request_finished":
            duration = event.get("duration_ms")
            record = (
                reported_usage(event.get("usage")),
                duration if isinstance(duration, int) and not isinstance(duration, bool) else None,
            )
            previous = finished.get(operation)
            if previous is not None:
                # 保留先到的一条：它对应的响应确实完成了；后到的多半是重放或竞态。
                if previous != record:
                    diagnostics.append(f"请求 {operation} 存在多个不同的终态，已保留先到的一条")
                continue
            finished[operation] = record

    usages = [usage for usage, _duration in finished.values()]
    known = [usage for usage in usages if usage is not None]
    durations = [duration for _usage, duration in finished.values() if duration is not None]

    def total_of(field: str) -> int | None:
        values = [value for usage in known if (value := getattr(usage, field)) is not None]
        return sum(values) if values else None

    return UsageSummary(
        totals=UsageTotals(
            total_tokens=total_of("total_tokens"),
            input_tokens=total_of("input_tokens"),
            output_tokens=total_of("output_tokens"),
            cache_read_tokens=total_of("cache_read_tokens"),
            cache_write_tokens=total_of("cache_write_tokens"),
            finished_requests=len(finished),
            requests_without_usage=sum(usage is None for usage in usages),
            requests_in_progress=len(started - set(finished)),
            model_duration_ms=sum(durations) if durations else None,
        ),
        through_seq=through_seq,
        diagnostics=tuple(diagnostics),
    )


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    """当前有效上下文的样子，全部由事件推导。

    ``pending`` 还没有任何请求，谈不上占用；``running`` 有一轮正在跑，取它发出前的
    估算；``ready`` 最近一轮已结束，估算再加上之后新增的公开对话。

    运行中的容量取自请求受理时冻结的那份；空闲后按当前选择读取容量与预算。
    同一连接、同一模型可以保留输入估算；切换模型后要重新估算，不能拼接旧占用。

    ``pending`` 时没有可读的冻结事实，容量字段改由调用方传入的当前选择填充——
    这样首次进入也能显示「待估算 / 项目上限」，但估算值仍为 null。
    """

    status: Literal["pending", "running", "ready"]
    estimated_active_tokens: int | None
    last_request_input_tokens: int | None
    #: 模型总窗口；未知时为 null，绝不写入项目预算。
    context_window_tokens: int | None
    #: 有效输入预算：主进度、自动压缩与请求检查共用的那个数。
    input_limit_tokens: int | None
    #: 项目策略上限；已绑定有效模型时是正整数。
    project_input_limit_tokens: int | None
    #: 由已知模型容量算出的可用输入；模型容量未知时为 null。
    model_input_limit_tokens: int | None
    #: 有效上限的出处：project / model / both。
    input_limit_source: str
    #: 连接与精确模型 ID，用于归属；不含密钥或账号凭据。
    model_identity: str
    output_reserve_tokens: int | None
    safety_tokens: int | None
    #: 窗口数字的出处：override / metadata / catalog / unknown。
    capacity_source: str
    budget: BudgetState
    instructions_tokens: int | None
    tools_tokens: int | None
    conversation_tokens: int | None
    method: str
    fingerprint: str
    limits_revision: str
    policy_revision: str


def _estimated_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _latest_measured_input(events: Sequence[Mapping[str, Any]]) -> int | None:
    for event in reversed(events):
        if event.get("type") != "model_request_finished":
            continue
        if event.get("stage") == "compaction":
            continue
        usage = reported_usage(event.get("usage"))
        if usage is not None and usage.input_tokens is not None:
            return usage.input_tokens
    return None


def context_snapshot(
    events: Sequence[Mapping[str, Any]],
    *,
    current: ModelLimits | None = None,
) -> ContextSnapshot:
    """把事件还原成「现在大概占多少」。不装配、不读文件、不调用模型。

    ``current`` 是当前选择的容量与预算。请求运行中保持冻结值；空闲时采用当前值，
    仅在连接与模型身份一致时复用输入估算。未提供当前选择时保留事件视图。
    """
    empty = _pending_snapshot(current)

    started: list[tuple[int, str]] = []
    finished: set[str] = set()
    open_turns: set[str] = set()
    request_turn: str | None = None
    for event in events:
        if event.get("stage") == "compaction":
            continue
        kind = event.get("type")
        turn = event.get("turn_id")
        if isinstance(turn, str):
            if kind == "turn_started":
                open_turns.add(turn)
            elif kind == "run_finished":
                open_turns.discard(turn)
        operation = event.get("operation_id")
        if not isinstance(operation, str) or not operation:
            continue
        seq = _estimated_int(event.get("seq")) or 0
        if kind == "model_request_started":
            started.append((seq, operation))
            request_turn = turn if isinstance(turn, str) and turn in open_turns else None
        elif kind == "model_request_finished":
            finished.add(operation)

    if not started:
        return empty

    # 一轮可能有多次模型请求；工具执行期间仍属于同一轮，不能提前换成新选择的预算。
    running = started[-1][1] not in finished or request_turn in open_turns
    status: Literal["pending", "running", "ready"] = "running" if running else "ready"

    estimate: Mapping[str, Any] | None = None
    estimate_seq = 0
    for event in events:
        if event.get("type") not in ("model_request_started", "model_request_retried"):
            continue
        if event.get("stage") == "compaction":
            continue
        candidate = event.get("request_estimate")
        if isinstance(candidate, Mapping):
            estimate = candidate
            estimate_seq = _estimated_int(event.get("seq")) or 0

    measured = _latest_measured_input(events)
    if estimate is None:
        return dataclasses.replace(
            empty,
            status=status,
            last_request_input_tokens=measured,
        )

    method = str(estimate.get("method") or "")
    instructions = _estimated_int(estimate.get("system_tokens"))
    tools = _estimated_int(estimate.get("tool_tokens"))
    message_tokens = _estimated_int(estimate.get("message_tokens"))
    protocol = _estimated_int(estimate.get("protocol_tokens"))
    base = _estimated_int(estimate.get("total_tokens"))
    conversation = (
        (message_tokens or 0) + (protocol or 0)
        if message_tokens is not None or protocol is not None
        else None
    )

    # 只在本轮结束之后才有「新增对话」：运行中的请求已经包含当时的内容。
    added = 0
    if status == "ready" and method == METHOD:
        added = sum(
            count_tokens(str(event.get("text")))
            for event in events
            if (_estimated_int(event.get("seq")) or 0) > estimate_seq
            and event.get("type") in ("turn_started", "assistant_message")
        )

    estimated = (base + added) if base is not None else None
    fingerprint = str(estimate.get("fingerprint") or "")
    # 请求完成后的工具原文不会跨轮回放；整理后也不能沿用摘要调用的输入。
    # 使用公开历史的同一投影重新估算消息部分，系统与工具沿用最近写作请求的已知值。
    if (
        status == "ready"
        and method == METHOD
        and any(
            event.get("type") in ("run_finished", "context_compacted")
            and (_estimated_int(event.get("seq")) or 0) > estimate_seq
            for event in events
        )
    ):
        from hastings.workspace.compaction import active_compaction
        from hastings.workspace.conversation_context import replay_public_messages

        public = replay_public_messages(
            [dict(event) for event in events], compaction=active_compaction(events)
        )
        wire = json.dumps(
            [message.model_dump(mode="json") for message in public],
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        # 工具开销保持不变，只替换消息开销。
        tool_overhead = _estimated_int(estimate.get("tool_overhead_tokens")) or 0
        conversation = count_tokens(wire) + len(public) * MESSAGE_OVERHEAD_TOKENS + tool_overhead
        added = 0
        estimated = (
            instructions + tools + conversation
            if instructions is not None and tools is not None
            else None
        )
        fingerprint = hashlib.sha256((fingerprint + wire).encode()).hexdigest()
    input_limit = _estimated_int(estimate.get("input_limit_tokens"))
    snapshot = ContextSnapshot(
        status=status,
        estimated_active_tokens=estimated,
        # 供应商给过实测输入时用它：那是真实发出的大小，比估算更可信。
        last_request_input_tokens=measured if measured is not None else base,
        context_window_tokens=_estimated_int(estimate.get("context_window_tokens")),
        input_limit_tokens=input_limit,
        project_input_limit_tokens=_estimated_int(estimate.get("project_input_limit_tokens")),
        model_input_limit_tokens=_estimated_int(estimate.get("model_input_limit_tokens")),
        input_limit_source=str(estimate.get("input_limit_source") or "project"),
        model_identity=str(estimate.get("model_identity") or estimate.get("model") or ""),
        output_reserve_tokens=_estimated_int(estimate.get("output_reserve_tokens")),
        safety_tokens=_estimated_int(estimate.get("safety_tokens")),
        capacity_source=str(estimate.get("capacity_source") or "unknown"),
        budget=budget_for_limit(total_tokens=estimated or 0, input_limit=input_limit),
        instructions_tokens=instructions,
        tools_tokens=tools,
        conversation_tokens=(conversation + added) if conversation is not None else None,
        method=method,
        fingerprint=fingerprint,
        limits_revision=str(estimate.get("limits_revision") or ""),
        policy_revision=str(estimate.get("policy_revision") or ""),
    )
    if running or current is None:
        return snapshot
    return _idle_snapshot(snapshot, current)


def _idle_snapshot(snapshot: ContextSnapshot, current: ModelLimits) -> ContextSnapshot:
    """空闲视图采用当前预算，不修改历史请求；不同模型的输入估算不复用。"""
    selected = _pending_snapshot(current)
    if snapshot.model_identity != selected.model_identity:
        return dataclasses.replace(
            selected,
            status=snapshot.status,
            last_request_input_tokens=snapshot.last_request_input_tokens,
        )

    fingerprint = snapshot.fingerprint
    if fingerprint and (
        snapshot.limits_revision != current.revision
        or snapshot.policy_revision != current.policy_revision
    ):
        fingerprint = hashlib.sha256(f"{fingerprint}|{current.revision}".encode()).hexdigest()
    return dataclasses.replace(
        selected,
        status=snapshot.status,
        estimated_active_tokens=snapshot.estimated_active_tokens,
        last_request_input_tokens=snapshot.last_request_input_tokens,
        budget=(
            budget_for_limit(
                total_tokens=snapshot.estimated_active_tokens, input_limit=current.input_limit()
            )
            if snapshot.estimated_active_tokens is not None
            else "unknown"
        ),
        instructions_tokens=snapshot.instructions_tokens,
        tools_tokens=snapshot.tools_tokens,
        conversation_tokens=snapshot.conversation_tokens,
        method=snapshot.method,
        fingerprint=fingerprint,
    )


def _pending_snapshot(current: ModelLimits | None) -> ContextSnapshot:
    """还没有请求时的空视图；有当前选择就带上容量与预算，估算仍为 null。"""
    return ContextSnapshot(
        status="pending",
        estimated_active_tokens=None,
        last_request_input_tokens=None,
        context_window_tokens=current.context_window_tokens if current else None,
        input_limit_tokens=current.input_limit() if current else None,
        project_input_limit_tokens=current.project_input_limit_tokens if current else None,
        model_input_limit_tokens=current.model_input_limit_tokens if current else None,
        input_limit_source=current.input_limit_source if current else "project",
        model_identity=(f"{current.connection_id}:{current.model}" if current is not None else ""),
        output_reserve_tokens=current.output_reserve_tokens if current else None,
        safety_tokens=current.safety_tokens if current else None,
        capacity_source=current.source if current else "unknown",
        # 没有估算就不判断占比：既不是 0%，也不是占满。
        budget="unknown",
        instructions_tokens=None,
        tools_tokens=None,
        conversation_tokens=None,
        method="",
        fingerprint="",
        limits_revision=current.revision if current else "",
        policy_revision=current.policy_revision if current else "",
    )
