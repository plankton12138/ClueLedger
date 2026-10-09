"""段落级 diff 纯函数测试（P5 T2，计划 D3）：空行分段 + SequenceMatcher hunks。

钉住的规约：
- 空行（含纯空白行）分段；段内单换行保留，行尾空白去除；首尾空段丢弃；
- diff：1→1 替换 = modified；多段替换（拆分/合并/重排）= removed+added；
  added/removed 携带正确的新旧段号与整段文本；
- 标题校验：首行必须是 `# 第N章` 前缀，错误消息含三要素。
"""

import pytest

from hastings.manuscript.diff import (
    DiffHunk,
    InvalidChapterFormat,
    diff_chapters,
    split_paragraphs,
    validate_title_heading,
)

# --- split_paragraphs ---


def test_split_paragraphs_blank_line_separation() -> None:
    assert split_paragraphs("第一段。\n\n第二段。\n\n第三段。") == [
        "第一段。",
        "第二段。",
        "第三段。",
    ]


def test_split_paragraphs_multiple_blank_and_whitespace_lines() -> None:
    assert split_paragraphs("第一段。\n\n\n  \n\n第二段。") == ["第一段。", "第二段。"]


def test_split_paragraphs_keeps_internal_newline_strips_trailing_spaces() -> None:
    assert split_paragraphs("一行　\n两行。\n\n新段。") == ["一行　\n两行。", "新段。"]


def test_split_paragraphs_discards_leading_and_trailing_blank_blocks() -> None:
    assert split_paragraphs("\n\n首段。\n\n尾段。\n\n") == ["首段。", "尾段。"]


def test_split_paragraphs_all_blank_returns_empty() -> None:
    assert split_paragraphs("\n  \n\n") == []


# --- diff_chapters：基础形状 ---


def test_diff_identical_text_returns_no_hunks() -> None:
    text = "# 第1章\n\n第一段。\n\n第二段。"
    assert diff_chapters(text, text) == []


def test_diff_both_empty_returns_no_hunks() -> None:
    assert diff_chapters("", "") == []


def test_diff_empty_to_content_marks_all_added() -> None:
    hunks = diff_chapters("", "# 第1章\n\n第一段。")

    assert hunks == [
        DiffHunk(
            kind="added",
            old_index=None,
            new_index=0,
            old_text="",
            new_text="# 第1章",
        ),
        DiffHunk(
            kind="added",
            old_index=None,
            new_index=1,
            old_text="",
            new_text="第一段。",
        ),
    ]


def test_diff_added_paragraph() -> None:
    old = "# 第1章\n\n第一段。"
    new = "# 第1章\n\n第一段。\n\n第二段。"

    hunks = diff_chapters(old, new)

    assert len(hunks) == 1
    hunk = hunks[0]
    assert hunk.kind == "added"
    assert hunk.old_index is None
    assert hunk.new_index == 2
    assert hunk.old_text == ""
    assert hunk.new_text == "第二段。"


def test_diff_removed_paragraph() -> None:
    old = "# 第1章\n\n第一段。\n\n第二段。"
    new = "# 第1章\n\n第一段。"

    hunks = diff_chapters(old, new)

    assert len(hunks) == 1
    hunk = hunks[0]
    assert hunk.kind == "removed"
    assert hunk.old_index == 2
    assert hunk.new_index is None
    assert hunk.old_text == "第二段。"
    assert hunk.new_text == ""


def test_diff_single_paragraph_modified() -> None:
    old = "# 第1章\n\n火车在九点四十七分抵达。"
    new = "# 第1章\n\n火车在十点零七分抵达。"

    hunks = diff_chapters(old, new)

    assert len(hunks) == 1
    hunk = hunks[0]
    assert hunk.kind == "modified"
    assert hunk.old_index == 1
    assert hunk.new_index == 1
    assert hunk.old_text == "火车在九点四十七分抵达。"
    assert hunk.new_text == "火车在十点零七分抵达。"


# --- 计划点名的五个边界用例 ---


def test_diff_empty_chapter_then_body() -> None:
    """空章节 → 有正文：全部 added（空章节的合法空结果一侧）。"""
    assert [h.kind for h in diff_chapters("", "# 第3章\n\n正文。")] == ["added", "added"]


def test_diff_delete_all_body_paragraphs() -> None:
    """全删：标题行保留、正文段落全部 removed。"""
    old = "# 第1章\n\n第一段。\n\n第二段。"
    new = "# 第1章"

    hunks = diff_chapters(old, new)

    assert [h.kind for h in hunks] == ["removed", "removed"]
    assert [h.old_index for h in hunks] == [1, 2]


def test_diff_trailing_newline_difference_is_noop() -> None:
    """无尾换行：仅差一个尾换行时不产生 hunk。"""
    old = "# 第1章\n\n第一段。"
    new = "# 第1章\n\n第一段。\n\n"
    assert diff_chapters(old, new) == []


def test_diff_heading_line_change_is_modified_hunk() -> None:
    """标题行变更：段号 0 的 modified hunk（标题是作者可改的 SSOT 字段）。"""
    old = "# 第3章 旧标题\n\n正文。"
    new = "# 第3章 新标题\n\n正文。"

    hunks = diff_chapters(old, new)

    assert len(hunks) == 1
    hunk = hunks[0]
    assert hunk.kind == "modified"
    assert hunk.old_index == 0
    assert hunk.new_index == 0
    assert hunk.old_text == "# 第3章 旧标题"
    assert hunk.new_text == "# 第3章 新标题"


def test_diff_paragraph_reorder_is_removed_and_added() -> None:
    """段落重排：不做移动检测（非目标），SequenceMatcher 落为 added+removed。"""
    old = "# 第1章\n\n甲段。\n\n乙段。"
    new = "# 第1章\n\n乙段。\n\n甲段。"

    hunks = diff_chapters(old, new)
    kinds = [h.kind for h in hunks]

    # 交换被识别为「插入乙 + 删除乙」，不产生伪装的 modified 对
    assert sorted(kinds) == ["added", "removed"]
    assert all(h.kind != "modified" for h in hunks)


def test_diff_paragraph_split_one_to_two_is_modified_and_added() -> None:
    """段落拆分（1→2）：旧段按位置配为 modified，多出的新段 added。"""
    old = "# 第1章\n\n一段话说两件事。"
    new = "# 第1章\n\n先说第一件。\n\n再说第二件。"

    hunks = diff_chapters(old, new)

    assert [h.kind for h in hunks] == ["modified", "added"]
    assert hunks[0].old_index == 1
    assert hunks[0].new_index == 1
    assert hunks[0].old_text == "一段话说两件事。"
    assert hunks[0].new_text == "先说第一件。"
    assert hunks[1].new_index == 2
    assert hunks[1].new_text == "再说第二件。"


# --- validate_title_heading ---


def test_validate_title_accepts_with_and_without_title_text() -> None:
    validate_title_heading("# 第3章\n\n正文。")
    validate_title_heading("# 第3章 雪夜来客\n\n正文。")
    validate_title_heading("#第12章  长标题 仍可\n")


def test_validate_title_rejects_non_heading_first_line() -> None:
    with pytest.raises(InvalidChapterFormat) as exc_info:
        validate_title_heading("正文直接开始，没有标题。")
    message = str(exc_info.value)
    assert "首行" in message
    assert "为什么错" in message
    assert "怎样算对" in message


@pytest.mark.parametrize("content", ["## 第3章\n", "# 第三章\n", ""])
def test_validate_title_rejects_wrong_prefix_shapes(content: str) -> None:
    with pytest.raises(InvalidChapterFormat):
        validate_title_heading(content)
