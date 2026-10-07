"""命令行交互与纯文本渲染器（Phase 1 T5）。

渲染器订阅 harness 事件流做流式打印——思路照搬 tau_coding 的 plain 渲染器，
但用内置 print 自写，不引入 typer/rich 这类依赖（项目保持依赖最小化）。
"""

from __future__ import annotations

from collections.abc import Mapping

import yaml
from pydantic import ValidationError

from hastings.bible.models import Character
from hastings.bible.store import StoryWorkspace, list_characters
from hastings.prompts import build_system_prompt
from hastings.tools.bible_tools import create_bible_tools
from hastings.tools.chapter_tools import create_chapter_tools
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
CHARACTERS_COMMAND = "/characters"


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


def create_writing_tools(workspace: StoryWorkspace) -> list[AgentTool]:
    return create_bible_tools(workspace) + create_chapter_tools(workspace)


def format_characters(characters: list[Character]) -> str:
    """``/characters`` 的输出格式。"""
    if not characters:
        return "Story Bible 为空：还没有任何角色。试着说「建立一个悬疑案的两位角色」。"
    lines = [f"Story Bible 共 {len(characters)} 位角色："]
    for character in characters:
        detail = character.identity or character.motivation or "（暂无身份描述）"
        lines.append(f"- {character.name}｜{detail}")
    return "\n".join(lines)


async def run_repl(workspace: StoryWorkspace, provider: ModelProvider, model: str) -> None:
    """终端 REPL：输入一句话，Agent 通过工具改故事工作区。"""
    tools = create_writing_tools(workspace)
    harness = build_harness(workspace, provider, model)
    renderer = PlainRenderer({tool.name: tool for tool in tools})
    workspace.ensure_dirs()

    print(f"案卷已打开：{workspace.story_dir}")
    print(f"模型：{model}｜工具：{'、'.join(tool.name for tool in tools)}")
    print("输入 /characters 查看账本，/quit 收工。")
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
        if line == CHARACTERS_COMMAND:
            try:
                print(format_characters(list_characters(workspace)))
            except (ValidationError, yaml.YAMLError, OSError) as exc:
                # 手改的 YAML 可能损坏；工具路径有 agent loop 兜底，这里是唯一直读入口。
                print(f"读取 Story Bible 失败：{exc}")
                print(f"（请检查 {workspace.characters_dir} 下的 YAML 文件）")
            print()
            continue
        async for event in harness.prompt(line):
            renderer.handle(event)
        print()

    if renderer.unhandled_event_types:
        print(f"（有 {len(renderer.unhandled_event_types)} 类事件未渲染，属调试信息）")
    print("故事已合上。")


__all__ = [
    "CHARACTERS_COMMAND",
    "QUIT_COMMANDS",
    "PlainRenderer",
    "build_harness",
    "create_writing_tools",
    "format_characters",
    "run_repl",
]
