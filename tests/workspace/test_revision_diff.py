"""1.2 修订学习 P1：段落级差异与可分析片段判定。"""

from __future__ import annotations

from hastings.workspace.revision_diff import (
    DiffHunk,
    active_hunk_ids,
    build_hunks,
    split_blocks,
)

BEFORE = """# 第七章

林岚很紧张，她害怕许砚继续追问。

窗外的雨没有停。

许砚合上笔记本。"""

AFTER = """# 第七章

林岚把杯沿转了半圈。「你刚才说到哪儿了？」

窗外的雨没有停。

许砚合上笔记本。"""


def _only(hunks: list[DiffHunk]) -> DiffHunk:
    assert len(hunks) == 1
    return hunks[0]


def test_replaces_one_paragraph_with_inline_spans() -> None:
    hunks = build_hunks(BEFORE, AFTER, scope="revision_a")

    hunk = _only(hunks)
    assert hunk.op == "replace"
    assert hunk.before.startswith("林岚很紧张")
    assert hunk.after.startswith("林岚把杯沿转了半圈")
    assert any(span.kind == "delete" for span in hunk.inline)
    assert any(span.kind == "insert" for span in hunk.inline)
    assert "".join(span.text for span in hunk.inline if span.kind != "insert") == hunk.before


def test_hunk_ids_are_stable_for_same_input() -> None:
    first = build_hunks(BEFORE, AFTER, scope="revision_a")
    second = build_hunks(BEFORE, AFTER, scope="revision_a")
    other = build_hunks(BEFORE, AFTER, scope="revision_b")

    assert [h.id for h in first] == [h.id for h in second]
    assert first[0].id != other[0].id


def test_edited_paragraph_stays_active_after_unrelated_edits() -> None:
    hunks = build_hunks(BEFORE, AFTER, scope="revision_a")
    current = AFTER.replace("窗外的雨没有停。", "窗外的雨小了些，檐口还在滴水。")

    assert active_hunk_ids(hunks, current) == {hunks[0].id}


def test_edited_paragraph_inactive_after_reverting_it() -> None:
    hunks = build_hunks(BEFORE, AFTER, scope="revision_a")

    assert active_hunk_ids(hunks, BEFORE) == set()


def test_inserted_paragraph_is_active_only_while_present() -> None:
    after = BEFORE.replace(
        "窗外的雨没有停。", "窗外的雨没有停。\n\n她把窗推开一条缝。"
    )
    hunks = build_hunks(BEFORE, after, scope="revision_a")
    insert = next(h for h in hunks if h.op == "insert")

    assert active_hunk_ids(hunks, after) == {insert.id}
    assert active_hunk_ids(hunks, BEFORE) == set()


def test_deleted_paragraph_stays_active_until_neighbours_change() -> None:
    before = "# 第七章\n\n第一段。\n\n第二段。\n\n第三段。"
    after = "# 第七章\n\n第一段。\n\n第三段。"
    hunks = build_hunks(before, after, scope="revision_a")
    delete = next(h for h in hunks if h.op == "delete")

    assert active_hunk_ids(hunks, after) == {delete.id}
    assert active_hunk_ids(hunks, before) == set()


def test_deleted_duplicate_paragraph_needs_adjacent_context() -> None:
    before = "他说好。\n\n门关上了。\n\n他说好。"
    after = "他说好。\n\n门关上了。"
    hunks = build_hunks(before, after, scope="revision_a")
    delete = _only(hunks)

    assert delete.op == "delete"
    # 同样的句子在开头还出现过，结尾那处无法单凭文本确认。
    assert active_hunk_ids(hunks, after) == {delete.id}
    assert active_hunk_ids(hunks, "他说好。\n\n门关上了。\n\n他说好。") == set()


def test_repeated_rewrite_without_anchor_is_not_active() -> None:
    before = "他点头。\n\n她笑了。\n\n他点头。"
    after = "他点头。\n\n她笑了一下。\n\n他点头。"
    hunks = build_hunks(before, after, scope="revision_a")
    hunk = _only(hunks)
    # 两段完全相同、相邻段落也相同的改写无法判断分析哪一处。
    ambiguous = "他点头。\n\n她笑了一下。\n\n他点头。\n\n她笑了一下。\n\n他点头。"

    assert active_hunk_ids(hunks, after) == {hunk.id}
    assert active_hunk_ids(hunks, ambiguous) == set()


def test_split_blocks_keeps_heading_and_paragraphs() -> None:
    blocks = split_blocks(BEFORE)

    assert blocks[0] == "# 第七章"
    assert blocks[1].startswith("林岚很紧张")
    assert len(blocks) == 4
