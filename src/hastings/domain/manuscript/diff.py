"""段落切分与整章文本 diff（T14.1）：纯函数，无 IO、无环境依赖。

导入整章时以此建立初始 TextUnit；作者提交完整文本时以此计算编辑操作
（服务器侧规范化为 Edit Operations，再交领域命令）。语义与 V1
`manuscript/diff.py` 一致，此处收编为领域纯函数（Phase 14 起 V1 同模块退役）。
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

__all__ = ["ParagraphHunk", "diff_paragraphs", "split_paragraphs"]

type ParagraphHunkKind = Literal["added", "removed", "modified"]


def split_paragraphs(text: str) -> list[str]:
    """空行分段：一个或多个空白行分隔，连续非空行（去行尾空白）成段。

    段内换行保留——Markdown 段落内的软换行属于同一段；行尾只去 ASCII
    空格/制表符，全角空格 U+3000 是 CJK 排版内容须保留。首/尾空块丢弃。
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


@dataclass(frozen=True, slots=True)
class ParagraphHunk:
    """一个段落级差异；不存在的一侧索引为 None、文本为空串。"""

    kind: ParagraphHunkKind
    old_index: int | None
    new_index: int | None
    old_text: str
    new_text: str


def diff_paragraphs(old: tuple[str, ...], new: tuple[str, ...]) -> tuple[ParagraphHunk, ...]:
    """段落级 diff：返回 equal 之外的全部 hunk（顺序随文本位置）。

    replace 块按位置配对为 modified，多出来的旧段 removed、新段 added；
    insert/delete 直接是 added/removed——移动识别不在本阶段范围。
    """
    matcher = SequenceMatcher(a=list(old), b=list(new), autojunk=False)
    hunks: list[ParagraphHunk] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            paired = min(i2 - i1, j2 - j1)
            for offset in range(paired):
                hunks.append(
                    ParagraphHunk(
                        kind="modified",
                        old_index=i1 + offset,
                        new_index=j1 + offset,
                        old_text=old[i1 + offset],
                        new_text=new[j1 + offset],
                    )
                )
            old_extra = range(i1 + paired, i2)
            new_extra = range(j1 + paired, j2)
        else:  # insert：无旧侧；delete：无新侧
            old_extra = range(i1, i2)
            new_extra = range(j1, j2)
        hunks.extend(
            ParagraphHunk("removed", offset, None, old[offset], "") for offset in old_extra
        )
        hunks.extend(ParagraphHunk("added", None, offset, "", new[offset]) for offset in new_extra)
    return tuple(hunks)
