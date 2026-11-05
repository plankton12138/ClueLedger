"""正文修订的段落级差异与仍在稿内的片段判定（1.2 修订学习 P1）。

差异只由前后两份文本决定，不读磁盘、不看版本号：同一对文本永远得到同一组片段，
片段 ID 因此可以跨请求复用。界面按段纵向展示，段落内部再给字符级增删；模型分析
拿到的是同一组片段 ID，建议可以指回具体改动。

判定「这次改动还在不在当前稿里」只看片段本身：

* 改写与新增按改写后的段落文本在当前稿中定位，唯一命中即可分析；
* 纯删除要求删除处两侧的相邻段落仍然相邻，避免把别处的删除误算成这一处；
* 同一段落在当前稿里出现多次、又无法用相邻段落锚定时按歧义处理，宁可留在历史里，
  也不把猜测送给模型。
"""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "ALGORITHM_VERSION",
    "DiffHunk",
    "InlineSpan",
    "active_hunk_ids",
    "build_hunks",
    "split_blocks",
]

#: 算法版本进入片段 ID：改判定规则时旧缓存的片段不会被误认成新结果。
ALGORITHM_VERSION = "paragraph-v1"

#: 段落内部差异的字符上限；超过时整段标为改写，避免不必要的平方级比较。
_INLINE_LIMIT = 4000
#: 替换块按相似度配对：太不像的两个段落分开显示成删除与新增，读起来更清楚。
_PAIR_RATIO = 0.3

_BLANK_LINE = re.compile(r"\n[ \t]*\n")

Op = Literal["replace", "insert", "delete"]
SpanKind = Literal["equal", "insert", "delete"]


@dataclass(frozen=True, slots=True)
class InlineSpan:
    """段落内部的字符级片段；``equal`` 之外都是本次增删。"""

    kind: SpanKind
    text: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "text": self.text}


@dataclass(frozen=True, slots=True)
class DiffHunk:
    """一处段落级改动；行号只用于显示，定位依靠文本与指纹。"""

    id: str
    op: Op
    before: str
    after: str
    inline: tuple[InlineSpan, ...]
    before_index: int
    before_count: int
    after_index: int
    after_count: int
    #: 改写后的前一段落；用于重复段落与删除处的定位。
    before_context: str = ""
    after_context: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "op": self.op,
            "before": self.before,
            "after": self.after,
            "inline": [span.to_dict() for span in self.inline],
            "before_index": self.before_index,
            "before_count": self.before_count,
            "after_index": self.after_index,
            "after_count": self.after_count,
        }


def split_blocks(text: str) -> list[str]:
    """按空行切段；段内保留原有换行与空白，标题行自成一到多段。"""
    body = text.strip("\r\n")
    if not body.strip():
        return []
    return [block for block in _BLANK_LINE.split(body) if block.strip()]


def build_hunks(before: str, after: str, *, scope: str) -> list[DiffHunk]:
    """对比两份正文；``scope`` 参与片段 ID，通常传修订 ID。"""
    before_blocks = split_blocks(before)
    after_blocks = split_blocks(after)
    matcher = difflib.SequenceMatcher(None, before_blocks, after_blocks, autojunk=False)
    hunks: list[DiffHunk] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete":
            hunks.append(
                _hunk(
                    scope,
                    len(hunks),
                    "delete",
                    "\n\n".join(before_blocks[i1:i2]),
                    "",
                    (),
                    before_blocks,
                    after_blocks,
                    i1,
                    i2 - i1,
                    j1,
                    0,
                )
            )
            continue
        if tag == "insert":
            hunks.append(
                _hunk(
                    scope,
                    len(hunks),
                    "insert",
                    "",
                    "\n\n".join(after_blocks[j1:j2]),
                    (),
                    before_blocks,
                    after_blocks,
                    i1,
                    0,
                    j1,
                    j2 - j1,
                )
            )
            continue
        paired = _pair_replacements(before_blocks[i1:i2], after_blocks[j1:j2])
        left_index = i1
        right_index = j1
        for left, right in paired:
            index = len(hunks)
            op: Op = "replace" if left and right else ("delete" if left else "insert")
            hunks.append(
                _hunk(
                    scope,
                    index,
                    op,
                    left,
                    right,
                    _inline_spans(left, right),
                    before_blocks,
                    after_blocks,
                    left_index,
                    1 if left else 0,
                    right_index,
                    1 if right else 0,
                )
            )
            left_index += 1 if left else 0
            right_index += 1 if right else 0
    return hunks


