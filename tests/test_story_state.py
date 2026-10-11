"""工作区生命周期状态机测试（P8 T1，计划 D1/D2）：合法边全过、非法边三要素、
推进前置条件（确认门：角色 ≥2 / 手法全部 locked / 章纲 ≥1）、回退边独立表。
"""

import pytest
from pydantic import ValidationError

from hastings.bible.story_state import (
    ADVANCE_EDGES,
    ROLLBACK_EDGES,
    STAGES,
    InvalidStageTransitionError,
    StagePrereqs,
    intake_write_rejection,
    validate_transition,
)

LEGAL_ADVANCES: list[tuple[str, str]] = [
    ("intake", "bible_drafting"),
    ("bible_drafting", "bible_confirmed"),
    ("bible_confirmed", "writing"),
    ("writing", "revising"),
    ("writing", "complete"),
    ("revising", "complete"),
]

LEGAL_ROLLBACKS: list[tuple[str, str]] = [
    ("bible_confirmed", "bible_drafting"),  # 作者要求推翻设定
    ("revising", "writing"),
    ("complete", "revising"),  # 终稿后作者驱动维护
]

SATISFIED = StagePrereqs(characters=2, methods=1, methods_locked=1, outlines=1)


def test_stage_table_covers_all_stages() -> None:
    """迁移表必须覆盖全部六个阶段（含 complete 的空出边表）。"""
    assert set(STAGES) == set(ADVANCE_EDGES)
    assert set(ROLLBACK_EDGES) == {"bible_confirmed", "revising", "complete"}
    assert ADVANCE_EDGES["complete"] == []


@pytest.mark.parametrize(("stage", "target"), LEGAL_ADVANCES)
def test_legal_advance_passes(stage: str, target: str) -> None:
    validate_transition(stage, target, prereqs=SATISFIED)  # 不抛即通过


@pytest.mark.parametrize(("stage", "target"), LEGAL_ROLLBACKS)
def test_legal_rollback_passes(stage: str, target: str) -> None:
    validate_transition(stage, target, prereqs=SATISFIED)  # 不抛即通过


def test_same_stage_is_noop() -> None:
    validate_transition("writing", "writing", prereqs=StagePrereqs())


@pytest.mark.parametrize(
    ("stage", "target"),
    [
        ("intake", "writing"),  # 跳过起草与确认
        ("intake", "bible_confirmed"),  # 跳过起草
        ("bible_drafting", "writing"),  # 未确认禁写
        ("writing", "intake"),  # 生命周期不可倒转
        ("bible_drafting", "complete"),  # 未写作谈何终稿
        ("complete", "writing"),  # 回退只到相邻维护态
        ("intake", "complete"),  # 跨多级
    ],
)
def test_illegal_edge_rejected_with_three_elements(stage: str, target: str) -> None:
    with pytest.raises(InvalidStageTransitionError) as exc_info:
        validate_transition(stage, target, prereqs=SATISFIED)
    msg = str(exc_info.value)
    assert stage in msg and target in msg  # 哪里错
    assert "为什么错" in msg  # 为什么错
    assert "怎样算对" in msg  # 怎样算对


def test_unknown_stage_rejected() -> None:
    with pytest.raises(InvalidStageTransitionError) as exc_info:
        validate_transition("archived", "writing")
    assert "archived" in str(exc_info.value)


def test_confirmation_gate_missing_prereqs() -> None:
    """确认门：角色 <2、手法未全部锁定、章纲 <1 逐项点明（哪里错+怎样算对）。"""
    with pytest.raises(InvalidStageTransitionError) as exc_info:
        validate_transition("bible_drafting", "bible_confirmed", prereqs=StagePrereqs())
    msg = str(exc_info.value)
    assert "角色" in msg
    assert "手法" in msg and "锁定" in msg
    assert "章纲" in msg


def test_confirmation_gate_partial_lock_rejected() -> None:
    """手法 ≥1 但未全部锁定同样过不了确认门。"""
    prereqs = StagePrereqs(characters=5, methods=2, methods_locked=1, outlines=3)
    with pytest.raises(InvalidStageTransitionError):
        validate_transition("bible_drafting", "bible_confirmed", prereqs=prereqs)


def test_prereqs_only_gate_confirmation() -> None:
    """前置条件只拦 →bible_confirmed；其余推进不要求账本计数。"""
    empty = StagePrereqs()
    validate_transition("intake", "bible_drafting", prereqs=empty)
    validate_transition("bible_confirmed", "writing", prereqs=empty)
    validate_transition("writing", "complete", prereqs=empty)


def test_prereqs_model_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        StagePrereqs(characters=-1)


def test_rollback_ignores_prereqs() -> None:
    """回退是作者驱动，不做账本前置检查（空账本也可推翻设定重来）。"""
    validate_transition("bible_confirmed", "bible_drafting", prereqs=StagePrereqs())


# --- P10 T1（D1/L2）：intake 数据面写门纯判定 ---


def test_intake_write_rejection_blocks_intake() -> None:
    """intake 阶段返回三要素拒绝文案（含逃生门）。"""
    message = intake_write_rejection("intake")
    assert message is not None
    assert "intake" in message  # 哪里错
    assert "为什么错" in message
    assert "怎样算对" in message
    assert "story_stage" in message  # 逃生门：作者明确要求建账时先推进


@pytest.mark.parametrize(
    "stage",
    ["bible_drafting", "bible_confirmed", "writing", "revising", "complete"],
)
def test_intake_write_rejection_allows_other_stages(stage: str) -> None:
    assert intake_write_rejection(stage) is None
