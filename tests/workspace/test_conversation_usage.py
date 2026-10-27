"""0.9 U0：会话用量契约样例。

这些断言冻结的是「事件里已有的 usage 如何换算成统计口径」，不是供应商原始字段。
四类协议（openai-completions / openai-responses / anthropic-messages /
google-generative-ai）在适配层已经各自归一化过一次，这里只处理两件协议之间
仍然不一致的事：缓存属于输入子项，推理已经算在输出里。

样例取自各适配器的解析结果，不凭空构造供应商 JSON：google 协议目前不解析用量，
所以它的样例就是「没有 usage」，而不是一组零。
"""

from __future__ import annotations

import pytest

from hastings.workspace.conversation_usage import (
    ReportedUsage,
    UsageTotals,
    reported_usage,
    summarize_events,
)


def test_openai_completions_full_usage_merges_cache_into_input() -> None:
    """OpenAI 的 input 是扣掉缓存后的新鲜输入，统计口径要求还原成完整输入。"""
    usage = reported_usage(
        {
            "input": 1000,
            "output": 200,
            "cache_read": 3000,
            "cache_write": 0,
            "cache_write_1h": None,
            "reasoning": None,
            "total_tokens": 4200,
        }
    )

    assert usage == ReportedUsage(
        total_tokens=4200,
        input_tokens=4000,
        output_tokens=200,
        cache_read_tokens=3000,
        cache_write_tokens=0,
    )


def test_reasoning_is_not_added_to_output_again() -> None:
    """两种协议都把推理计入输出；再叠加一次会虚增累计消耗。"""
    usage = reported_usage(
        {
            "input": 100,
            "output": 900,
            "cache_read": 0,
            "cache_write": 0,
            "reasoning": 600,
            "total_tokens": 1000,
        }
    )

    assert usage is not None
    assert usage.output_tokens == 900
    assert usage.total_tokens == 1000


def test_anthropic_reports_fresh_input_and_cache_write_separately() -> None:
    """Anthropic 的 input 本身不含缓存，缓存写入单列，仍要合并成完整输入。"""
    usage = reported_usage(
        {
            "input": 500,
            "output": 100,
            "cache_read": 200,
            "cache_write": 300,
            "cache_write_1h": 300,
            "reasoning": None,
            "total_tokens": 1100,
        }
    )

    assert usage == ReportedUsage(
        total_tokens=1100,
        input_tokens=1000,
        output_tokens=100,
        cache_read_tokens=200,
        cache_write_tokens=300,
    )


@pytest.mark.parametrize("raw", [None, {}, "usage"])
def test_missing_usage_is_unknown_not_zero(raw: object) -> None:
    """没有用量与「用量为零」是两件事：前者不能累加成 0。"""
    assert reported_usage(raw) is None  # type: ignore[arg-type]


def test_all_zero_usage_counts_as_not_reported() -> None:
    """真实请求必然有输入；全零只能说明适配层没解析到字段。"""
    assert reported_usage({"input": 0, "output": 0, "total_tokens": 0}) is None


def test_partial_usage_keeps_known_fields_and_leaves_rest_unknown() -> None:
    """只报了输出时，输入仍是未知，总量按已知部分重算而不是留 0。"""
    usage = reported_usage({"output": 50})

    assert usage == ReportedUsage(
        total_tokens=50,
        input_tokens=None,
        output_tokens=50,
        cache_read_tokens=None,
        cache_write_tokens=None,
    )


def test_stale_total_is_recomputed_from_known_parts() -> None:
    """适配层只填了 input/output 时 total 仍是默认 0，不能当成真实总量。"""
    usage = reported_usage({"input": 120, "output": 30, "total_tokens": 0})

    assert usage is not None
    assert usage.total_tokens == 150


@pytest.mark.parametrize("value", ["120", True, 12.5])
def test_non_integer_values_are_treated_as_missing(value: object) -> None:
    """供应商偶尔把数字写成字符串；不做静默转换，按未上报处理。"""
    usage = reported_usage({"input": value, "output": 10})

    assert usage is not None
    assert usage.input_tokens is None
    assert usage.output_tokens == 10


def test_totals_view_keeps_unknown_separate_from_zero() -> None:
    """没有请求的会话是零，有用量缺失的会话是未知——视图必须能区分这两种。"""
    idle = UsageTotals(
        total_tokens=None,
        input_tokens=None,
        output_tokens=None,
        cache_read_tokens=None,
        cache_write_tokens=None,
        finished_requests=0,
        requests_without_usage=0,
        requests_in_progress=0,
        model_duration_ms=None,
    )
    missing = UsageTotals(
        total_tokens=None,
        input_tokens=None,
        output_tokens=None,
        cache_read_tokens=None,
        cache_write_tokens=None,
        finished_requests=2,
        requests_without_usage=2,
        requests_in_progress=0,
        model_duration_ms=1800,
    )

    assert idle.finished_requests == 0 and idle.total_tokens is None
    assert missing.finished_requests == 2 and missing.total_tokens is None


