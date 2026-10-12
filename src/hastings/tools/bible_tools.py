"""Bible 读写工具：bible_upsert / bible_query（P2 五账本 + P8 outline 泛化）。

实体注册表模式：entity_type → _Spec（模型/读写/摘要/跨账本校验），
新增账本只加一处注册，杜绝 if-else 汤。硬约束（状态迁移、参与者建档、
章号唯一）只在本工具层强制——store 纯读写、prompt 只解释世界；错误消息
三要素（哪里错/为什么错/怎样算对）经 loop 原样喂回模型，助其自我修正。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from pydantic import BaseModel, ValidationError

from hastings.bible.models import (
    Character,
    Clue,
    Foreshadow,
    Method,
    OutlineEntry,
    TimelineEntry,
)
from hastings.bible.state_machine import (
    INITIAL_STATUS,
    TRANSITIONS,
    InvalidTransitionError,
    validate_initial,
    validate_transition,
)
from hastings.bible.store import (
    InvalidEntityNameError,
    StoryWorkspace,
    list_characters,
    load_ledger,
    load_story_doc,
    model_to_yaml,
    save_character,
    save_ledger,
)
from hastings.bible.story_state import intake_write_rejection
from hastings.tools.errors import ToolInputError
from tau_agent.messages import TextContent
from tau_agent.tools import AgentTool, AgentToolResult, ToolCancellationToken, ToolUpdateCallback
from tau_agent.types import JSONValue

type ReadAllFn = Callable[[StoryWorkspace], Sequence[BaseModel]]
type WriteAllFn = Callable[[StoryWorkspace, Sequence[BaseModel]], None]


# 参数校验错误的最小正确示例（P10 T5，D4/L3）：schema description 是第一 API、
# 错误消息是第二 API——弱模型对同一 schema 错误原样重试 7-16 次才改行为（F1），
# 双通道冗余是刚需。仅覆盖压测实中的高频字段（name 空 / alibi day / requirements），
# 不求全类型；未覆盖实体的类型错误不硬塞示例（避免牛头不对马嘴）。
_NAME_EXAMPLE: Final = 'name 是账本主键（如角色 "宫泽健一"、章纲 "ch01"）'
_EXAMPLES: Final[dict[str, str]] = {
    "outline": 'data.name 是章纲主键，取 chNN 形态（如 "ch01"），chapter 字段才是章号',
    "method": (
        'alibi_claims 的 day 是整数天数（如 1）、t_start/t_end 是 "HH:MM" 字符串'
        '（如 "18:00"），requirements 是字符串列表（如 ["需停电"]）'
    ),
}


def _validation_example_note(entity_type: str) -> str:
    """字段校验错误时按实体类型追加最小示例；未覆盖类型不追加。"""
    example = _EXAMPLES.get(entity_type)
    return f"怎样算对（最小示例）：{example}。" if example else ""


def _require_str(arguments: Mapping[str, JSONValue], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ToolInputError(f"{name} 必须是非空字符串")
    return value


def _character_read_all(workspace: StoryWorkspace) -> Sequence[BaseModel]:
    return list_characters(workspace)


def _character_write_all(workspace: StoryWorkspace, entities: Sequence[BaseModel]) -> None:
    for entity in entities:
        assert isinstance(entity, Character)
        save_character(workspace, entity)


def _make_ledger_ops[M: (Clue, Foreshadow, Method, OutlineEntry, TimelineEntry)](
    path_getter: Callable[[StoryWorkspace], Path], model_cls: type[M]
) -> tuple[ReadAllFn, WriteAllFn]:
    """单文件账本的读写闭包：读整表、按 name 合并后整表覆盖写（D7）。"""

    def read_all(workspace: StoryWorkspace) -> Sequence[BaseModel]:
        return load_ledger(path_getter(workspace), model_cls)

    def write_all(workspace: StoryWorkspace, entities: Sequence[BaseModel]) -> None:
        save_ledger(path_getter(workspace), [e for e in entities if isinstance(e, model_cls)])

    return read_all, write_all


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


# --- 跨账本引用校验（D8：守门人职责） ---


def _check_timeline_participants(workspace: StoryWorkspace, data: Mapping[str, JSONValue]) -> None:
    participants = data.get("participants")
    if not isinstance(participants, list):
        return  # 缺省/类型问题交给 pydantic 字段校验回喂
    known = {c.name for c in list_characters(workspace)}
    missing = sorted({p for p in participants if isinstance(p, str) and p not in known})
    if missing:
        raise ToolInputError(
            f"时间线参与者未建档：{'、'.join(missing)}。"
            "为什么错：人物分身/不在场检测依赖参与者身份，participants 必须是已建档角色。"
            "怎样算对：先用 bible_upsert(entity_type='character') 逐个建档，再登记该事件。"
        )


def _check_outline_chapter_unique(workspace: StoryWorkspace, data: Mapping[str, JSONValue]) -> None:
    """章号唯一性（P8 T3，计划 D3）：name 之外的第二主键，工具层强制。

    同章号两条章纲 = 写作目标歧义，upsert 直接拒绝；同名条目保持自身
    章号（覆盖更新）或改到空位（重排）均放行。chapter 缺失/类型不对时
    放行，交给 pydantic 字段校验回喂更精确的字段错误。
    """
    chapter = data.get("chapter")
    if not isinstance(chapter, int) or isinstance(chapter, bool):
        return
    name = data.get("name")
    entries = load_ledger(workspace.outline_path, OutlineEntry)
    conflict = next(
        (e for e in entries if e.chapter == chapter and e.name != name),
        None,
    )
    if conflict is not None:
        raise ToolInputError(
            f"章纲「{conflict.name}」已占用章号 {chapter}，章号必须唯一。"
            f"为什么错：同一章号两条章纲会让 write_chapter 的写作目标产生歧义。"
            "怎样算对：新章纲改用未占用的章号；若确属重排，先把"
            f"「{conflict.name}」的 chapter 改到其他章号（upsert 同名覆盖）。"
        )


def _reject_locked_method_edit(name: str, existing: Method, data: Mapping[str, JSONValue]) -> None:
    """locked 手法的 upsert 拦截（T4）：内容冻结，修改须先 method_unlock。

    history 由 lock/unlock 工具维护，覆盖写时原样保留，不参与变化判定；
    数据本身不合法时放行（让后续 pydantic 校验回喂更精确的字段错误）。
    无内容变化时仅允许 locked（no-op）与 revealed（locked→revealed 是
    唯一经 upsert 的出边）；locked→drafted 一律拒——这条边只归 unlock 工具。
    """
    try:
        candidate = Method.model_validate(dict(data))
    except ValidationError:
        return
    changed = sorted(
        field
        for field in Method.model_fields
        if field not in ("status", "history")
        and getattr(candidate, field) != getattr(existing, field)
    )
    # upsert 缺省 status = 保持现状态；data 显式给出的才参与边判定
    requested = data.get("status")
    status_after = requested if isinstance(requested, str) else existing.status
    if changed or status_after not in ("locked", "revealed"):
        fields = "、".join(changed) if changed else f"status={status_after}"
        raise ToolInputError(
            f"手法「{name}」处于 locked 状态，bible_upsert 拒绝修改：{fields}。"
            "为什么错：locked 冻结手法内容，改任何字段（含退回 drafted）都必须先解锁，"
            "locked→drafted 这条边只归 method_unlock 工具。"
            "怎样算对：改内容先 method_unlock(name=..., reason=...)；"
            f"若诡计已揭晓，用 bible_query 取回「{name}」全量数据，"
            "把 data.status 设为 'revealed' 后整体重交（upsert 为覆盖写）。"
        )


@dataclass(frozen=True, slots=True)
class _Spec:
    """单类实体的全部差异点：注册表消 if-else 的关键。"""

    label: str  # 中文名，用于渲染与错误消息
    model_cls: type[BaseModel]
    read_all: ReadAllFn
    write_all: WriteAllFn
    summarize: Callable[[BaseModel], str]
    status_field: str | None  # character → "arc"，其余 → "status"；timeline 无状态机
    cross_check: Callable[[StoryWorkspace, Mapping[str, JSONValue]], None] | None = None


_clue_read, _clue_write = _make_ledger_ops(lambda ws: ws.clues_path, Clue)
_foreshadow_read, _foreshadow_write = _make_ledger_ops(lambda ws: ws.foreshadows_path, Foreshadow)
_method_read, _method_write = _make_ledger_ops(lambda ws: ws.method_path, Method)
_timeline_read, _timeline_write = _make_ledger_ops(lambda ws: ws.timeline_path, TimelineEntry)
_outline_read, _outline_write = _make_ledger_ops(lambda ws: ws.outline_path, OutlineEntry)

_REGISTRY: Final[dict[str, _Spec]] = {
    "character": _Spec(
        label="角色",
        model_cls=Character,
        read_all=_character_read_all,
        write_all=_character_write_all,
        summarize=_summarize_character,
        status_field="arc",
    ),
    "clue": _Spec(
        label="线索",
        model_cls=Clue,
        read_all=_clue_read,
        write_all=_clue_write,
        summarize=_summarize_clue,
        status_field="status",
    ),
    "foreshadow": _Spec(
        label="伏笔",
        model_cls=Foreshadow,
        read_all=_foreshadow_read,
        write_all=_foreshadow_write,
        summarize=_summarize_foreshadow,
        status_field="status",
    ),
    "method": _Spec(
        label="手法",
        model_cls=Method,
        read_all=_method_read,
        write_all=_method_write,
        summarize=_summarize_method,
        status_field="status",
    ),
    "timeline": _Spec(
        label="时间线事件",
        model_cls=TimelineEntry,
        read_all=_timeline_read,
        write_all=_timeline_write,
        summarize=_summarize_timeline,
        status_field=None,  # D1：事件只增改，无生命周期
        cross_check=_check_timeline_participants,
    ),
    "outline": _Spec(
        label="章纲",
        model_cls=OutlineEntry,
        read_all=_outline_read,
        write_all=_outline_write,
        summarize=_summarize_outline,
        status_field="status",
        cross_check=_check_outline_chapter_unique,
    ),
}


def reject_intake_write(workspace: StoryWorkspace) -> None:
    """L2 数据面写门（P10 D1）：intake 阶段拒绝建账。

    bible_upsert 与 method_lock/method_unlock 共用（一处判定全写路径生效）。
    全新工作区（story.yaml 未落盘且账本为空）按 intake 处理——门必须在此生效，
    否则首轮抢跑建账恰好绕过；仅「旧工作区」（story.yaml 缺失但已有账本内容）
    放行，遵循「不强制迁移旧数据」。
    """
    if not workspace.story_path.exists() and _has_ledger_content(workspace):
        return
    message = intake_write_rejection(load_story_doc(workspace).stage)
    if message is not None:
        raise ToolInputError(message)


def _has_ledger_content(workspace: StoryWorkspace) -> bool:
    """账本内容判定：无 story.yaml 的旧工作区据此与全新空工作区区分。"""
    if any(workspace.characters_dir.glob("*.yaml")):
        return True
    ledger_paths = (
        workspace.clues_path,
        workspace.foreshadows_path,
        workspace.method_path,
        workspace.timeline_path,
        workspace.outline_path,
    )
    if any(path.exists() for path in ledger_paths):
        return True
    return any(workspace.chapters_dir.glob("*.md"))


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


def _upsert_render_call(arguments: Mapping[str, JSONValue]) -> str | None:
    entity_type = arguments.get("entity_type")
    spec = _REGISTRY.get(entity_type) if isinstance(entity_type, str) else None
    label = spec.label if spec else "实体"
    data = arguments.get("data")
    if not isinstance(data, dict):
        return f"写入{label}"
    name = data.get("name")
    prefix = f"写入{label} {name}" if isinstance(name, str) else f"写入{label}"
    status = data.get("status") if isinstance(data.get("status"), str) else data.get("arc")
    return f"{prefix} → {status}" if isinstance(status, str) and status else prefix


def _query_render_call(arguments: Mapping[str, JSONValue]) -> str | None:
    entity_type = arguments.get("entity_type")
    spec = _REGISTRY.get(entity_type) if isinstance(entity_type, str) else None
    label = spec.label if spec else "实体"
    name = arguments.get("name")
    if isinstance(name, str) and name:
        return f"查询{label} {name}"
    return f"列出全部{label}"


def create_bible_tools(workspace: StoryWorkspace) -> list[AgentTool]:
    """创建绑定 workspace 的 bible_upsert / bible_query 工具。"""

    async def upsert_execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        reject_intake_write(workspace)  # L2 数据面写门：intake 阶段拒绝建账
        entity_type, spec = _require_spec(arguments)
        data = arguments.get("data")
        if not isinstance(data, dict):
            raise ToolInputError(f"data 必须是{spec.label}字段对象（至少包含 name）")
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ToolInputError(
                "data.name 必须是非空字符串。"
                f"为什么错：{spec.label}以 name 为主键，缺失或非法会导致无法落盘/覆盖歧义。"
                f"怎样算对：{_NAME_EXAMPLE}。"
            )
        if spec.cross_check is not None:
            spec.cross_check(workspace, data)

        entities = list(spec.read_all(workspace))
        existing = next((e for e in entities if getattr(e, "name", None) == name), None)
        if entity_type == "method" and isinstance(existing, Method) and existing.status == "locked":
            _reject_locked_method_edit(name, existing, data)

        new_status: str | None = None
        if spec.status_field is not None:
            field = spec.status_field
            raw_new = data.get(field)
            if raw_new is not None and not isinstance(raw_new, str):
                legal = "、".join(sorted(TRANSITIONS[entity_type]))
                raise ToolInputError(
                    f"data.{field} 必须是字符串，收到 {type(raw_new).__name__}。"
                    f"{spec.label} 的合法状态：{legal}。"
                )
            if existing is None:
                new_status = raw_new if raw_new is not None else INITIAL_STATUS[entity_type]
                try:
                    validate_initial(entity_type, new_status)
                except InvalidTransitionError as exc:
                    raise ToolInputError(str(exc)) from exc
            else:
                old_status = str(getattr(existing, field))
                new_status = raw_new if raw_new is not None else old_status
                try:
                    validate_transition(entity_type, old_status, new_status)
                except InvalidTransitionError as exc:
                    raise ToolInputError(str(exc)) from exc
            data = {**data, field: new_status}

        try:
            model = spec.model_cls.model_validate(data)
        except ValidationError as exc:
            err_details = "; ".join(
                f"{'.'.join(str(loc) for loc in error['loc'])}: {error['msg']}"
                for error in exc.errors(include_url=False)
            )
            raise ToolInputError(
                f"{spec.label}数据不合法：{err_details}。{_validation_example_note(entity_type)}"
            ) from exc
        if isinstance(model, Method):
            # history 是 lock/unlock 工具的专属账目（T4）：upsert 覆盖写时
            # 原样保留既有审计链，模型数据里的 history 键一律不采纳。
            model.history = list(existing.history) if isinstance(existing, Method) else []

        if existing is not None:
            entities[entities.index(existing)] = model
        else:
            entities.append(model)
        try:
            spec.write_all(workspace, entities)
        except InvalidEntityNameError as exc:
            raise ToolInputError(str(exc)) from exc

        note = "" if new_status is None else f"（{spec.status_field}={new_status}）"
        details: dict[str, JSONValue] = {"entity_type": entity_type, "name": name}
        if new_status is not None and spec.status_field is not None:
            details[spec.status_field] = new_status
        return AgentToolResult(
            content=[TextContent(text=f"{spec.label}「{name}」已写入 Story Bible{note}")],
            details=details,
        )

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
                                f"{spec.label}「{name}」不存在。可用 bible_upsert 创建，"
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

    upsert = AgentTool(
        name="bible_upsert",
        label="写入 Bible 实体",
        description=(
            "把一条实体写入 Story Bible（YAML 落盘，同名覆盖），"
            "支持 character/clue/foreshadow/method/timeline/outline 六类账本。"
            "正文引用任何设定前，必须先用本工具落盘；状态只能沿合法迁移边推进。"
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
                "data": {
                    "type": "object",
                    "description": (
                        "实体字段对象，name 必填（六类实体都要求非空 name，包括 timeline）。"
                        "类型形状——character：identity/motivation/secret/notes 为字符串，"
                        'relationships 为字符串列表（每项如"名字: 关系"）；'
                        "clue：content/chapter/points_to 为字符串，"
                        "is_red_herring/is_genuine 为布尔；"
                        "foreshadow：content/plant_chapter/expected_resolve_chapter 为字符串，"
                        "plant_mode(explicit|hint|symbolic)；"
                        "method：description/time_window 为字符串，requirements 为字符串列表，"
                        "alibi_claims 为对象列表[{person,day,t_start,t_end,claim}]；"
                        "timeline：day 为整数(第几天)，t_start/t_end/chapter/note 为字符串，"
                        "participants 为已建档角色名的字符串列表；"
                        "outline：chapter 为整数(章号,工具层校验唯一)，act/end_hook 为字符串，"
                        "beats/plant_plans 为字符串列表。"
                        "状态字段：character arc(flat|developed|resolved)；"
                        "clue/foreshadow/outline status(初始 planned)；"
                        "method status(初始 drafted)。"
                        '示例：outline 的 name 取 chNN 形态（如 "ch01"）、chapter 才是整数章号；'
                        "method 的 alibi_claims 为对象列表，day 是整数、t_start/t_end 是 "
                        '"HH:MM"，requirements 是字符串列表。'
                    ),
                },
            },
            "required": ["entity_type", "data"],
            "additionalProperties": False,
        },
        execute_fn=upsert_execute,
        prompt_guidelines=(
            "任何设定必须先经 bible_upsert 落盘再写进正文；同名再次调用视为更新。",
            "状态不可凭空跳级：新建即初始状态（角色 arc=flat；clue/foreshadow/outline "
            "status=planned；method status=drafted），被拒时按错误消息给出的合法去向修正。",
            'bible_upsert 最小正确示例：{"entity_type": "character", "data": '
            '{"name": "宫泽", "identity": "退休刑警", "relationships": '
            '["宫泽惠: 妹妹"]}}——relationships/requirements/participants/beats 都是'
            "字符串列表，不要传成单个字符串；每条实体（含 timeline）都必须带非空 name。",
            "timeline 的 participants 必须是已建档角色：先建角色，再登记事件。",
            "outline 章号唯一且章号≥1：每章一条章纲（name 可用本章标题），"
            "beats 列本章节拍、plant_plans 列本章拟埋伏笔名（须与伏笔账本对齐）、"
            "end_hook 列章末悬念；写作前先 query 本章章纲。",
        ),
        render_call=_upsert_render_call,
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
                    "description": "实体类型，与 bible_upsert 相同的六种",
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
    return [upsert, query]


__all__ = ["ENTITY_LABELS", "create_bible_tools", "entity_summary", "reject_intake_write"]
