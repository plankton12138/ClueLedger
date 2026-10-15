"""命令行交互与纯文本渲染器（Phase 1 T5）。

渲染器订阅 harness 事件流做流式打印——思路照搬 tau_coding 的 plain 渲染器，
但用内置 print 自写，不引入 typer/rich 这类依赖（项目保持依赖最小化）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

import yaml
from pydantic import BaseModel, ValidationError

from hastings.bible.models import Clue, Foreshadow, Method, OutlineEntry, TimelineEntry
from hastings.bible.store import (
    LedgerFormatError,
    StoryWorkspace,
    list_characters,
    load_ledger,
)
from hastings.cli_v2 import v2_bible_output
from hastings.context_manager import compact_context
from hastings.events_store import EventStore, events_file, make_event_persister
from hastings.manuscript.guards import attach_guards
from hastings.manuscript.prose_guard import warn_empty_chapters
from hastings.manuscript.write_guard import EditMarkRegistry, RunKindGuard
from hastings.prompts import build_system_prompt, refresh_system_prompt
from hastings.session_persist import restore_and_attach
from hastings.session_store import (
    SessionNotFoundError,
    activate_session,
    active_session_id,
    create_session,
    list_sessions,
)
from hastings.settings import load_settings, workspace_architecture_version
from hastings.tools.bible_tools import ENTITY_LABELS, create_bible_tools, entity_summary
from hastings.tools.chapter_tools import create_chapter_tools
from hastings.tools.method_tools import create_method_tools
from hastings.tools.story_tools import create_story_tools
from hastings.tools.timeline_tools import create_timeline_tools
from tau_agent.events import (
    AgentEndEvent,
    AgentEvent,
    AgentStartEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from tau_agent.harness import AgentHarness, AgentHarnessConfig
from tau_agent.provider import ModelProvider
from tau_agent.provider_events import TextDeltaEvent
from tau_agent.tools import AgentTool

QUIT_COMMANDS = frozenset({"/quit", "/exit", "/q"})
BIBLE_COMMAND = "/bible"
COMPACT_COMMAND = "/compact"
SESSIONS_COMMAND = "/sessions"
SESSION_COMMAND = "/session"
NEW_COMMAND = "/new"
_ALL_ENTITY_TYPES: tuple[str, ...] = tuple(ENTITY_LABELS)


class PlainRenderer:
    """把 AgentEvent 流渲染成终端文本。

    未识别的事件类型不会被静默吞掉，而是记入 ``unhandled_event_types``——
    事件契约演进时宁可暴露出来，也不要让调试视图出现"少了一段"的灵异现象。
    """

    def __init__(self, tools: Mapping[str, AgentTool] | None = None) -> None:
        self._tools = dict(tools or {})
        self._unhandled: list[str] = []
        self._assistant_has_text = False

    @property
    def unhandled_event_types(self) -> tuple[str, ...]:
        return tuple(self._unhandled)

    def handle(self, event: AgentEvent) -> None:
        match event:
            case MessageUpdateEvent(assistant_message_event=TextDeltaEvent(delta=delta)):
                print(delta, end="", flush=True)
                self._assistant_has_text = True
            case ToolExecutionStartEvent() as start:
                print(f"[tool] {start.tool_name}{self._render_call(start)}", flush=True)
            case ToolExecutionEndEvent() as end if end.is_error:
                detail = " ".join(end.result.text.split())
                print(f"[tool error] {end.tool_name} {detail}", flush=True)
            case MessageEndEvent(message=message) if (
                message.role == "assistant" and self._assistant_has_text
            ):
                print(flush=True)
                self._assistant_has_text = False
            case (
                AgentStartEvent()
                | AgentEndEvent()
                | TurnStartEvent()
                | TurnEndEvent()
                | MessageStartEvent()
                | MessageEndEvent()
                | MessageUpdateEvent()
                | ToolExecutionStartEvent()
                | ToolExecutionUpdateEvent()
                | ToolExecutionEndEvent()
            ):
                pass
            case _:
                self._unhandled.append(str(getattr(event, "type", "unknown")))

    def _render_call(self, start: ToolExecutionStartEvent) -> str:
        tool = self._tools.get(start.tool_name)
        if tool is None or tool.render_call is None:
            return ""
        rendered = tool.render_call(start.args)
        return f" {rendered}" if rendered else ""


def build_harness(
    workspace: StoryWorkspace,
    provider: ModelProvider,
    model: str,
    max_advances_per_prompt: int | None = None,
) -> AgentHarness:
    """按工作区装配工具集、system prompt、事件账本与 harness。

    system 与 HastingsSession.from_workspace 对齐：workspace 传入状态注入
    （P8 T5 补齐）——CLI 冒烟观察到的模型行为必须与 Web 端同链路，否则
    「intake 协议遵循度」等走查结论不可信。事件持久化同理（F4）：在装配点
    挂 events.jsonl，run_repl 与 eval driver 一处接线两端生效。

    ``max_advances_per_prompt``：评测口径放宽回合规速用（缺省 None 保持
    产品语义——每回合至多一次成功推进）；eval driver 传值须随运行归档。
    """
    # 项目级架构版本在这一处解析并传入工具集：V2 项目装配拒绝桩，V1 行为不变
    # （ADR architecture-version-routing §2.2：「项目解析」是唯一判定点）。
    tools = create_writing_tools(
        workspace, architecture_version=workspace_architecture_version(workspace)
    )
    language = load_settings(workspace).writing_language
    harness = AgentHarness(
        AgentHarnessConfig(
            provider=provider,
            model=model,
            system=build_system_prompt(tools, language=language, workspace=workspace),
            tools=tools,
        )
    )
    harness.subscribe(make_event_persister(EventStore(events_file(workspace))))
    if max_advances_per_prompt is None:
        attach_guards(harness, workspace)
    else:
        attach_guards(harness, workspace, max_advances=max_advances_per_prompt)
    return harness


def create_writing_tools(
    workspace: StoryWorkspace,
    edit_marks: EditMarkRegistry | None = None,
    run_kind: RunKindGuard | None = None,
    *,
    architecture_version: str = "v1",
) -> list[AgentTool]:
    """装配写作工具集；``architecture_version="v2"`` 时旧通用写工具（bible_upsert、
    write_chapter、method_lock/unlock、story_stage）全部替换为拒绝桩——V2 项目只经
    领域命令与 Commit Service 写入（T17.6）；只读工具保持可用。缺省 ``"v1"`` 行为不变。"""
    return (
        create_bible_tools(workspace, architecture_version=architecture_version)
        + create_chapter_tools(
            workspace,
            edit_marks=edit_marks,
            run_kind=run_kind,
            architecture_version=architecture_version,
        )
        + create_method_tools(workspace, architecture_version=architecture_version)
        + create_timeline_tools(workspace)
        + create_story_tools(workspace, architecture_version=architecture_version)
    )


def _ledger_entities(workspace: StoryWorkspace, entity_type: str) -> Sequence[BaseModel]:
    """按账本类型直读实体（/bible 命令专用，绕过 agent loop）。"""
    loaders: dict[str, Callable[[], Sequence[BaseModel]]] = {
        "character": lambda: list_characters(workspace),
        "clue": lambda: load_ledger(workspace.clues_path, Clue),
        "foreshadow": lambda: load_ledger(workspace.foreshadows_path, Foreshadow),
        "method": lambda: load_ledger(workspace.method_path, Method),
        "timeline": lambda: load_ledger(workspace.timeline_path, TimelineEntry),
        "outline": lambda: load_ledger(workspace.outline_path, OutlineEntry),
    }
    return loaders[entity_type]()


def format_ledger(workspace: StoryWorkspace, entity_type: str) -> str:
    """``/bible <type>`` 的输出：单账本摘要列表。"""
    entities = _ledger_entities(workspace, entity_type)
    label = ENTITY_LABELS[entity_type]
    if not entities:
        return f"{label}账本为空。"
    lines = [f"{label}账本共 {len(entities)} 条："]
    lines.extend(f"- {entity_summary(e)}" for e in entities)
    return "\n".join(lines)


def format_bible(workspace: StoryWorkspace, entity_type: str | None = None) -> str:
    """``/bible`` 的输出：缺省六账本总览，带 type 则只看该账本。"""
    if entity_type is None:
        return "\n".join(format_ledger(workspace, t) for t in _ALL_ENTITY_TYPES)
    if entity_type not in _ALL_ENTITY_TYPES:
        return f"未知账本类型：{entity_type}。可用值：{'、'.join(_ALL_ENTITY_TYPES)}。"
    return format_ledger(workspace, entity_type)


def bible_command_output(workspace: StoryWorkspace, arg: str) -> str:
    """/bible 命令的完整输出。

    架构判定（唯一判定点）：已迁移工作区走 V2 查询（``cli_v2.v2_bible_output``），
    不再读 YAML——迁移后账本事实源在 V2 仓库，读旧 YAML 会把用户带回旧真相。
    V1 工作区保持直读；直读入口是唯一绕过工具层碰 YAML 的地方：手改的文件可能
    损坏，读取失败转成可读文本而不是抛异常崩 REPL（R3 #4 教训）。
    """
    try:
        if workspace_architecture_version(workspace) == "v2":
            return v2_bible_output(workspace, arg or None)
        return format_bible(workspace, arg or None)
    except (ValidationError, LedgerFormatError, yaml.YAMLError, OSError) as exc:
        return f"读取 Story Bible 失败：{exc}\n（请检查 {workspace.bible_dir} 下的 YAML 文件）"


async def compact_command_output(harness: AgentHarness, workspace: StoryWorkspace) -> str:
    """/compact 命令输出（P10 T13，D12）：手动两档压缩（绕过阈值）。

    无可压缩空间 → 「无需压缩」正常文本（合法空结果）；不抛异常崩 REPL。
    """
    result = await compact_context(harness, workspace, manual=True)
    if result.compacted:
        parts: list[str] = []
        if result.stripped_chapters:
            parts.append(f"剥离 {len(result.stripped_chapters)} 章正文参数")
        if result.summarized:
            parts.append("生成前情摘要")
        detail = "、".join(parts) if parts else "压缩完成"
        archived = f"（已归档 {result.archived}）" if result.archived else ""
        return (
            f"已压缩上下文：{detail}，"
            f"{result.tokens_before} → {result.tokens_after} token{archived}。"
        )
    if result.reason == "nothing_to_strip":
        return "当前上下文无需压缩。怎样算对：继续写作即可，超阈值时会自动压缩。"
    if result.reason == "running":
        return "压缩被拒：有写作流进行中。怎样算对：等本轮结束后再执行 /compact。"
    return f"未压缩（原因：{result.reason}）。"


def format_sessions(workspace: StoryWorkspace) -> str:
    """``/sessions`` 的输出：会话列表，星号标记当前活跃会话。"""
    active = active_session_id(workspace)
    metas = list_sessions(workspace)
    lines = [f"会话共 {len(metas)} 个（* 为当前活跃）："]
    for meta in metas:
        marker = "*" if meta.id == active else " "
        lines.append(f"{marker} {meta.id}  {meta.title}（{meta.message_count} 条）")
    return "\n".join(lines)


async def run_repl(workspace: StoryWorkspace, provider: ModelProvider, model: str) -> None:
    """终端 REPL：输入一句话，Agent 通过工具改故事工作区。"""
    tools = create_writing_tools(workspace)
    harness = build_harness(workspace, provider, model)
    renderer = PlainRenderer({tool.name: tool for tool in tools})
    workspace.ensure_dirs()
    unsubscribe = await restore_and_attach(workspace, harness, on_warning=print)

    print(f"文章已打开：{workspace.story_dir}")
    print(f"模型：{model}｜工具：{'、'.join(tool.name for tool in tools)}")
    print(
        "输入 /bible [type] 查看账本，/compact 压缩上下文，"
        "/sessions 列出会话，/session <id> 切换，/new 新建，/quit 收工。"
    )
    print()

    async def rebind() -> Callable[[], None]:
        """退订旧会话存储/索引订阅，重新接线到当前活跃会话。"""
        unsubscribe()
        return await restore_and_attach(workspace, harness, on_warning=print)

    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in QUIT_COMMANDS:
            break
        if line == BIBLE_COMMAND or line.startswith(BIBLE_COMMAND + " "):
            print(bible_command_output(workspace, line[len(BIBLE_COMMAND) :].strip()))
            print()
            continue
        if line == COMPACT_COMMAND:
            print(await compact_command_output(harness, workspace))
            print()
            continue
        if line == SESSIONS_COMMAND:
            print(format_sessions(workspace))
            print()
            continue
        if line.startswith(SESSION_COMMAND + " "):
            target = line[len(SESSION_COMMAND) + 1 :].strip()
            try:
                meta = activate_session(workspace, target)
            except SessionNotFoundError as exc:
                print(exc)
                print()
                continue
            unsubscribe = await rebind()
            print(f"已切换到会话 {meta.id}（{meta.title}）。")
            print()
            continue
        if line == NEW_COMMAND:
            meta = create_session(workspace)
            unsubscribe = await rebind()
            print(f"已新建会话 {meta.id}（{meta.title}）。")
            print()
            continue

        refresh_system_prompt(harness, workspace)  # L7：每轮 prompt 前注入最新状态
        await compact_context(harness, workspace)  # L9：刷新后两档压缩（超阈值才动）
        async for event in harness.prompt(line):
            renderer.handle(event)
        print()

    if renderer.unhandled_event_types:
        print(f"（有 {len(renderer.unhandled_event_types)} 类事件未渲染，属调试信息）")
    warn_empty_chapters(workspace)  # D2 第 3 层：写作期空稿观测信号
    print("故事已合上。")


__all__ = [
    "BIBLE_COMMAND",
    "COMPACT_COMMAND",
    "NEW_COMMAND",
    "QUIT_COMMANDS",
    "SESSION_COMMAND",
    "SESSIONS_COMMAND",
    "PlainRenderer",
    "bible_command_output",
    "build_harness",
    "compact_command_output",
    "create_writing_tools",
    "format_bible",
    "format_ledger",
    "format_sessions",
    "run_repl",
]
