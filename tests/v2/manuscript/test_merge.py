"""段落级三方合并（V03-5.2）：干净变更自动合并、双方重叠报冲突。

语义边界：合并单位是段落；「两边是否改成同一结果」是字符串比较，不做语义
推断；冲突只报告事实（base/ours/theirs 三段原文），不替作者决定保留哪边。
"""

from __future__ import annotations

from hastings.domain.manuscript.merge import merge_texts

P0 = "侦探在雨夜抵达旅馆。"
P1 = "前台声称只有一位客人。"
P2 = "门后是一条向下的楼梯。"
P3 = "有人在楼梯口放了一把伞。"

BASE = "\n\n".join((P0, P1, P2, P3))


def _merge(base: str, ours: str, theirs: str):
    return merge_texts(base=base, ours=ours, theirs=theirs)


def test_no_change_anywhere_is_clean() -> None:
    result = _merge(BASE, BASE, BASE)
    assert result.clean
    assert result.text == BASE
    assert result.conflicts == ()


def test_only_ours_changed_takes_ours() -> None:
    ours = "\n\n".join(("侦探在雪夜抵达旅馆。", P1, P2, P3))
    result = _merge(BASE, ours, BASE)
    assert result.clean
    assert result.text == ours


def test_only_theirs_changed_takes_theirs() -> None:
    theirs = "\n\n".join((P0, P1, "门后的楼梯断了。", P3))
    result = _merge(BASE, BASE, theirs)
    assert result.clean
    assert result.text == theirs


def test_disjoint_paragraph_changes_are_both_kept() -> None:
    """核心场景：作者改第一段，正式稿改第三段——两边都保留，无需作者介入。"""
    ours = "\n\n".join(("侦探在雪夜抵达旅馆。", P1, P2, P3))
    theirs = "\n\n".join((P0, P1, "门后的楼梯断了。", P3))
    result = _merge(BASE, ours, theirs)
    assert result.clean
    assert result.text.split("\n\n") == [
        "侦探在雪夜抵达旅馆。",
        P1,
        "门后的楼梯断了。",
        P3,
    ]


def test_identical_changes_on_both_sides_are_taken_once() -> None:
    same = "\n\n".join((P0, "前台空无一人。", P2, P3))
    result = _merge(BASE, same, same)
    assert result.clean
    assert result.text == same


def test_overlapping_changes_report_one_conflict_with_three_sides() -> None:
    ours = "\n\n".join((P0, "前台站着一个女人。", P2, P3))
    theirs = "\n\n".join((P0, "前台只有一部电话。", P2, P3))
    result = _merge(BASE, ours, theirs)
    assert not result.clean
    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert conflict.base == (P1,)
    assert conflict.ours == ("前台站着一个女人。",)
    assert conflict.theirs == ("前台只有一部电话。",)


def test_deletion_versus_modification_is_a_conflict() -> None:
    ours = "\n\n".join((P0, P2, P3))  # 删掉第二段
    theirs = "\n\n".join((P0, "前台只有一部电话。", P2, P3))
    result = _merge(BASE, ours, theirs)
    assert not result.clean
    assert result.conflicts[0].ours == ()
    assert result.conflicts[0].theirs == ("前台只有一部电话。",)


def test_both_sides_delete_the_same_paragraph_is_clean() -> None:
    deleted = "\n\n".join((P0, P2, P3))
    result = _merge(BASE, deleted, deleted)
    assert result.clean
    assert result.text == deleted


def test_insertion_at_the_edges_does_not_conflict_with_rewrites() -> None:
    """贴边插入与别人改写相邻段互不干扰：插入贴边不属于对方的改写区。"""
    ours = "\n\n".join(("序章：一切从雨夜开始。", P0, P1, P2, P3))
    theirs = "\n\n".join((P0, P1, P2, "伞是凶手留下的。"))
    result = _merge(BASE, ours, theirs)
    assert result.clean
    assert result.text.split("\n\n") == [
        "序章：一切从雨夜开始。",
        P0,
        P1,
        P2,
        "伞是凶手留下的。",
    ]


