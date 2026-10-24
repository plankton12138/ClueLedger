"""七种预设模块类型（0.8 B）。

类型是应用支持的固定能力，实例才是作者的内容：同一类型可以创建多份人物、世界或故事线，
两份同名人物不会自动合并。这里只放类型表与可筛选字段，不做存储，也不认识任何作品。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from hastings.workspace.errors import WorkspaceError

__all__ = [
    "PRESETS",
    "ModuleKind",
    "ModuleSpec",
    "is_kind",
    "preset_view",
    "presets_view",
    "spec",
]

ModuleKind = Literal["characters", "world", "clues", "truth", "timeline", "plot", "reference"]


@dataclass(frozen=True, slots=True)
class ModuleSpec:
    """一种预设类型：允许的条目种类、在作品内可用的整理动作与头部字段。

    ``organize_domain`` 是作品内整理动作的名字，结果留在作品，不写进库；``None`` 表示这类
    资料没有对应的整理任务，只能由作者直接编辑。
    """

    kind: ModuleKind
    label: str
    source_kinds: tuple[str, ...]
    organize_domain: str | None
    #: 条目头部允许出现的字段名；长说明写正文，不进头部。
    allowed_fields: tuple[str, ...]


PRESETS: tuple[ModuleSpec, ...] = (
    ModuleSpec(
        kind="characters",
        label="人物",
        source_kinds=("character",),
        organize_domain="characters",
        allowed_fields=("roles", "surface", "revealed", "planned"),
    ),
    ModuleSpec(
        kind="world",
        label="世界设定",
        source_kinds=("world_rule", "place", "organization"),
        organize_domain=None,
        allowed_fields=("scope", "region", "parent"),
    ),
    ModuleSpec(
        kind="clues",
        label="线索与伏笔",
        source_kinds=("clue_plan",),
        organize_domain="clues",
        allowed_fields=("planned_plant_chapter", "planned_resolve_chapter", "decision", "supports"),
    ),
    ModuleSpec(
        kind="truth",
        label="真相与事件经过",
        source_kinds=("truth", "author_event"),
        organize_domain=None,
        allowed_fields=("basis", "planned_chapter", "story_time"),
    ),
    ModuleSpec(
        kind="timeline",
        label="时间线",
        source_kinds=("author_event",),
        organize_domain="story_index",
        allowed_fields=("story_time", "planned_chapter"),
    ),
    ModuleSpec(
        kind="plot",
        label="剧情安排",
        source_kinds=("plot_plan", "author_event"),
        organize_domain=None,
        allowed_fields=("planned_chapter", "decision", "story_time"),
    ),
    ModuleSpec(
        kind="reference",
        label="自由资料",
        source_kinds=("reference",),
        organize_domain="glossary",
        allowed_fields=("tags", "source"),
    ),
)

_BY_KIND: dict[str, ModuleSpec] = {item.kind: item for item in PRESETS}


def is_kind(value: object) -> bool:
    return isinstance(value, str) and value in _BY_KIND


def spec(kind: str) -> ModuleSpec:
    """取类型定义；未知类型直接报错，不按最接近的类型猜。"""
    found = _BY_KIND.get(kind)
    if found is None:
        raise WorkspaceError(
            code="MODULE_KIND_UNKNOWN",
            where="library.presets",
            why=f"没有这种模块类型：{kind or '（空）'}",
            fix="用预设类型之一：characters、world、clues、truth、timeline、plot、reference",
            details={"kind": kind, "available": list(_BY_KIND)},
        )
    return found


def preset_view(item: ModuleSpec) -> dict[str, object]:
    return {
        "kind": item.kind,
        "label": item.label,
        "source_kinds": list(item.source_kinds),
        "organize_domain": item.organize_domain,
    }


def presets_view() -> list[dict[str, object]]:
    return [preset_view(item) for item in PRESETS]
