"""v1.0.7 P1：界面与模型共用的选段解析。

这些用例只判断「同一段正文、同一个引文，界面和模型得到同一个区间」，不判断文学含义。
坐标是规范化 Markdown 源文本的 Unicode 码点半开区间，不是渲染后的可见文字位置。
"""

from __future__ import annotations

import hashlib

import pytest

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.story_bindings import (
    BindProseInput,
    Span,
    anchor_state,
    build_binding,
    locate_saved_span,
    resolve_selection,
    text_digest,
    unique_span,
)
from hastings.workspace.story_models import ProseBinding

TEXT = "钟停在23:00。\n她抬头看了一眼，又低下头。\n"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def code_of(call: object) -> str:
    with pytest.raises(WorkspaceError) as caught:
        call()  # type: ignore[operator]
    return caught.value.code


def test_unique_span_returns_code_point_range() -> None:
    span = unique_span(TEXT, "钟停在23:00。")
    assert span == Span(start=0, end=9, quote="钟停在23:00。")
    assert TEXT[span.start : span.end] == span.quote


def test_missing_quote_is_reported_not_guessed() -> None:
    assert code_of(lambda: unique_span(TEXT, "钟停在22:00。")) == "PROSE_ANCHOR_MISSING"


def test_empty_quote_is_rejected() -> None:
    assert code_of(lambda: unique_span(TEXT, "")) == "PROSE_ANCHOR_MISMATCH"


def test_overlapping_repeat_counts_as_ambiguous() -> None:
    # 「哈哈」在「哈哈哈」里出现两次，非重叠计数会误判为唯一。
    assert code_of(lambda: unique_span("哈哈哈", "哈哈")) == "PROSE_ANCHOR_AMBIGUOUS"


def test_emoji_and_crlf_use_code_points() -> None:
    text = "甲😀乙\r\n丙"
    span = unique_span(text, "😀乙")
    assert (span.start, span.end) == (1, 3)
    assert text[span.start : span.end] == "😀乙"


def test_selection_with_matching_digest_and_range_is_exact() -> None:
    span = resolve_selection(
        text=TEXT,
        actual_digest=digest(TEXT),
        expected_digest=digest(TEXT),
        quote="她抬头看了一眼",
        start=10,
        end=17,
    )
    assert TEXT[span.start : span.end] == "她抬头看了一眼"


def test_stale_chapter_rejects_write_before_any_lookup() -> None:
    assert (
        code_of(
            lambda: resolve_selection(
                text=TEXT,
                actual_digest=digest(TEXT),
                expected_digest=digest("旧内容"),
                quote="钟停在23:00。",
                start=None,
                end=None,
            )
        )
        == "PROSE_SOURCE_STALE"
    )


def test_range_that_does_not_match_quote_is_rejected() -> None:
    assert (
        code_of(
            lambda: resolve_selection(
                text=TEXT,
                actual_digest=digest(TEXT),
                expected_digest=digest(TEXT),
                quote="钟停在23:00。",
                start=1,
                end=10,
            )
        )
        == "PROSE_ANCHOR_MISMATCH"
    )


def test_half_range_is_rejected() -> None:
    assert (
        code_of(
            lambda: resolve_selection(
                text=TEXT,
                actual_digest=digest(TEXT),
                expected_digest=digest(TEXT),
                quote="钟停在23:00。",
                start=0,
                end=None,
            )
        )
        == "PROSE_ANCHOR_MISMATCH"
    )


def test_model_without_range_uses_unique_quote() -> None:
    span = resolve_selection(
        text=TEXT,
        actual_digest=digest(TEXT),
        expected_digest=digest(TEXT),
        quote="钟停在23:00。",
        start=None,
        end=None,
    )
    assert (span.start, span.end) == (0, 9)


def test_saved_span_keeps_range_when_digest_matches() -> None:
    saved = Span(start=0, end=9, quote="钟停在23:00。")
    span = locate_saved_span(
        text=TEXT, current_digest=digest(TEXT), saved_digest=digest(TEXT), saved=saved
    )
    assert span == saved


def test_saved_span_relocates_after_unrelated_edit() -> None:
    saved = Span(start=0, end=9, quote="钟停在23:00。")
    edited = "序章\n\n" + TEXT
    span = locate_saved_span(
        text=edited, current_digest=digest(edited), saved_digest=digest(TEXT), saved=saved
    )
    assert edited[span.start : span.end] == "钟停在23:00。"


def test_saved_span_reports_ambiguity_instead_of_reusing_old_position() -> None:
    saved = Span(start=0, end=9, quote="钟停在23:00。")
    edited = TEXT + "\n钟停在23:00。\n"
    assert (
        code_of(
            lambda: locate_saved_span(
                text=edited, current_digest=digest(edited), saved_digest=digest(TEXT), saved=saved
            )
        )
        == "PROSE_ANCHOR_AMBIGUOUS"
    )


def binding(node_id: str | None = "n1", clue_id: str | None = None) -> ProseBinding:
    return ProseBinding(
        id="b1",
        node_id=node_id,
        clue_id=clue_id,
        document_id="doc_1",
        path="正文/0001 雨夜.md",
        digest=digest(TEXT),
        span_digest=digest("钟停在23:00。"),
        start=0,
        end=9,
        quote="钟停在23:00。",
    )


def test_anchor_state_separates_exact_relocated_and_failed() -> None:
    saved = binding()
    exact = anchor_state(saved, TEXT, digest(TEXT))
    assert (exact.state, exact.span) == ("exact", Span(0, 9, "钟停在23:00。"))

    edited = "序章\n\n" + TEXT
    relocated = anchor_state(saved, edited, digest(edited))
    assert relocated.state == "relocated"
    assert relocated.span is not None
    assert edited[relocated.span.start : relocated.span.end] == saved.quote

    doubled = TEXT + "\n钟停在23:00。"
    assert anchor_state(saved, "空章。", digest("空章。")).state == "missing"
    assert anchor_state(saved, doubled, digest(doubled)).state == "ambiguous"


def test_binding_only_needs_a_clue_target() -> None:
    input_row = BindProseInput(
        clue_id="c1",
        path="正文/0005 停住的钟.md",
        quote="钟停在23:00。",
        start=0,
        end=9,
    )
    row = build_binding(
        "b-mark",
        input_row,
        text=TEXT,
        chapter_digest=digest(TEXT),
        document_id="doc_1",
        contract_digest=None,
        source="manual",
    )
    assert row.node_id is None
    assert row.clue_id == "c1"
    assert row.span_digest == digest("钟停在23:00。")
    assert row.contract_digest is None


def test_binding_requires_a_target() -> None:
    with pytest.raises(ValueError, match="正文关联缺少节点或线索目标"):
        ProseBinding(
            id="b1",
            document_id="doc_1",
            path="正文/0001 雨夜.md",
            digest=digest(TEXT),
            start=0,
            end=9,
            quote="钟停在23:00。",
        )


def test_build_binding_rejects_quote_that_is_not_at_the_given_range() -> None:
    input_row = BindProseInput(
        node_id="n1",
        path="正文/0001 雨夜.md",
        quote="钟停在23:00。",
        start=1,
        end=10,
    )
    with pytest.raises(WorkspaceError) as caught:
        build_binding(
            "b1",
            input_row,
            text=TEXT,
            chapter_digest=digest(TEXT),
            document_id="doc_1",
        )
    assert caught.value.code == "PROSE_ANCHOR_MISMATCH"


def test_text_digest_is_shared_with_binding_digests() -> None:
    assert text_digest("钟停在23:00。") == digest("钟停在23:00。")