def test_insert_inside_the_others_rewritten_region_conflicts() -> None:
    """插进对方正改写的区域内部：位置已不存在，交给作者。"""
    ours = "\n\n".join((P0, "前台声称——", "只有一位客人。", P2, P3))
    theirs = "\n\n".join((P0, "前台空无一人，登记簿摊开着。", P2, P3))
    result = _merge(BASE, ours, theirs)
    assert not result.clean
    assert len(result.conflicts) == 1


def test_both_insert_at_the_same_point() -> None:
    ours = "\n\n".join((P0, "作者的注脚。", P1, P2, P3))
    theirs = "\n\n".join((P0, "正式稿的注脚。", P1, P2, P3))
    result = _merge(BASE, ours, theirs)
    assert not result.clean
    assert result.conflicts[0].ours == ("作者的注脚。",)
    assert result.conflicts[0].theirs == ("正式稿的注脚。",)


def test_both_insert_the_same_text_at_the_same_point_is_clean() -> None:
    same = "\n\n".join((P0, "同一个注脚。", P1, P2, P3))
    result = _merge(BASE, same, same)
    assert result.clean
    assert result.text == same


def test_adjacent_paragraph_changes_do_not_conflict() -> None:
    """各改相邻的一段：区域不重叠，两次修改都成立。"""
    ours = "\n\n".join((P0, "前台声称只有一位客人，姓林。", P2, P3))
    theirs = "\n\n".join((P0, P1, "门后的楼梯尽头有光。", P3))
    result = _merge(BASE, ours, theirs)
    assert result.clean
    assert result.text.split("\n\n") == [
        P0,
        "前台声称只有一位客人，姓林。",
        "门后的楼梯尽头有光。",
        P3,
    ]


def test_partially_overlapping_ranges_conflict() -> None:
    """一侧改 [1,3)，另一侧改 [2,4)：区域重叠，报冲突并给出整段事实。

    冲突区是两侧改动区间的**并集**（[1,4)），两边的效果都按这个完整区间
    计算——作者看到的不是片段，而是「这片区域两人各改成了什么样」。
    """
    ours = "\n\n".join((P0, "前台换了说法。", "电话响了。", P3))
    theirs = "\n\n".join((P0, P1, "楼梯改了方向。", "伞还在。"))
    result = _merge(BASE, ours, theirs)
    assert not result.clean
    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert conflict.base == (P1, P2, P3)
    assert conflict.ours == ("前台换了说法。", "电话响了。", P3)
    assert conflict.theirs == (P1, "楼梯改了方向。", "伞还在。")


def test_whole_chapter_rewritten_on_both_sides_conflicts() -> None:
    """两侧各自重写全章：没有可自动叠加的部分，只能作者选择。"""
    result = _merge(BASE, "全新的第一章。", "另一版第一章。")
    assert not result.clean
    assert len(result.conflicts) == 1
    assert result.conflicts[0].base == (P0, P1, P2, P3)


def test_conflicted_text_stays_readable_and_wire_is_serializable() -> None:
    """冲突不抛异常：文本按 ours 占位保持可读，wire 形状可直接进错误详情。"""
    ours = "\n\n".join((P0, "前台站着一个女人。", P2, P3))
    theirs = "\n\n".join((P0, "前台只有一部电话。", P2, P3))
    result = _merge(BASE, ours, theirs)
    assert result.text.split("\n\n")[1] == "前台站着一个女人。"
    wire = result.to_wire()
    assert wire["clean"] is False
    assert wire["conflicts"] == [
        {"base": [P1], "ours": ["前台站着一个女人。"], "theirs": ["前台只有一部电话。"]}
    ]


def test_empty_and_single_paragraph_inputs() -> None:
    assert _merge("", "", "").text == ""
    assert _merge("只有一段。", "只有一段，改了。", "只有一段。").text == "只有一段，改了。"
    conflict = _merge("只有一段。", "甲。", "乙。")
    assert not conflict.clean
