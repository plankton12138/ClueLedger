"""Bible 只读查询工具：bible_query（V1 弃用后的存留读通道）。

V1 写路径（bible_upsert / method_lock / method_unlock / story_stage /
write_chapter）已随「V1 工作区只读退役」删除（Phase 17 收尾决策：项目未上线，
V1 直接弃用）；本模块保留实体注册表的**读半区**（模型/摘要/标签），供：
- ``bible_query`` 工具：agent 在 V1 只读工作区核对既有设定；
- CLI ``/bible``：直读入口复用同一注册表（标签与摘要行的唯一事实源）。

写入已无处可去：V2 项目经领域命令与 Commit Service；V1 工作区只剩
``bible/store.py`` 的 save_* 作为测试夹具级工具（无生产调用方）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from pydantic import BaseModel

from hastings.bible.models import (
    Character,
    Clue,
    Foreshadow,
    Method,
    OutlineEntry,
    TimelineEntry,
)
from hastings.bible.store import (
    StoryWorkspace,
    list_characters,
    load_ledger,
    model_to_yaml,
)
from hastings.tools.errors import ToolInputError
from tau_agent.messages import TextContent
from tau_agent.tools import (
    AgentTool,
    AgentToolResult,
    ToolCancellationToken,
    ToolUpdateCallback,
)
from tau_agent.types import JSONValue

type ReadAllFn = Callable[[StoryWorkspace], Sequence[BaseModel]]


def _require_str(arguments: Mapping[str, JSONValue], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ToolInputError(f"{name} 必须是非空字符串")
    return value


def _character_read_all(workspace: StoryWorkspace) -> Sequence[BaseModel]:
    return list_characters(workspace)


def _make_ledger_read[M: (Clue, Foreshadow, Method, OutlineEntry, TimelineEntry)](
    path_getter: Callable[[StoryWorkspace], Path], model_cls: type[M]
) -> ReadAllFn:
    """单文件账本的只读闭包：读整表。"""

    def read_all(workspace: StoryWorkspace) -> Sequence[BaseModel]:
        return load_ledger(path_getter(workspace), model_cls)

    return read_all


# --- 每账本摘要行（查询列表展示；带状态让模型对账不必查全文） ---


def _summarize_character(entity: BaseModel) -> str:
    assert isinstance(entity, Character)
    parts = [entity.name]
    if entity.identity:
        parts.append(entity.identity)
    if entity.motivation:
        parts.append(f"动机：{entity.motivation}")
    parts.append(f"弧光：{entity.arc}")
    return " | ".join(parts)


def _summarize_clue(entity: BaseModel) -> str:
    assert isinstance(entity, Clue)
    parts = [entity.name, entity.status]
    if entity.chapter:
        parts.append(f"出现 {entity.chapter}")
    if entity.points_to:
        parts.append(f"指向 {entity.points_to}")
    if entity.is_red_herring:
        parts.append("干扰项")
    return " | ".join(parts)


def _summarize_foreshadow(entity: BaseModel) -> str:
    assert isinstance(entity, Foreshadow)
    parts = [entity.name, entity.status]
    if entity.plant_chapter:
        parts.append(f"埋设 {entity.plant_chapter}")
    if entity.expected_resolve_chapter:
        parts.append(f"预计回收 {entity.expected_resolve_chapter}")
    return " | ".join(parts)


def _summarize_method(entity: BaseModel) -> str:
    assert isinstance(entity, Method)
    parts = [entity.name, entity.status]
    if entity.requirements:
        parts.append(f"前提 {len(entity.requirements)} 条")
    if entity.alibi_claims:
        parts.append(f"不在场主张 {len(entity.alibi_claims)} 条")
    if entity.history:
        parts.append(f"锁定历史 {len(entity.history)} 条")
    return " | ".join(parts)


def _summarize_timeline(entity: BaseModel) -> str:
    assert isinstance(entity, TimelineEntry)
    parts = [entity.name, f"第 {entity.day} 天"]
    if entity.t_start:
        parts.append(f"{entity.t_start}-{entity.t_end or '?'}")
    if entity.chapter:
        parts.append(f"见 {entity.chapter}")
    if entity.participants:
        parts.append(f"参与者：{'、'.join(entity.participants)}")
    return " | ".join(parts)


def _summarize_outline(entity: BaseModel) -> str:
    assert isinstance(entity, OutlineEntry)
    parts = [entity.name, f"ch{entity.chapter}", entity.status]
    if entity.act:
        parts.append(entity.act)
    if entity.beats:
        parts.append(f"节拍 {len(entity.beats)} 拍")
    if entity.plant_plans:
        parts.append(f"拟埋伏笔：{'、'.join(entity.plant_plans)}")
    if entity.end_hook:
        parts.append(f"章末钩：{entity.end_hook}")
    return " | ".join(parts)


@dataclass(frozen=True, slots=True)
class _Spec:
    """单类实体的读差异点：注册表消 if-else 的关键。"""

    label: str  # 中文名，用于渲染与消息
    model_cls: type[BaseModel]
    read_all: ReadAllFn
    summarize: Callable[[BaseModel], str]


_clue_read = _make_ledger_read(lambda ws: ws.clues_path, Clue)
_foreshadow_read = _make_ledger_read(lambda ws: ws.foreshadows_path, Foreshadow)
_method_read = _make_ledger_read(lambda ws: ws.method_path, Method)
_timeline_read = _make_ledger_read(lambda ws: ws.timeline_path, TimelineEntry)
_outline_read = _make_ledger_read(lambda ws: ws.outline_path, OutlineEntry)

_REGISTRY: Final[dict[str, _Spec]] = {
    "character": _Spec(
        label="角色",
        model_cls=Character,
        read_all=_character_read_all,
        summarize=_summarize_character,
    ),
    "clue": _Spec(
        label="线索",
        model_cls=Clue,
        read_all=_clue_read,
        summarize=_summarize_clue,
    ),
    "foreshadow": _Spec(
        label="伏笔",
        model_cls=Foreshadow,
        read_all=_foreshadow_read,
        summarize=_summarize_foreshadow,
    ),
    "method": _Spec(
        label="手法",
        model_cls=Method,
        read_all=_method_read,
        summarize=_summarize_method,
    ),
    "timeline": _Spec(
        label="时间线事件",
        model_cls=TimelineEntry,
        read_all=_timeline_read,
        summarize=_summarize_timeline,
    ),
    "outline": _Spec(
        label="章纲",
        model_cls=OutlineEntry,
        read_all=_outline_read,
        summarize=_summarize_outline,
    ),
}


def _require_spec(arguments: Mapping[str, JSONValue]) -> tuple[str, _Spec]:
    entity_type = _require_str(arguments, "entity_type")
    spec = _REGISTRY.get(entity_type)
    if spec is None:
        raise ToolInputError(
            f"未知 entity_type={entity_type!r}：可选值 {'、'.join(sorted(_REGISTRY))}。"
        )
    return entity_type, spec


# --- 直读入口复用（CLI /bible）：标签与摘要行的唯一事实源是本注册表 ---

ENTITY_LABELS: Final[dict[str, str]] = {name: spec.label for name, spec in _REGISTRY.items()}

_SUMMARIZERS: Final[dict[type[BaseModel], Callable[[BaseModel], str]]] = {
    spec.model_cls: spec.summarize for spec in _REGISTRY.values()
}


def entity_summary(entity: BaseModel) -> str:
    """按实体实际类型渲染单行摘要（bible_query 列表与 CLI /bible 共用）。"""
    summarizer = _SUMMARIZERS.get(type(entity))
    return summarizer(entity) if summarizer is not None else repr(entity)


# JSONValue 列表不变型：sorted 返回 list[str] 需显式收窄
_ENTITY_ENUM: Final[list[JSONValue]] = cast("list[JSONValue]", sorted(_REGISTRY))


def _query_render_call(arguments: Mapping[str, JSONValue]) -> str | None:
    entity_type = arguments.get("entity_type")
    spec = _REGISTRY.get(entity_type) if isinstance(entity_type, str) else None
    label = spec.label if spec else "实体"
    name = arguments.get("name")
    if isinstance(name, str) and name:
        return f"查询{label} {name}"
    return f"列出全部{label}"


def create_bible_tools(workspace: StoryWorkspace) -> list[AgentTool]:
    """创建绑定 workspace 的 bible_query 工具（只读）。"""

    async def query_execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        entity_type, spec = _require_spec(arguments)
        name = arguments.get("name")
        if name is not None and not isinstance(name, str):
            raise ToolInputError("name 必须是字符串")
        entities = list(spec.read_all(workspace))

        if isinstance(name, str) and name:
            found = next((e for e in entities if getattr(e, "name", None) == name), None)
            if found is None:
                # 泄压阀：查无结果是合法空结果，给引导性提示而非 error
                return AgentToolResult(
                    content=[
                        TextContent(
                            text=(
                                f"{spec.label}「{name}」不存在。"
                                f"或不带 name 调用 bible_query 列出全部{spec.label}。"
                            )
                        )
                    ],
                    details={"found": False, "name": name},
                )
            return AgentToolResult(
                content=[TextContent(text=model_to_yaml(found))],
                details={"found": True, entity_type: found.model_dump()},
            )

        if not entities:
            return AgentToolResult(
                content=[TextContent(text=f"Story Bible 为空：还没有任何{spec.label}。")],
                details={"count": 0, "names": []},
            )
        lines = [f"Story Bible 中共 {len(entities)} 条{spec.label}："]
        lines.extend(f"- {spec.summarize(e)}" for e in entities)
        return AgentToolResult(
            content=[TextContent(text="\n".join(lines))],
            details={
                "count": len(entities),
                "names": [str(getattr(e, "name", "")) for e in entities],
            },
        )

    query = AgentTool(
        name="bible_query",
        label="查询 Bible 实体",
        description=(
            "查询 Story Bible：带 name 返回该实体完整 YAML；"
            "不带 name 返回该类账本摘要列表（含状态）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "entity_type": {
                    "type": "string",
                    "enum": _ENTITY_ENUM,
                    "description": (
                        "实体类型：character 角色 / clue 线索 / foreshadow 伏笔 / "
                        "method 手法 / timeline 时间线事件 / outline 章纲"
                    ),
                },
                "name": {"type": "string", "description": "实体名；缺省则列出全部"},
            },
            "required": ["entity_type"],
            "additionalProperties": False,
        },
        execute_fn=query_execute,
        prompt_guidelines=("动笔前先 bible_query 核对既有设定，避免正文与 Bible 矛盾。",),
        render_call=_query_render_call,
    )
    return [query]


__all__ = ["ENTITY_LABELS", "create_bible_tools", "entity_summary"]
