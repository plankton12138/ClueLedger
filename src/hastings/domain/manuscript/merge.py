"""段落级三方合并（V03-5.2）：base（作者读到的版本）/ ours（副本草稿）/
theirs（当前正式稿）→ 合并稿 + 冲突清单。

正式稿在副本打开期间前进时，「修改完成」不能再简单地以旧基础覆盖——那会
丢掉别人（或另一页）的保存；也不该一律硬冲突——那会让任何一处无关修改
挡住整轮完成。三方合并按段落粒度把两边的变化叠起来：

- 只有一方改的区域 → 取那一方；
- 双方改成同一结果 → 取该结果；
- 双方改成不同结果 → **冲突**，带 base/ours/theirs 三段原文交给作者选择；
- 双方在相邻但不相交的区域各自修改 → 互不干扰，全部保留。

合并单位是段落（``split_paragraphs`` 的空行分段），与整章保存同一粒度。
判断是确定性字符串比较，不做语义推断——「这两段意思是否相同」交给作者，
代码只回答「这两段文本是否相同」。

冲突时的选择由作者做：本模块只报告事实（每处冲突的三段原文），不替作者
决定保留哪边。调用方可以给 ``choices``（冲突序号 → ``ours`` / ``theirs``）
指定每处冲突取哪边——选择仍由作者显式做出，本模块只是按选择拼装文本。
副本永不因合并失败而被丢弃。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher

from hastings.domain.manuscript.diff import split_paragraphs

__all__ = ["MergeConflict", "MergeResult", "merge_texts"]

#: 一侧对某段 base 区间的改动：None 表示该侧未改动这个区间。
_Replacement = tuple[str, ...] | None


@dataclass(frozen=True, slots=True)
class MergeConflict:
    """一处双方都改且结果不同的区域。

    ``base`` / ``ours`` / ``theirs`` 都是该区域的段落原文；某一侧为空表示
    那一侧删除了这段区域（纯插入冲突时三者里的 base 为空）。
    """

    base: tuple[str, ...]
    ours: tuple[str, ...]
    theirs: tuple[str, ...]

    def to_wire(self) -> dict[str, list[str]]:
        return {
            "base": list(self.base),
            "ours": list(self.ours),
            "theirs": list(self.theirs),
        }


@dataclass(frozen=True, slots=True)
class MergeResult:
    """合并结果：文本 + 冲突清单（干净时冲突为空）。"""

    text: str
    conflicts: tuple[MergeConflict, ...] = ()
    ours_paragraphs: int = 0
    theirs_paragraphs: int = 0

    @property
    def clean(self) -> bool:
        return not self.conflicts

    def to_wire(self) -> dict[str, object]:
        return {
            "text": self.text,
            "clean": self.clean,
            "conflicts": [conflict.to_wire() for conflict in self.conflicts],
            "ours_paragraphs": self.ours_paragraphs,
            "theirs_paragraphs": self.theirs_paragraphs,
        }


def merge_texts(
    *,
    base: str,
    ours: str,
    theirs: str,
    choices: Mapping[int, str] | None = None,
) -> MergeResult:
    """三方合并整章文本；输入输出都是整章文本（空行分段）。

    ``choices`` 按**冲突序号**（``result.conflicts`` 的顺序）指定取哪边：
    ``theirs`` 取正式稿，其余（含 ``ours``）取副本。冲突仍会如实记录在
    ``conflicts`` 里——选过之后它们也是事实（这次合并动过哪里），只是文本
    按选择拼装。序号稳定性由输入的确定性保证：同样三段文本 → 同样冲突顺序。
    """
    base_parts = tuple(split_paragraphs(base))
    ours_parts = tuple(split_paragraphs(ours))
    theirs_parts = tuple(split_paragraphs(theirs))
    merged, conflicts = _merge(base_parts, ours_parts, theirs_parts, choices or {})
    return MergeResult(
        text="\n\n".join(merged),
        conflicts=tuple(conflicts),
        ours_paragraphs=len(ours_parts),
        theirs_paragraphs=len(theirs_parts),
    )


# ---------------------------------------------------------------------------
# 内部：区间合并
# ---------------------------------------------------------------------------


def _intervals(
    matcher: SequenceMatcher[str], new: tuple[str, ...]
) -> list[tuple[int, int, tuple[str, ...]]]:
    """把一侧的 diff 变成 base 上的改动区间列表。

    区间 ``(i, j, repl)``：``base[i:j]`` 被替换为 ``repl``；``i == j`` 表示
    在 base[i] 之前的纯插入（``repl`` 为非空）；``j > i`` 且 ``repl`` 为空
    表示删除 ``base[i:j]``。
    """
    result: list[tuple[int, int, tuple[str, ...]]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        result.append((i1, i2, new[j1:j2]))
    return result


def _touches(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """两个改动区间是否落在同一个「 contested 区域」里。

    - 两个实质区间：严格重叠才算（相邻不算——各改一段是干净合并）；
    - 点区间（插入）落在实质区间的**内部**才算；贴边不算（在别人改写区
      的紧外侧插入，两边都成立）；
    - 两个点区间：位置相同才算。
    """
    (a1, a2), (b1, b2) = a, b
    if a1 == a2 and b1 == b2:
        return a1 == b1
    if a1 == a2:
        return b1 < a1 < b2
    if b1 == b2:
        return a1 < b1 < a2
    return a1 < b2 and b1 < a2


def _segments(
    ours: list[tuple[int, int, tuple[str, ...]]],
    theirs: list[tuple[int, int, tuple[str, ...]]],
) -> list[tuple[int, int]]:
    """把两侧的实质改动区间按「严格重叠」合并成 contested 区域。

    区间按 (start, end) 排序后线性扫描；与当前区域有任何重叠就并进去。
    点区间（插入）不参与区域生成——贴边的插入自成零长区域，内部的插入
    在扫描后归并到包含它的区域。
    """
    solids = sorted(
        [(i, j) for i, j, _ in (*ours, *theirs) if j > i],
    )
    regions: list[list[int]] = []
    for start, end in solids:
        if regions and start < regions[-1][1]:
            regions[-1][1] = max(regions[-1][1], end)
        else:
            regions.append([start, end])
    points = sorted({(i, i) for i, j, _ in (*ours, *theirs) if i == j})
    for point in points:
        for region in regions:
            if region[0] < point[0] < region[1]:
                break  # 插入在某区域内部：与该区域同段裁决
        else:
            regions.append([point[0], point[0]])
    regions.sort()
    return [(region[0], region[1]) for region in regions]


def _effect(
    matcher: SequenceMatcher[str],
    base: tuple[str, ...],
    side: tuple[str, ...],
    start: int,
    end: int,
) -> tuple[str, ...]:
    """某一侧对 ``base[start:end]`` 产生的内容。

    该侧在这个区域里的实质改动必然被完整包含（区域由重叠扫描生成），等值
    部分按区域裁剪后原样带回。插入位置（零长 opcode）只归**严格内部**或
    它自己的零长区域——贴边的插入属于相邻区域，不能在这里重复计入。
    """
    out: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            if i1 < end and start < i2:
                out.extend(base[max(i1, start) : min(i2, end)])
            continue
        if i1 == i2:
            # 纯插入：位置 p。严格内部属于本区域；p 贴边时归相邻的零长区域。
            if start < i1 < end or (start == end == i1):
                out.extend(side[j1:j2])
            continue
        if i1 < end and start < i2:
            out.extend(side[j1:j2])
    return tuple(out)


def _merge(
    base: tuple[str, ...],
    ours: tuple[str, ...],
    theirs: tuple[str, ...],
    choices: Mapping[int, str],
) -> tuple[list[str], list[MergeConflict]]:
    ours_matcher = SequenceMatcher(None, base, ours, autojunk=False)
    theirs_matcher = SequenceMatcher(None, base, theirs, autojunk=False)
    ours_ivs = _intervals(ours_matcher, ours)
    theirs_ivs = _intervals(theirs_matcher, theirs)

    merged: list[str] = []
    conflicts: list[MergeConflict] = []
    pos = 0
    for start, end in _segments(ours_ivs, theirs_ivs):
        merged.extend(base[pos:start])
        ours_hit = any(_touches((i, j), (start, end)) for i, j, _ in ours_ivs)
        theirs_hit = any(_touches((i, j), (start, end)) for i, j, _ in theirs_ivs)
        ours_side = _effect(ours_matcher, base, ours, start, end)
        theirs_side = _effect(theirs_matcher, base, theirs, start, end)
        if not ours_hit:
            merged.extend(theirs_side)  # 只有正式稿动了这里
        elif not theirs_hit:
            merged.extend(ours_side)  # 只有副本动了这里
        elif ours_side == theirs_side:
            merged.extend(ours_side)  # 两边改成同一结果
        else:
            index = len(conflicts)
            conflicts.append(
                MergeConflict(base=base[start:end], ours=ours_side, theirs=theirs_side)
            )
            # 冲突区域默认按 ours 占位（文本保持可读）；作者做过选择就按选择拼装。
            chosen = theirs_side if choices.get(index) == "theirs" else ours_side
            merged.extend(chosen)
        pos = end
    merged.extend(base[pos:])
    return merged, conflicts
