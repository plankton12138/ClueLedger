"""0.9 C1：跨轮压缩的记录与投影。

压缩记录是追加的事件，不改写原对话。投影时把被覆盖的轮次换成一条明确标识的摘要消息，
其余原样保留。这样做的理由是：原话永远是校验依据，摘要只是给下一轮看的简报。

本文件只覆盖记录读取与投影（无模型调用）。摘要生成与候选校验在后续用例里。
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from hastings.workspace.compaction import (
    CompactionRecord,
    active_compaction,
    build_candidate,
    parse_summary,
    plan_compaction,
    source_digest,
    validate_candidate,
)
from hastings.workspace.conversation_context import replay_public_messages
from hastings.workspace.errors import WorkspaceError
from tau_agent.messages import message_text


def _events() -> list[dict[str, Any]]:
    return [
        {"type": "turn_started", "seq": 1, "turn_id": "t1", "text": "写第一章，雨夜"},
        {"type": "assistant_message", "seq": 2, "turn_id": "t1", "text": "第一章写好了"},
        {"type": "run_finished", "seq": 3, "turn_id": "t1", "status": "completed"},
        {"type": "turn_started", "seq": 4, "turn_id": "t2", "text": "再写第二章"},
        {"type": "assistant_message", "seq": 5, "turn_id": "t2", "text": "第二章写好了"},
        {"type": "run_finished", "seq": 6, "turn_id": "t2", "status": "completed"},
        {"type": "turn_started", "seq": 7, "turn_id": "t3", "text": "改成第三人称"},
    ]


def _record(events: list[dict[str, Any]], **overrides: Any) -> CompactionRecord:
    covered = 6
    payload: dict[str, Any] = {
        "compaction_id": "cmp1",
        "kind": "session",
        "source_head_seq": 7,
        "covered_through_seq": covered,
        "first_kept_turn_id": "t3",
        "source_digest": source_digest(events, through_seq=covered),
        "previous_id": "",
        "summary": "作者写了第一、二章，决定继续推进。",
        "items": [
            {
                "item_id": "i1",
                "kind": "author_decision",
                "text": "第一章是雨夜",
                "source_event_ids": ["t1"],
                "quote": "写第一章，雨夜",
                "supersedes": [],
                "profiles": ["writer"],
                "as_of_chapter": None,
            }
        ],
        "model": "deepseek:deepseek-chat",
        "prompt_version": 1,
        "estimated_before": 9000,
        "estimated_after": 4000,
        "request_operation_id": "op-summary",
        "reason": "manual",
        "created_at": "2026-10-27T10:00:00+08:00",
    }
    payload.update(overrides)
    return CompactionRecord(**payload)


def _with_record(events: list[dict[str, Any]], record: CompactionRecord) -> list[dict[str, Any]]:
    return [*events, {"type": "context_compacted", "seq": 8, "record": record.as_dict()}]


# -- 记录读取 ---------------------------------------------------------------


def test_no_record_means_no_compaction() -> None:
    assert active_compaction(_events()) is None


def test_valid_record_is_loaded() -> None:
    events = _events()
    record = _record(events)

    loaded = active_compaction(_with_record(events, record))

    assert loaded is not None
    assert loaded.compaction_id == "cmp1"
    assert loaded.first_kept_turn_id == "t3"
    assert loaded.items[0].quote == "写第一章，雨夜"


def test_record_with_changed_source_is_ignored() -> None:
    """原事件被改写后，旧摘要不再代表它们，宁可不压缩。"""
    events = _events()
    record = _record(events)
    tampered = [dict(event) for event in events]
    tampered[1]["text"] = "第一章被改成了另一个版本"

    assert active_compaction(_with_record(tampered, record)) is None


def test_record_pointing_at_a_missing_turn_is_ignored() -> None:
    """保留点找不到时不能把整段历史丢掉。"""
    events = _events()
    record = _record(events, first_kept_turn_id="t9")

    assert active_compaction(_with_record(events, record)) is None


def test_record_without_items_is_ignored() -> None:
    events = _events()
    record = _record(events, items=[])

    assert active_compaction(_with_record(events, record)) is None


def test_record_covering_everything_is_ignored() -> None:
    """覆盖到最后就意味着没有近期对话，那不是压缩而是删除。"""
    events = _events()
    record = _record(events, first_kept_turn_id="t3", covered_through_seq=8)

    assert active_compaction(_with_record(events, record)) is None


def test_latest_valid_record_wins() -> None:
    events = _events()
    first = _record(events, covered_through_seq=3, first_kept_turn_id="t2")
    second = _record(
        events,
        compaction_id="cmp2",
        covered_through_seq=6,
        first_kept_turn_id="t3",
        previous_id="cmp1",
        source_digest=source_digest(events, through_seq=6),
    )
    with_records = [
        *events,
        {"type": "context_compacted", "seq": 8, "record": first.as_dict()},
        {"type": "context_compacted", "seq": 9, "record": second.as_dict()},
    ]

    loaded = active_compaction(with_records)

    assert loaded is not None
    assert loaded.compaction_id == "cmp2"


def test_malformed_event_does_not_break_reading() -> None:
    events = [*_events(), {"type": "context_compacted", "seq": 8, "record": {"bad": True}}]

    assert active_compaction(events) is None


# -- 投影 -------------------------------------------------------------------


def test_projection_puts_the_summary_before_the_kept_turns() -> None:
    events = _events()
    record = _record(events)

    messages = replay_public_messages(events, compaction=record)

    assert len(messages) == 2
    text = message_text(messages[0])
    assert text.startswith("（应用整理的较早对话摘要")
    assert "第一、二章" in text
    assert message_text(messages[1]) == "改成第三人称"


def test_projection_keeps_the_original_messages_when_no_record() -> None:
    messages = replay_public_messages(_events())

    assert len(messages) == 5
    assert message_text(messages[0]) == "写第一章，雨夜"


def test_projection_never_touches_the_events() -> None:
    events = _events()
    record = _record(events)
    snapshot = [dict(event) for event in events]

    replay_public_messages(events, compaction=record)

    assert events == snapshot


# -- 切点：只切完整轮次 -----------------------------------------------------


def _turns() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seq = 0
    for index in range(1, 5):
        seq += 1
        events.append(
            {"type": "turn_started", "seq": seq, "turn_id": f"t{index}", "text": "再写一段" * 40}
        )
        seq += 1
        events.append(
            {"type": "assistant_message", "seq": seq, "turn_id": f"t{index}", "text": "写好了" * 40}
        )
        seq += 1
        events.append({"type": "run_finished", "seq": seq, "turn_id": f"t{index}"})
    return events


def test_plan_keeps_at_least_the_latest_turn() -> None:
    """保留量再小也要留一轮：把最近一轮压掉，模型就接不上话。"""
    plan = plan_compaction(_turns(), keep_recent_tokens=1)

    assert plan is not None
    assert plan.first_kept_turn_id == "t4"
    assert plan.covered_through_seq == 9


def test_plan_keeps_more_turns_when_the_budget_is_larger() -> None:
    plan = plan_compaction(_turns(), keep_recent_tokens=100_000)

    assert plan is None  # 全留着就不需要整理


def test_plan_needs_more_than_one_turn() -> None:
    assert plan_compaction(_turns()[:3], keep_recent_tokens=1) is None


def test_unanswered_question_keeps_its_turn() -> None:
    """待回答的提问必须在近期窗口里，否则作者答了模型也不知道在答什么。"""
    events = [
        *_turns(),
        {"type": "turn_started", "seq": 10, "turn_id": "t5", "text": "这里要不要改成冬天"},
        {"type": "author_input_requested", "seq": 11, "turn_id": "t5", "question": "要改吗"},
        {"type": "assistant_message", "seq": 12, "turn_id": "t5", "text": "等你确认"},
        {"type": "turn_started", "seq": 13, "turn_id": "t6", "text": "继续"},
        {"type": "assistant_message", "seq": 14, "turn_id": "t6", "text": "好"},
    ]

    plan = plan_compaction(events, keep_recent_tokens=1)

    assert plan is not None
    kept = plan.first_kept_turn_id
    order = ["t1", "t2", "t3", "t4", "t5", "t6"]
    assert order.index(kept) <= order.index("t5")


def test_plan_reports_what_it_will_cover() -> None:
    plan = plan_compaction(_turns(), keep_recent_tokens=1)

    assert plan is not None
    assert plan.covered_turns == ("t1", "t2", "t3")
    assert plan.tokens_before > plan.tokens_after


# -- 摘要解析与校验 ---------------------------------------------------------


def _summary_json(**overrides: Any) -> str:
    payload: dict[str, Any] = {
        "summary": "作者在改雨夜这一章，决定保留第一人称。",
        "items": [
            {
                "kind": "author_decision",
                "text": "保留第一人称",
                "quote": "别改成第三人称",
                "sources": ["t1"],
            }
        ],
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def test_parse_summary_reads_json_items() -> None:
    parsed = parse_summary(_summary_json(), allowed_sources=frozenset({"t1", "t2"}))

    assert parsed is not None
    assert parsed.summary.startswith("作者在改雨夜")
    assert parsed.items[0].kind == "author_decision"
    assert parsed.items[0].source_event_ids == ("t1",)


def test_parse_summary_rejects_unknown_sources() -> None:
    """引用了不存在的轮次说明模型在编，整份摘要不能用。"""
    parsed = parse_summary(_summary_json(), allowed_sources=frozenset({"t2"}))

    assert parsed is None


def test_parse_summary_rejects_items_without_sources() -> None:
    text = _summary_json(
        items=[{"kind": "goal", "text": "写完第三章", "sources": []}]
    )

    assert parse_summary(text, allowed_sources=frozenset({"t1"})) is None


def test_parse_summary_tolerates_a_code_fence() -> None:
    parsed = parse_summary(
        f"```json\n{_summary_json()}\n```", allowed_sources=frozenset({"t1"})
    )

    assert parsed is not None


def test_parse_summary_rejects_non_json() -> None:
    assert parse_summary("好的，我整理了一下：……", allowed_sources=frozenset({"t1"})) is None


def _candidate(events: list[dict[str, Any]] | None = None):
    events = events or _turns()
    plan = plan_compaction(events, keep_recent_tokens=1)
    assert plan is not None
    parsed = parse_summary(_summary_json(), allowed_sources=frozenset({"t1", "t2", "t3"}))
    assert parsed is not None
    return build_candidate(
        plan=plan,
        parsed=parsed,
        model="deepseek:deepseek-chat",
        digest=source_digest(events, through_seq=plan.covered_through_seq),
    )


def test_valid_candidate_passes() -> None:
    candidate = _candidate()

    validate_candidate(
        candidate, allowed_sources=frozenset({"t1", "t2", "t3"}), input_limit=None
    )

    assert candidate.estimated_after < candidate.estimated_before


def test_summary_that_does_not_shrink_is_rejected() -> None:
    """整理完反而更长，就不能替换旧上下文。"""
    candidate = _candidate()
    grown = replace(candidate, estimated_after=candidate.estimated_before + 1)

    with pytest.raises(WorkspaceError) as caught:
        validate_candidate(
            grown, allowed_sources=frozenset({"t1", "t2", "t3"}), input_limit=None
        )

    assert caught.value.code == "COMPACTION_NOT_EFFECTIVE"


def test_candidate_over_the_input_limit_is_rejected() -> None:
    candidate = _candidate()

    with pytest.raises(WorkspaceError) as caught:
        validate_candidate(
            candidate, allowed_sources=frozenset({"t1", "t2", "t3"}), input_limit=100
        )

    assert caught.value.code == "CONTEXT_REQUIRED_INPUT_TOO_LARGE"


def test_question_turn_stays_while_earlier_turns_are_covered() -> None:
    """待答问题的轮次留在保留侧，它之前的普通轮次照常可以被覆盖。

    如果保护范围放大到整段，作者问一个问题就会让压缩永久失效。
    """
    events = [
        {"type": "turn_started", "seq": 1, "turn_id": "t1", "text": "先写第三章开头" * 40},
        {"type": "assistant_message", "seq": 2, "turn_id": "t1", "text": "写好了" * 40},
        {"type": "turn_started", "seq": 3, "turn_id": "t2", "text": "结局还没定" * 40},
        {
            "type": "author_input_requested",
            "seq": 4,
            "turn_id": "t2",
            "question": "结局用 A 版还是 B 版",
        },
        {"type": "assistant_message", "seq": 5, "turn_id": "t2", "text": "等你确认" * 40},
    ]
    for index in range(3, 7):
        events.extend(
            [
                {
                    "type": "turn_started",
                    "seq": index * 3,
                    "turn_id": f"t{index}",
                    "text": "再写" * 40,
                },
                {
                    "type": "assistant_message",
                    "seq": index * 3 + 1,
                    "turn_id": f"t{index}",
                    "text": "写好了" * 40,
                },
            ]
        )

    plan = plan_compaction(events, keep_recent_tokens=1)

    assert plan is not None
    assert plan.first_kept_turn_id == "t2"
    assert plan.covered_turns == ("t1",)
    assert plan.covered_through_seq < 3