def test_totals_dict_matches_the_agreed_wire_fields() -> None:
    """接口字段名在此冻结；前端同名接口与它逐项对应，改名要同时改两侧。"""
    totals = UsageTotals(
        total_tokens=4200,
        input_tokens=4000,
        output_tokens=200,
        cache_read_tokens=3000,
        cache_write_tokens=0,
        finished_requests=3,
        requests_without_usage=1,
        requests_in_progress=1,
        model_duration_ms=5300,
    )

    assert totals.as_dict() == {
        "total_tokens": 4200,
        "input_tokens": 4000,
        "output_tokens": 200,
        "cache_read_tokens": 3000,
        "cache_write_tokens": 0,
        "finished_requests": 3,
        "requests_without_usage": 1,
        "requests_in_progress": 1,
        "model_duration_ms": 5300,
    }


# -- 累计统计 ---------------------------------------------------------------


def _started(seq: int, operation: str, *, turn: str = "t1") -> dict[str, object]:
    return {
        "type": "model_request_started",
        "seq": seq,
        "turn_id": turn,
        "operation_id": operation,
        "stage": "write",
        "model": "deepseek-chat",
    }


def _finished(
    seq: int,
    operation: str,
    *,
    usage: object = None,
    duration: int | None = 1200,
    turn: str = "t1",
) -> dict[str, object]:
    return {
        "type": "model_request_finished",
        "seq": seq,
        "turn_id": turn,
        "operation_id": operation,
        "stage": "write",
        "status": "completed",
        "usage": usage,
        "duration_ms": duration,
    }


def test_two_requests_add_up() -> None:
    summary = summarize_events(
        [
            _started(1, "op1"),
            _finished(2, "op1", usage={"input": 100, "output": 20, "total_tokens": 120}),
            _started(3, "op2"),
            _finished(4, "op2", usage={"input": 200, "output": 30, "total_tokens": 230}),
        ]
    )

    assert summary.totals.finished_requests == 2
    assert summary.totals.total_tokens == 350
    assert summary.totals.input_tokens == 300
    assert summary.totals.output_tokens == 50
    assert summary.totals.requests_without_usage == 0
    assert summary.totals.model_duration_ms == 2400
    assert summary.through_seq == 4


def test_repeated_terminals_for_the_same_request_count_once() -> None:
    """断线重连会重放终态；同一请求不能因为推送两次就翻倍。"""
    usage = {"input": 100, "output": 20, "total_tokens": 120}
    summary = summarize_events(
        [
            _started(1, "op1"),
            _finished(2, "op1", usage=usage),
            _finished(3, "op1", usage=usage),
        ]
    )

    assert summary.totals.finished_requests == 1
    assert summary.totals.total_tokens == 120
    assert summary.diagnostics == ()


def test_conflicting_terminals_keep_the_first_and_report_it() -> None:
    """两个不同终态只能留一个；另一条作为诊断，不能静默改写。"""
    summary = summarize_events(
        [
            _started(1, "op1"),
            _finished(2, "op1", usage={"input": 100, "output": 20, "total_tokens": 120}),
            _finished(3, "op1", usage={"input": 999, "output": 99, "total_tokens": 1098}),
        ]
    )

    assert summary.totals.total_tokens == 120
    assert "op1" in " ".join(summary.diagnostics)


def test_started_without_terminal_is_counted_as_in_progress() -> None:
    summary = summarize_events([_started(1, "op1")])

    assert summary.totals.requests_in_progress == 1
    assert summary.totals.finished_requests == 0
    assert summary.totals.total_tokens is None


def test_requests_without_usage_are_marked_not_zeroed() -> None:
    summary = summarize_events(
        [_started(1, "op1"), _finished(2, "op1", usage=None)]
    )

    assert summary.totals.finished_requests == 1
    assert summary.totals.requests_without_usage == 1
    assert summary.totals.total_tokens is None


def test_mixed_usage_reports_known_total_and_marks_gaps() -> None:
    summary = summarize_events(
        [
            _started(1, "op1"),
            _finished(2, "op1", usage={"input": 100, "output": 20, "total_tokens": 120}),
            _started(3, "op2"),
            _finished(4, "op2", usage=None),
        ]
    )

    assert summary.totals.total_tokens == 120
    assert summary.totals.requests_without_usage == 1


def test_unknown_durations_leave_the_field_empty() -> None:
    summary = summarize_events(
        [
            _started(1, "op1"),
            _finished(2, "op1", usage={"output": 10}, duration=None),
        ]
    )

    assert summary.totals.model_duration_ms is None


def test_other_events_are_ignored() -> None:
    summary = summarize_events(
        [
            {"type": "turn_started", "seq": 1, "text": "写一章"},
            _started(2, "op1"),
            _finished(3, "op1", usage={"output": 10}),
            {"type": "assistant_message", "seq": 4, "text": "好"},
        ]
    )

    assert summary.totals.finished_requests == 1
    assert summary.totals.total_tokens == 10
    assert summary.through_seq == 4


def test_malformed_usage_is_treated_as_unreported() -> None:
    summary = summarize_events(
        [_started(1, "op1"), _finished(2, "op1", usage="not-a-mapping")]
    )

    assert summary.totals.requests_without_usage == 1


def test_empty_conversation_has_zero_requests() -> None:
    summary = summarize_events([])

    assert summary.totals.finished_requests == 0
    assert summary.totals.total_tokens is None
    assert summary.through_seq == 0
