"""状态机测试：合法边全过、非法边三要素、新建非初始拒绝（P2 T1，D1 迁移表）。"""

import pytest

from hastings.bible.state_machine import (
    INITIAL_STATUS,
    TRANSITIONS,
    InvalidTransitionError,
    validate_initial,
    validate_transition,
)

LEGAL_EDGES: list[tuple[str, str, str]] = [
    # character 弧光：flat → developing → transformed
    ("character", "flat", "developing"),
    ("character", "developing", "transformed"),
    # clue 严格线性生命周期
    ("clue", "planned", "planted"),
    ("clue", "planted", "discovered"),
    ("clue", "discovered", "interpreted"),
    ("clue", "interpreted", "resolved"),
    # foreshadow：echo 可选，planted 可直达 resolved
    ("foreshadow", "planned", "planted"),
    ("foreshadow", "planted", "echoed"),
    ("foreshadow", "planted", "resolved"),
    ("foreshadow", "echoed", "resolved"),
    # method：locked → drafted 仅 method_unlock 可走（工具层保证，迁移表允许此边）
    ("method", "drafted", "locked"),
    ("method", "locked", "revealed"),
    ("method", "locked", "drafted"),
]


@pytest.mark.parametrize(("entity_type", "old", "new"), LEGAL_EDGES)
def test_legal_edge_passes(entity_type: str, old: str, new: str) -> None:
    validate_transition(entity_type, old, new)  # 不抛即通过


def test_same_status_is_noop() -> None:
    """D1：传入与旧值相同 → no-op 放行。"""
    validate_transition("clue", "planted", "planted")
    validate_transition("character", "flat", "flat")


@pytest.mark.parametrize(
    ("entity_type", "old", "new"),
    [
        ("character", "flat", "transformed"),  # 跳过 developing
        ("clue", "planned", "discovered"),  # 没写进正文谈不上被发现
        ("clue", "resolved", "planned"),  # 终态回退
        ("foreshadow", "planned", "echoed"),  # 未埋设先回声
        ("method", "drafted", "revealed"),  # 未锁定直接揭示
        ("method", "revealed", "drafted"),  # 已揭示不可回退
    ],
)
def test_illegal_edge_rejected_with_three_elements(entity_type: str, old: str, new: str) -> None:
    """错误消息三要素：哪里错（old → new）、为什么错、怎样算对（合法去向全列出）。"""
    with pytest.raises(InvalidTransitionError) as exc_info:
        validate_transition(entity_type, old, new)
    msg = str(exc_info.value)

    assert old in msg and new in msg  # 要素一：哪里错
    legal = TRANSITIONS[entity_type][old]
    for target in legal:  # 要素三：每个合法去向都在消息里
        assert target in msg, f"合法去向 {target} 未出现在错误消息中"
    assert "去向" in msg  # 合法去向段落的可读锚点


def test_terminal_state_error_says_no_legal_target() -> None:
    """从终态出发：消息必须明确说无合法去向，而非留空误导模型重试。"""
    with pytest.raises(InvalidTransitionError, match="终态"):
        validate_transition("clue", "resolved", "planted")


@pytest.mark.parametrize(
    ("entity_type", "bad_status"),
    [
        ("character", "transformed"),
        ("clue", "resolved"),
        ("foreshadow", "planted"),
        ("method", "locked"),
    ],
)
def test_new_entity_must_start_from_initial_status(entity_type: str, bad_status: str) -> None:
    """防模型直接造一个终态/半途状态绕过流程（D1）。"""
    with pytest.raises(InvalidTransitionError) as exc_info:
        validate_initial(entity_type, bad_status)
    msg = str(exc_info.value)

    assert bad_status in msg
    assert INITIAL_STATUS[entity_type] in msg  # 怎样算对：指出初始状态


@pytest.mark.parametrize("entity_type", sorted(INITIAL_STATUS))
def test_initial_status_accepted(entity_type: str) -> None:
    validate_initial(entity_type, INITIAL_STATUS[entity_type])


def test_timeline_has_no_state_machine() -> None:
    """timeline 事件只增改，无生命周期（D1）；请求其状态机须显式报错。"""
    with pytest.raises(InvalidTransitionError, match="timeline"):
        validate_transition("timeline", "x", "y")


def test_unknown_entity_type_rejected() -> None:
    with pytest.raises(InvalidTransitionError):
        validate_transition("plot", "a", "b")
    with pytest.raises(InvalidTransitionError):
        validate_initial("plot", "a")


def test_transition_table_consistent_with_initial_status() -> None:
    """每个账本的初始状态必须是迁移表中的已知节点。"""
    for entity_type, initial in INITIAL_STATUS.items():
        assert initial in TRANSITIONS[entity_type]
