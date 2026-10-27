"""跨轮压缩记录（0.9 C1）。

压缩不改写对话。它追加一条 ``context_compacted`` 事件，记录「哪些轮次被整理成了什么」，
投影时用这条记录把被覆盖的轮次换成一段摘要。原事件一条不动：作者原话是范围校验与
语言一致性的最终依据，摘要只是给下一轮看的简报。

四条读取校验，任何一条不成立就不用这份记录、按未压缩处理：

- 覆盖范围必须是「过去的完整轮次」，保留点必须真的在覆盖范围之后；
- 摘要与条目都不为空——空摘要等于凭空删历史；
- 条目必须引用原事件，没有来源的概括无法回查；
- 覆盖范围内的事件指纹必须与记录一致。原事件被改写后，旧摘要不再代表它们。

降级到未压缩是有代价的（上下文更长），但它不会让模型看到与原文不符的记忆。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from hastings.workspace.context_usage import count_tokens
from hastings.workspace.errors import WorkspaceError
from tau_agent.messages import (
    AgentMessage,
    AssistantMessage,
    ToolResultMessage,
    UserMessage,
    message_text,
)

__all__ = [
    "SUMMARY_PREFIX",
    "CompactionPlan",
    "CompactionRecord",
    "ParsedSummary",
    "SummaryItem",
    "PROMPT_VERSION",
    "active_compaction",
    "build_candidate",
    "build_summary_prompt",
    "parse_summary",
    "plan_compaction",
    "source_digest",
    "summary_message",
    "trim_request_messages",
    "validate_candidate",
]

#: 摘要消息的固定标识。它必须一眼看出是应用整理的，不能被当成作者的新要求。
SUMMARY_PREFIX = "（应用整理的较早对话摘要，供继续任务使用，不是作者的新要求）\n\n"

ItemKind = Literal["goal", "constraint", "author_decision", "suggestion", "pending", "reference"]


@dataclass(frozen=True, slots=True)
class SummaryItem:
    """一条有来源的摘要要点。"""

    item_id: str
    kind: ItemKind
    text: str
    #: 引用哪些原事件（本轮 ``turn_id`` 或事件标识）；空引用不允许。
    source_event_ids: tuple[str, ...]
    #: 作者原话；保留它比保留改写后的概括更能防止语义漂移。
    quote: str | None = None
    supersedes: tuple[str, ...] = ()
    #: 适用用途；事实提取类任务不该拿到整份自由摘要。
    profiles: tuple[str, ...] = ()
    as_of_chapter: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_event_ids", tuple(self.source_event_ids))
        object.__setattr__(self, "supersedes", tuple(self.supersedes))
        object.__setattr__(self, "profiles", tuple(self.profiles))

    def as_dict(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "kind": self.kind,
            "text": self.text,
            "source_event_ids": list(self.source_event_ids),
            "quote": self.quote,
            "supersedes": list(self.supersedes),
            "profiles": list(self.profiles),
            "as_of_chapter": self.as_of_chapter,
        }


@dataclass(frozen=True, slots=True)
class CompactionRecord:
    """一次跨轮整理的完整记录；字段即 ``context_compacted`` 的内容。"""

    compaction_id: str
    kind: Literal["session", "run"]
    #: 受理时冻结的历史上界。
    source_head_seq: int
    #: 被整理到的最后一个事件序号。
    covered_through_seq: int
    #: 第一个原样保留的轮次。
    first_kept_turn_id: str
    source_digest: str
    previous_id: str
    summary: str
    items: tuple[SummaryItem, ...] = field(default_factory=tuple)
    model: str = ""
    prompt_version: int = 1
    estimated_before: int = 0
    estimated_after: int = 0
    request_operation_id: str = ""
    reason: str = "manual"
    created_at: str = ""

    def __post_init__(self) -> None:
        # 允许直接传 dict 列表，读取路径与构造路径共用一份归一化。
        object.__setattr__(
            self,
            "items",
            tuple(
                item if isinstance(item, SummaryItem) else SummaryItem(**item)
                for item in self.items
            ),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "compaction_id": self.compaction_id,
            "kind": self.kind,
            "source_head_seq": self.source_head_seq,
            "covered_through_seq": self.covered_through_seq,
            "first_kept_turn_id": self.first_kept_turn_id,
            "source_digest": self.source_digest,
            "previous_id": self.previous_id,
            "summary": self.summary,
            "items": [item.as_dict() for item in self.items],
            "model": self.model,
            "prompt_version": self.prompt_version,
            "estimated_before": self.estimated_before,
            "estimated_after": self.estimated_after,
            "request_operation_id": self.request_operation_id,
            "reason": self.reason,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: object) -> CompactionRecord | None:
        """从事件读回记录；结构不对就返回 ``None``，由调用方按未压缩处理。"""
        if not isinstance(raw, Mapping):
            return None
        try:
            items = raw.get("items")
            return cls(
                compaction_id=str(raw["compaction_id"]),
                kind=str(raw.get("kind") or "session"),  # type: ignore[arg-type]
                source_head_seq=int(raw["source_head_seq"]),
                covered_through_seq=int(raw["covered_through_seq"]),
                first_kept_turn_id=str(raw["first_kept_turn_id"]),
                source_digest=str(raw["source_digest"]),
                previous_id=str(raw.get("previous_id") or ""),
                summary=str(raw["summary"]),
                items=tuple(
                    item if isinstance(item, SummaryItem) else SummaryItem(**item)
                    for item in (items if isinstance(items, list) else [])
                ),
                model=str(raw.get("model") or ""),
                prompt_version=int(raw.get("prompt_version") or 1),
                estimated_before=int(raw.get("estimated_before") or 0),
                estimated_after=int(raw.get("estimated_after") or 0),
                request_operation_id=str(raw.get("request_operation_id") or ""),
                reason=str(raw.get("reason") or "manual"),
                created_at=str(raw.get("created_at") or ""),
            )
        except (KeyError, TypeError, ValueError):
            return None


def source_digest(events: Sequence[Mapping[str, Any]], *, through_seq: int) -> str:
    """覆盖范围内公开文本的指纹；只含参与复述的内容，不含执行细节。"""
    parts: list[list[object]] = []
    for event in events:
        seq = event.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq > through_seq:
            continue
        kind = event.get("type")
        if kind not in ("turn_started", "assistant_message"):
            continue
        parts.append([seq, str(event.get("turn_id") or ""), kind, str(event.get("text") or "")])
    encoded = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def active_compaction(
    events: Sequence[Mapping[str, Any]],
) -> CompactionRecord | None:
    """最新一份仍然成立的压缩记录；读取时从头校验，不信任历史标记。"""
    turn_seqs: dict[str, int] = {}
    for event in events:
        if event.get("type") != "turn_started":
            continue
        turn_id = event.get("turn_id")
        seq = event.get("seq")
        if isinstance(turn_id, str) and isinstance(seq, int):
            turn_seqs[turn_id] = seq

    for event in reversed(events):
        if event.get("type") != "context_compacted":
            continue
        record = CompactionRecord.from_dict(event.get("record"))
        if record is None:
            continue
        if not record.summary.strip() or not record.items:
            continue
        if not 0 <= record.covered_through_seq <= record.source_head_seq:
            continue
        kept_seq = turn_seqs.get(record.first_kept_turn_id)
        if kept_seq is None:
            # 保留点不在日志里：要么记录过期，要么日志被裁剪；两种都不能压缩。
            continue
        if kept_seq <= record.covered_through_seq:
            # 保留点自己就在覆盖范围内，等于没有近期对话。
            continue
        if source_digest(events, through_seq=record.covered_through_seq) != record.source_digest:
            continue
        return record
    return None


def summary_message(record: CompactionRecord) -> UserMessage:
    """摘要作为一条带明确标识的应用历史消息，不提升为系统指令。"""
    return UserMessage(content=summary_text(record.summary, record.items))


def summary_text(summary: str, items: Sequence[SummaryItem]) -> str:
    lines = [SUMMARY_PREFIX + summary.strip()]
    for item in items:
        line = f"- [{','.join(item.source_event_ids)}] {item.kind}：{item.text}"
        if item.quote:
            line += f"；原话：{item.quote}"
        lines.append(line)
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class CompactionPlan:
    """打算整理哪些轮次，以及整理前后的规模。"""

    first_kept_turn_id: str
    covered_through_seq: int
    covered_turns: tuple[str, ...]
    #: 覆盖范围与保留部分的估算规模；摘要本身还没算进去。
    tokens_before: int
    tokens_after: int


@dataclass(slots=True)
class _Unit:
    """构建期的可变单元；对外只暴露不可变结果。"""

    turn_id: str
    last_seq: int = 0
    tokens: int = 0
    protected: bool = False


def _units(events: Sequence[Mapping[str, Any]], count: Callable[[str], int]) -> list[_Unit]:
    """按轮次切单元；一轮的提示与回复不拆分。"""
    units: list[_Unit] = []
    current: _Unit | None = None
    for event in events:
        turn_id = event.get("turn_id")
        if not isinstance(turn_id, str) or not turn_id:
            continue
        kind = event.get("type")
        seq = event.get("seq")
        if kind == "turn_started":
            current = _Unit(turn_id=turn_id)
            units.append(current)
        if current is None or current.turn_id != turn_id:
            continue
        if isinstance(seq, int) and not isinstance(seq, bool):
            current.last_seq = max(current.last_seq, seq)
        if kind in ("turn_started", "assistant_message"):
            text = event.get("text")
            if isinstance(text, str):
                current.tokens += count(text)
        elif kind == "author_input_requested":
            # 问了没答的轮次必须留在近期：作者的回答只有在看到原问题时才有意义。
            current.protected = True
        elif kind == "author_replied":
            current.protected = False
    return units


def plan_compaction(
    events: Sequence[Mapping[str, Any]],
    *,
    keep_recent_tokens: int,
    count: Callable[[str], int] = count_tokens,
) -> CompactionPlan | None:
    """选一个只切完整轮次的切点；没有可整理的较早内容时返回 ``None``。

    从最近一轮往前累计，累计到保留量就停。最新一轮无论如何都保留——把最近一轮压掉，
    模型接不上作者刚说的话。待回答的提问所在轮次同样强制保留。
    """
    units = _units(events, count)
    if len(units) < 2 or keep_recent_tokens <= 0:
        return None

    accumulated = 0
    first_kept = len(units)
    for index in range(len(units) - 1, -1, -1):
        unit = units[index]
        if accumulated < keep_recent_tokens or unit.protected:
            first_kept = index
        accumulated += unit.tokens
    if not 0 < first_kept < len(units):
        return None

    covered = units[:first_kept]
    kept = units[first_kept:]
    return CompactionPlan(
        first_kept_turn_id=kept[0].turn_id,
        covered_through_seq=covered[-1].last_seq,
        covered_turns=tuple(unit.turn_id for unit in covered),
        tokens_before=sum(unit.tokens for unit in units),
        tokens_after=sum(unit.tokens for unit in kept),
    )


#: 摘要条目允许的种类；与产品里的「目标、约束、决定、建议、待办、定位」对应。
ITEM_KINDS = ("goal", "constraint", "author_decision", "suggestion", "pending", "reference")


@dataclass(frozen=True, slots=True)
class ParsedSummary:
    summary: str
    items: tuple[SummaryItem, ...]


def _json_payload(text: str) -> Mapping[str, Any] | None:
    """取第一段 JSON 对象；模型常带代码围栏或前后说明。"""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, Mapping) else None


def parse_summary(text: str, *, allowed_sources: frozenset[str]) -> ParsedSummary | None:
    """解析摘要候选；任何一条来源不明就整份作废。

    宁可不用这份摘要，也不接受一份引用了不存在轮次的概括——那意味着模型在补写，
    而补写出来的内容会被下一轮当成事实。
    """
    payload = _json_payload(text)
    if payload is None:
        return None
    summary = payload.get("summary")
    raw_items = payload.get("items")
    if not isinstance(summary, str) or not summary.strip():
        return None
    if not isinstance(raw_items, list) or not raw_items:
        return None

    items: list[SummaryItem] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, Mapping):
            return None
        sources = raw.get("sources")
        if not isinstance(sources, list) or not sources:
            return None
        source_ids = tuple(str(source) for source in sources)
        if not set(source_ids) <= allowed_sources:
            return None
        kind = str(raw.get("kind") or "")
        if kind not in ITEM_KINDS:
            return None
        body = str(raw.get("text") or "").strip()
        if not body:
            return None
        quote = raw.get("quote")
        items.append(
            SummaryItem(
                item_id=f"s{index + 1}",
                kind=kind,  # type: ignore[arg-type]
                text=body,
                source_event_ids=source_ids,
                quote=str(quote).strip() if isinstance(quote, str) and quote.strip() else None,
            )
        )
    return ParsedSummary(summary=summary.strip(), items=tuple(items))


def build_candidate(
    *,
    plan: CompactionPlan,
    parsed: ParsedSummary,
    model: str,
    digest: str,
    previous_id: str = "",
    reason: str = "manual",
    prompt_version: int = 1,
    created_at: str = "",
    count: Callable[[str], int] = count_tokens,
) -> CompactionRecord:
    """把计划与解析结果合成待提交的记录；摘要本身的规模在这里计入。"""
    identity = hashlib.sha256((digest + plan.first_kept_turn_id).encode()).hexdigest()[:16]
    return CompactionRecord(
        compaction_id=f"cmp_{identity}",
        kind="session",
        source_head_seq=plan.covered_through_seq,
        covered_through_seq=plan.covered_through_seq,
        first_kept_turn_id=plan.first_kept_turn_id,
        source_digest=digest,
        previous_id=previous_id,
        summary=parsed.summary,
        items=parsed.items,
        model=model,
        prompt_version=prompt_version,
        estimated_before=plan.tokens_before,
        estimated_after=plan.tokens_after + count(summary_text(parsed.summary, parsed.items)),
        reason=reason,
        created_at=created_at,
    )


def validate_candidate(
    candidate: CompactionRecord,
    *,
    allowed_sources: frozenset[str],
    input_limit: int | None,
) -> None:
    """提交前的结构校验；不通过就抛错，由调用方保留旧投影。"""
    if (
        not candidate.summary.strip()
        or not candidate.items
        or candidate.estimated_after >= candidate.estimated_before
    ):
        raise _invalid(
            "COMPACTION_NOT_EFFECTIVE",
            "摘要为空或没有减少输入",
            "保留当前上下文或缩小压缩范围",
        )
    if not 0 <= candidate.covered_through_seq <= candidate.source_head_seq:
        raise _invalid(
            "COMPACTION_SOURCE_INVALID",
            "摘要覆盖范围无效",
            "重新读取原会话后再压缩",
        )
    for item in candidate.items:
        if not item.source_event_ids or not set(item.source_event_ids) <= allowed_sources:
            raise _invalid(
                "COMPACTION_SOURCE_INVALID",
                "摘要引用了不存在的原对话",
                "重新生成摘要",
            )
    if input_limit is not None and candidate.estimated_after > input_limit:
        raise _invalid(
            "CONTEXT_REQUIRED_INPUT_TOO_LARGE",
            "压缩后仍超出输入预算",
            "缩小本次处理范围，或选择容量更大的模型",
        )


def _invalid(code: str, why: str, fix: str) -> WorkspaceError:
    return WorkspaceError(code=code, where="conversation.compaction", why=why, fix=fix)


#: 摘要提示词版本；改提示词要加一，旧记录的摘要不能当成同一种东西比较。
PROMPT_VERSION = 1

SUMMARY_SYSTEM = """你在为一段写作对话整理可续写的摘要。

