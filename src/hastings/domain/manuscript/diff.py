"""段落切分（T14.1）：纯函数，无 IO、无环境依赖。

导入整章时以此建立初始 TextUnit，正文指纹也按同一分段口径计算。
"""

from __future__ import annotations

__all__ = ["split_paragraphs"]


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
