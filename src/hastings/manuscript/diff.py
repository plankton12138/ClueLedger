"""段落级 diff 纯函数（P5 T2，计划 D3）：空行分段 + SequenceMatcher hunks。

保存一经落盘即以此模块产出的 hunk 驱动归档与后续对账——纯函数，无 IO、
无时间、无环境依赖，行为全部可由文本断言。

hunk 形状（D3 明文）：{kind, old_index, new_index, old_text, new_text}。
已知限制（计划非目标）：不做段落移动检测。replace 块按位置配对为
modified（相邻两段同时被改时各自成 hunk），数量不等的一侧落为
removed/added；真正的段落重排由 SequenceMatcher 自然产出 insert/delete，
即 del+add；段落拆分/合并落为 modified+added/removed。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

type HunkKind = Literal["added", "removed", "modified"]


@dataclass(frozen=True, slots=True)
class DiffHunk:
    """一个段落级变更；不存在的一侧索引为 None、文本为空串。"""

    kind: HunkKind
    old_index: int | None
    new_index: int | None
    old_text: str
    new_text: str


def split_paragraphs(text: str) -> list[str]:
    """空行分段：一个或多个空白行分隔，连续非空行（去行尾空白）成段。

    段内换行保留——与前端呈现侧 splitParagraphs（会抹掉段内换行）不同，
    hunk 必须携带整段原文，段内字符高亮在前端做（D3）。
    行尾只去 ASCII 空格/制表符：全角空格 U+3000 是 CJK 排版内容，须保留。
    splitlines 天然处理无尾换行；首/尾空块丢弃。
    """
    paragraphs: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip():
            current.append(line.rstrip(" \t"))
        elif current:
            paragraphs.append("\n".join(current))
            current = []
    if current:
        paragraphs.append("\n".join(current))
    return paragraphs


_TITLE_PATTERN = re.compile(r"#\s*第\d+章(?:[ \t]+\S.*)?")


class InvalidChapterFormat(ValueError):
    """章节首行标题格式不合法（保存管线的字段级校验，D2/T2）。"""


def validate_title_heading(content: str) -> None:
    """校验首行 ``# 第N章`` 前缀（标题后缀任意）；不合法抛 InvalidChapterFormat。

    错误消息三要素：哪里错（首行）、为什么错（标题行是目录/阅读/对账的
    锚点）、怎样算对（保留前缀、标题文字可改）——经 HTTP 原样回给作者。
    """
    first_line = content.split("\n", 1)[0].strip()
    if _TITLE_PATTERN.fullmatch(first_line) is None:
        raise InvalidChapterFormat(
            "章节首行标题格式不合法："
            f"收到 {first_line!r}。"
            "为什么错：章节文件必须以标题行开头，目录/阅读与对账都依赖该锚点。"
            "怎样算对：首行保留 `# 第N章` 前缀（如 `# 第3章 雪夜来客`），"
            "标题文字可以修改；正文从第二行开始。"
        )


def _removed(offset: int, paragraphs: list[str]) -> DiffHunk:
    return DiffHunk(
        kind="removed",
        old_index=offset,
        new_index=None,
        old_text=paragraphs[offset],
        new_text="",
    )


def _added(offset: int, paragraphs: list[str]) -> DiffHunk:
    return DiffHunk(
        kind="added",
        old_index=None,
        new_index=offset,
        old_text="",
        new_text=paragraphs[offset],
    )


def diff_chapters(old: str, new: str) -> list[DiffHunk]:
    """段落级 diff：返回 equal 之外的全部 hunk（顺序随文本位置）。

    replace 块按位置配对为 modified，多出来的旧段 removed、新段 added；
    insert/delete 直接是 added/removed——移动识别是本阶段非目标。
    """
    old_paras = split_paragraphs(old)
    new_paras = split_paragraphs(new)
    matcher = SequenceMatcher(a=old_paras, b=new_paras, autojunk=False)

    hunks: list[DiffHunk] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            paired = min(i2 - i1, j2 - j1)
            for offset in range(paired):
                hunks.append(
                    DiffHunk(
                        kind="modified",
                        old_index=i1 + offset,
                        new_index=j1 + offset,
                        old_text=old_paras[i1 + offset],
                        new_text=new_paras[j1 + offset],
                    )
                )
            old_extra = range(i1 + paired, i2)
            new_extra = range(j1 + paired, j2)
        else:  # insert：无旧侧；delete：无新侧
            old_extra = range(i1, i2)
            new_extra = range(j1, j2)
        hunks.extend(_removed(offset, old_paras) for offset in old_extra)
        hunks.extend(_added(offset, new_paras) for offset in new_extra)
    return hunks


__all__ = [
    "DiffHunk",
    "HunkKind",
    "InvalidChapterFormat",
    "diff_chapters",
    "split_paragraphs",
    "validate_title_heading",
]