只输出一个 JSON 对象，不要解释、不要 Markdown 代码块之外的任何文字：

{
  "summary": "一段话概括这段讨论",
  "items": [
    {"kind": "...", "text": "...", "quote": "...", "sources": ["t1"]}
  ]
}

规则：
- kind 只能是 goal、constraint、author_decision、suggestion、pending、reference；
- 每条要点必须给出 sources，写它来自哪个轮次 ID（用户消息里用方括号标出）；
- 只写对话里确实出现过的内容，不要补写、不要推测；
- 作者确认的决定、助手的建议、尚未确定的事项要分开写；
- 作者最新的修正与被否定的方案都要保留，不能只留结论；
- 不要复述改了哪些文件、保存是否成功，那些由应用自己的记录说明。"""


def build_summary_prompt(
    events: Sequence[Mapping[str, Any]], plan: CompactionPlan
) -> tuple[str, str]:
    """生成摘要请求的 system 与 user 文本；只包含被覆盖的那些轮次。"""
    lines: list[str] = []
    for event in events:
        turn_id = event.get("turn_id")
        if not isinstance(turn_id, str) or turn_id not in plan.covered_turns:
            continue
        text = event.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        kind = event.get("type")
        if kind == "turn_started":
            lines.append(f"[{turn_id}] 作者：{text.strip()}")
        elif kind == "assistant_message":
            lines.append(f"[{turn_id}] 助手：{text.strip()}")
    return SUMMARY_SYSTEM, "\n\n".join(lines)


def _message_units(
    messages: Sequence[AgentMessage], count: Callable[[str], int]
) -> list[list[AgentMessage]] | None:
    """把消息切成完整单元；出现残缺调用关系时返回 ``None``。

    一个单元是「一条助手消息 + 它要求的全部工具结果」。切在单元内部会让供应商收到
    有调用没有结果、或有结果没有调用的请求，那种请求必然失败，而且失败原因很难看懂。
    """
    units: list[list[AgentMessage]] = []
    index = 0
    total = len(messages)
    while index < total:
        message = messages[index]
        if isinstance(message, AssistantMessage) and message.tool_calls:
            expected = {call.id for call in message.tool_calls}
            unit: list[AgentMessage] = [message]
            index += 1
            collected: set[str] = set()
            while index < total:
                candidate = messages[index]
                if not isinstance(candidate, ToolResultMessage):
                    break
                if candidate.tool_call_id not in expected:
                    return None
                collected.add(candidate.tool_call_id)
                unit.append(candidate)
                index += 1
            if collected != expected:
                return None
            units.append(unit)
            continue
        if isinstance(message, ToolResultMessage):
            # 找不到对应调用的结果：这段历史已经不可裁剪。
            return None
        units.append([message])
        index += 1
    return units


def _unit_tokens(unit: Sequence[AgentMessage], count: Callable[[str], int]) -> int:
    total = 0
    for message in unit:
        payload = message.model_dump(mode="json")
        total += count(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return total


def trim_request_messages(
    messages: Sequence[AgentMessage],
    *,
    keep_recent_tokens: int,
    count: Callable[[str], int] = count_tokens,
) -> list[AgentMessage] | None:
    """轮内裁剪：丢弃较早的完整单元，保留最近内容；不需要或不能裁时返回 ``None``。

    与跨轮压缩的区别是**不生成摘要**：轮内旧工具结果多是已经过期的文件正文，
    模型需要时会重新读取；为此再调用一次模型既慢又可能递归触发整理。
    读取凭据与任务范围保存在应用自己的账本里，不会因为消息被裁掉而失效。
    """
    if keep_recent_tokens <= 0 or not messages:
        return None
    units = _message_units(messages, count)
    if units is None or len(units) < 2:
        return None

    accumulated = 0
    first_kept = len(units)
    for index in range(len(units) - 1, -1, -1):
        if accumulated < keep_recent_tokens:
            first_kept = index
        accumulated += _unit_tokens(units[index], count)
    if first_kept == 0:
        return None
    # 当前作者原话是本轮范围与任务的依据，即使其后的工具结果被裁也必须保留。
    current_user = next(
        (i for i in range(len(units) - 1, -1, -1) if isinstance(units[i][0], UserMessage)),
        None,
    )
    latest_tool = next(
        (
            i
            for i in range(len(units) - 1, -1, -1)
            if isinstance(head := units[i][0], AssistantMessage) and head.tool_calls
        ),
        None,
    )
    kept = [
        unit
        for i, unit in enumerate(units)
        if i >= first_kept
        or i in (current_user, latest_tool)
        or (isinstance(unit[0], UserMessage) and message_text(unit[0]).startswith(SUMMARY_PREFIX))
    ]
    if sum(len(unit) for unit in kept) >= len(messages):
        return None
    return [message for unit in kept for message in unit]
