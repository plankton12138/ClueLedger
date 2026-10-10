"""账本状态机：迁移表、初始状态与校验（P2 T1，计划 D1；P8 T3 增 outline）。

约定：
- 未列出的迁移一律拒绝；timeline 无状态机（事件只增改，无生命周期）；
- 错误消息三要素——哪里错（old → new）、为什么错（无此边/无此状态）、
  怎样算对（合法去向或初始状态）。消息经工具层原样喂回模型，
  模型据此自我修正，而非陷入试错循环。
"""

from typing import Final

# 各账本新建时的初始状态（与 models.py 中各 status 字段默认值保持一致）。
INITIAL_STATUS: Final[dict[str, str]] = {
    "character": "flat",
    "clue": "planned",
    "foreshadow": "planned",
    "method": "drafted",
    "outline": "planned",
}

# 全部允许边（D1 迁移表）；空列表 = 终态。未列出的边一律拒绝。
TRANSITIONS: Final[dict[str, dict[str, list[str]]]] = {
    "character": {
        "flat": ["developing"],
        "developing": ["transformed"],
        "transformed": [],
    },
    "clue": {
        "planned": ["planted"],
        "planted": ["discovered"],
        "discovered": ["interpreted"],
        "interpreted": ["resolved"],
        "resolved": [],
    },
    "foreshadow": {
        "planned": ["planted"],
        "planted": ["echoed", "resolved"],
        "echoed": ["resolved"],
        "resolved": [],
    },
    "method": {
        "drafted": ["locked"],
        # locked → drafted 仅 method_unlock 工具可走（工具层保证）
        "locked": ["revealed", "drafted"],
        "revealed": [],
    },
    # outline 章纲（P8 T3，计划 D3）：written/revised 由 write_chapter 落盘
    # 自动推进（T4），作者手动改回 planned 不设边——正文是 SSOT。
    "outline": {
        "planned": ["written"],
        "written": ["revised"],
        "revised": [],
    },
}


class InvalidTransitionError(ValueError):
    """非法状态迁移或非法初始状态。

    agent loop 会把异常消息转为 is_error=True 的工具结果喂回模型
    （tau_agent/loop.py），文案必须保持三要素让模型可自我修正。
    """


def validate_transition(entity_type: str, old: str, new: str) -> None:
    """校验 old → new 是否为允许的迁移边；old == new 视为 no-op 放行。

    Raises:
        InvalidTransitionError: entity_type 无状态机、old 未知、或迁移边未登记。
    """
    table = TRANSITIONS.get(entity_type)
    if table is None:
        hint = (
            "timeline 无状态机，事件只增改，无需状态迁移"
            if entity_type == "timeline"
            else f"状态机只覆盖 {'、'.join(sorted(TRANSITIONS))}"
        )
        raise InvalidTransitionError(
            f"{entity_type} 状态迁移被拒：{old} → {new}。为什么错：{hint}。"
        )

    legal = table.get(old)
    if legal is None:
        raise InvalidTransitionError(
            f"{entity_type} 状态迁移被拒：{old} → {new}。"
            f"为什么错：{entity_type} 没有 {old!r} 这个状态；"
            f"新建实体必须从初始状态 {INITIAL_STATUS[entity_type]!r} 开始。"
        )

    if new == old or new in legal:
        return

    targets = "、".join(legal) if legal else "无（已是终态，不可再迁移）"
    raise InvalidTransitionError(
        f"{entity_type} 状态迁移被拒：{old} → {new}。"
        f"为什么错：迁移表没有这条边。"
        f"从 {old} 出发的合法去向：{targets}。"
    )


def validate_initial(entity_type: str, status: str) -> None:
    """新建实体校验：status 必须等于该账本初始状态（防直接造出终态绕过流程）。

    Raises:
        InvalidTransitionError: entity_type 无状态机，或 status 非初始状态。
    """
    initial = INITIAL_STATUS.get(entity_type)
    if initial is None:
        hint = (
            "timeline 无状态机，新建事件不涉及状态"
            if entity_type == "timeline"
            else f"状态机只覆盖 {'、'.join(sorted(TRANSITIONS))}"
        )
        raise InvalidTransitionError(
            f"{entity_type} 新建被拒：status={status!r}。为什么错：{hint}。"
        )
    if status != initial:
        raise InvalidTransitionError(
            f"{entity_type} 新建被拒：status={status!r}。"
            f"为什么错：新建实体必须从初始状态开始，不允许直接造出半途或终态状态。"
            f"怎样算对：先以 {initial!r} 新建，再通过 bible_upsert 逐步迁移状态。"
        )


__all__ = [
    "INITIAL_STATUS",
    "TRANSITIONS",
    "InvalidTransitionError",
    "validate_initial",
    "validate_transition",
]