def active_hunk_ids(hunks: list[DiffHunk], current_text: str) -> set[str]:
    """返回仍然能在当前正文中确认的片段；其余只留在历史里。"""
    blocks = split_blocks(current_text)
    active: set[str] = set()
    for hunk in hunks:
        if hunk.op == "delete":
            if _deletion_intact(hunk, blocks):
                active.add(hunk.id)
            continue
        if _paragraph_present(hunk, blocks):
            active.add(hunk.id)
    return active


def _hunk(
    scope: str,
    index: int,
    op: Op,
    before: str,
    after: str,
    inline: tuple[InlineSpan, ...],
    before_blocks: list[str],
    after_blocks: list[str],
    before_index: int,
    before_count: int,
    after_index: int,
    after_count: int,
) -> DiffHunk:
    identity = "\n".join([ALGORITHM_VERSION, scope, str(index), op, before, after])
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    before_context = before_blocks[before_index - 1] if before_index > 0 else ""
    after_context = (
        after_blocks[after_index + after_count]
        if after_index + after_count < len(after_blocks)
        else ""
    )
    return DiffHunk(
        id=f"hunk_{digest}",
        op=op,
        before=before,
        after=after,
        inline=inline,
        before_index=before_index,
        before_count=before_count,
        after_index=after_index,
        after_count=after_count,
        before_context=before_context,
        after_context=after_context,
    )


def _pair_replacements(before_blocks: list[str], after_blocks: list[str]) -> list[tuple[str, str]]:
    """按位置配对改写块：段数相同就一一对应，段数不同再按相似度取舍。"""
    if len(before_blocks) == len(after_blocks):
        return list(zip(before_blocks, after_blocks, strict=True))
    pairs: list[tuple[str, str]] = []
    shared = min(len(before_blocks), len(after_blocks))
    for index in range(shared):
        left = before_blocks[index]
        right = after_blocks[index]
        ratio = difflib.SequenceMatcher(None, left, right, autojunk=False).ratio()
        if ratio >= _PAIR_RATIO:
            pairs.append((left, right))
        else:
            pairs.append((left, ""))
            pairs.append(("", right))
    pairs.extend((left, "") for left in before_blocks[shared:])
    pairs.extend(("", right) for right in after_blocks[shared:])
    return pairs


def _inline_spans(before: str, after: str) -> tuple[InlineSpan, ...]:
    if not before or not after:
        return ()
    if len(before) > _INLINE_LIMIT or len(after) > _INLINE_LIMIT:
        return (
            InlineSpan("delete", before),
            InlineSpan("insert", after),
        )
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    spans: list[InlineSpan] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            _append_span(spans, "equal", before[i1:i2])
        elif tag == "delete":
            _append_span(spans, "delete", before[i1:i2])
        elif tag == "insert":
            _append_span(spans, "insert", after[j1:j2])
        else:
            _append_span(spans, "delete", before[i1:i2])
            _append_span(spans, "insert", after[j1:j2])
    return tuple(spans)


def _append_span(spans: list[InlineSpan], kind: SpanKind, text: str) -> None:
    if not text:
        return
    if spans and spans[-1].kind == kind:
        spans[-1] = InlineSpan(kind, spans[-1].text + text)
        return
    spans.append(InlineSpan(kind, text))


def _paragraph_present(hunk: DiffHunk, blocks: list[str]) -> bool:
    if not hunk.after:
        return False
    hits = [index for index, block in enumerate(blocks) if block == hunk.after]
    if len(hits) == 1:
        return True
    if not hits:
        return False
    # 重复段落：只有相邻段落能唯一定位时才认为改动仍然成立。
    return _anchored(hunk.after, hunk.before_context, hunk.after_context, blocks)


def _deletion_intact(hunk: DiffHunk, blocks: list[str]) -> bool:
    """删除仍然成立：被删段落不在稿内，且两侧锚点仍保持原有相邻关系。"""
    if not hunk.before:
        return False
    left, right = hunk.before_context, hunk.after_context
    if left and right:
        return any(
            blocks[index] == left and blocks[index + 1] == right for index in range(len(blocks) - 1)
        )
    if left:
        return left in blocks and not any(
            blocks[index] == left and blocks[index + 1] == hunk.before
            for index in range(len(blocks) - 1)
        )
    if right:
        return not any(
            blocks[index] == hunk.before and blocks[index + 1] == right
            for index in range(len(blocks) - 1)
        )
    return hunk.before not in blocks


def _anchored(text: str, left: str, right: str, blocks: list[str]) -> bool:
    """文本出现多次时，用相邻段落确认唯一位置。"""
    hits = 0
    for index, block in enumerate(blocks):
        if block != text:
            continue
        previous_ok = not left or (index > 0 and blocks[index - 1] == left)
        following_ok = not right or (index + 1 < len(blocks) and blocks[index + 1] == right)
        if previous_ok and following_ok:
            hits += 1
    return hits == 1
