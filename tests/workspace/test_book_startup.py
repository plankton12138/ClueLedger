"""v1.1 Q1 M1：对话起步——设定卡候选检查、一次性确认门槛与起步结构草稿检查。"""

from __future__ import annotations

from pathlib import Path

import pytest

from hastings.workspace.book_startup import (
    CARD_TEXT_LIMIT,
    STARTUP_QUESTION_LIMIT,
    card_report,
    check_first_structure,
    confirm_startup_book_intent,
    guided_reply_report,
    structure_draft_prompt,
)
from hastings.workspace.chapter_write_policy import ChapterWritePolicy
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.story_store import MAIN_VARIANT_ID, StoryStore

CARD = "暴雪封山的旅馆发生命案，湿手套先让林砚怀疑管家；真相未定，悬念在于谁换了手套。"


@pytest.fixture
def store(tmp_path: Path) -> StoryStore:
    workspace = Workspace.create(tmp_path / "书")
    store = StoryStore(tmp_path / "state", workspace, format_version=4)
    workspace.chapter_policy = ChapterWritePolicy(store)
    return store


def test_card_report_flags_empty_and_oversize() -> None:
    assert card_report("   ") == ["设定卡为空"]
    assert card_report("x" * (CARD_TEXT_LIMIT + 1)) == [
        f"设定卡超过 {CARD_TEXT_LIMIT} 字符（当前 {CARD_TEXT_LIMIT + 1}）"
    ]
    assert card_report(CARD) == []


def test_confirm_startup_writes_intent_once(store: StoryStore) -> None:
    result = confirm_startup_book_intent(store, text=CARD, request_key="startup:1")
    variant = store.require_variant(MAIN_VARIANT_ID)
    assert variant.book_intent is not None
    assert "湿手套" in variant.book_intent.text
    assert result.plan_revision == variant.revision
    assert result.change_id


def test_confirm_startup_rejects_repeat(store: StoryStore) -> None:
    confirm_startup_book_intent(store, text=CARD, request_key="startup:1")
    with pytest.raises(WorkspaceError) as exc:
        confirm_startup_book_intent(store, text="另一句", request_key="startup:2")
    assert exc.value.code == "BOOK_INTENT_CONFIRMED_CONFLICT"
    # 拒绝不落盘：意图仍是第一次确认的文本。
    variant = store.require_variant(MAIN_VARIANT_ID)
    assert variant.book_intent is not None and "湿手套" in variant.book_intent.text


def test_confirm_startup_rejects_invalid_card(store: StoryStore) -> None:
    with pytest.raises(WorkspaceError) as exc:
        confirm_startup_book_intent(store, text="  ", request_key="startup:1")
    assert exc.value.code == "BOOK_INTENT_INVALID"
    assert store.read().variant(MAIN_VARIANT_ID) is None


def test_confirm_startup_undo_then_reconfirm(store: StoryStore) -> None:
    """确认可整体撤销；撤销后可以重新起步。"""
    result = confirm_startup_book_intent(store, text=CARD, request_key="startup:1")
    store.undo(change_id=result.change_id, request_key="undo:1")
    assert store.read().variant(MAIN_VARIANT_ID).book_intent is None
    again = confirm_startup_book_intent(store, text="重来的意图", request_key="startup:2")
    assert again.change_id != result.change_id
    assert store.require_variant(MAIN_VARIANT_ID).book_intent is not None


def test_confirm_startup_rejects_old_format() -> None:
    """旧格式作品不做起步确认（本版不迁移旧数据）。"""
    from types import SimpleNamespace

    old_variant = SimpleNamespace(format_version=2, book_intent=None)
    old_store = SimpleNamespace(read=lambda: SimpleNamespace(variant=lambda vid: old_variant))
    with pytest.raises(WorkspaceError) as exc:
        confirm_startup_book_intent(old_store, text=CARD, request_key="startup:1")  # type: ignore[arg-type]
    assert exc.value.code == "STORY_FORMAT_UNSUPPORTED"


def test_guided_reply_report_counts_questions() -> None:
    one = guided_reply_report("故事发生在哪里？")
    assert one["questions"] == 1 and one["single_question"] and one["within_limit"]
    many = guided_reply_report("在哪里？谁死了？几点？")
    assert many["questions"] == 3
    assert not many["single_question"] and many["note"]
    within = guided_reply_report("？" * STARTUP_QUESTION_LIMIT)
    assert within["within_limit"]
    over = guided_reply_report("？" * (STARTUP_QUESTION_LIMIT + 1))
    assert not over["within_limit"]
    assert guided_reply_report("好的，就这样开始。")["questions"] == 0


def test_structure_draft_prompt_states_contract() -> None:
    system, prompt = structure_draft_prompt(CARD, ("伏笔账", "人物"))
    assert "## 铁律" in prompt
    assert "1600" in prompt
    assert "===MODULES===" in prompt
    assert "伏笔账" in prompt
    assert CARD in prompt
    assert "保持未知" in system


def test_check_first_structure_accepts_wellformed() -> None:
    text = (
        "## 铁律\n第三人称限制视角。\n凶手不在未出场人物中。\n\n"
        "## 路线\n暴雪之夜逐步收束。\n\n## 骨架\n1. 抵达旅馆。\n"
    )
    report = check_first_structure(text)
    assert report.ok
    assert report.iron_rules.rules == ("第三人称限制视角。", "凶手不在未出场人物中。")


def test_check_first_structure_flags_missing_marker() -> None:
    report = check_first_structure("# 我的作品\n## 铁律\n规则\n")
    assert not report.iron_rules.ok
    assert not report.ok


def test_check_first_structure_flags_over_budget() -> None:
    text = "## 铁律\n规则\n" + "x" * 1700
    report = check_first_structure(text)
    assert report.iron_rules.ok
    assert not report.budget.ok
    assert not report.ok
