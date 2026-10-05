"""Bible 读写工具：bible_upsert / bible_query。

工厂闭包模式（学 tau_coding 的 create_read_tool_definition，但不 import tau_coding）：
工具经闭包绑定 StoryWorkspace；参数错误一律抛 ToolInputError，由 loop 转为
工具错误结果喂回模型。
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import ValidationError

from hastings.bible.models import Character
from hastings.bible.store import (
    InvalidEntityNameError,
    StoryWorkspace,
    character_to_yaml,
    list_characters,
    load_character,
    save_character,
)
from hastings.tools.errors import ToolInputError
from tau_agent.messages import TextContent
from tau_agent.tools import AgentTool, AgentToolResult, ToolCancellationToken, ToolUpdateCallback
from tau_agent.types import JSONValue

_UNSUPPORTED_ENTITY = (
    "暂不支持 entity_type={!r}：当前仅支持 'character'"
    "（clue/foreshadow/method/timeline 将在 Phase 2 提供）。"
)


def _require_str(arguments: Mapping[str, JSONValue], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ToolInputError(f"{name} 必须是非空字符串")
    return value


def _check_entity_type(arguments: Mapping[str, JSONValue]) -> None:
    entity_type = _require_str(arguments, "entity_type")
    if entity_type != "character":
        raise ToolInputError(_UNSUPPORTED_ENTITY.format(entity_type))


def _upsert_render_call(arguments: Mapping[str, JSONValue]) -> str | None:
    data = arguments.get("data")
    name = data.get("name") if isinstance(data, dict) else None
    return f"写入角色 {name}" if isinstance(name, str) else "写入角色"


def _query_render_call(arguments: Mapping[str, JSONValue]) -> str | None:
    name = arguments.get("name")
    return f"查询角色 {name}" if isinstance(name, str) and name else "列出全部角色"


def create_bible_tools(workspace: StoryWorkspace) -> list[AgentTool]:
    """创建绑定 workspace 的 bible_upsert / bible_query 工具。"""

    async def upsert_execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        _check_entity_type(arguments)
        data = arguments.get("data")
        if not isinstance(data, dict):
            raise ToolInputError("data 必须是角色字段对象（至少包含 name）")
        try:
            character = Character.model_validate(data)
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(str(loc) for loc in error['loc'])}: {error['msg']}"
                for error in exc.errors(include_url=False)
            )
            raise ToolInputError(f"角色数据不合法：{details}") from exc
        try:
            path = save_character(workspace, character)
        except InvalidEntityNameError as exc:
            raise ToolInputError(str(exc)) from exc
        return AgentToolResult(
            content=[TextContent(text=f"角色「{character.name}」已写入 {path}")],
            details={"entity_type": "character", "name": character.name, "path": str(path)},
        )

    async def query_execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        _check_entity_type(arguments)
        name = arguments.get("name")
        if name is not None and not isinstance(name, str):
            raise ToolInputError("name 必须是字符串")
        if isinstance(name, str) and name:
            try:
                character = load_character(workspace, name)
            except InvalidEntityNameError as exc:
                raise ToolInputError(str(exc)) from exc
            if character is None:
                return AgentToolResult(
                    content=[
                        TextContent(
                            text=(
                                f"角色「{name}」不存在。可用 bible_upsert 创建，"
                                "或不带 name 调用 bible_query 列出全部角色。"
                            )
                        )
                    ],
                    details={"found": False, "name": name},
                )
            return AgentToolResult(
                content=[TextContent(text=character_to_yaml(character))],
                details={"found": True, "character": character.model_dump()},
            )
        characters = list_characters(workspace)
        if not characters:
            return AgentToolResult(
                content=[TextContent(text="Story Bible 为空：还没有任何角色。")],
                details={"count": 0, "names": []},
            )
        lines = [f"Story Bible 中共 {len(characters)} 个角色："]
        for c in characters:
            parts = [c.name]
            if c.identity:
                parts.append(c.identity)
            if c.motivation:
                parts.append(f"动机：{c.motivation}")
            lines.append("- " + " | ".join(parts))
        return AgentToolResult(
            content=[TextContent(text="\n".join(lines))],
            details={"count": len(characters), "names": [c.name for c in characters]},
        )

    upsert = AgentTool(
        name="bible_upsert",
        label="写入角色卡",
        description=(
            "把一张角色卡写入 Story Bible（YAML 落盘，同名覆盖）。"
            "正文引用任何角色设定前，必须先用本工具落盘。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "entity_type": {
                    "type": "string",
                    "enum": ["character"],
                    "description": "实体类型，当前仅支持 character",
                },
                "data": {
                    "type": "object",
                    "description": (
                        "角色字段：name 必填；identity/motivation/secret/relationships/notes 可选"
                    ),
                },
            },
            "required": ["entity_type", "data"],
            "additionalProperties": False,
        },
        execute_fn=upsert_execute,
        prompt_guidelines=(
            "任何角色设定必须先经 bible_upsert 落盘，再写进正文。",
            "data 至少包含 name；同名再次调用视为更新。",
        ),
        render_call=_upsert_render_call,
    )
    query = AgentTool(
        name="bible_query",
        label="查询角色卡",
        description=(
            "查询 Story Bible 中的角色卡：带 name 返回该角色完整 YAML；不带 name 返回全部角色摘要。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "entity_type": {
                    "type": "string",
                    "enum": ["character"],
                    "description": "实体类型，当前仅支持 character",
                },
                "name": {"type": "string", "description": "角色名；缺省则列出全部"},
            },
            "required": ["entity_type"],
            "additionalProperties": False,
        },
        execute_fn=query_execute,
        prompt_guidelines=("动笔前先 bible_query 核对既有设定，避免正文与 Bible 矛盾。",),
        render_call=_query_render_call,
    )
    return [upsert, query]


__all__ = ["create_bible_tools"]
