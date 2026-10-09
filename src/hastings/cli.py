"""命令行交互与纯文本渲染器（Phase 1 T5）。

渲染器订阅 harness 事件流做流式打印——思路照搬 tau_coding 的 plain 渲染器，
但用内置 print 自写，不引入 typer/rich 这类依赖（项目保持依赖最小化）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

import yaml
from pydantic import BaseModel, ValidationError

from hastings.bible.models import Clue, Foreshadow, Method, TimelineEntry
from hastings.bible.store import (
    LedgerFormatError,
    StoryWorkspace,
    list_characters,
    load_ledger,
)
from hastings.manuscript.write_guard import EditMarkRegistry, RunKindGuard
from hastings.prompts import build_system_prompt
from hastings.session_persist import restore_and_attach
from hastings.tools.bible_tools import ENTITY_LABELS, create_bible_tools, entity_summary
from hastings.tools.chapter_tools import create_chapter_tools
from hastings.tools.method_tools import create_method_tools
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


def build_harness(workspace: StoryWorkspace, provider: ModelProvider, model: str) -> AgentHarness:
    """按工作区装配工具集、system prompt 与 harness。"""
    tools = create_writing_tools(workspace)
    return AgentHarness(
        AgentHarnessConfig(
            provider=provider,
            model=model,
            system=build_system_prompt(tools),
            tools=tools,
        )
    )


def create_writing_tools(
    workspace: StoryWorkspace,
    edit_marks: EditMarkRegistry | None = None,
    run_kind: RunKindGuard | None = None,
) -> list[AgentTool]:
    return (
        create_bible_tools(workspace)
        + create_chapter_tools(workspace, edit_marks=edit_marks, run_kind=run_kind)
        + create_method_tools(workspace)
        + create_timeline_tools(workspace)
    )


def _ledger_entities(workspace: StoryWorkspace, entity_type: str) -> Sequence[BaseModel]:
    """按账本类型直读实体（/bible 命令专用，绕过 agent loop）。"""
    loaders: dict[str, Callable[[], Sequence[BaseModel]]] = {
        "character": lambda: list_characters(workspace),
        "clue": lambda: load_ledger(workspace.clues_path, Clue),
        "foreshadow": lambda: load_ledger(workspace.foreshadows_path, Foreshadow),
        "method": lambda: load_ledger(workspace.method_path, Method),
        "timeline": lambda: load_ledger(workspace.timeline_path, TimelineEntry),
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
    """``/bible`` 的输出：缺省五账本总览，带 type 则只看该账本。"""
    if entity_type is None:
        return "\n".join(format_ledger(workspace, t) for t in _ALL_ENTITY_TYPES)
    if entity_type not in _ALL_ENTITY_TYPES:
        return f"未知账本类型：{entity_type}。可用值：{'、'.join(_ALL_ENTITY_TYPES)}。"
    return format_ledger(workspace, entity_type)


def bible_command_output(workspace: StoryWorkspace, arg: str) -> str:
    """/bible 命令的完整输出。

    直读入口是唯一绕过工具层碰 YAML 的地方：手改的文件可能损坏，
    读取失败转成可读文本而不是抛异常崩 REPL（R3 #4 教训）。
    """
    try:
        return format_bible(workspace, arg or None)
    except (ValidationError, LedgerFormatError, yaml.YAMLError, OSError) as exc:
        return f"读取 Story Bible 失败：{exc}\n（请检查 {workspace.bible_dir} 下的 YAML 文件）"


async def run_repl(workspace: StoryWorkspace, provider: ModelProvider, model: str) -> None:
    """终端 REPL：输入一句话，Agent 通过工具改故事工作区。"""
    tools = create_writing_tools(workspace)
    harness = build_harness(workspace, provider, model)
    renderer = PlainRenderer({tool.name: tool for tool in tools})
    workspace.ensure_dirs()
    await restore_and_attach(workspace, harness, on_warning=print)

    print(f"案卷已打开：{workspace.story_dir}")
    print(f"模型：{model}｜工具：{'、'.join(tool.name for tool in tools)}")
    print("输入 /bible [type] 查看账本，/quit 收工。")
    print()

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
        async for event in harness.prompt(line):
            renderer.handle(event)
        print()

    if renderer.unhandled_event_types:
        print(f"（有 {len(renderer.unhandled_event_types)} 类事件未渲染，属调试信息）")
    print("故事已合上。")


__all__ = [
    "BIBLE_COMMAND",
    "QUIT_COMMANDS",
    "PlainRenderer",
    "bible_command_output",
    "build_harness",
    "create_writing_tools",
    "format_bible",
    "format_ledger",
    "run_repl",
]
