"""T14.1 段落切分与文本 diff：确定性、可重建、无环境依赖。"""

from __future__ import annotations

from hastings.domain.manuscript import diff


def test_split_paragraphs_blank_line_separates() -> None:
    assert diff.split_paragraphs("a\n\nb") == ["a", "b"]


def test_split_paragraphs_keeps_internal_newline() -> None:
    # 段内单换行不切段：Markdown 段落内的软换行属于同一段
    assert diff.split_paragraphs("a\nb") == ["a\nb"]


def test_split_paragraphs_strips_trailing_ascii_space_but_keeps_ideographic() -> None:
    # 全角空格 U+3000 是 CJK 排版内容，必须保留
    assert diff.split_paragraphs("a  \n\n　　全角") == ["a", "　　全角"]


def test_split_paragraphs_empty_or_blank_only() -> None:
    assert diff.split_paragraphs("\n\n  \n") == []


def test_diff_paragraphs_marks_modified_and_added() -> None:
    hunks = diff.diff_paragraphs(("a", "b", "c"), ("a", "B", "c", "d"))
    kinds_seen = [h.kind for h in hunks]
    assert "modified" in kinds_seen
    assert "added" in kinds_seen
    modified = next(h for h in hunks if h.kind == "modified")
    assert modified.old_index == 1
    assert modified.new_index == 1
    assert modified.old_text == "b"
    assert modified.new_text == "B"


def test_diff_paragraphs_marks_removed() -> None:
    hunks = diff.diff_paragraphs(("a", "b", "c"), ("a", "c"))
    removed = next(h for h in hunks if h.kind == "removed")
    assert removed.old_index == 1
    assert removed.new_index is None
    assert removed.old_text == "b"


def test_diff_paragraphs_identical_is_empty() -> None:
    assert diff.diff_paragraphs(("a", "b"), ("a", "b")) == ()
