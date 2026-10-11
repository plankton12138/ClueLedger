"""上下文估算与两档压缩（P10 T8，计划 D8/L9）：长篇上下文的零兜底收口。

蓝图移植自 tau_coding/context_window.py（参考层不可 import，骨架照搬、场景重做）：
- **估算器**：provider usage 锚定（最近一次成功 assistant 的 ``usage`` 描述其前缀的
  精确体积）+ 尾部消息字符 fallback；中文按 2 字符/token 校准（上游按英文 4，
  宁高估早压，不低估爆窗）；
- **第一档 确定性正文剥离**（主力，零 LLM）：keep-window 之外的 assistant 消息里，
  ``write_chapter`` 的 ``content`` 参数替换为占位符——正文已落盘，需要时走
  ``read_chapter``。用 ``model_copy`` 重建消息，**tool_call_id 与调用结构原样保留**，
  provider 无感。确定性、可单测（消息序列 → 消息序列的纯函数）；
- **第二档 LLM 摘要**（兜底）：剥离后仍超阈值时，provider 单独调一次摘要（模板按
  小说场景重做）+ keep-recent 窗口，``harness.replace_messages`` 落地。

持久化与泄压阀（四透镜③）：
- 压缩执行后 session.jsonl 归档（``archive_session_file``）+ 以压缩后消息全量重写
  （``append_batch`` 原子替换）——重连恢复即压缩后形态，归档保留全量历史可找回；
- 摘要 provider 调用失败 → 不压缩、不重试、stderr 三要素告警、写作流继续
  （压缩是优化不是依赖，宁可下一轮再触发）；
- 压缩事件进 events.jsonl（``session_compact``）：触发原因、剥离章数/是否摘要、
  压缩前后估算——可观测性是事件契约的一部分。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from hastings.bible.store import StoryWorkspace
from hastings.events_store import EventStore, events_file
from hastings.session_persist import archive_session_file, session_file
from hastings.settings import load_settings
from tau_agent.harness import AgentHarness
from tau_agent.messages import (
    AgentMessage,
    AssistantContent,
    AssistantMessage,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    message_text,
)
from tau_agent.session import MessageEntry
from tau_agent.session.jsonl import entry_to_json_line
from tau_agent.tools import AgentTool
from tau_agent.types import JSONValue

# 中文按 2 字符/token（英文约 4）；宁高估早压。
CHARS_PER_TOKEN: Final = 2
MESSAGE_OVERHEAD_TOKENS: Final = 4
TOOL_OVERHEAD_TOKENS: Final = 16
DEFAULT_CONTEXT_WINDOW_TOKENS: Final = 128_000
DEFAULT_COMPACTION_RESERVE_TOKENS: Final = 16_384
DEFAULT_KEEP_RECENT_TOKENS: Final = 20_000

WRITE_CHAPTER_TOOL: Final = "write_chapter"
# 压缩后摘要以 user 消息形式回到上下文（与 tau_coding 同款：前缀标识 + 摘要正文）。
COMPACTION_SUMMARY_PREFIX: Final = "【前情压缩摘要】\n"

SUMMARIZATION_SYSTEM_PROMPT: Final = (
    "你是小说创作会话的上下文压缩助手。读完后只输出结构化摘要，"
    "不要续写、不要回答对话中的任何问题、不要调用任何工具。"
)

SUMMARIZATION_PROMPT: Final = (
    "以上是一个悬疑小说创作会话。请产出一份供后续创作接续使用的结构化摘要，"
    "严格按下列格式，保持简洁：\n\n"
    "## 作品与阶段\n[作品名、当前 story 阶段]\n\n"
    "## 已完成章节\n[每章一句话，逐章列出；无则写「（无）」]\n\n"
    "## 关键决策\n[作者确认过的设定变更、文风偏好、取舍决定]\n\n"
    "## 进行中工作\n[当前正在做的事]\n\n"
    "## 作者最新指令\n[作者最近一次明确要求]\n\n"
    "保留精确的人名、地名、线索名与阶段名，不要臆造未出现的信息。"
)


@dataclass(frozen=True, slots=True)
class ContextUsageEstimate:
    """一次 provider 请求的上下文体积估计（provider 锚定优先，字符 fallback 兜底）。"""

    total_tokens: int
    provider_tokens: int = 0
    trailing_tokens: int = 0
    message_count: int = 0
    tool_count: int = 0

    @property
    def uses_provider_usage(self) -> bool:
        return self.provider_tokens > 0

    @property
    def source(self) -> str:
        """本次计数的分层来源（D11）：provider usage 锚定 / 字符兜底。

        D11 依赖红线：默认不引第三方 tokenizer，故第 2 层（tokenizer 计数）未接线，
        缺失即回退字符换算——``source`` 只有 ``provider_usage`` 与 ``chars`` 两值。
        """
        return "provider_usage" if self.uses_provider_usage else "chars"


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """一轮 token 用量快照（D11）：最近一次 assistant 的 usage 明细 + 当前上下文占用。

    落账（events.jsonl ``token_usage``）与前端占用展示读同一份数据（唯一数据源）。
    """

    context_tokens: int
    window_tokens: int
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    source: str = "chars"

    def to_payload(self) -> dict[str, object]:
        """事件帧/推送帧共用的扁平载荷（type 由调用方补）。"""
        return {
            "input": self.input_tokens,
            "output": self.output_tokens,
            "total": self.total_tokens,
            "cache_read": self.cache_read_tokens,
            "cache_write": self.cache_write_tokens,
            "context_tokens": self.context_tokens,
            "window_tokens": self.window_tokens,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class StripResult:
    """第一档剥离结果：重建后的消息序列 + 被替换的章号清单。"""

    messages: tuple[AgentMessage, ...]
    stripped_chapters: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class CompactionResult:
    """一次压缩的结果与依据（REST/CLI/事件留痕共用同一口径）。"""

    compacted: bool
    reason: str  # below_threshold / nothing_to_strip / summary_failed / auto / manual
    tokens_before: int
    tokens_after: int
    stripped_chapters: tuple[int, ...] = ()
    summarized: bool = False
    archived: str | None = None


# ---------- 估算器（纯函数） ----------


def estimate_text_tokens(text: str) -> int:
    """字符换算的确定性粗略 token 估计（中文 2 字符/token）。"""
    if not text:
        return 0
    return max(1, (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


def estimate_message_tokens(message: AgentMessage) -> int:
    """单条消息的粗略 token 估计（含 thinking 与工具调用参数）。"""
    tokens = MESSAGE_OVERHEAD_TOKENS + estimate_text_tokens(message_text(message))
    if isinstance(message, AssistantMessage):
        tokens += sum(
            estimate_text_tokens(block.thinking)
            for block in message.content
            if isinstance(block, ThinkingContent)
        )
        tokens += sum(
            estimate_text_tokens(call.name) + estimate_text_tokens(str(call.arguments))
            for call in message.tool_calls
        )
    elif isinstance(message, ToolResultMessage):
        tokens += estimate_text_tokens(message.tool_name)
    return tokens


def estimate_tool_tokens(tool: AgentTool) -> int:
    """单个工具定义的粗略 token 估计。"""
    return (
        TOOL_OVERHEAD_TOKENS
        + estimate_text_tokens(tool.name)
        + estimate_text_tokens(tool.description)
        + estimate_text_tokens(str(tool.input_schema))
    )


def provider_context_tokens(message: AssistantMessage) -> int:
    """provider 上报的该响应所代表的上下文体积。"""
    usage = message.usage
    return usage.total_tokens or (usage.input + usage.output + usage.cache_read + usage.cache_write)


def _last_applicable_provider_usage(
    messages: tuple[AgentMessage, ...],
) -> tuple[int, int] | None:
    """最近一次仍描述当前前缀的成功 assistant 的 usage（压缩改写后旧 usage 不再适用）。

    压缩会把老消息替换成摘要（新前缀），此时更早 assistant 的 usage 描述的是改写前的
    上下文，不能再作锚——用 timestamp 单调性筛选：
    """
    latest_timestamp = -1
    usage_info: tuple[int, int] | None = None
    for index, message in enumerate(messages):
        if isinstance(message, AssistantMessage):
            tokens = provider_context_tokens(message)
            if (
                message.timestamp >= latest_timestamp
                and message.stop_reason not in {"aborted", "error"}
                and tokens > 0
            ):
                usage_info = (index, tokens)
        latest_timestamp = max(latest_timestamp, message.timestamp)
    return usage_info


def count_context_tokens(
    *,
    system: str,
    messages: tuple[AgentMessage, ...],
    tools: tuple[AgentTool, ...],
) -> ContextUsageEstimate:
    """上下文体积分层计数（D11 唯一数据源）：压缩触发、事件留痕、前端展示共用本函数。

    分层（provider usage 锚定 → tokenizer 计数 → 字符兜底）：
    1. 最近一次仍描述当前前缀的成功 assistant 的 ``usage`` 是精确锚点；
    2. 锚点之后追加的消息按本地 tokenizer 计——D11 依赖红线默认不引第三方
       tokenizer，此层未接线，直接落到第 3 层；
    3. 字符换算兜底（中文 2 字符/token，宁高估早压）。

    有可用 usage 时只估锚点之后的消息与工具；否则整段请求走字符换算。
    """
    usage_info = _last_applicable_provider_usage(messages)
    if usage_info is not None:
        usage_index, provider_tokens = usage_info
        trailing = messages[usage_index + 1 :]
        trailing_tokens = sum(estimate_message_tokens(message) for message in trailing)
        return ContextUsageEstimate(
            total_tokens=provider_tokens + trailing_tokens,
            provider_tokens=provider_tokens,
            trailing_tokens=trailing_tokens,
            message_count=len(messages),
            tool_count=len(tools),
        )
    system_tokens = estimate_text_tokens(system)
    message_tokens = sum(estimate_message_tokens(message) for message in messages)
    tool_tokens = sum(estimate_tool_tokens(tool) for tool in tools)
    total = system_tokens + message_tokens + tool_tokens
    return ContextUsageEstimate(
        total_tokens=total,
        trailing_tokens=total,
        message_count=len(messages),
        tool_count=len(tools),
    )


def _last_assistant_message(messages: tuple[AgentMessage, ...]) -> AssistantMessage | None:
    """最近一条 assistant 消息（其 usage 是本轮 token 用量的明细来源）。"""
    for message in reversed(messages):
        if isinstance(message, AssistantMessage):
            return message
    return None


def build_token_usage(harness: AgentHarness, workspace: StoryWorkspace) -> TokenUsage:
    """组装一轮 token 用量快照（D11）：最近 assistant 的 usage 明细 + 当前上下文占用。

    ``context_tokens`` 走唯一计数源 ``count_context_tokens``；窗口取 settings 值。
    """
    estimate = count_context_tokens(
        system=harness.config.system,
        messages=harness.messages,
        tools=tuple(harness.config.tools),
    )
    last = _last_assistant_message(harness.messages)
    window = load_settings(workspace).context_window_tokens
    if last is None:
        return TokenUsage(
            context_tokens=estimate.total_tokens,
            window_tokens=window,
            source=estimate.source,
        )
    usage = last.usage
    return TokenUsage(
        context_tokens=estimate.total_tokens,
        window_tokens=window,
        input_tokens=usage.input,
        output_tokens=usage.output,
        total_tokens=usage.total_tokens or (usage.input + usage.output),
        cache_read_tokens=usage.cache_read,
        cache_write_tokens=usage.cache_write,
        source=estimate.source,
    )


async def record_token_usage(
    store: EventStore, harness: AgentHarness, workspace: StoryWorkspace
) -> TokenUsage:
    """把一轮用量写进 events.jsonl（D11 ``token_usage`` 事件）：可观测性账的一部分。"""
    usage = build_token_usage(harness, workspace)
    await store.append("token_usage", cast("dict[str, JSONValue]", usage.to_payload()))
    return usage


def compaction_threshold(context_window_tokens: int) -> int | None:
    """压缩触发阈值 = 窗口 − reserve；窗口 ≤ 0 视为未配置（不触发）。"""
    if context_window_tokens <= 0:
        return None
    return max(1, context_window_tokens - DEFAULT_COMPACTION_RESERVE_TOKENS)


# ---------- 第一档：确定性正文剥离（纯函数） ----------


def _chapter_placeholder(chapter: object) -> str:
    if isinstance(chapter, int):
        return (
            f"（第 {chapter} 章正文已落盘 chapters/ch{chapter:02d}.md，需要时用 read_chapter 读取）"
        )
    return "（本章正文已落盘 chapters/，需要时用 read_chapter 读取）"


def _strip_write_calls(message: AssistantMessage) -> tuple[list[AssistantContent], tuple[int, ...]]:
    """把一条 assistant 消息里的 write_chapter 正文参数替换为占位符。

    tool_call_id 与调用顺序原样保留（只换 arguments["content"]），call/result 配对不破。
    """
    blocks: list[AssistantContent] = []
    chapters: list[int] = []
    for block in message.content:
        if isinstance(block, ToolCall) and block.name == WRITE_CHAPTER_TOOL:
            chapter = block.arguments.get("chapter")
            arguments = dict(block.arguments)
            arguments["content"] = _chapter_placeholder(chapter)
            blocks.append(block.model_copy(update={"arguments": arguments}))
            if isinstance(chapter, int):
                chapters.append(chapter)
        else:
            blocks.append(block)
    return blocks, tuple(chapters)


def _keep_start_index(messages: tuple[AgentMessage, ...], keep_recent_tokens: int) -> int:
    """从尾部累加 token，返回 keep-window 的起点下标（其前为可剥离区）。"""
    total = 0
    for index in range(len(messages) - 1, -1, -1):
        total += estimate_message_tokens(messages[index])
        if total > keep_recent_tokens:
            return index + 1
    return 0


def strip_chapter_args(
    messages: tuple[AgentMessage, ...],
    *,
    keep_recent_tokens: int = DEFAULT_KEEP_RECENT_TOKENS,
) -> StripResult:
    """keep-window 之外的 write_chapter 正文参数替换为占位符（确定性纯函数）。"""
    keep_start = _keep_start_index(messages, keep_recent_tokens)
    out: list[AgentMessage] = []
    stripped: list[int] = []
    for index, message in enumerate(messages):
        if index >= keep_start or not isinstance(message, AssistantMessage):
            out.append(message)
            continue
        content, chapters = _strip_write_calls(message)
        if chapters:
            out.append(message.model_copy(update={"content": content}))
            stripped.extend(chapters)
        else:
            out.append(message)
    return StripResult(messages=tuple(out), stripped_chapters=tuple(stripped))


def _keep_recent_messages(
    messages: tuple[AgentMessage, ...], keep_recent_tokens: int
) -> tuple[AgentMessage, ...]:
    return messages[_keep_start_index(messages, keep_recent_tokens) :]


def serialize_messages_for_compaction(messages: tuple[AgentMessage, ...]) -> str:
    """把消息序列折成摘要提示词用的紧凑文本（工具调用参数已剥离正文）。"""
    if not messages:
        return "（无历史消息）"
    lines: list[str] = []
    for index, message in enumerate(messages, start=1):
        role: str = message.role
        if isinstance(message, ToolResultMessage):
            role = f"toolResult({message.tool_name})"
        lines.append(f"<message index={index} role={role}>")
        text = message_text(message)
        if text:
            lines.append(text)
        if isinstance(message, AssistantMessage) and message.tool_calls:
            lines.append("<tool-calls>")
            for call in message.tool_calls:
                lines.append(f"- {call.name}: {call.arguments}")
            lines.append("</tool-calls>")
        lines.append("</message>")
    return "\n".join(lines)


def build_summary_prompt(messages: tuple[AgentMessage, ...]) -> str:
    """摘要提示词：会话序列 + 小说场景模板。"""
    conversation = serialize_messages_for_compaction(messages)
    return f"<conversation>\n{conversation}\n</conversation>\n\n{SUMMARIZATION_PROMPT}"


# ---------- 压缩执行与持久化 ----------


async def _summarize(harness: AgentHarness, messages: tuple[AgentMessage, ...]) -> str:
    """调一次 provider 生成摘要；空文本视为失败（环境故障，交调用方泄压）。"""
    from tau_agent.provider_events import AssistantMessageEvent, TextDeltaEvent

    chunks: list[str] = []
    event: AssistantMessageEvent
    async for event in harness.config.provider.stream_response(
        model=harness.config.model,
        system=SUMMARIZATION_SYSTEM_PROMPT,
        messages=[UserMessage(content=build_summary_prompt(messages))],
        tools=[],
    ):
        if isinstance(event, TextDeltaEvent):
            chunks.append(event.delta)
    text = "".join(chunks).strip()
    if not text:
        msg = "摘要 provider 未返回任何文本"
        raise RuntimeError(msg)
    return text


def _warn_summary_failed(exc: Exception) -> None:
    print(
        f"[context] 摘要生成失败，本次不压缩、写作继续。为什么错：{exc}。"
        "怎样算对：检查模型端点与网络；下一轮仍超阈值时会自动重试。",
        file=sys.stderr,
    )


def _persist_compacted(path: Path, messages: tuple[AgentMessage, ...]) -> str | None:
    """归档旧 session.jsonl 并以压缩后消息全量重写；返回归档文件名。

    同步重写（不占 asyncio 锁）：压缩发生在 prompt 入口、无并发消息落盘；沿用
    ``entry_to_json_line`` 保持与 ``JsonlSessionStorage`` 同格式。
    """
    archived = archive_session_file(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for message in messages:
            handle.write(entry_to_json_line(MessageEntry(message=message)))
    return archived.name if archived is not None else None


async def compact_context(
    harness: AgentHarness,
    workspace: StoryWorkspace,
    *,
    manual: bool = False,
    keep_recent_tokens: int = DEFAULT_KEEP_RECENT_TOKENS,
) -> CompactionResult:
    """两档压缩的统一原语（自动=超阈值触发，手动=绕过阈值直接调用）。

    自动且未超阈值 → ``below_threshold`` 空返回；手动但无可剥离内容 → ``nothing_to_strip``
    空返回（泄压阀三情形中的「合法但空结果」：不报错、指明下一步）。
    """
    if harness.is_running:
        # 运行中替换消息会撕裂正在进行的工具链——与 reset/切换的 running 拒绝同语义。
        return CompactionResult(False, "running", 0, 0)

    # 压缩可能等待摘要 provider；路径必须在等待前固定，不能在完成后重读“当前
    # 活跃会话”，否则会话切换会让旧 harness 覆盖新会话的历史。
    target_session_path = session_file(workspace)
    messages = harness.messages
    system = harness.config.system
    tools = tuple(harness.config.tools)
    window = load_settings(workspace).context_window_tokens
    threshold = compaction_threshold(window)
    before = count_context_tokens(system=system, messages=messages, tools=tools).total_tokens

    if not manual and (threshold is None or before <= threshold):
        return CompactionResult(False, "below_threshold", before, before)

    strip = strip_chapter_args(messages, keep_recent_tokens=keep_recent_tokens)
    after_strip = count_context_tokens(
        system=system, messages=strip.messages, tools=tools
    ).total_tokens

    summarized = False
    new_messages: tuple[AgentMessage, ...] = strip.messages
    if threshold is not None and after_strip > threshold:
        try:
            summary = await _summarize(harness, strip.messages)
        except Exception as exc:  # provider/网络/解析等一切环境故障 → 泄压阀
            _warn_summary_failed(exc)
            return CompactionResult(
                False, "summary_failed", before, before, strip.stripped_chapters
            )
        keep_recent = _keep_recent_messages(strip.messages, keep_recent_tokens)
        new_messages = (
            UserMessage(content=COMPACTION_SUMMARY_PREFIX + summary),
            *keep_recent,
        )
        summarized = True

    if not strip.stripped_chapters and not summarized:
        return CompactionResult(False, "nothing_to_strip", before, before)

    harness.replace_messages(new_messages)
    try:
        archived = _persist_compacted(target_session_path, new_messages)
    except OSError as exc:
        archived = None
        print(
            f"[context] 压缩后会话重写失败，内存已压缩但磁盘未同步。为什么错：{exc}。"
            "怎样算对：检查磁盘空间/文件占用；重启后会以旧（未压缩）会话恢复，"
            "下一轮自动压缩会再次尝试。",
            file=sys.stderr,
        )

    after = count_context_tokens(system=system, messages=new_messages, tools=tools).total_tokens
    await EventStore(events_file(workspace)).append(
        "session_compact",
        {
            "reason": "manual" if manual else "auto",
            "stripped_chapters": list(strip.stripped_chapters),
            "summarized": summarized,
            "tokens_before": before,
            "tokens_after": after,
            "threshold": threshold,
        },
    )
    return CompactionResult(
        True,
        "manual" if manual else "auto",
        before,
        after,
        strip.stripped_chapters,
        summarized,
        archived,
    )


__all__ = [
    "CHARS_PER_TOKEN",
    "COMPACTION_SUMMARY_PREFIX",
    "DEFAULT_COMPACTION_RESERVE_TOKENS",
    "DEFAULT_CONTEXT_WINDOW_TOKENS",
    "DEFAULT_KEEP_RECENT_TOKENS",
    "WRITE_CHAPTER_TOOL",
    "CompactionResult",
    "ContextUsageEstimate",
    "StripResult",
    "TokenUsage",
    "build_summary_prompt",
    "build_token_usage",
    "compact_context",
    "compaction_threshold",
    "count_context_tokens",
    "estimate_message_tokens",
    "estimate_text_tokens",
    "estimate_tool_tokens",
    "provider_context_tokens",
    "record_token_usage",
    "serialize_messages_for_compaction",
    "strip_chapter_args",
]
